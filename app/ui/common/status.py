from __future__ import annotations

from dataclasses import dataclass
import weakref
from typing import Any

from PySide6.QtCore import QObject, Qt, QTimer
from PySide6.QtWidgets import QLabel, QWidget

from app.services.models import AcquisitionStatus, DeviceStatus

ALARM_PULSE_PERIOD_MS = 800
ALARM_PULSE_TOGGLE_MS = ALARM_PULSE_PERIOD_MS // 2


@dataclass(frozen=True, slots=True)
class StatusVisual:
    text: str
    property_value: str
    alarm_value: str | None = None
    pulse_eligible: bool = False


DEVICE_STATUS_VISUALS: dict[DeviceStatus, StatusVisual] = {
    DeviceStatus.NORMAL: StatusVisual("正常", "normal"),
    DeviceStatus.ALARM_LOW: StatusVisual("低报", "lowAlarm", "low", True),
    DeviceStatus.ALARM_HIGH: StatusVisual("高报", "highAlarm", "high", True),
    DeviceStatus.FAULT: StatusVisual("故障", "fault", "fault", True),
    DeviceStatus.OFFLINE: StatusVisual("离线", "offline"),
    DeviceStatus.DISABLED: StatusVisual("已屏蔽", "shielded"),
    DeviceStatus.OVER_RANGE: StatusVisual("超量程", "overRange", "overRange", True),
    DeviceStatus.WARMING: StatusVisual("预热中", "warmup"),
    DeviceStatus.INVALID: StatusVisual("异常", "warning"),
}

ACQUISITION_STATUS_VISUALS: dict[AcquisitionStatus, StatusVisual] = {
    AcquisitionStatus.NOT_STARTED: StatusVisual("未启动", "offline"),
    AcquisitionStatus.RUNNING: StatusVisual("采集中", "running"),
    AcquisitionStatus.ERROR: StatusVisual("异常", "fault"),
    AcquisitionStatus.RECONNECTING: StatusVisual("重连中", "warning"),
    AcquisitionStatus.STOPPED: StatusVisual("已停止", "offline"),
}


def device_status_visual(status: DeviceStatus | str) -> StatusVisual:
    return DEVICE_STATUS_VISUALS[DeviceStatus(status)]


def acquisition_status_visual(status: AcquisitionStatus | str) -> StatusVisual:
    return ACQUISITION_STATUS_VISUALS[AcquisitionStatus(status)]


class StatusBadge(QLabel):
    def __init__(
        self,
        status: DeviceStatus | AcquisitionStatus | str = DeviceStatus.NORMAL,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setProperty("role", "statusBadge")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.set_status(status)

    def set_status(
        self,
        status: DeviceStatus | AcquisitionStatus | str,
        *,
        text: str | None = None,
        active_alarm: bool = False,
    ) -> None:
        visual = visual_for_status(status)
        self.setText(visual.text if text is None else str(text))
        self.setProperty("status", visual.property_value)
        if active_alarm and visual.pulse_eligible and visual.alarm_value is not None:
            self.setProperty("alarm", visual.alarm_value)
        else:
            # Recovery must remove the alarm property, otherwise reused widgets can
            # keep stale flashing QSS after the business alarm period has ended.
            self.setProperty("alarm", None)
            self.setProperty("alarmPulse", None)
        repolish(self)


def visual_for_status(status: DeviceStatus | AcquisitionStatus | str) -> StatusVisual:
    try:
        return device_status_visual(status)  # type: ignore[arg-type]
    except ValueError:
        return acquisition_status_visual(status)  # type: ignore[arg-type]


def alarm_property_for_status(
    status: DeviceStatus | AcquisitionStatus | str,
    *,
    active_alarm: bool = True,
) -> str | None:
    visual = visual_for_status(status)
    if not active_alarm or not visual.pulse_eligible:
        return None
    return visual.alarm_value


class AlarmPulseController(QObject):
    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._widgets: weakref.WeakSet[QWidget] = weakref.WeakSet()
        self._pulse_on = False
        self._timer = QTimer(self)
        self._timer.setInterval(ALARM_PULSE_TOGGLE_MS)
        self._timer.timeout.connect(self._tick)

    @property
    def interval_ms(self) -> int:
        return self._timer.interval()

    @property
    def pulse_period_ms(self) -> int:
        return ALARM_PULSE_PERIOD_MS

    def is_active(self) -> bool:
        return self._timer.isActive()

    def start_for_status(
        self,
        widget: QWidget,
        status: DeviceStatus | AcquisitionStatus | str,
        *,
        recovered: bool = False,
    ) -> bool:
        alarm_value = alarm_property_for_status(status, active_alarm=not recovered)
        if alarm_value is None:
            self.stop(widget)
            return False
        self.start(widget, alarm_value)
        return True

    def start(self, widget: QWidget, alarm_value: str) -> None:
        widget.setProperty("alarm", alarm_value)
        widget.setProperty("alarmPulse", False)
        self._widgets.add(widget)
        repolish(widget)
        if not self._timer.isActive():
            self._timer.start()

    def stop(self, widget: QWidget) -> None:
        if widget in self._widgets:
            self._widgets.discard(widget)
        # Clearing both properties is the reusable-widget recovery boundary.
        widget.setProperty("alarm", None)
        widget.setProperty("alarmPulse", None)
        repolish(widget)
        if not self._widgets and self._timer.isActive():
            self._timer.stop()
            self._pulse_on = False

    def stop_all(self) -> None:
        for widget in list(self._widgets):
            widget.setProperty("alarm", None)
            widget.setProperty("alarmPulse", None)
            repolish(widget)
        self._widgets.clear()
        self._timer.stop()
        self._pulse_on = False

    def _tick(self) -> None:
        self._pulse_on = not self._pulse_on
        for widget in list(self._widgets):
            try:
                widget.setProperty("alarmPulse", self._pulse_on)
                repolish(widget)
            except RuntimeError:
                self._widgets.discard(widget)
        if not self._widgets:
            self._timer.stop()
            self._pulse_on = False


def repolish(widget: QWidget) -> None:
    style: Any = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()
