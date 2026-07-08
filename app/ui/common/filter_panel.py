from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.ui.common.errors import ValidationHint
from app.ui.common.safe_text import SafeTextLabel
from app.ui.common.status import repolish


@dataclass(slots=True)
class FilterField:
    key: str
    label: SafeTextLabel
    editor: QWidget
    hint: ValidationHint


class FilterPanel(QFrame):
    searchRequested = Signal()
    resetRequested = Signal()
    collapsedChanged = Signal(bool)

    def __init__(self, title: str = "筛选条件", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("widget", "filterPanel")
        self.setProperty("panel", "true")
        self._fields: dict[str, FilterField] = {}
        self._collapsed = False

        self._title_label = SafeTextLabel(title, selectable=False)
        self._title_label.setProperty("role", "panelTitle")
        self._toggle_button = QPushButton("收起")
        self._toggle_button.setProperty("variant", "subtle")
        self._toggle_button.clicked.connect(self.toggle_collapsed)

        header = QHBoxLayout()
        header.addWidget(self._title_label, 1)
        header.addWidget(self._toggle_button)

        self._fields_widget = QWidget(self)
        self._fields_layout = QGridLayout(self._fields_widget)
        self._fields_layout.setContentsMargins(0, 0, 0, 0)
        self._fields_layout.setHorizontalSpacing(12)
        self._fields_layout.setVerticalSpacing(6)

        self.search_button = QPushButton("查询")
        self.search_button.setProperty("variant", "primary")
        self.search_button.clicked.connect(self.searchRequested)
        self.reset_button = QPushButton("重置")
        self.reset_button.clicked.connect(self.resetRequested)

        actions = QHBoxLayout()
        actions.addStretch(1)
        actions.addWidget(self.reset_button)
        actions.addWidget(self.search_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)
        layout.addLayout(header)
        layout.addWidget(self._fields_widget)
        layout.addLayout(actions)

    def add_field(self, key: str, label: str, editor: QWidget) -> QWidget:
        if not key or key in self._fields:
            raise ValueError("filter field key must be unique")
        row = len(self._fields) * 2
        label_widget = SafeTextLabel(label, selectable=False)
        label_widget.setProperty("role", "fieldLabel")
        hint = ValidationHint(parent=self._fields_widget)
        hint.clear()
        editor.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self._fields_layout.addWidget(label_widget, row, 0, Qt.AlignmentFlag.AlignLeft)
        self._fields_layout.addWidget(editor, row, 1)
        self._fields_layout.addWidget(hint, row + 1, 1)
        self._fields[key] = FilterField(key=key, label=label_widget, editor=editor, hint=hint)
        return editor

    def field(self, key: str) -> QWidget:
        return self._fields[key].editor

    def set_validation_error(self, key: str, message: object | None) -> None:
        item = self._fields[key]
        if message:
            item.editor.setProperty("validation", "error")
            item.hint.set_validation_error(message)
        else:
            item.editor.setProperty("validation", None)
            item.hint.clear()
        repolish(item.editor)

    def clear_validation_errors(self) -> None:
        for key in self._fields:
            self.set_validation_error(key, None)

    def set_querying(self, querying: bool) -> None:
        self.search_button.setDisabled(querying)
        self.reset_button.setDisabled(querying)
        for field in self._fields.values():
            field.editor.setDisabled(querying)

    def is_collapsed(self) -> bool:
        return self._collapsed

    def set_collapsed(self, collapsed: bool) -> None:
        if self._collapsed == collapsed:
            return
        self._collapsed = collapsed
        self._fields_widget.setVisible(not collapsed)
        self.search_button.setVisible(not collapsed)
        self.reset_button.setVisible(not collapsed)
        self._toggle_button.setText("展开" if collapsed else "收起")
        self.setProperty("collapsed", collapsed)
        repolish(self)
        self.collapsedChanged.emit(collapsed)

    def toggle_collapsed(self) -> None:
        self.set_collapsed(not self._collapsed)

    def set_title(self, title: object) -> None:
        self._title_label.set_safe_text(title)

    @property
    def title_label(self) -> QLabel:
        return self._title_label
