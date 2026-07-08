from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping, Sequence

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QStackedLayout,
    QHeaderView,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from app.ui.common.errors import controlled_error_text
from app.ui.common.safe_text import SafeTextLabel, normalize_plain_text


class TableState(StrEnum):
    READY = "ready"
    LOADING = "loading"
    EMPTY = "empty"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class TableColumn:
    key: str
    title: str
    width: int | None = None
    alignment: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignLeft


class PlainTextTableModel(QAbstractTableModel):
    def __init__(self, columns: Sequence[TableColumn] = (), parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._columns = tuple(columns)
        self._rows: tuple[Mapping[str, Any] | Sequence[Any], ...] = ()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._columns)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        if role in {Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole}:
            value = self._value_at(index.row(), index.column())
            # QTableView paints DisplayRole/ToolTipRole as text here; keeping the
            # model text-only prevents untrusted device/import/user strings from becoming markup.
            max_chars = 1024 if role == Qt.ItemDataRole.DisplayRole else 2048
            return normalize_plain_text(value, max_chars=max_chars)
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return self._columns[index.column()].alignment | Qt.AlignmentFlag.AlignVCenter
        return None

    def headerData(  # noqa: N802
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if role != Qt.ItemDataRole.DisplayRole or orientation != Qt.Orientation.Horizontal:
            return None
        if section < 0 or section >= len(self._columns):
            return None
        return normalize_plain_text(self._columns[section].title, max_chars=128)

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def set_columns(self, columns: Sequence[TableColumn]) -> None:
        self.beginResetModel()
        self._columns = tuple(columns)
        self._rows = ()
        self.endResetModel()

    def set_rows(self, rows: Sequence[Mapping[str, Any] | Sequence[Any]]) -> None:
        self.beginResetModel()
        self._rows = tuple(rows)
        self.endResetModel()

    def _value_at(self, row: int, column: int) -> object:
        item = self._rows[row]
        table_column = self._columns[column]
        if isinstance(item, Mapping):
            return item.get(table_column.key, "")
        if column < len(item):
            return item[column]
        return ""


class DataTable(QWidget):
    retryRequested = Signal()
    pageChanged = Signal(int, int)
    exportRequested = Signal()
    emptyActionRequested = Signal()

    def __init__(self, columns: Sequence[TableColumn] = (), parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._state = TableState.READY
        self._page = 1
        self._per_page = 20
        self._total = 0

        self._model = PlainTextTableModel(columns, self)
        self.table = QTableView(self)
        self.table.setModel(self._model)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(False)
        self.table.setWordWrap(False)
        self.table.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        self._apply_column_layout(columns)

        self._stack = QStackedLayout()
        self._ready_page = QWidget(self)
        ready_layout = QVBoxLayout(self._ready_page)
        ready_layout.setContentsMargins(0, 0, 0, 0)
        ready_layout.addWidget(self.table)

        self._loading_page = self._build_loading_page()
        self._empty_page = self._build_message_page("empty", "暂无数据")
        self._error_page = self._build_error_page()
        self._stack.addWidget(self._ready_page)
        self._stack.addWidget(self._loading_page)
        self._stack.addWidget(self._empty_page)
        self._stack.addWidget(self._error_page)

        body = QFrame(self)
        body.setProperty("panel", "true")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.addLayout(self._stack)

        self.export_button = QPushButton("导出")
        self.export_button.clicked.connect(self.exportRequested)
        self.prev_button = QPushButton("上一页")
        self.next_button = QPushButton("下一页")
        self.prev_button.clicked.connect(lambda: self._emit_page(self._page - 1))
        self.next_button.clicked.connect(lambda: self._emit_page(self._page + 1))
        self.page_label = SafeTextLabel("第 1 页 / 共 0 页", selectable=False)
        self.page_label.setProperty("role", "muted")

        footer = QHBoxLayout()
        footer.addWidget(self.export_button)
        footer.addStretch(1)
        footer.addWidget(self.prev_button)
        footer.addWidget(self.page_label)
        footer.addWidget(self.next_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(body)
        layout.addLayout(footer)
        self._apply_pagination_state()

    def set_columns(self, columns: Sequence[TableColumn]) -> None:
        self._model.set_columns(columns)
        self._apply_column_layout(columns)

    def _apply_column_layout(self, columns: Sequence[TableColumn]) -> None:
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        for index, column in enumerate(columns):
            if column.width is not None:
                self.table.setColumnWidth(index, column.width)

    def set_rows(self, rows: Sequence[Mapping[str, Any] | Sequence[Any]]) -> None:
        self._model.set_rows(rows)
        if rows and self._state in {TableState.EMPTY, TableState.ERROR}:
            self.set_state(TableState.READY)

    def set_page(self, page: int, total: int, per_page: int | None = None) -> None:
        if page < 1:
            raise ValueError("page must be greater than or equal to 1")
        if total < 0:
            raise ValueError("total must be greater than or equal to 0")
        if per_page is not None:
            if per_page < 1:
                raise ValueError("per_page must be greater than or equal to 1")
            self._per_page = per_page
        self._page = page
        self._total = total
        self._apply_pagination_state()

    def set_state(self, state: TableState | str, message: object = "") -> None:
        self._state = TableState(state)
        if self._state is TableState.READY:
            self._stack.setCurrentWidget(self._ready_page)
        elif self._state is TableState.LOADING:
            self._loading_message.set_safe_text(message or "正在加载数据")
            self._stack.setCurrentWidget(self._loading_page)
        elif self._state is TableState.EMPTY:
            self._empty_message.set_safe_text(message or "暂无数据")
            self._stack.setCurrentWidget(self._empty_page)
        else:
            self._error_message.set_safe_text(controlled_error_text(message))
            self._stack.setCurrentWidget(self._error_page)
        self._apply_pagination_state()

    def state(self) -> TableState:
        return self._state

    def model(self) -> PlainTextTableModel:
        return self._model

    def _emit_page(self, page: int) -> None:
        total_pages = self._total_pages()
        if page < 1 or (total_pages and page > total_pages) or self._state is TableState.LOADING:
            return
        self.pageChanged.emit(page, self._per_page)

    def _apply_pagination_state(self) -> None:
        loading = self._state is TableState.LOADING
        total_pages = self._total_pages()
        self.export_button.setDisabled(loading)
        self.prev_button.setDisabled(loading or self._page <= 1)
        self.next_button.setDisabled(loading or total_pages == 0 or self._page >= total_pages)
        self.page_label.set_safe_text(f"第 {self._page} 页 / 共 {total_pages} 页，共 {self._total} 条")

    def _total_pages(self) -> int:
        if self._total == 0:
            return 0
        return (self._total + self._per_page - 1) // self._per_page

    def _build_loading_page(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        progress = QProgressBar(page)
        progress.setRange(0, 0)
        self._loading_message = SafeTextLabel("正在加载数据", selectable=False)
        self._loading_message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(progress)
        layout.addWidget(self._loading_message)
        return page

    def _build_message_page(self, role: str, message: str) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon = QLabel("—", page)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setProperty("role", f"{role}Icon")
        self._empty_message = SafeTextLabel(message, selectable=False)
        self._empty_message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_action_button = QPushButton("下一步", page)
        self.empty_action_button.clicked.connect(self.emptyActionRequested)
        layout.addWidget(icon)
        layout.addWidget(self._empty_message)
        layout.addWidget(self.empty_action_button, alignment=Qt.AlignmentFlag.AlignCenter)
        return page

    def _build_error_page(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._error_message = SafeTextLabel("操作失败，请稍后重试。", selectable=True)
        self._error_message.setProperty("role", "errorText")
        self._error_message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.retry_button = QPushButton("重试", page)
        self.retry_button.setProperty("variant", "primary")
        self.retry_button.clicked.connect(self.retryRequested)
        layout.addWidget(self._error_message)
        layout.addWidget(self.retry_button, alignment=Qt.AlignmentFlag.AlignCenter)
        return page
