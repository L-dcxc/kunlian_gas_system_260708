from __future__ import annotations

from collections.abc import Callable, Iterable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from app.services.models import DeviceStatus
from app.ui.common.errors import controlled_error_text
from app.ui.common.safe_text import SafeTextLabel
from app.ui.common.status import StatusBadge

POPUP_ELIGIBLE_STATUSES = {
    DeviceStatus.ALARM_LOW.value,
    DeviceStatus.ALARM_HIGH.value,
    DeviceStatus.OVER_RANGE.value,
    DeviceStatus.FAULT.value,
}


class AlarmPopup(QDialog):
    def __init__(self, alarm: object, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("当前报警")
        self.setModal(False)
        self.setProperty("role", "alarmPopup")
        self.setMinimumWidth(360)
        self.alarm_key = alarm_dedupe_key(alarm)
        self.status_badge = StatusBadge(_alarm_status(alarm))
        self.status_badge.set_status(_alarm_status(alarm), active_alarm=True)
        self.title_label = SafeTextLabel(_popup_title(alarm), selectable=True)
        self.title_label.setProperty("role", "panelTitle")
        self.message_label = SafeTextLabel(_popup_message(alarm), selectable=True)
        self.message_label.setProperty("role", "warningText")
        self.time_label = SafeTextLabel(_alarm_time(alarm), selectable=True)
        self.time_label.setProperty("role", "muted")
        self.close_button = QPushButton("知道了")
        self.close_button.clicked.connect(self.accept)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(8)
        top.addWidget(self.status_badge)
        top.addWidget(self.title_label, 1)

        actions = QHBoxLayout()
        actions.addStretch(1)
        actions.addWidget(self.close_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        layout.addLayout(top)
        layout.addWidget(self.message_label)
        layout.addWidget(self.time_label)
        layout.addLayout(actions)


PopupFactory = Callable[[object, QWidget | None], AlarmPopup]


class AlarmPopupManager:
    def __init__(self, parent: QWidget | None = None, *, popup_factory: PopupFactory | None = None) -> None:
        self._parent = parent
        self._popup_factory = popup_factory or AlarmPopup
        self._shown_keys: set[str] = set()
        self._open_popups: dict[str, AlarmPopup] = {}

    @property
    def shown_keys(self) -> frozenset[str]:
        return frozenset(self._shown_keys)

    def notify(self, alarms: Iterable[object]) -> int:
        active_popup_alarms = tuple(alarm for alarm in alarms if popup_eligible(alarm))
        active_keys = {alarm_dedupe_key(alarm) for alarm in active_popup_alarms}
        self._clear_recovered(active_keys)
        shown_count = 0
        for alarm in active_popup_alarms:
            key = alarm_dedupe_key(alarm)
            if key in self._shown_keys:
                continue
            # Active alarm id, or detector/status/start timestamp, is the UI
            # period key. Repeated refreshes in one active period must not toast.
            self._shown_keys.add(key)
            popup = self._popup_factory(alarm, self._parent)
            self._open_popups[key] = popup
            popup.finished.connect(lambda _code, popup_key=key: self._open_popups.pop(popup_key, None))
            popup.show()
            shown_count += 1
        return shown_count

    def clear(self) -> None:
        self._shown_keys.clear()
        for popup in tuple(self._open_popups.values()):
            popup.close()
        self._open_popups.clear()

    def _clear_recovered(self, active_keys: set[str]) -> None:
        for key in tuple(self._shown_keys - active_keys):
            self._shown_keys.discard(key)
            popup = self._open_popups.pop(key, None)
            if popup is not None:
                popup.close()


def popup_eligible(alarm: object) -> bool:
    return _alarm_status(alarm) in POPUP_ELIGIBLE_STATUSES


def alarm_dedupe_key(alarm: object) -> str:
    explicit_key = _value(alarm, "key")
    active_id = _value(alarm, "active_alarm_id", _value(alarm, "id"))
    if active_id not in {None, ""}:
        return f"alarm:{active_id}"
    if explicit_key not in {None, ""}:
        return str(explicit_key)
    detector_id = _value(alarm, "detector_id", "unknown")
    status = _alarm_status(alarm)
    timestamp = _value(alarm, "started_at", _value(alarm, "timestamp", "unknown"))
    return f"detector:{detector_id}:{status}:{timestamp}"


def _popup_title(alarm: object) -> str:
    name = _value(alarm, "detector_name", _value(alarm, "name", "探测器"))
    status_text = _value(alarm, "status_text", _alarm_status(alarm))
    return controlled_error_text(f"{name} {status_text}", fallback="当前报警")


def _popup_message(alarm: object) -> str:
    message = _value(alarm, "message", "")
    if message:
        return controlled_error_text(message, fallback="报警信息已隐藏")
    value = _value(alarm, "value_text", "--")
    unit = _value(alarm, "unit", "")
    return controlled_error_text(f"当前值：{value} {unit}".strip(), fallback="报警信息已隐藏")


def _alarm_time(alarm: object) -> str:
    started_at = _value(alarm, "started_at", _value(alarm, "timestamp", ""))
    return controlled_error_text(f"发生时间：{started_at}" if started_at else "发生时间：--", fallback="发生时间：--")


def _alarm_status(alarm: object) -> str:
    raw = _value(alarm, "status", _value(alarm, "alarm_type", ""))
    mapping = {
        "alarm_low": DeviceStatus.ALARM_LOW.value,
        "low_alarm": DeviceStatus.ALARM_LOW.value,
        "alarm_high": DeviceStatus.ALARM_HIGH.value,
        "high_alarm": DeviceStatus.ALARM_HIGH.value,
        "over_range": DeviceStatus.OVER_RANGE.value,
        "fault": DeviceStatus.FAULT.value,
        "offline": DeviceStatus.OFFLINE.value,
        "disabled": DeviceStatus.DISABLED.value,
        "warming": DeviceStatus.WARMING.value,
    }
    return mapping.get(str(raw), str(raw))


def _value(source: object, name: str, default: object = None) -> object:
    if isinstance(source, dict):
        return source.get(name, default)
    return getattr(source, name, default)
