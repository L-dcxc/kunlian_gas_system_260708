from __future__ import annotations

from typing import Any, Final, Iterable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from app.ui.common.data_table import DataTable, TableColumn, TableState
from app.ui.common.errors import ErrorBanner, controlled_error_text
from app.ui.common.safe_text import SafeTextLabel, normalize_plain_text
from app.ui.common.status import repolish

LOAD_FAILED_TEXT: Final[str] = "联动记录读取失败，请稍后重试。"
NO_FACADE_TEXT: Final[str] = "联动记录查询服务未配置，当前仅显示自动触发状态占位。"
TRIGGER_REASON_LABELS: Final[dict[str, str]] = {
    "manual": "手动控制",
    "automatic_alarm": "自动报警触发",
}


class LinkageRecordsPanel(QFrame):
    def __init__(
        self,
        linkage_service: object | None = None,
        session: object | None = None,
        parent: QWidget | None = None,
        *,
        alarm_record_id: int | None = None,
    ) -> None:
        super().__init__(parent)
        self.setProperty("panel", "true")
        self._service = linkage_service
        self._session = session
        self._alarm_record_id = alarm_record_id
        self._rows: tuple[dict[str, object], ...] = ()
        self._busy = False

        self.title_label = SafeTextLabel("联动记录与自动触发", selectable=False)
        self.title_label.setProperty("role", "panelTitle")
        self.subtitle_label = SafeTextLabel("查看手动联动结果和持续报警期间的自动触发状态。", selectable=False)
        self.subtitle_label.setProperty("role", "muted")
        self.status_card = QFrame()
        self.status_card.setProperty("linkageStatus", "idle")
        self.status_title = SafeTextLabel("自动联动状态", selectable=False)
        self.status_detail = SafeTextLabel("暂无持续报警自动触发记录", selectable=True)
        status_layout = QVBoxLayout(self.status_card)
        status_layout.setContentsMargins(12, 10, 12, 10)
        status_layout.addWidget(self.status_title)
        status_layout.addWidget(self.status_detail)

        self.error_banner = ErrorBanner(); self.error_banner.clear()
        self.refresh_button = QPushButton("刷新记录")
        self.refresh_button.clicked.connect(self.reload)
        self.table = DataTable(
            [
                TableColumn("id", "ID", 58, Qt.AlignmentFlag.AlignRight),
                TableColumn("created_at", "时间", 170),
                TableColumn("object_label", "联动对象", 160),
                TableColumn("action", "动作", 120),
                TableColumn("trigger_reason_label", "触发来源", 120),
                TableColumn("trigger_status", "状态", 100),
                TableColumn("alarm_record_id", "报警记录", 90, Qt.AlignmentFlag.AlignRight),
                TableColumn("rule_id", "规则", 70, Qt.AlignmentFlag.AlignRight),
                TableColumn("message", "原因/结果", 260),
            ]
        )
        self.table.export_button.setVisible(False)
        self.table.retryRequested.connect(self.reload)

        actions = QHBoxLayout()
        actions.addStretch(1)
        actions.addWidget(self.refresh_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        layout.addWidget(self.title_label)
        layout.addWidget(self.subtitle_label)
        layout.addWidget(self.status_card)
        layout.addWidget(self.error_banner)
        layout.addLayout(actions)
        layout.addWidget(self.table, 1)
        self._apply_state()

    def set_alarm_record_id(self, alarm_record_id: int | None) -> None:
        self._alarm_record_id = alarm_record_id if _valid_positive_int(alarm_record_id) else None

    def reload(self) -> None:
        if self._busy:
            return
        self.error_banner.clear()
        self._set_busy(True)
        self.table.set_state(TableState.LOADING, "正在加载联动记录")
        try:
            records = self._query_records()
        except Exception:
            self._show_table_error(LOAD_FAILED_TEXT)
            self._set_busy(False)
            return
        self._set_busy(False)
        if records is None:
            self._rows = ()
            self.table.set_rows([])
            self.table.set_page(1, 0, 1)
            self.table.set_state(TableState.EMPTY, NO_FACADE_TEXT)
            self._update_status_card(())
            return
        self._rows = tuple(_record_to_table(row) for row in _dedupe_records(records))
        self.table.set_rows(self._rows)
        self.table.set_page(1, len(self._rows), max(1, len(self._rows) or 1))
        self.table.set_state(TableState.READY if self._rows else TableState.EMPTY, "暂无联动记录")
        self._update_status_card(self._rows)

    def _query_records(self) -> tuple[object, ...] | None:
        if self._service is None:
            return None
        result: object
        if hasattr(self._service, "list_records"):
            result = self._service.list_records(self._session)
        elif hasattr(self._service, "list_linkage_records"):
            result = self._service.list_linkage_records(self._session)
        elif self._alarm_record_id is not None and hasattr(self._service, "list_for_alarm"):
            result = self._service.list_for_alarm(self._alarm_record_id)
        elif self._alarm_record_id is not None and hasattr(self._service, "list_records_for_alarm"):
            result = self._service.list_records_for_alarm(self._session, self._alarm_record_id)
        else:
            # Current LinkageService has no public record-query facade. The UI
            # stays read-only and does not bypass the service boundary.
            return None
        if bool(getattr(result, "success", False)):
            return _extract_items(getattr(result, "data", ()))
        if hasattr(result, "success"):
            self.error_banner.set_error(getattr(result, "message", LOAD_FAILED_TEXT))
            return ()
        return _extract_items(result)

    def _update_status_card(self, rows: Iterable[dict[str, object]]) -> None:
        automatic = [row for row in rows if row.get("trigger_reason") == "automatic_alarm"]
        failed = [row for row in rows if _is_failure(row.get("result"))]
        if failed:
            self.status_card.setProperty("linkageStatus", "error")
            self.status_detail.set_safe_text(f"联动失败：{failed[0].get('message', '原因未提供')}")
        elif automatic:
            self.status_card.setProperty("linkageStatus", "triggered")
            self.status_detail.set_safe_text(f"已触发：{len(automatic)} 条自动联动记录；同一报警与规则仅显示一次。")
        else:
            self.status_card.setProperty("linkageStatus", "idle")
            self.status_detail.set_safe_text("暂无持续报警自动触发记录")
        repolish(self.status_card)

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._apply_state()

    def _apply_state(self) -> None:
        self.refresh_button.setEnabled(not self._busy)

    def _show_table_error(self, message: object) -> None:
        self._rows = ()
        self.table.set_rows([])
        self.table.set_page(1, 0, 1)
        self.table.set_state(TableState.ERROR, controlled_error_text(message, fallback=LOAD_FAILED_TEXT))
        self._update_status_card(())


def _dedupe_records(records: Iterable[object]) -> tuple[object, ...]:
    seen_auto_keys: set[tuple[int, int]] = set()
    deduped: list[object] = []
    for record in records:
        reason = str(_value(record, "trigger_reason", ""))
        alarm_id = _optional_int(_value(record, "alarm_record_id", None))
        rule_id = _optional_int(_value(record, "rule_id", None))
        # Automatic linkage is deduplicated by the same key enforced in storage:
        # one alarm period plus one rule should render as an already-triggered state.
        if reason == "automatic_alarm" and alarm_id is not None and rule_id is not None:
            key = (alarm_id, rule_id)
            if key in seen_auto_keys:
                continue
            seen_auto_keys.add(key)
        deduped.append(record)
    return tuple(deduped)


def _record_to_table(record: object) -> dict[str, object]:
    reason = str(_value(record, "trigger_reason", ""))
    result = str(_value(record, "result", ""))
    object_name = _value(record, "object_name", None) or _value(record, "name", None)
    object_id = _value(record, "object_id", "-")
    return {
        "id": _value(record, "id", ""),
        "created_at": _safe_text(_value(record, "created_at", ""), max_chars=120),
        "object_label": _safe_text(object_name or f"对象 {object_id}", max_chars=160),
        "action": _safe_text(_value(record, "action", ""), max_chars=100),
        "trigger_reason": reason,
        "trigger_reason_label": TRIGGER_REASON_LABELS.get(reason, _safe_text(reason, max_chars=80)),
        "trigger_status": _status_text(reason, result),
        "alarm_record_id": _value(record, "alarm_record_id", "") or "",
        "rule_id": _value(record, "rule_id", "") or "",
        "result": result,
        "message": _safe_record_message(_value(record, "message", "")),
    }


def _status_text(reason: str, result: str) -> str:
    if _is_failure(result):
        return "失败"
    if reason == "automatic_alarm":
        return "已触发"
    if result:
        return "执行完成"
    return "待确认"


def _is_failure(result: object) -> bool:
    text = str(result).lower()
    return any(marker in text for marker in ("fail", "error", "denied", "timeout"))


def _extract_items(data: object) -> tuple[object, ...]:
    if data is None:
        return ()
    if hasattr(data, "items"):
        return tuple(getattr(data, "items") or ())
    if isinstance(data, tuple) and len(data) == 2 and isinstance(data[1], int):
        return tuple(data[0] or ())
    if isinstance(data, list | tuple):
        return tuple(data)
    return ()


def _safe_record_message(value: object) -> str:
    return controlled_error_text(value, fallback="-", max_chars=240) or "-"


def _safe_text(value: object, *, max_chars: int) -> str:
    return controlled_error_text(normalize_plain_text(value, max_chars=max_chars), fallback="-", max_chars=max_chars) or "-"


def _optional_int(value: object) -> int | None:
    if value in {None, ""}:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _valid_positive_int(value: object) -> bool:
    return _optional_int(value) is not None


def _value(source: object, key: str, default: object = None) -> object:
    if isinstance(source, dict):
        return source.get(key, default)
    return getattr(source, key, default)


__all__ = ["LinkageRecordsPanel"]
