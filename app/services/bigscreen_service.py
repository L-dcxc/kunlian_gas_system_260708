from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.core.state_store import StateStore
from app.db.connection import Database
from app.db.repositories.alarm_repository import AlarmRepository
from app.db.repositories.device_config_repository import ControllerRepository, DetectorRepository, GasTypeRepository
from app.db.repositories.map_repository import MapPointRepository, MapRepository
from app.db.repositories.settings_repository import SettingsRepository
from app.db.unit_of_work import UnitOfWork
from app.services.errors import ErrorCode
from app.services.models import AcquisitionState, AcquisitionStatus, DeviceStatus, ServiceResult

BIGSCREEN_PAGES_KEY = "bigscreen.carousel.pages"
BIGSCREEN_INTERVAL_KEY = "bigscreen.carousel.interval_seconds"
BIGSCREEN_ALARM_PRIORITY_KEY = "bigscreen.alarm_priority.enabled"
BIGSCREEN_REFRESH_MS_KEY = "bigscreen.refresh_interval_ms"
ACQUISITION_STATUS_STATE_KEY = "acquisition.status"

DEFAULT_PAGES = ("data", "map", "devices")
SUPPORTED_PAGES = frozenset({"data", "map", "devices"})
DEFAULT_INTERVAL_SECONDS = 15
DEFAULT_REFRESH_AFTER_MS = 1000
MIN_INTERVAL_SECONDS = 5
MAX_INTERVAL_SECONDS = 3600
MIN_REFRESH_AFTER_MS = 250
MAX_REFRESH_AFTER_MS = 60000
TEXT_MAX = 160

ALARM_STATUSES = frozenset({DeviceStatus.ALARM_LOW.value, DeviceStatus.ALARM_HIGH.value, DeviceStatus.OVER_RANGE.value})
ALARM_PRIORITY = {
    "over_range": 0,
    "alarm_high": 1,
    "alarm_low": 2,
    "fault": 3,
    "offline": 4,
    "disabled": 5,
    "warming": 6,
}
SENSITIVE_RE = re.compile(
    r"([A-Za-z]:\\[^\s]+|/[^\s]+|\b(?:select|insert|update|delete|drop|sqlite|traceback|"
    r"password|secret|token|license)\b)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class BigscreenCarouselConfig:
    pages: tuple[str, ...]
    interval_seconds: int
    alarm_priority_enabled: bool
    refresh_after_ms: int


@dataclass(frozen=True, slots=True)
class BigscreenMetricSummary:
    total_detectors: int
    normal_count: int
    alarm_count: int
    offline_count: int
    fault_count: int
    disabled_count: int
    warming_count: int
    invalid_count: int
    active_alarm_count: int
    acquisition_status: str
    generated_at: str
    refresh_after_ms: int


@dataclass(frozen=True, slots=True)
class BigscreenDeviceCard:
    detector_id: int
    position_code: str
    detector_name: str
    controller_id: int | None
    controller_name: str | None
    status: str
    concentration: float | None
    gas_type: str | None
    unit: str | None
    alarm_level: int | None
    timestamp: str | None
    active_alarm: bool
    active_alarm_type: str | None


@dataclass(frozen=True, slots=True)
class BigscreenMapPoint:
    point_id: int
    map_id: int
    map_name: str
    detector_id: int
    x_ratio: float
    y_ratio: float
    label: str | None
    detector_name: str | None
    status: str
    concentration: float | None
    unit: str | None
    active_alarm: bool
    active_alarm_type: str | None


@dataclass(frozen=True, slots=True)
class BigscreenAlarmFocus:
    alarm_id: int
    detector_id: int
    alarm_type: str
    alarm_level: int | None
    trigger_value: float | None
    start_time: str
    device_card: BigscreenDeviceCard
    map_point: BigscreenMapPoint | None
    refresh_after_ms: int


@dataclass(frozen=True, slots=True)
class BigscreenSnapshot:
    config: BigscreenCarouselConfig
    summary: BigscreenMetricSummary
    alarm_focus: BigscreenAlarmFocus | None
    device_cards: tuple[BigscreenDeviceCard, ...]
    map_points: tuple[BigscreenMapPoint, ...]


@dataclass(frozen=True, slots=True)
class _Dataset:
    config: BigscreenCarouselConfig
    detectors: dict[int, dict[str, object]]
    controllers: dict[int, dict[str, object]]
    gas_types: dict[int, dict[str, object]]
    maps: dict[int, dict[str, object]]
    point_rows: tuple[dict[str, object], ...]
    active_alarms: tuple[dict[str, object], ...]
    readings: dict[int, Any]
    acquisition_status: str


class BigscreenService:
    """Read-only aggregation service for the future fullscreen bigscreen UI."""

    def __init__(self, database: Database, state_store: StateStore) -> None:
        self._database = database
        self._state_store = state_store

    def get_carousel_config(self) -> ServiceResult[BigscreenCarouselConfig]:
        try:
            # Keep the settings read inside the repository/UoW boundary; this service intentionally has no write path.
            with UnitOfWork(self._database) as uow:
                config = _config_from_settings(SettingsRepository(uow))
                uow.commit()
            return ServiceResult.ok(config)
        except Exception:
            return ServiceResult.fail(code=int(ErrorCode.INTERNAL_ERROR), message="大屏配置读取失败")

    def get_metrics_summary(self) -> ServiceResult[BigscreenMetricSummary]:
        try:
            dataset = self._load_dataset()
            cards = _device_cards(dataset)
            return ServiceResult.ok(_summary(cards, dataset))
        except Exception:
            return ServiceResult.fail(code=int(ErrorCode.INTERNAL_ERROR), message="大屏摘要读取失败")

    def get_alarm_focus(self) -> ServiceResult[BigscreenAlarmFocus | None]:
        try:
            dataset = self._load_dataset()
            cards = _device_cards(dataset)
            points = _map_points(dataset)
            return ServiceResult.ok(_alarm_focus(dataset, cards, points))
        except Exception:
            return ServiceResult.fail(code=int(ErrorCode.INTERNAL_ERROR), message="大屏报警焦点读取失败")

    def get_snapshot(self) -> ServiceResult[BigscreenSnapshot]:
        try:
            dataset = self._load_dataset()
            cards = _device_cards(dataset)
            points = _map_points(dataset)
            return ServiceResult.ok(
                BigscreenSnapshot(
                    config=dataset.config,
                    summary=_summary(cards, dataset),
                    alarm_focus=_alarm_focus(dataset, cards, points),
                    device_cards=cards,
                    map_points=points,
                )
            )
        except Exception:
            return ServiceResult.fail(code=int(ErrorCode.INTERNAL_ERROR), message="大屏数据读取失败")

    def _load_dataset(self) -> _Dataset:
        # Active alarms come from persisted state-machine rows, not UI color or transient status.
        with UnitOfWork(self._database) as uow:
            settings = SettingsRepository(uow)
            maps_repo = MapRepository(uow)
            points_repo = MapPointRepository(uow)
            map_rows = tuple(_row_dict(row) for row in maps_repo.list_enabled())
            point_rows: list[dict[str, object]] = []
            for row in map_rows:
                point_rows.extend(
                    _row_dict(point) for point in points_repo.list_active_for_map_with_detectors(int(row["id"]))
                )
            dataset = _Dataset(
                config=_config_from_settings(settings),
                detectors={int(row["id"]): _row_dict(row) for row in DetectorRepository(uow).list_active()},
                controllers={int(row["id"]): _row_dict(row) for row in ControllerRepository(uow).list_active()},
                gas_types={int(row["id"]): _row_dict(row) for row in GasTypeRepository(uow).list_active()},
                maps={int(row["id"]): row for row in map_rows},
                point_rows=tuple(point_rows),
                active_alarms=tuple(_row_dict(row) for row in AlarmRepository(uow).list_active()),
                readings={},
                acquisition_status=AcquisitionStatus.NOT_STARTED.value,
            )
            uow.commit()
        readings = self._state_store.get_realtime_snapshot()
        return _Dataset(
            config=dataset.config,
            detectors=dataset.detectors,
            controllers=dataset.controllers,
            gas_types=dataset.gas_types,
            maps=dataset.maps,
            point_rows=dataset.point_rows,
            active_alarms=dataset.active_alarms,
            readings={
                detector_id: reading
                for reading in readings
                if (detector_id := _positive_id(_value(reading, "detector_id"))) is not None
            },
            acquisition_status=_acquisition_status(self._state_store.get_value(ACQUISITION_STATUS_STATE_KEY)),
        )


def _device_cards(dataset: _Dataset) -> tuple[BigscreenDeviceCard, ...]:
    active_by_detector = _active_alarm_by_detector(dataset.active_alarms)
    cards: list[BigscreenDeviceCard] = []
    for detector_id, detector in sorted(dataset.detectors.items()):
        reading = dataset.readings.get(detector_id)
        controller = _controller_for_detector(detector, dataset.controllers)
        alarm = active_by_detector.get(detector_id)
        status = _status(reading) or (DeviceStatus.NORMAL.value if _enabled(detector) else DeviceStatus.DISABLED.value)
        if reading is None and _enabled(detector):
            status = DeviceStatus.OFFLINE.value
        cards.append(
            BigscreenDeviceCard(
                detector_id=detector_id,
                position_code=_safe_text(_row_value(detector, "position_code")),
                detector_name=_safe_text(_row_value(detector, "name")),
                controller_id=_positive_id(_row_value(detector, "controller_id")),
                controller_name=_optional_text(_row_value(controller, "name")),
                status=status,
                concentration=_float_or_none(_value(reading, "concentration")),
                gas_type=_optional_text(_value(reading, "gas_type")) or _gas_name(detector, dataset.gas_types),
                unit=_optional_text(_value(reading, "unit")) or _optional_text(_row_value(detector, "unit")),
                alarm_level=_int_or_none(_value(reading, "alarm_level")),
                timestamp=_timestamp(_value(reading, "timestamp")),
                active_alarm=alarm is not None,
                active_alarm_type=None if alarm is None else _safe_text(alarm.get("alarm_type"), max_length=40),
            )
        )
    return tuple(cards)


def _map_points(dataset: _Dataset) -> tuple[BigscreenMapPoint, ...]:
    active_by_detector = _active_alarm_by_detector(dataset.active_alarms)
    points: list[BigscreenMapPoint] = []
    for row in dataset.point_rows:
        detector_id = int(row["detector_id"])
        reading = dataset.readings.get(detector_id)
        alarm = active_by_detector.get(detector_id)
        map_row = dataset.maps.get(int(row["map_id"]))
        status = _status(reading) or (
            DeviceStatus.OFFLINE.value if _enabled(row, "detector_is_enabled") else DeviceStatus.DISABLED.value
        )
        # Bigscreen reuses persisted ratio coordinates and deliberately never creates display-pixel coordinates.
        points.append(
            BigscreenMapPoint(
                point_id=int(row["id"]),
                map_id=int(row["map_id"]),
                map_name=_safe_text(_row_value(map_row, "name")),
                detector_id=detector_id,
                x_ratio=float(row["x_ratio"]),
                y_ratio=float(row["y_ratio"]),
                label=_optional_text(row.get("label")),
                detector_name=_optional_text(row.get("detector_name")),
                status=status,
                concentration=_float_or_none(_value(reading, "concentration")),
                unit=_optional_text(_value(reading, "unit")) or _optional_text(row.get("detector_unit")),
                active_alarm=alarm is not None,
                active_alarm_type=None if alarm is None else _safe_text(alarm.get("alarm_type"), max_length=40),
            )
        )
    return tuple(points)


def _summary(cards: tuple[BigscreenDeviceCard, ...], dataset: _Dataset) -> BigscreenMetricSummary:
    counts = {"normal": 0, "alarm": 0, "offline": 0, "fault": 0, "disabled": 0, "warming": 0, "invalid": 0}
    for card in cards:
        if card.status in ALARM_STATUSES:
            counts["alarm"] += 1
        elif card.status == DeviceStatus.NORMAL.value:
            counts["normal"] += 1
        elif card.status == DeviceStatus.OFFLINE.value:
            counts["offline"] += 1
        elif card.status == DeviceStatus.FAULT.value:
            counts["fault"] += 1
        elif card.status == DeviceStatus.DISABLED.value:
            counts["disabled"] += 1
        elif card.status == DeviceStatus.WARMING.value:
            counts["warming"] += 1
        else:
            counts["invalid"] += 1
    return BigscreenMetricSummary(
        total_detectors=len(cards),
        normal_count=counts["normal"],
        alarm_count=counts["alarm"],
        offline_count=counts["offline"],
        fault_count=counts["fault"],
        disabled_count=counts["disabled"],
        warming_count=counts["warming"],
        invalid_count=counts["invalid"],
        active_alarm_count=len(dataset.active_alarms),
        acquisition_status=dataset.acquisition_status,
        generated_at=datetime.now(timezone.utc).isoformat(),
        refresh_after_ms=dataset.config.refresh_after_ms,
    )


def _alarm_focus(
    dataset: _Dataset,
    cards: tuple[BigscreenDeviceCard, ...],
    points: tuple[BigscreenMapPoint, ...],
) -> BigscreenAlarmFocus | None:
    if not dataset.config.alarm_priority_enabled or not dataset.active_alarms:
        return None
    alarm = sorted(dataset.active_alarms, key=_alarm_sort_key)[0]
    detector_id = int(alarm["detector_id"])
    card = next((item for item in cards if item.detector_id == detector_id), None)
    if card is None:
        return None
    point = next((item for item in points if item.detector_id == detector_id), None)
    return BigscreenAlarmFocus(
        alarm_id=int(alarm["id"]),
        detector_id=detector_id,
        alarm_type=_safe_text(alarm.get("alarm_type"), max_length=40),
        alarm_level=_int_or_none(alarm.get("alarm_level")),
        trigger_value=_float_or_none(alarm.get("trigger_value")),
        start_time=_safe_text(alarm.get("start_time"), max_length=80),
        device_card=card,
        map_point=point,
        refresh_after_ms=dataset.config.refresh_after_ms,
    )


def _config_from_settings(settings: SettingsRepository) -> BigscreenCarouselConfig:
    pages = _parse_pages(settings.get_value(BIGSCREEN_PAGES_KEY))
    return BigscreenCarouselConfig(
        pages=pages,
        interval_seconds=_bounded_int(
            settings.get_value(BIGSCREEN_INTERVAL_KEY),
            DEFAULT_INTERVAL_SECONDS,
            MIN_INTERVAL_SECONDS,
            MAX_INTERVAL_SECONDS,
        ),
        alarm_priority_enabled=_bool_setting(settings.get_value(BIGSCREEN_ALARM_PRIORITY_KEY), True),
        refresh_after_ms=_bounded_int(
            settings.get_value(BIGSCREEN_REFRESH_MS_KEY),
            DEFAULT_REFRESH_AFTER_MS,
            MIN_REFRESH_AFTER_MS,
            MAX_REFRESH_AFTER_MS,
        ),
    )


def _parse_pages(value: str | None) -> tuple[str, ...]:
    if not value:
        return DEFAULT_PAGES
    try:
        raw = json.loads(value)
        items = raw if isinstance(raw, list) else []
    except json.JSONDecodeError:
        items = [item.strip() for item in value.split(",")]
    pages = tuple(str(item) for item in items if str(item) in SUPPORTED_PAGES)
    return pages or DEFAULT_PAGES


def _active_alarm_by_detector(rows: tuple[dict[str, object], ...]) -> dict[int, dict[str, object]]:
    alarms: dict[int, dict[str, object]] = {}
    for row in sorted(rows, key=_alarm_sort_key):
        alarms.setdefault(int(row["detector_id"]), row)
    return alarms


def _alarm_sort_key(row: dict[str, object]) -> tuple[int, float, int]:
    start = str(row.get("start_time") or "")
    try:
        started_at = datetime.fromisoformat(start).timestamp()
    except ValueError:
        started_at = 0.0
    return (ALARM_PRIORITY.get(str(row.get("alarm_type")), 99), -started_at, -int(row.get("id") or 0))


def _acquisition_status(state: object) -> str:
    if isinstance(state, AcquisitionState):
        return state.status.value
    try:
        return AcquisitionStatus(str(state)).value
    except Exception:
        return AcquisitionStatus.NOT_STARTED.value


def _row_dict(row: Any) -> dict[str, object]:
    return {key: row[key] for key in row.keys()}


def _controller_for_detector(
    detector: dict[str, object],
    controllers: dict[int, dict[str, object]],
) -> dict[str, object] | None:
    controller_id = _positive_id(_row_value(detector, "controller_id"))
    return controllers.get(controller_id) if controller_id is not None else None


def _gas_name(detector: dict[str, object], gas_types: dict[int, dict[str, object]]) -> str | None:
    gas_id = _positive_id(_row_value(detector, "gas_type_id"))
    return _optional_text(_row_value(gas_types.get(gas_id), "name")) if gas_id is not None else None


def _value(obj: Any, field: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(field)
    return getattr(obj, field, None)


def _row_value(row: dict[str, object] | None, field: str) -> object | None:
    return None if row is None else row.get(field)


def _status(reading: Any) -> str | None:
    value = _value(reading, "status")
    if hasattr(value, "value"):
        value = value.value
    return _safe_text(value, max_length=40) if value is not None else None


def _timestamp(value: object) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    return _optional_text(value, max_length=80)


def _enabled(row: dict[str, object], field: str = "is_enabled") -> bool:
    try:
        return bool(int(row.get(field, 0)))
    except (TypeError, ValueError):
        return False


def _safe_text(value: object, *, max_length: int = TEXT_MAX) -> str:
    text = "" if value is None else str(value)
    text = text.replace("\x00", " ").replace("<", " ").replace(">", " ")
    text = " ".join(text.replace("\r", " ").replace("\n", " ").split())
    text = SENSITIVE_RE.sub("[redacted]", text)
    return text[:max_length]


def _optional_text(value: object, *, max_length: int = TEXT_MAX) -> str | None:
    if value is None:
        return None
    text = _safe_text(value, max_length=max_length)
    return text or None


def _positive_id(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _int_or_none(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float_or_none(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _bounded_int(value: str | None, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(str(value)) if value is not None else default
    except ValueError:
        return default
    return parsed if minimum <= parsed <= maximum else default


def _bool_setting(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default
