from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal

from app.core.event_bus import EventBus, Subscription
from app.core.state_store import STATE_READINGS_UPDATED, StateStore
from app.services.errors import ErrorCode
from app.services.map_service import MapUploadCommand, SaveMapPointCommand
from app.services.models import DeviceStatus
from app.ui.common.errors import controlled_error_text
from app.ui.common.safe_text import normalize_plain_text
from app.ui.common.status import device_status_visual

DEFAULT_REFRESH_THROTTLE_MS = 250
MAP_LOAD_FAILED_TEXT = "地图加载失败"
MAP_UPLOAD_VALIDATION_TEXT = "图片格式或大小不符合要求"
MAP_SAVE_FAILED_TEXT = "地图点位保存失败"
MAP_DELETE_FAILED_TEXT = "地图删除失败"
MAP_EVENT_TYPES = ("map.changed", "map.point.changed", "alarm.active_changed", "alarm.created", "alarm.recovered")
PULSE_STATUSES = {
    DeviceStatus.ALARM_LOW.value,
    DeviceStatus.ALARM_HIGH.value,
    DeviceStatus.OVER_RANGE.value,
    DeviceStatus.FAULT.value,
}
ALARM_LIST_STATUSES = PULSE_STATUSES | {DeviceStatus.OFFLINE.value}
_INTERNAL_ERROR_MARKERS = ("traceback", "sqlite", " sql", "select ", "insert ", "update ", "delete ")


@dataclass(frozen=True, slots=True)
class MapListItemDisplay:
    map_id: int
    name: str
    original_file_name: str
    safe_file_name: str
    relative_path: str
    point_count: int = 0
    is_enabled: bool = True
    selected: bool = False

    @property
    def subtitle(self) -> str:
        return f"点位 {self.point_count} 个 / 原文件 {self.original_file_name or '-'}"


@dataclass(frozen=True, slots=True)
class MapPointDisplay:
    point_id: int
    map_id: int
    detector_id: int
    x_ratio: float
    y_ratio: float
    label: str
    detector_position_code: str
    detector_name: str
    controller_name: str
    status: str
    status_text: str
    status_property: str
    concentration_text: str
    gas_type: str
    unit: str
    alarm_level: str
    timestamp: str
    active_alarm: bool
    active_alarm_type: str

    @property
    def display_name(self) -> str:
        return self.label or self.detector_name or self.detector_position_code or f"探测器 {self.detector_id}"

    @property
    def pulse_eligible(self) -> bool:
        return self.active_alarm and self.status in PULSE_STATUSES

    @property
    def is_offline(self) -> bool:
        return self.status == DeviceStatus.OFFLINE.value

    @property
    def value_text(self) -> str:
        if self.is_offline:
            return "--"
        return f"{self.concentration_text} {self.unit}".strip()


@dataclass(frozen=True, slots=True)
class MapRuntimeDisplay:
    map: MapListItemDisplay
    points: tuple[MapPointDisplay, ...]

    @property
    def is_empty(self) -> bool:
        return False


class MapMonitoringViewModel(QObject):
    loading_changed = Signal(bool)
    maps_changed = Signal(object)
    runtime_changed = Signal(object)
    error_changed = Signal(str)
    detail_changed = Signal(object)
    upload_result_changed = Signal(str, bool)
    point_save_result_changed = Signal(str, bool)
    _event_received = Signal()

    def __init__(
        self,
        map_service: object | None = None,
        state_store: StateStore | None = None,
        event_bus: EventBus | None = None,
        parent: QObject | None = None,
        *,
        throttle_ms: int = DEFAULT_REFRESH_THROTTLE_MS,
        auto_subscribe: bool = True,
    ) -> None:
        super().__init__(parent)
        self._service = map_service
        self._state_store = state_store
        self._event_bus = event_bus
        self._subscriptions: list[Subscription] = []
        self._maps: tuple[MapListItemDisplay, ...] = ()
        self._runtime: MapRuntimeDisplay | None = None
        self._selected_map_id: int | None = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(max(1, int(throttle_ms)))
        self._timer.timeout.connect(self.refresh_current)
        # Background state/alarm publishers must cross this Qt signal before the UI
        # is touched; direct worker-to-widget updates are intentionally avoided.
        self._event_received.connect(self._schedule_refresh)
        if auto_subscribe:
            self.subscribe_events()

    @property
    def maps(self) -> tuple[MapListItemDisplay, ...]:
        return self._maps

    @property
    def runtime(self) -> MapRuntimeDisplay | None:
        return self._runtime

    @property
    def selected_map_id(self) -> int | None:
        return self._selected_map_id

    @property
    def throttle_interval_ms(self) -> int:
        return self._timer.interval()

    def subscribe_events(self) -> None:
        if self._state_store is not None:
            self._subscriptions.append(self._state_store.subscribe(STATE_READINGS_UPDATED, self._on_state_event))
        if self._event_bus is not None:
            for event_type in MAP_EVENT_TYPES:
                self._subscriptions.append(self._event_bus.subscribe(event_type, self._on_state_event))

    def dispose(self) -> None:
        for subscription in self._subscriptions:
            subscription.unsubscribe()
        self._subscriptions.clear()
        self._timer.stop()

    def load(self) -> None:
        self.loading_changed.emit(True)
        try:
            self._maps = self._load_maps()
        except Exception:
            self.error_changed.emit(MAP_LOAD_FAILED_TEXT)
            self.maps_changed.emit(())
            self.runtime_changed.emit(None)
        else:
            self.error_changed.emit("")
            self.maps_changed.emit(self._maps)
            if self._maps:
                selected = self._selected_map_id if any(item.map_id == self._selected_map_id for item in self._maps) else self._maps[0].map_id
                self.select_map(selected)
            else:
                self._runtime = None
                self._selected_map_id = None
                self.runtime_changed.emit(None)
        finally:
            self.loading_changed.emit(False)

    def retry(self) -> None:
        self.load()

    def select_map(self, map_id: int) -> None:
        self._selected_map_id = int(map_id)
        self._maps = tuple(_select_map(item, self._selected_map_id) for item in self._maps)
        self.maps_changed.emit(self._maps)
        self.refresh_current()

    def refresh_current(self) -> None:
        if self._selected_map_id is None:
            self.runtime_changed.emit(None)
            return
        try:
            runtime = self._load_runtime(self._selected_map_id)
        except _ViewModelError as exc:
            self.error_changed.emit(exc.message)
            self.runtime_changed.emit(None)
            return
        self._runtime = runtime
        self._maps = tuple(_with_point_count(_select_map(item, runtime.map.map_id), runtime) for item in self._maps)
        self.error_changed.emit("")
        self.maps_changed.emit(self._maps)
        self.runtime_changed.emit(runtime)

    def select_point(self, point_id: int) -> None:
        point = self.find_point(point_id)
        self.detail_changed.emit(point)

    def find_point(self, point_id: int) -> MapPointDisplay | None:
        if self._runtime is None:
            return None
        return next((point for point in self._runtime.points if point.point_id == point_id), None)

    def upload_map(self, session: object, source_path: Path, *, name: str | None = None) -> bool:
        if self._service is None or not hasattr(self._service, "upload_map"):
            self.upload_result_changed.emit(MAP_LOAD_FAILED_TEXT, False)
            return False
        try:
            result = self._service.upload_map(session, MapUploadCommand(source_path=Path(source_path), name=name))
        except Exception:
            self.upload_result_changed.emit(MAP_UPLOAD_VALIDATION_TEXT, False)
            return False
        if not _result_success(result):
            self.upload_result_changed.emit(_upload_error_text(result), False)
            return False
        uploaded = _map_from_row(getattr(result, "data", None))
        self._selected_map_id = uploaded.map_id if uploaded is not None else self._selected_map_id
        self.upload_result_changed.emit("地图上传成功", True)
        self.load()
        return True

    def delete_map(self, session: object, map_id: int) -> bool:
        if self._service is None or not hasattr(self._service, "delete_map"):
            self.error_changed.emit(MAP_DELETE_FAILED_TEXT)
            return False
        try:
            result = self._service.delete_map(session, int(map_id))
        except Exception:
            self.error_changed.emit(MAP_DELETE_FAILED_TEXT)
            return False
        if not _result_success(result):
            self.error_changed.emit(_map_error_text(getattr(result, "message", ""), fallback=MAP_DELETE_FAILED_TEXT))
            return False
        if self._selected_map_id == int(map_id):
            self._selected_map_id = None
        self.load()
        return True

    def save_point_position(self, session: object, point: MapPointDisplay, x_ratio: float, y_ratio: float) -> bool:
        if self._service is None or not hasattr(self._service, "save_point"):
            self.point_save_result_changed.emit(MAP_SAVE_FAILED_TEXT, False)
            return False
        # The UI clamps drag output for feedback, but only the service owns the final
        # 0..1 persistence decision and permission check.
        command = SaveMapPointCommand(
            map_id=point.map_id,
            detector_id=point.detector_id,
            x_ratio=_clamp_ratio(x_ratio),
            y_ratio=_clamp_ratio(y_ratio),
            label=point.label or None,
        )
        try:
            result = self._service.save_point(session, command)
        except Exception:
            self.point_save_result_changed.emit(MAP_SAVE_FAILED_TEXT, False)
            return False
        if not _result_success(result):
            self.point_save_result_changed.emit(_map_error_text(getattr(result, "message", ""), fallback=MAP_SAVE_FAILED_TEXT), False)
            return False
        self.point_save_result_changed.emit("点位坐标已保存", True)
        self.refresh_current()
        return True

    def _load_maps(self) -> tuple[MapListItemDisplay, ...]:
        if self._service is None or not hasattr(self._service, "list_maps"):
            return ()
        result = self._service.list_maps()
        if hasattr(result, "success"):
            if not _result_success(result):
                raise _ViewModelError(_map_error_text(getattr(result, "message", ""), fallback=MAP_LOAD_FAILED_TEXT))
            rows = tuple(getattr(result, "data", ()) or ())
        else:
            rows = tuple(result or ())
        items: list[MapListItemDisplay] = []
        for row in rows:
            item = _map_from_row(row)
            if item is not None:
                items.append(_select_map(item, self._selected_map_id))
        return tuple(items)

    def _load_runtime(self, map_id: int) -> MapRuntimeDisplay:
        if self._service is None or not hasattr(self._service, "get_map_runtime_view"):
            raise _ViewModelError(MAP_LOAD_FAILED_TEXT)
        result = self._service.get_map_runtime_view(int(map_id))
        if not _result_success(result):
            raise _ViewModelError(_map_error_text(getattr(result, "message", ""), fallback=MAP_LOAD_FAILED_TEXT))
        data = getattr(result, "data", None)
        map_item = _map_from_row(getattr(data, "map", None))
        if data is None or map_item is None:
            raise _ViewModelError(MAP_LOAD_FAILED_TEXT)
        points = tuple(_point_from_row(row) for row in tuple(getattr(data, "points", ()) or ()))
        selected_map = _select_map(_with_count(map_item, len(points)), map_id)
        return MapRuntimeDisplay(selected_map, points)

    def _schedule_refresh(self) -> None:
        if self._selected_map_id is not None and not self._timer.isActive():
            self._timer.start()

    def _on_state_event(self, event_type: str, payload: object) -> None:
        self._event_received.emit()


@dataclass(frozen=True)
class _ViewModelError(Exception):
    message: str


def _result_success(result: object) -> bool:
    return bool(getattr(result, "success", False))


def _map_from_row(row: object | None) -> MapListItemDisplay | None:
    if row is None:
        return None
    map_id = _positive_int(_value(row, "id", _value(row, "map_id", 0)))
    if map_id <= 0:
        return None
    return MapListItemDisplay(
        map_id=map_id,
        name=_plain(_value(row, "name", f"地图 {map_id}"), 120),
        original_file_name=_plain(_value(row, "original_file_name", _value(row, "original_filename", "")), 180),
        safe_file_name=_plain(_value(row, "safe_file_name", _value(row, "safe_filename", "")), 180),
        relative_path=_plain(_value(row, "relative_path", ""), 260),
        is_enabled=bool(_value(row, "is_enabled", True)),
    )


def _point_from_row(row: object) -> MapPointDisplay:
    status = _status_value(_value(row, "status", DeviceStatus.INVALID.value))
    visual = device_status_visual(status)
    concentration = _value(row, "concentration", None)
    return MapPointDisplay(
        point_id=_positive_int(_value(row, "id", _value(row, "point_id", 0))),
        map_id=_positive_int(_value(row, "map_id", 0)),
        detector_id=_positive_int(_value(row, "detector_id", 0)),
        x_ratio=_clamp_ratio(_value(row, "x_ratio", 0)),
        y_ratio=_clamp_ratio(_value(row, "y_ratio", 0)),
        label=_plain(_value(row, "label", ""), 120),
        detector_position_code=_plain(_value(row, "detector_position_code", ""), 80),
        detector_name=_plain(_value(row, "detector_name", ""), 120),
        controller_name=_plain(_value(row, "controller_name", ""), 120),
        status=status,
        status_text=visual.text,
        status_property=visual.property_value,
        concentration_text=_format_concentration(concentration),
        gas_type=_plain(_value(row, "gas_type", ""), 80),
        unit=_plain(_value(row, "unit", ""), 32),
        alarm_level=_plain(_value(row, "alarm_level", ""), 32),
        timestamp=_plain(_value(row, "timestamp", ""), 80),
        active_alarm=bool(_value(row, "active_alarm", False)),
        active_alarm_type=_plain(_value(row, "active_alarm_type", ""), 80),
    )


def _select_map(item: MapListItemDisplay, selected_id: int | None) -> MapListItemDisplay:
    return MapListItemDisplay(
        map_id=item.map_id,
        name=item.name,
        original_file_name=item.original_file_name,
        safe_file_name=item.safe_file_name,
        relative_path=item.relative_path,
        point_count=item.point_count,
        is_enabled=item.is_enabled,
        selected=item.map_id == selected_id,
    )


def _with_count(item: MapListItemDisplay, point_count: int) -> MapListItemDisplay:
    return MapListItemDisplay(
        map_id=item.map_id,
        name=item.name,
        original_file_name=item.original_file_name,
        safe_file_name=item.safe_file_name,
        relative_path=item.relative_path,
        point_count=max(0, int(point_count)),
        is_enabled=item.is_enabled,
        selected=item.selected,
    )


def _with_point_count(item: MapListItemDisplay, runtime: MapRuntimeDisplay) -> MapListItemDisplay:
    if item.map_id != runtime.map.map_id:
        return item
    return _select_map(_with_count(item, len(runtime.points)), runtime.map.map_id)


def _upload_error_text(result: object) -> str:
    code = int(getattr(result, "code", 0) or 0)
    if code == int(ErrorCode.VALIDATION_ERROR) or tuple(getattr(result, "errors", ()) or ()):
        return MAP_UPLOAD_VALIDATION_TEXT
    return _map_error_text(getattr(result, "message", ""), fallback=MAP_UPLOAD_VALIDATION_TEXT)


def _map_error_text(message: object, *, fallback: str) -> str:
    # Service messages are still treated as untrusted view input; internal paths,
    # stack traces and SQL fragments collapse to a stable user-facing sentence.
    raw = "" if message is None else str(message)
    lowered = f" {raw.lower()}"
    if any(marker in lowered for marker in _INTERNAL_ERROR_MARKERS):
        return fallback
    return controlled_error_text(raw, fallback=fallback)


def _status_value(value: object) -> str:
    try:
        return DeviceStatus(str(value)).value
    except ValueError:
        return DeviceStatus.INVALID.value


def _format_concentration(value: object) -> str:
    if value is None or value == "":
        return "--"
    try:
        return f"{float(value):g}"
    except (TypeError, ValueError):
        return "--"


def _clamp_ratio(value: object) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return min(1.0, max(0.0, number))


def _positive_int(value: object) -> int:
    if value is None or isinstance(value, bool):
        return 0
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return number if number > 0 else 0


def _plain(value: object, max_chars: int) -> str:
    return normalize_plain_text(value, max_chars=max_chars)


def _value(row: object, name: str, default: object = None) -> object:
    if isinstance(row, dict):
        return row.get(name, default)
    return getattr(row, name, default)
