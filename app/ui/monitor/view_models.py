from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from PySide6.QtCore import QObject, QTimer, Signal

from app.core.event_bus import EventBus, Subscription
from app.core.state_store import STATE_KEY_CHANGED, STATE_READINGS_UPDATED, StateStore
from app.services.models import AcquisitionState, AcquisitionStatus, DeviceStatus
from app.ui.common.errors import controlled_error_text
from app.ui.common.status import acquisition_status_visual, device_status_visual

ACQUISITION_STATUS_STATE_KEY = "acquisition.status"
DEFAULT_REFRESH_THROTTLE_MS = 250
LOAD_FAILED_TEXT = "实时状态加载失败"
ALARM_EVENT_TYPES = ("alarm.active_changed", "alarm.created", "alarm.recovered")
ALARM_LIST_STATUSES = {
    DeviceStatus.ALARM_LOW.value,
    DeviceStatus.ALARM_HIGH.value,
    DeviceStatus.OVER_RANGE.value,
    DeviceStatus.FAULT.value,
    DeviceStatus.OFFLINE.value,
    DeviceStatus.DISABLED.value,
    DeviceStatus.WARMING.value,
}
POPUP_STATUSES = {
    DeviceStatus.ALARM_LOW.value,
    DeviceStatus.ALARM_HIGH.value,
    DeviceStatus.OVER_RANGE.value,
    DeviceStatus.FAULT.value,
}


@dataclass(frozen=True, slots=True)
class MetricDisplay:
    title: str
    value: str
    unit: str = ""
    status: str = "normal"
    subtitle: str = ""


@dataclass(frozen=True, slots=True)
class DetectorDisplayItem:
    detector_id: int
    name: str
    controller_id: int | None
    controller_name: str
    port_id: int | None
    address: str
    gas_type: str
    status: str
    status_text: str
    status_property: str
    concentration_text: str
    unit: str
    timestamp: str
    quality: str = "valid"
    location: str = ""

    @property
    def pulse_eligible(self) -> bool:
        return self.status in POPUP_STATUSES

    @property
    def is_offline(self) -> bool:
        return self.status == DeviceStatus.OFFLINE.value

    @property
    def is_warming(self) -> bool:
        return self.status == DeviceStatus.WARMING.value


@dataclass(frozen=True, slots=True)
class AlarmItemDisplay:
    key: str
    detector_id: int
    detector_name: str
    status: str
    status_text: str
    message: str
    started_at: str
    active_alarm_id: int | None = None
    value_text: str = "--"
    unit: str = ""

    @property
    def popup_eligible(self) -> bool:
        return self.status in POPUP_STATUSES


@dataclass(frozen=True, slots=True)
class ControllerGroupDisplay:
    controller_id: int | None
    title: str
    total_count: int
    alarm_count: int
    offline_count: int
    detectors: tuple[DetectorDisplayItem, ...]


@dataclass(frozen=True, slots=True)
class MonitoringSnapshot:
    metrics: tuple[MetricDisplay, ...]
    alarms: tuple[AlarmItemDisplay, ...]
    groups: tuple[ControllerGroupDisplay, ...]
    detectors: tuple[DetectorDisplayItem, ...]
    acquisition_status: str
    acquisition_message: str = ""

    @property
    def is_empty(self) -> bool:
        return not self.detectors


class MonitoringViewModel(QObject):
    loading_changed = Signal(bool)
    snapshot_changed = Signal(object)
    error_changed = Signal(str)
    detail_changed = Signal(object)
    alarm_popup_requested = Signal(object)
    _event_received = Signal()

    def __init__(
        self,
        read_service: object | None = None,
        state_store: StateStore | None = None,
        event_bus: EventBus | None = None,
        parent: QObject | None = None,
        *,
        throttle_ms: int = DEFAULT_REFRESH_THROTTLE_MS,
        page_size: int = 100,
        auto_subscribe: bool = True,
    ) -> None:
        super().__init__(parent)
        self._read_service = read_service
        self._state_store = state_store
        self._event_bus = event_bus
        self._page_size = page_size
        self._snapshot = MonitoringSnapshot((), (), (), (), AcquisitionStatus.NOT_STARTED.value)
        self._subscriptions: list[Subscription] = []
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(max(1, int(throttle_ms)))
        self._timer.timeout.connect(self._refresh_from_timer)
        # State/alarm callbacks can originate from acquisition workers; this signal
        # is the handoff point back to the Qt object thread before touching UI state.
        self._event_received.connect(self._schedule_refresh)
        if auto_subscribe:
            self.subscribe_events()

    @property
    def snapshot(self) -> MonitoringSnapshot:
        return self._snapshot

    @property
    def throttle_interval_ms(self) -> int:
        return self._timer.interval()

    def subscribe_events(self) -> None:
        if self._state_store is not None:
            self._subscriptions.append(self._state_store.subscribe(STATE_READINGS_UPDATED, self._on_state_event))
            self._subscriptions.append(self._state_store.subscribe(STATE_KEY_CHANGED, self._on_state_event))
        if self._event_bus is not None:
            for event_type in ALARM_EVENT_TYPES:
                self._subscriptions.append(self._event_bus.subscribe(event_type, self._on_alarm_event))

    def dispose(self) -> None:
        for subscription in self._subscriptions:
            subscription.unsubscribe()
        self._subscriptions.clear()
        self._timer.stop()

    def load(self) -> None:
        self._load(show_loading=True)

    def refresh(self) -> None:
        self._load(show_loading=False)

    def retry(self) -> None:
        self.load()

    def load_detail(self, detector_id: int) -> None:
        item = self._find_detector(detector_id)
        if self._read_service is not None and hasattr(self._read_service, "get_realtime"):
            try:
                result = self._read_service.get_realtime(detector_id)
                if _result_success(result) and getattr(result, "data", None) is not None:
                    item = _detector_from_row(getattr(result, "data"), fallback=item)
                elif not _result_success(result):
                    self.error_changed.emit(controlled_error_text(getattr(result, "message", ""), fallback=LOAD_FAILED_TEXT))
            except Exception:
                self.error_changed.emit(LOAD_FAILED_TEXT)
        self.detail_changed.emit(item)

    def _load(self, *, show_loading: bool) -> None:
        if show_loading:
            self.loading_changed.emit(True)
        try:
            snapshot = self._build_snapshot()
        except _ViewModelError as exc:
            self.error_changed.emit(exc.message)
        except Exception:
            self.error_changed.emit(LOAD_FAILED_TEXT)
        else:
            self._snapshot = snapshot
            self.error_changed.emit("")
            self.snapshot_changed.emit(snapshot)
            self.alarm_popup_requested.emit(snapshot.alarms)
        finally:
            if show_loading:
                self.loading_changed.emit(False)

    def _build_snapshot(self) -> MonitoringSnapshot:
        detectors = self._load_detectors()
        alarms = self._load_alarms(detectors)
        acquisition = self._acquisition_state()
        return MonitoringSnapshot(
            metrics=_build_metrics(detectors, alarms, acquisition),
            alarms=alarms,
            groups=_group_detectors(detectors),
            detectors=detectors,
            acquisition_status=acquisition.status.value,
            acquisition_message=acquisition.message,
        )

    def _load_detectors(self) -> tuple[DetectorDisplayItem, ...]:
        if self._read_service is not None and hasattr(self._read_service, "list_realtime"):
            result = self._read_service.list_realtime(page=1, per_page=self._page_size)
            if not _result_success(result):
                raise _ViewModelError(controlled_error_text(getattr(result, "message", ""), fallback=LOAD_FAILED_TEXT))
            data = getattr(result, "data", None)
            rows = tuple(getattr(data, "items", ()) if data is not None else ())
            return tuple(_detector_from_row(row) for row in rows)
        if self._state_store is None:
            return ()
        # UI consumes the unified DeviceReading/state DTO only; protocol registers,
        # CRC and raw frame fields remain behind acquisition/protocol adapters.
        return tuple(_detector_from_row(row) for row in self._state_store.get_realtime_snapshot())

    def _load_alarms(self, detectors: tuple[DetectorDisplayItem, ...]) -> tuple[AlarmItemDisplay, ...]:
        by_id = {item.detector_id: item for item in detectors}
        if self._read_service is not None and hasattr(self._read_service, "list_active_alarms"):
            result = self._read_service.list_active_alarms()
            if not _result_success(result):
                raise _ViewModelError(controlled_error_text(getattr(result, "message", ""), fallback=LOAD_FAILED_TEXT))
            return tuple(
                item
                for item in (_alarm_from_row(row, by_id) for row in tuple(getattr(result, "data", ()) or ()))
                if item is not None
            )
        return tuple(_alarm_from_detector(item) for item in detectors if item.status in ALARM_LIST_STATUSES)

    def _acquisition_state(self) -> AcquisitionState:
        if self._state_store is not None:
            state = self._state_store.get_value(ACQUISITION_STATUS_STATE_KEY)
            if isinstance(state, AcquisitionState):
                return state
        if self._read_service is not None and hasattr(self._read_service, "get_acquisition_status"):
            try:
                state = self._read_service.get_acquisition_status()
                if isinstance(state, AcquisitionState):
                    return state
            except Exception:
                return AcquisitionState(AcquisitionStatus.ERROR, "采集状态读取失败")
        return AcquisitionState(AcquisitionStatus.NOT_STARTED)

    def _find_detector(self, detector_id: int) -> DetectorDisplayItem | None:
        return next((item for item in self._snapshot.detectors if item.detector_id == detector_id), None)

    def _on_state_event(self, event_type: str, payload: object) -> None:
        if event_type == STATE_KEY_CHANGED and isinstance(payload, dict) and payload.get("key") != ACQUISITION_STATUS_STATE_KEY:
            return
        self._event_received.emit()

    def _on_alarm_event(self, event_type: str, payload: object) -> None:
        self._event_received.emit()

    def _schedule_refresh(self) -> None:
        if not self._timer.isActive():
            self._timer.start()

    def _refresh_from_timer(self) -> None:
        self.refresh()


@dataclass(frozen=True)
class _ViewModelError(Exception):
    message: str


def _result_success(result: object) -> bool:
    return bool(getattr(result, "success", False))


def _build_metrics(
    detectors: tuple[DetectorDisplayItem, ...],
    alarms: tuple[AlarmItemDisplay, ...],
    acquisition: AcquisitionState,
) -> tuple[MetricDisplay, ...]:
    online = sum(1 for item in detectors if item.status not in {DeviceStatus.OFFLINE.value, DeviceStatus.DISABLED.value})
    alarm_count = sum(1 for item in detectors if item.status in {DeviceStatus.ALARM_LOW.value, DeviceStatus.ALARM_HIGH.value, DeviceStatus.OVER_RANGE.value})
    fault_count = sum(1 for item in detectors if item.status == DeviceStatus.FAULT.value)
    visual = acquisition_status_visual(acquisition.status)
    return (
        MetricDisplay("在线设备", str(online), "台", "running" if online else "offline", f"总数 {len(detectors)} 台"),
        MetricDisplay("当前报警", str(alarm_count), "条", "highAlarm" if alarm_count else "normal", f"未恢复 {len(alarms)} 条"),
        MetricDisplay("故障设备", str(fault_count), "台", "fault" if fault_count else "normal"),
        MetricDisplay("采集状态", visual.text, "", visual.property_value, acquisition.message),
    )


def _group_detectors(detectors: tuple[DetectorDisplayItem, ...]) -> tuple[ControllerGroupDisplay, ...]:
    buckets: dict[tuple[int | None, str], list[DetectorDisplayItem]] = {}
    for item in detectors:
        buckets.setdefault((item.controller_id, item.controller_name), []).append(item)
    groups: list[ControllerGroupDisplay] = []
    for (controller_id, title), items in sorted(buckets.items(), key=lambda pair: (pair[0][0] is None, pair[0][0] or 0)):
        group_items = tuple(sorted(items, key=lambda item: item.detector_id))
        groups.append(
            ControllerGroupDisplay(
                controller_id=controller_id,
                title=title,
                total_count=len(group_items),
                alarm_count=sum(1 for item in group_items if item.status in POPUP_STATUSES),
                offline_count=sum(1 for item in group_items if item.is_offline),
                detectors=group_items,
            )
        )
    return tuple(groups)


def _detector_from_row(row: object, fallback: DetectorDisplayItem | None = None) -> DetectorDisplayItem:
    detector_id = _positive_int(_value(row, "detector_id", fallback.detector_id if fallback else 0))
    status = _status_value(_value(row, "status", fallback.status if fallback else DeviceStatus.INVALID.value))
    visual = device_status_visual(status)
    controller_id = _optional_int(_value(row, "controller_id", fallback.controller_id if fallback else None))
    concentration = _value(row, "concentration", None)
    unit = _text(_value(row, "unit", fallback.unit if fallback else ""))
    return DetectorDisplayItem(
        detector_id=detector_id,
        name=_text(_value(row, "name", _value(row, "detector_name", f"探测器 {detector_id}"))),
        controller_id=controller_id,
        controller_name=_text(_value(row, "controller_name", "直连探头" if controller_id is None else f"控制器 {controller_id}")),
        port_id=_optional_int(_value(row, "port_id", fallback.port_id if fallback else None)),
        address=_text(_value(row, "address", _value(row, "detector_address", "-"))),
        gas_type=_text(_value(row, "gas_type", fallback.gas_type if fallback else "-")),
        status=status,
        status_text=visual.text,
        status_property=visual.property_value,
        concentration_text="--" if status == DeviceStatus.OFFLINE.value else _format_concentration(concentration),
        unit=unit,
        timestamp=_text(_value(row, "timestamp", fallback.timestamp if fallback else "-")),
        quality=_text(_value(row, "quality", fallback.quality if fallback else "valid")),
        location=_text(_value(row, "location", fallback.location if fallback else "")),
    )


def _alarm_from_row(row: object, detectors: dict[int, DetectorDisplayItem]) -> AlarmItemDisplay | None:
    detector_id = _optional_int(_value(row, "detector_id"))
    if detector_id is None:
        return None
    status = _alarm_status(_value(row, "alarm_type", _value(row, "status", DeviceStatus.INVALID.value)))
    if status not in ALARM_LIST_STATUSES:
        return None
    detector = detectors.get(detector_id)
    visual = device_status_visual(status)
    active_id = _optional_int(_value(row, "id", _value(row, "active_alarm_id", None)))
    started_at = _text(_value(row, "start_time", _value(row, "started_at", _value(row, "timestamp", detector.timestamp if detector else ""))))
    value = _value(row, "trigger_value", _value(row, "concentration", None))
    unit = _text(_value(row, "unit", detector.unit if detector else ""))
    value_text = "--" if status == DeviceStatus.OFFLINE.value else _format_concentration(value)
    name = _text(_value(row, "detector_name", detector.name if detector else f"探测器 {detector_id}"))
    key = _alarm_key(active_id, detector_id, status, started_at)
    return AlarmItemDisplay(
        key=key,
        detector_id=detector_id,
        detector_name=name,
        status=status,
        status_text=visual.text,
        message=f"{visual.text}：{value_text} {unit}".strip(),
        started_at=started_at,
        active_alarm_id=active_id,
        value_text=value_text,
        unit=unit,
    )


def _alarm_from_detector(item: DetectorDisplayItem) -> AlarmItemDisplay:
    return AlarmItemDisplay(
        key=_alarm_key(None, item.detector_id, item.status, item.timestamp),
        detector_id=item.detector_id,
        detector_name=item.name,
        status=item.status,
        status_text=item.status_text,
        message=f"{item.status_text}：{item.concentration_text} {item.unit}".strip(),
        started_at=item.timestamp,
        value_text=item.concentration_text,
        unit=item.unit,
    )


def _alarm_key(active_id: int | None, detector_id: int, status: str, started_at: str) -> str:
    if active_id is not None:
        return f"alarm:{active_id}"
    return f"detector:{detector_id}:{status}:{started_at or 'unknown'}"


def _alarm_status(value: object) -> str:
    text = _text(value)
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
    return mapping.get(text, _status_value(text))


def _status_value(value: object) -> str:
    try:
        return DeviceStatus(str(value)).value
    except ValueError:
        return DeviceStatus.INVALID.value


def _format_concentration(value: object) -> str:
    if value is None or value == "":
        return "--"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "--"
    return f"{number:g}"


def _value(row: object, name: str, default: object = None) -> object:
    if isinstance(row, dict):
        return row.get(name, default)
    return getattr(row, name, default)


def _text(value: object) -> str:
    return "" if value is None else str(value)


def _positive_int(value: object) -> int:
    number = _optional_int(value)
    return number if number is not None and number > 0 else 0


def _optional_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
