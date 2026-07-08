from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

from PySide6.QtCore import QDateTime
from PySide6.QtWidgets import QComboBox, QDateTimeEdit, QLineEdit, QSpinBox, QVBoxLayout, QWidget

from app.ui.common.filter_panel import FilterPanel
from app.ui.common.safe_text import normalize_plain_text

RecordType = Literal["alarm", "running", "operation"]

MAX_RECORDS_PER_PAGE = 100
DEFAULT_RECORDS_PER_PAGE = 20
TIME_RANGE_ERROR = "结束时间不能早于开始时间"

RECORD_TYPE_LABELS: dict[RecordType, str] = {
    "alarm": "报警记录",
    "running": "运行记录",
    "operation": "操作记录",
}

# UI-side filter whitelists intentionally mirror service/repository contracts so
# hidden fields cannot accidentally be sent when users switch record types.
FILTER_WHITELISTS: dict[RecordType, frozenset[str]] = {
    "alarm": frozenset({"start_time", "end_time", "detector_id", "controller_id", "position_code", "alarm_type", "status"}),
    "running": frozenset({"start_time", "end_time", "detector_id", "controller_id", "port_id", "position_code", "status"}),
    "operation": frozenset({"start_time", "end_time", "username", "action_type", "result", "keyword"}),
}

_ALARM_TYPES = (
    ("全部", None),
    ("低报", "alarm_low"),
    ("高报", "alarm_high"),
    ("超量程", "over_range"),
    ("故障", "fault"),
    ("离线", "offline"),
    ("屏蔽", "disabled"),
    ("预热", "warming"),
)
_ALARM_STATUS = (("全部", None), ("未恢复", "active"), ("已恢复", "recovered"))
_RUNNING_STATUS = (
    ("全部", None),
    ("正常", "normal"),
    ("低报", "alarm_low"),
    ("高报", "alarm_high"),
    ("故障", "fault"),
    ("离线", "offline"),
    ("屏蔽", "disabled"),
    ("超量程", "over_range"),
    ("预热", "warming"),
    ("无效", "invalid"),
)
_OPERATION_RESULTS = (("全部", None), ("成功", "success"), ("失败", "failed"), ("拒绝", "denied"))


@dataclass(frozen=True, slots=True)
class RecordFilterValues:
    record_type: RecordType
    filters: dict[str, object]
    page: int = 1
    per_page: int = DEFAULT_RECORDS_PER_PAGE


class RecordFilterWidget(QWidget):
    def __init__(self, record_type: RecordType, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.record_type = _record_type(record_type)
        self.panel = FilterPanel(f"{RECORD_TYPE_LABELS[self.record_type]}筛选", self)
        self.start_time_edit: QDateTimeEdit
        self.end_time_edit: QDateTimeEdit
        self.per_page_spin: QSpinBox
        self._editors: dict[str, QWidget] = {}
        self._build_fields()

        wrapper_layout = QVBoxLayout(self)
        wrapper_layout.setContentsMargins(0, 0, 0, 0)
        wrapper_layout.addWidget(self.panel)

    def collect(self, *, page: int = 1) -> RecordFilterValues | None:
        self.panel.clear_validation_errors()
        if self.end_time_edit.dateTime() < self.start_time_edit.dateTime():
            self.panel.set_validation_error("end_time", TIME_RANGE_ERROR)
            return None
        filters: dict[str, object] = {
            "start_time": _editor_iso(self.start_time_edit),
            "end_time": _editor_iso(self.end_time_edit),
        }
        for key in FILTER_WHITELISTS[self.record_type]:
            if key in {"start_time", "end_time"}:
                continue
            editor = self._editors.get(key)
            if editor is None:
                continue
            value = _editor_value(editor)
            if value in {None, ""}:
                continue
            try:
                filters[key] = _normalize_filter_value(key, value)
            except ValueError as exc:
                self.panel.set_validation_error(key, str(exc))
                return None
        # per_page is intentionally a pagination control, not a backend filter.
        return RecordFilterValues(self.record_type, _whitelisted_filters(self.record_type, filters), page, self.per_page())

    def per_page(self) -> int:
        return min(max(int(self.per_page_spin.value()), 1), MAX_RECORDS_PER_PAGE)

    def set_querying(self, querying: bool) -> None:
        self.panel.set_querying(querying)

    def reset(self) -> None:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        self.start_time_edit.setDateTime(QDateTime(now - timedelta(hours=1)))
        self.end_time_edit.setDateTime(QDateTime(now))
        for key, editor in self._editors.items():
            if key in {"start_time", "end_time", "per_page"}:
                continue
            if isinstance(editor, QLineEdit):
                editor.clear()
            elif isinstance(editor, QComboBox):
                editor.setCurrentIndex(0)
        self.per_page_spin.setValue(DEFAULT_RECORDS_PER_PAGE)
        self.panel.clear_validation_errors()

    def has_field(self, key: str) -> bool:
        return key in self._editors or key in {"start_time", "end_time", "per_page"}

    def _build_fields(self) -> None:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        self.start_time_edit = _date_time_edit(now - timedelta(hours=1))
        self.end_time_edit = _date_time_edit(now)
        self.panel.add_field("start_time", "开始时间", self.start_time_edit)
        self.panel.add_field("end_time", "结束时间", self.end_time_edit)

        if self.record_type in {"alarm", "running"}:
            self._add_line("detector_id", "探测器 ID", "正整数")
            self._add_line("controller_id", "控制器 ID", "正整数")
            self._add_line("position_code", "位置编号", "按位置编号模糊查询")
        if self.record_type == "running":
            self._add_line("port_id", "端口 ID", "正整数")
            self._add_combo("status", "状态", _RUNNING_STATUS)
        if self.record_type == "alarm":
            self._add_combo("alarm_type", "报警类型", _ALARM_TYPES)
            self._add_combo("status", "状态", _ALARM_STATUS)
        if self.record_type == "operation":
            self._add_line("username", "用户名", "按用户名模糊查询")
            self._add_line("action_type", "日志类型", "示例：records.delete")
            self._add_combo("result", "结果", _OPERATION_RESULTS)
            self._add_line("keyword", "内容关键字", "按日志内容模糊查询")

        self.per_page_spin = QSpinBox()
        self.per_page_spin.setRange(1, MAX_RECORDS_PER_PAGE)
        self.per_page_spin.setValue(DEFAULT_RECORDS_PER_PAGE)
        self.panel.add_field("per_page", "每页条数", self.per_page_spin)

    def _add_line(self, key: str, label: str, placeholder: str = "") -> None:
        editor = QLineEdit()
        editor.setPlaceholderText(placeholder)
        self.panel.add_field(key, label, editor)
        self._editors[key] = editor

    def _add_combo(self, key: str, label: str, items: tuple[tuple[str, object], ...]) -> None:
        editor = QComboBox()
        for text, value in items:
            editor.addItem(text, value)
        self.panel.add_field(key, label, editor)
        self._editors[key] = editor


def _date_time_edit(value: datetime) -> QDateTimeEdit:
    editor = QDateTimeEdit(QDateTime(value))
    editor.setCalendarPopup(True)
    editor.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
    return editor


def _editor_value(editor: QWidget) -> object:
    if isinstance(editor, QLineEdit):
        return normalize_plain_text(editor.text().strip(), max_chars=120)
    if isinstance(editor, QComboBox):
        return editor.currentData()
    return None


def _normalize_filter_value(key: str, value: object) -> object:
    if key in {"detector_id", "controller_id", "port_id", "actor_id"}:
        try:
            parsed = int(str(value).strip())
        except ValueError as exc:
            raise ValueError("请输入正整数") from exc
        if parsed <= 0:
            raise ValueError("请输入正整数")
        return parsed
    if key in {"position_code", "username", "keyword"}:
        return normalize_plain_text(value, max_chars=120)
    if key in {"action_type", "result", "status", "alarm_type"}:
        return normalize_plain_text(value, max_chars=80)
    return value


def _whitelisted_filters(record_type: RecordType, filters: dict[str, object]) -> dict[str, object]:
    allowed = FILTER_WHITELISTS[_record_type(record_type)]
    return {key: value for key, value in filters.items() if key in allowed and value not in {None, ""}}


def _editor_iso(editor: QDateTimeEdit) -> str:
    value = editor.dateTime().toPython()
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _record_type(value: str) -> RecordType:
    if value not in RECORD_TYPE_LABELS:
        raise ValueError("unsupported record type")
    return value  # type: ignore[return-value]
