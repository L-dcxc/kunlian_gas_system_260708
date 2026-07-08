from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.api.schemas import (
    AlarmHistoryQuery,
    RealtimeDevicesQuery,
    envelope_from_result,
    paginated_data,
    validate_alarm_history_query,
    validate_detector_id,
    validate_realtime_devices_query,
)
from app.services.api_read_service import ApiReadService
from app.services.errors import ErrorCode
from app.services.models import ServiceResult

API_V1_PREFIX = "/api/v1"


def create_read_model_router(service: ApiReadService) -> APIRouter:
    router = APIRouter(prefix=API_V1_PREFIX)

    # This container only exposes GET read-model routes; state-changing API
    # surfaces remain absent so unsupported methods fall through to 405/404.
    @router.get("/health")
    def health() -> JSONResponse:
        return _service_response(service.health())

    @router.get("/devices/realtime")
    def devices_realtime(request: Request) -> JSONResponse:
        query = validate_realtime_devices_query(
            port_id=request.query_params.get("port_id"),
            controller_id=request.query_params.get("controller_id"),
            status=request.query_params.get("status"),
            page=request.query_params.get("page", "1"),
            per_page=request.query_params.get("per_page", "20"),
        )
        if not query.success:
            return _service_response(query)
        return _paged_response(service.list_realtime_devices(_require_data(query)))

    @router.get("/devices/{detector_id}/realtime")
    def device_realtime(detector_id: str) -> JSONResponse:
        validated = validate_detector_id(detector_id)
        if not validated.success:
            return _service_response(validated)
        return _service_response(service.get_realtime_device(_require_data(validated)))

    @router.get("/alarms/active")
    def alarms_active() -> JSONResponse:
        return _service_response(service.list_active_alarms())

    @router.get("/alarms/history")
    def alarms_history(request: Request) -> JSONResponse:
        query = validate_alarm_history_query(
            detector_id=request.query_params.get("detector_id"),
            controller_id=request.query_params.get("controller_id"),
            alarm_type=request.query_params.get("alarm_type"),
            status=request.query_params.get("status"),
            start_time=request.query_params.get("start_time"),
            end_time=request.query_params.get("end_time"),
            page=request.query_params.get("page", "1"),
            per_page=request.query_params.get("per_page", "20"),
            sort_by=request.query_params.get("sort_by", "start_time"),
            sort_direction=request.query_params.get("sort_direction", "DESC"),
        )
        if not query.success:
            return _service_response(query)
        return _paged_response(service.list_alarm_history(_require_data(query)))

    @router.get("/controllers")
    def controllers() -> JSONResponse:
        return _service_response(service.list_controllers())

    @router.get("/detectors")
    def detectors() -> JSONResponse:
        return _service_response(service.list_detectors())

    return router


def _paged_response(result: ServiceResult[object]) -> JSONResponse:
    if result.success:
        return _service_response(ServiceResult.ok(paginated_data(_require_data(result)), result.message))
    return _service_response(result)


def _service_response(result: ServiceResult[object]) -> JSONResponse:
    envelope = envelope_from_result(result)
    return JSONResponse(status_code=_status_code(envelope.code), content=envelope.to_dict())


def _status_code(code: int) -> int:
    if code == 0:
        return 200
    if code in {400, 404, 405, 409, 500, 503}:
        return code
    return int(ErrorCode.INTERNAL_ERROR)


def _require_data(result: ServiceResult[object]) -> object:
    return result.data
