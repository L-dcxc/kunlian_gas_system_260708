from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from PySide6.QtWidgets import QFrame, QHBoxLayout, QWidget

from app.services.models import DeviceStatus
from app.ui.common.errors import controlled_error_text
from app.ui.common.safe_text import SafeTextLabel
from app.ui.common.status import repolish

ALERT_READINGS_STATE_KEY = "alarms.active"
ALERT_STATUSES = {
    DeviceStatus.ALARM_HIGH.value,
    DeviceStatus.OVER_RANGE.value,
    DeviceStatus.FAULT.value,
    "alarm_high",
    "over_range",
    "fault",
    "high",
    "overRange",
}


class GlobalAlertBar(QFrame):
    def __init__(self, state_store: object | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("GlobalAlertBar")
        self.setProperty("active", "false")
        self._state_store = state_store
        self.label = SafeTextLabel("当前无未恢复高危警情", selectable=True, max_chars=256)
        self.label.setProperty("role", "globalAlertText")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 8, 16, 8)
        layout.addWidget(self.label, 1)
        self.refresh()

    def show_alert(self, message: object) -> None:
        self.label.set_safe_text(controlled_error_text(message, fallback="当前存在未恢复高危警情", max_chars=256))
        self.setProperty("active", "true")
        repolish(self)

    def clear_alert(self) -> None:
        self.label.set_safe_text("当前无未恢复高危警情")
        self.setProperty("active", "false")
        repolish(self)

    def refresh(self) -> None:
        readings = _state_readings(self._state_store)
        alert = _first_alert(readings)
        if alert is None:
            self.clear_alert()
            return
        self.show_alert(_alert_text(alert))


def _state_readings(state_store: object | None) -> tuple[Any, ...]:
    if state_store is None or not hasattr(state_store, "get_value"):
        return ()
    try:
        value = state_store.get_value(ALERT_READINGS_STATE_KEY, ())
    except Exception:
        return ()
    if isinstance(value, dict):
        return tuple(value.values())
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes)):
        return tuple(value)
    return ()


def _first_alert(readings: tuple[Any, ...]) -> Any | None:
    for reading in readings:
        if _status(reading) in ALERT_STATUSES:
            return reading
    return None


def _alert_text(reading: Any) -> str:
    name = _value(reading, "detector_name") or _value(reading, "name") or _value(reading, "detector_id") or "未知探测器"
    status = _status(reading)
    value = _value(reading, "concentration")
    unit = _value(reading, "unit") or ""
    status_text = {
        DeviceStatus.ALARM_HIGH.value: "高报",
        DeviceStatus.OVER_RANGE.value: "超量程",
        DeviceStatus.FAULT.value: "故障",
        "high": "高报",
        "overRange": "超量程",
    }.get(status, "高危警情")
    value_text = f"：{value}{unit}" if value not in (None, "") else ""
    return f"{status_text} - {name}{value_text}"


def _status(reading: Any) -> str:
    status = _value(reading, "status")
    return str(getattr(status, "value", status) or "")


def _value(reading: Any, key: str) -> Any:
    if isinstance(reading, dict):
        return reading.get(key)
    return getattr(reading, key, None)
