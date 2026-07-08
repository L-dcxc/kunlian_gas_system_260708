from __future__ import annotations

from typing import Mapping

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QTabWidget, QVBoxLayout, QWidget

from app.ui.common.data_table import DataTable, TableColumn, TableState
from app.ui.common.errors import ErrorBanner, controlled_error_text
from app.ui.common.safe_text import SafeTextLabel
from app.ui.records.record_actions import RecordActionBar
from app.ui.records.record_filters import RECORD_TYPE_LABELS, RecordFilterWidget, RecordType
from app.ui.records.view_models import RecordQueryViewModel, RecordRowsPage, table_columns


class RecordsPage(QWidget):
    recordChanged = Signal(str)
    exportBuilt = Signal(object)
    printBuilt = Signal(object)

    def __init__(
        self,
        record_service: object,
        session: object | None = None,
        parent: QWidget | None = None,
        *,
        can_delete: bool | None = None,
        confirm_danger: object | None = None,
    ) -> None:
        super().__init__(parent)
        self.record_service = record_service
        self.session = session
        self.title_label = SafeTextLabel("记录查询", selectable=False)
        self.title_label.setProperty("role", "panelTitle")
        self.subtitle_label = SafeTextLabel("查询报警记录、运行记录和操作记录；危险删除操作需管理员确认。", selectable=False)
        self.subtitle_label.setProperty("role", "muted")
        self.error_banner = ErrorBanner(); self.error_banner.clear()
        self.tabs = QTabWidget(self)
        self.panes: dict[RecordType, RecordPane] = {}
        for record_type, label in RECORD_TYPE_LABELS.items():
            pane = RecordPane(record_type, record_service, session, can_delete=can_delete, confirm_danger=confirm_danger)
            pane.recordChanged.connect(lambda rt=record_type: self._record_changed(rt))
            pane.exportBuilt.connect(self.exportBuilt)
            pane.printBuilt.connect(self.printBuilt)
            self.panes[record_type] = pane
            self.tabs.addTab(pane, label)

        header = QFrame(self)
        header.setProperty("panel", "true")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(16, 16, 16, 16)
        header_layout.setSpacing(6)
        header_layout.addWidget(self.title_label)
        header_layout.addWidget(self.subtitle_label)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        layout.addWidget(header)
        layout.addWidget(self.error_banner)
        layout.addWidget(self.tabs, 1)

    def current_record_type(self) -> RecordType:
        for record_type, pane in self.panes.items():
            if pane is self.tabs.currentWidget():
                return record_type
        return "alarm"

    def current_pane(self) -> "RecordPane":
        return self.panes[self.current_record_type()]

    def query_current(self) -> bool:
        return self.current_pane().query_records()

    def _record_changed(self, record_type: RecordType) -> None:
        self.panes[record_type].query_records(page=1)
        self.recordChanged.emit(record_type)


class RecordPane(QWidget):
    recordChanged = Signal()
    exportBuilt = Signal(object)
    printBuilt = Signal(object)

    def __init__(
        self,
        record_type: RecordType,
        record_service: object,
        session: object | None,
        parent: QWidget | None = None,
        *,
        can_delete: bool | None = None,
        confirm_danger: object | None = None,
    ) -> None:
        super().__init__(parent)
        self.record_type = record_type
        self.view_model = RecordQueryViewModel(record_service, session, record_type)
        self._rows: tuple[dict[str, object], ...] = ()
        self._selected_record_id: int | None = None
        self._last_filter_values = None

        self.filter_widget = RecordFilterWidget(record_type)
        self.filter_widget.panel.searchRequested.connect(lambda: self.query_records(page=1))
        self.filter_widget.panel.resetRequested.connect(self.reset_filters)

        self.table = DataTable(_table_columns(record_type), self)
        self.table.export_button.setText("导出 Excel")
        self.table.export_button.setVisible(False)
        self.table.retryRequested.connect(lambda: self.query_records(page=self.view_model.pagination.page))
        self.table.pageChanged.connect(self._change_page)
        self.table.empty_action_button.setText("重新查询")
        self.table.emptyActionRequested.connect(lambda: self.query_records(page=1))
        self.table.table.selectionModel().selectionChanged.connect(self._selection_changed)

        self.actions = RecordActionBar(
            record_service,
            session,
            self,
            can_delete=can_delete,
            confirm_danger=confirm_danger,
        )
        self.actions.recordChanged.connect(self.recordChanged)
        self.actions.exportBuilt.connect(self.exportBuilt)
        self.actions.printBuilt.connect(self.printBuilt)

        split = QHBoxLayout()
        split.addWidget(self.table, 1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        layout.addWidget(self.filter_widget)
        layout.addLayout(split, 1)
        layout.addWidget(self.actions)
        self._sync_actions()

    def query_records(self, *, page: int = 1) -> bool:
        if self.view_model.is_querying:
            return False
        values = self.filter_widget.collect(page=page)
        if values is None:
            self.table.set_state(TableState.ERROR, "输入内容校验失败，请检查后重试。")
            return False
        self._last_filter_values = values
        self._set_querying(True)
        try:
            result = self.view_model.query(values)
        finally:
            self._set_querying(False)
        if not bool(getattr(result, "success", False)):
            self._show_error(getattr(result, "message", "记录查询失败"))
            return False
        self._apply_page(getattr(result, "data", RecordRowsPage()))
        return True

    def reset_filters(self) -> None:
        self.filter_widget.reset()
        self.query_records(page=1)

    def selected_record_id(self) -> int | None:
        return self._selected_record_id

    def current_filters(self) -> dict[str, object]:
        return dict(self.view_model.current_filters)

    def _change_page(self, page: int, per_page: int) -> None:
        if self._last_filter_values is not None:
            self.query_records(page=page)
            return
        self.query_records(page=page)

    def _selection_changed(self) -> None:
        indexes = self.table.table.selectionModel().selectedRows()
        if not indexes:
            self._selected_record_id = None
        else:
            row = indexes[0].row()
            if 0 <= row < len(self._rows):
                self._selected_record_id = _record_id(self._rows[row])
            else:
                self._selected_record_id = None
        self._sync_actions()

    def _apply_page(self, page: RecordRowsPage) -> None:
        self._rows = tuple(page.rows)
        self._selected_record_id = None
        self.table.set_rows(self._rows)
        self.table.set_page(page.pagination.page, page.pagination.total, page.pagination.per_page)
        self.table.set_state(TableState.READY if self._rows else TableState.EMPTY, "当前筛选条件下无记录")
        self._sync_actions()

    def _show_error(self, message: object) -> None:
        safe = controlled_error_text(message, fallback="记录查询失败")
        self._rows = ()
        self._selected_record_id = None
        self.table.set_rows([])
        self.table.set_page(self.view_model.pagination.page, 0, self.view_model.pagination.per_page)
        self.table.set_state(TableState.ERROR, safe)
        self._sync_actions()

    def _set_querying(self, querying: bool) -> None:
        self.filter_widget.set_querying(querying)
        self.actions.set_busy(querying)
        if querying:
            self.table.set_state(TableState.LOADING, "正在查询记录...")
        self._sync_actions()

    def _sync_actions(self) -> None:
        self.actions.set_context(self.record_type, self._selected_record_id, self.view_model.export_command_filters())


def _table_columns(record_type: RecordType) -> tuple[TableColumn, ...]:
    columns: list[TableColumn] = []
    for item in table_columns(record_type):
        alignment = Qt.AlignmentFlag.AlignRight if item["key"] in {"id", "alarm_level", "trigger_value", "concentration"} else Qt.AlignmentFlag.AlignLeft
        columns.append(TableColumn(str(item["key"]), str(item["title"]), int(item.get("width") or 120), alignment))
    return tuple(columns)


def _record_id(row: Mapping[str, object]) -> int | None:
    try:
        value = int(row.get("id", 0))
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None
