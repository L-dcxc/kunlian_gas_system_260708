from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.api.schemas import (
    AlarmHistoryQuery,
    AlarmResponse,
    ControllerResponse,
    DetectorResponse,
    DeviceRealtimeResponse,
    HealthResponse,
    RealtimeDevicesQuery,
)
from app.config.defaults import AppConfig
from app.core.state_store import RealtimeFilter, StateStore
from app.db.connection import Database
from app.db.repositories.alarm_repository import AlarmRepository
from app.db.repositories.device_config_repository import ControllerRepository, DetectorRepository, GasTypeRepository, PortRepository
from app.db.repositories.record_repository import RecordRepository
from app.db.unit_of_work import UnitOfWork
from app.services.errors import ErrorCode
from app.services.models import AcquisitionState, AcquisitionStatus, Page, Pagination, ServiceResult

ACQUISITION_STATUS_STATE_KEY = "acquisition.status"


@dataclass(frozen=True, slots=True)
class _ConfigIndex:
    detectors: dict[int, dict[str, object]]
    controllers: dict[int, dict[str, object]]
    ports: dict[int, dict[str, object]]
    gas_types: dict[int, dict[str, object]]


class ApiReadService:
    """Read-only aggregation service for the future local HTTP API handlers."""

    def __init__(self, database: Database, state_store: StateStore, config: AppConfig | None = None) -> None:
        self._database = database
        self._state_store = state_store
        self._config = config

    def update_config(self, config: AppConfig) -> None:
        self._config = config

    def health(self) -> ServiceResult[HealthResponse]:
        return ServiceResult.ok(
            HealthResponse(
                status="ok",
                api_enabled=bool(self._config.api.enabled) if self._config is not None else False,
                acquisition_status=self.get_acquisition_status().status.value,
            )
        )

    def get_acquisition_status(self) -> AcquisitionState:
        state = self._state_store.get_value(ACQUISITION_STATUS_STATE_KEY)
        if isinstance(state, AcquisitionState):
            return state
        try:
            return AcquisitionState(AcquisitionStatus(str(state)))
        except Exception:
            return AcquisitionState(AcquisitionStatus.NOT_STARTED)

    def list_realtime_devices(self, query: RealtimeDevicesQuery) -> ServiceResult[Page[DeviceRealtimeResponse]]:
        try:
            pagination = Pagination(page=query.page, per_page=query.per_page)
            index = self._load_config_index()
            readings = self._state_store.get_realtime_snapshot(RealtimeFilter(status=query.status))
            views = tuple(
                view
                for view in (_realtime_view(reading, index) for reading in readings)
                if view is not None
                and _matches_realtime_filters(view, query.port_id, query.controller_id, index.detectors.get(view.detector_id))
            )
            total = len(views)
            start = pagination.offset
            return ServiceResult.ok(Page(views[start : start + pagination.limit], pagination, total))
        except ValueError as exc:
            return _validation(str(exc))
        except Exception:
            return ServiceResult.fail(code=int(ErrorCode.INTERNAL_ERROR), message="实时设备数据读取失败")

    def get_realtime_device(self, detector_id: int) -> ServiceResult[DeviceRealtimeResponse]:
        if not _valid_id(detector_id):
            return _validation("detector_id 必须为正整数")
        try:
            index = self._load_config_index()
            if detector_id not in index.detectors:
                return ServiceResult.fail(code=int(ErrorCode.NOT_FOUND), message="探测器不存在")
            readings = self._state_store.get_realtime_snapshot(RealtimeFilter(detector_ids=(detector_id,)))
            if not readings:
                return ServiceResult.fail(code=int(ErrorCode.NOT_FOUND), message="实时数据不存在")
            view = _realtime_view(readings[0], index)
            if view is None:
                return ServiceResult.fail(code=int(ErrorCode.NOT_FOUND), message="实时数据不存在")
            return ServiceResult.ok(view)
        except Exception:
            return ServiceResult.fail(code=int(ErrorCode.INTERNAL_ERROR), message="实时设备数据读取失败")

    def list_active_alarms(self) -> ServiceResult[tuple[AlarmResponse, ...]]:
        try:
            index = self._load_config_index()
            reading_by_detector = self._reading_by_detector()
            # Active alarm reads never call the alarm state machine; they only expose persisted active rows plus current display data.
            with UnitOfWork(self._database) as uow:
                rows = AlarmRepository(uow).list_active()
                uow.commit()
            return ServiceResult.ok(tuple(_alarm_view(row, index, reading_by_detector) for row in rows))
        except Exception:
            return ServiceResult.fail(code=int(ErrorCode.INTERNAL_ERROR), message="当前报警读取失败")

    def list_alarm_history(self, query: AlarmHistoryQuery) -> ServiceResult[Page[AlarmResponse]]:
        try:
            index = self._load_config_index()
            reading_by_detector = self._reading_by_detector()
            with UnitOfWork(self._database) as uow:
                rows, repo_page, total = RecordRepository(uow).list_alarm_records(
                    detector_id=query.detector_id,
                    controller_id=query.controller_id,
                    alarm_type=query.alarm_type,
                    status=query.status,
                    start_time=query.start_time,
                    end_time=query.end_time,
                    page=query.page,
                    per_page=query.per_page,
                    sort_by=query.sort_by,
                    sort_direction=query.sort_direction,
                )
                uow.commit()
            page = Pagination(page=repo_page.page, per_page=repo_page.per_page)
            return ServiceResult.ok(Page(tuple(_alarm_view(row, index, reading_by_detector) for row in rows), page, total))
        except ValueError as exc:
            return _validation(str(exc))
        except Exception:
            return ServiceResult.fail(code=int(ErrorCode.INTERNAL_ERROR), message="历史报警读取失败")

    def list_controllers(self) -> ServiceResult[tuple[ControllerResponse, ...]]:
        try:
            index = self._load_config_index()
            return ServiceResult.ok(tuple(_controller_view(row) for row in index.controllers.values()))
        except Exception:
            return ServiceResult.fail(code=int(ErrorCode.INTERNAL_ERROR), message="控制器列表读取失败")

    def list_detectors(self) -> ServiceResult[tuple[DetectorResponse, ...]]:
        try:
            index = self._load_config_index()
            return ServiceResult.ok(tuple(_detector_view(row, index.gas_types) for row in index.detectors.values()))
        except Exception:
            return ServiceResult.fail(code=int(ErrorCode.INTERNAL_ERROR), message="探测器列表读取失败")

    def _load_config_index(self) -> _ConfigIndex:
        # Even read paths go through repositories/UoW so API handlers cannot open ad hoc SQLite connections.
        with UnitOfWork(self._database) as uow:
            ports = tuple(_row_dict(row) for row in PortRepository(uow).list_active())
            controllers = tuple(_row_dict(row) for row in ControllerRepository(uow).list_active())
            gas_types = tuple(_row_dict(row) for row in GasTypeRepository(uow).list_active())
            detectors = tuple(_row_dict(row) for row in DetectorRepository(uow).list_active())
            uow.commit()
        return _ConfigIndex(
            detectors={int(row["id"]): row for row in detectors},
            controllers={int(row["id"]): row for row in controllers},
            ports={int(row["id"]): row for row in ports},
            gas_types={int(row["id"]): row for row in gas_types},
        )

    def _reading_by_detector(self) -> dict[int, Any]:
        readings = self._state_store.get_realtime_snapshot()
        return {detector_id: reading for reading in readings if (detector_id := _optional_positive_int(_value(reading, "detector_id"))) is not None}


def _realtime_view(reading: Any, index: _ConfigIndex) -> DeviceRealtimeResponse | None:
    detector_id = _optional_positive_int(_value(reading, "detector_id"))
    if detector_id is None:
        return None
    detector = index.detectors.get(detector_id)
    controller = _controller_for_detector(detector, index.controllers)
    return DeviceRealtimeResponse(
        detector_id=detector_id,
        position_code=_optional_text(_row_value(detector, "position_code")),
        detector_name=_optional_text(_row_value(detector, "name")),
        controller_id=_optional_positive_int(_value(reading, "controller_id")) or _optional_positive_int(_row_value(detector, "controller_id")),
        controller_name=_optional_text(_row_value(controller, "name")),
        status=_status_value(_value(reading, "status")),
        concentration=_optional_float(_value(reading, "concentration")),
        gas_type=_optional_text(_value(reading, "gas_type")) or _gas_name(detector, index.gas_types),
        unit=_optional_text(_value(reading, "unit")) or _optional_text(_row_value(detector, "unit")),
        alarm_level=_optional_non_negative_int(_value(reading, "alarm_level")),
        timestamp=_timestamp(_value(reading, "timestamp")),
    )


def _matches_realtime_filters(
    view: DeviceRealtimeResponse,
    port_id: int | None,
    controller_id: int | None,
    detector: dict[str, object] | None,
) -> bool:
    if port_id is not None and _optional_positive_int(_row_value(detector, "port_id")) != port_id:
        return False
    detector_controller_id = _optional_positive_int(_row_value(detector, "controller_id"))
    if controller_id is not None and view.controller_id != controller_id and detector_controller_id != controller_id:
        return False
    return True


def _alarm_view(row: Any, index: _ConfigIndex, reading_by_detector: dict[int, Any]) -> AlarmResponse:
    detector_id = int(row["detector_id"])
    detector = index.detectors.get(detector_id)
    controller = _controller_for_detector(detector, index.controllers)
    reading = reading_by_detector.get(detector_id)
    return AlarmResponse(
        alarm_id=int(row["id"]),
        detector_id=detector_id,
        position_code=_optional_text(_row_value(row, "position_code")) or _optional_text(_row_value(detector, "position_code")),
        detector_name=_optional_text(_row_value(row, "detector_name")) or _optional_text(_row_value(detector, "name")),
        controller_id=_optional_positive_int(_row_value(row, "controller_id")) or _optional_positive_int(_row_value(detector, "controller_id")),
        controller_name=_optional_text(_row_value(row, "controller_name")) or _optional_text(_row_value(controller, "name")),
        alarm_type=str(row["alarm_type"]),
        status=str(row["status"]),
        alarm_level=_optional_non_negative_int(row["alarm_level"]),
        trigger_value=_optional_float(row["trigger_value"]),
        start_time=str(row["start_time"]),
        end_time=None if row["end_time"] is None else str(row["end_time"]),
        current_status=None if reading is None else _status_value(_value(reading, "status")),
        concentration=None if reading is None else _optional_float(_value(reading, "concentration")),
        gas_type=None if reading is None else _optional_text(_value(reading, "gas_type")),
        unit=None if reading is None else _optional_text(_value(reading, "unit")),
    )


def _controller_view(row: dict[str, object]) -> ControllerResponse:
    return ControllerResponse(
        controller_id=int(row["id"]),
        port_id=int(row["port_id"]),
        controller_name=str(row["name"]),
        address=int(row["address"]),
        model=_optional_text(row.get("model")),
        detector_count=int(row["detector_count"]),
        enabled=bool(int(row["is_enabled"])),
    )


def _detector_view(row: dict[str, object], gas_types: dict[int, dict[str, object]]) -> DetectorResponse:
    gas_type_id = _optional_positive_int(row.get("gas_type_id"))
    gas = gas_types.get(gas_type_id) if gas_type_id is not None else None
    return DetectorResponse(
        detector_id=int(row["id"]),
        position_code=str(row["position_code"]),
        detector_name=str(row["name"]),
        port_id=int(row["port_id"]),
        controller_id=_optional_positive_int(row.get("controller_id")),
        gas_type_id=gas_type_id,
        gas_type=_optional_text(_row_value(gas, "name")),
        unit=str(row["unit"]),
        range_min=float(row["range_min"]),
        range_max=float(row["range_max"]),
        alarm_low=_optional_float(row.get("alarm_low")),
        alarm_high=_optional_float(row.get("alarm_high")),
        enabled=bool(int(row["is_enabled"])),
    )


def _controller_for_detector(detector: dict[str, object] | None, controllers: dict[int, dict[str, object]]) -> dict[str, object] | None:
    controller_id = _optional_positive_int(_row_value(detector, "controller_id"))
    return controllers.get(controller_id) if controller_id is not None else None


def _gas_name(detector: dict[str, object] | None, gas_types: dict[int, dict[str, object]]) -> str | None:
    gas_type_id = _optional_positive_int(_row_value(detector, "gas_type_id"))
    if gas_type_id is None:
        return None
    return _optional_text(_row_value(gas_types.get(gas_type_id), "name"))


def _value(reading: Any, field: str) -> Any:
    if isinstance(reading, dict):
        return reading.get(field)
    return getattr(reading, field, None)


def _row_value(row: Any, field: str) -> Any:
    if row is None:
        return None
    if isinstance(row, dict):
        return row.get(field)
    try:
        return row[field]
    except (KeyError, IndexError):
        return None


def _status_value(value: object) -> str:
    if hasattr(value, "value"):
        return str(value.value)
    return _optional_text(value) or "invalid"


def _timestamp(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return _optional_text(value)


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).replace("\r", " ").replace("\n", " ").split())
    return text[:160]


def _optional_positive_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str) and value.isdigit() and int(value) > 0:
        return int(value)
    return None


def _optional_non_negative_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _optional_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    try:
        return float(str(value))
    except ValueError:
        return None


def _row_dict(row: Any) -> dict[str, object]:
    return {key: row[key] for key in row.keys()}


def _valid_id(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _validation(message: str) -> ServiceResult:
    return ServiceResult.fail(code=int(ErrorCode.VALIDATION_ERROR), message=message)
