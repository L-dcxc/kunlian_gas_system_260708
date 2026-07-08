from __future__ import annotations

from typing import Any, Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from app.ui.common.data_table import DataTable, TableColumn, TableState
from app.ui.common.safe_text import SafeTextLabel


class ImportResultDialog(QDialog):
    def __init__(
        self,
        *,
        imported_count: int = 0,
        errors: Sequence[object] = (),
        source_name: object = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("导入结果")
        self.setModal(True)
        self.resize(720, 420)
        self.imported_count = max(0, int(imported_count))
        self.errors = tuple(errors)

        self.title_label = SafeTextLabel("导入结果", selectable=False)
        self.title_label.setProperty("role", "dialogTitle")
        self.source_label = SafeTextLabel(f"文件：{source_name}", selectable=True)
        self.source_label.setProperty("role", "muted")
        self.summary_label = SafeTextLabel(selectable=False)

        self.table = DataTable(
            [
                TableColumn("row_number", "行号", width=80, alignment=Qt.AlignmentFlag.AlignRight),
                TableColumn("field", "字段", width=160),
                TableColumn("message", "原因", width=360),
            ]
        )
        self.table.export_button.setVisible(False)
        self.table.prev_button.setVisible(False)
        self.table.next_button.setVisible(False)
        self.table.page_label.setVisible(False)

        self.close_button = QPushButton("关闭")
        self.close_button.setProperty("variant", "primary")
        self.close_button.clicked.connect(self.accept)

        actions = QHBoxLayout()
        actions.addStretch(1)
        actions.addWidget(self.close_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)
        layout.addWidget(self.title_label)
        layout.addWidget(self.source_label)
        layout.addWidget(self.summary_label)
        layout.addWidget(self.table, 1)
        layout.addLayout(actions)
        self._render()

    def _render(self) -> None:
        rows = [_error_to_row(item) for item in self.errors]
        self.summary_label.set_safe_text(f"成功 {self.imported_count} 行，失败 {len(rows)} 行")
        self.table.set_rows(rows)
        self.table.set_page(1, total=len(rows), per_page=max(1, len(rows) or 1))
        # Row-level import failures are kept visible instead of being collapsed
        # into a single message so operators can correct the source template.
        self.table.set_state(TableState.READY if rows else TableState.EMPTY, "未发现错误行")


def _error_to_row(item: object) -> dict[str, Any]:
    return {
        "row_number": _value(item, "row_number", ""),
        "field": _value(item, "field", ""),
        "message": _value(item, "message", item),
    }


def _value(source: object, key: str, default: object = None) -> object:
    if isinstance(source, dict):
        return source.get(key, default)
    return getattr(source, key, default)
