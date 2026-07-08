from __future__ import annotations

import re
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Any

from app.db.repositories.record_repository import MAX_QUERY_SPAN_DAYS
from app.services.errors import ErrorCode
from app.services.models import DeviceStatus, Page, ServiceError, ServiceResult

API_OK_MESSAGE = "ok"
API_ERROR_FALLBACK_MESSAGE = "操作失败，请稍后重试。"
API_VALIDATION_MESSAGE = "参数校验失败"
API_MAX_PER_PAGE = 100
ALLOWED_ALARM_TYPES = frozenset({"alarm_low", "alarm_high", "over_range", "fault", "offline", "disabled", "warming"})
ALLOWED_ALARM_STATUS = frozenset({"active", "recovered"})
ALLOWED_HISTORY_SORTS = frozenset({"id", "detector_id", "alarm_type", "status", "start_time", "end_time", "position_code"})
ALLOWED_SORT_DIRECTIONS = frozenset({"ASC", "DESC"})
ALLOWED_DEVICE_STATUSES = frozenset(status.value for status in DeviceStatus)

_STACK_MARKER = re.compile(
    r"(?i)(traceback|stack trace|\bfile\s+\".*?\"|\bline\s+\d+|\bat\s+[A-Za-z_][\w.]+)"
)
_ABSOLUTE_PATH = re.compile(
    r"(?i)([A-Z]:\\[^\s,;]+|\\\\[^\s,;]+\\[^\s,;]+|/[A-Za-z0-9_.-]+(?:/[^\s,;]+)+)"
)
_AUTH_DETAIL = re.compile(
    r"(?i)(license algorithm|authorization algorithm|machine fingerprint|signature key|"
    r"machine[_ -]?(?:id|code)|hardware[_ -]?id|hwid)"
)
_SQL_DETAIL = re.compile(
    r"(?is)(sqlite3?\.\w+|sqlalchemy|operationalerror|integrityerror|programmingerror|"
    r"\bselect\b.+\bfrom\b|\binsert\b.+\binto\b|\bupdate\b.+\bset\b|\bdelete\b.+\bfrom\b|"
    r"\bdrop\s+table\b|\bpragma\b)"
)
_SENSITIVE_KEYWORD = re.compile(
    r"(?i)(password|passwd|pwd|api[_ -]?token|token|secret|authorization[_ -]?code|auth[_ -]?code|"
    r"license[_ -]?code|machine[_ -]?(?:id|code)|hardware[_ -]?id|private[_ -]?key|secret[_ -]?key)"
)


@dataclass(frozen=True, slots=True)
class ApiErrorItem:
    field: str
    message: str


@dataclass(frozen=True, slots=True)
class ApiEnvelope:
    success: bool
    code: int
    message: str
    data: object | None = None

    def to_dict(self) -> dict[str, object | None]:
        return {"success": self.success, "code": self.code, "message": self.message, "data": _to_plain(self.data)}


@dataclass(frozen=True, slots=True)
class ApiPagination:
    page: int
    per_page: int
    total: int
    total_pages: int


@dataclass(frozen=True, slots=True)
class PaginatedData:
    items: tuple[object, ...]
    pagination: ApiPagination


@dataclass(frozen=True, slots=True)
class RealtimeDevicesQuery:
    port_id: int | None = None
    controller_id: int | None = None
    status: str | None = None
    page: int = 1
    per_page: int = 20


@dataclass(frozen=True, slots=True)
class AlarmHistoryQuery:
    detector_id: int | None = None
    controller_id: int | None = None
    alarm_type: str | None = None
    status: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    page: int = 1
    per_page: int = 20
    sort_by: str = "start_time"
    sort_direction: str = "DESC"


@dataclass(frozen=True, slots=True)
class HealthResponse:
    status: str
    api_enabled: bool
    acquisition_status: str


@dataclass(frozen=True, slots=True)
class DeviceRealtimeResponse:
    detector_id: int
    position_code: str | None
    detector_name: str | None
    controller_id: int | None
    controller_name: str | None
    status: str
    concentration: float | None
    gas_type: str | None
    unit: str | None
    alarm_level: int | None
    timestamp: str | None


@dataclass(frozen=True, slots=True)
class AlarmResponse:
    alarm_id: int
    detector_id: int
    position_code: str | None
    detector_name: str | None
    controller_id: int | None
    controller_name: str | None
    alarm_type: str
    status: str
    alarm_level: int | None
    trigger_value: float | None
    start_time: str
    end_time: str | None
    current_status: str | None
    concentration: float | None
    gas_type: str | None
    unit: str | None


@dataclass(frozen=True, slots=True)
class ControllerResponse:
    controller_id: int
    port_id: int
    controller_name: str
    address: int
    model: str | None
    detector_count: int
    enabled: bool


@dataclass(frozen=True, slots=True)
class DetectorResponse:
    detector_id: int
    position_code: str
    detector_name: str
    port_id: int
    controller_id: int | None
    gas_type_id: int | None
    gas_type: str | None
    unit: str
    range_min: float
    range_max: float
    alarm_low: float | None
    alarm_high: float | None
    enabled: bool


def success_envelope(data: object | None = None, message: str = API_OK_MESSAGE) -> ApiEnvelope:
    return ApiEnvelope(success=True, code=0, message=_safe_success_message(message), data=data)


def error_envelope(code: int, message: str, errors: tuple[ApiErrorItem, ...] = ()) -> ApiEnvelope:
    safe_errors = tuple(
        ApiErrorItem(field=_safe_error_field(item.field), message=_safe_error_message(item.message, code=code))
        for item in errors
    )
    data = {"errors": safe_errors} if safe_errors else None
    return ApiEnvelope(success=False, code=code, message=_safe_error_message(message, code=code), data=data)


def envelope_from_result(result: ServiceResult[Any]) -> ApiEnvelope:
    if result.success:
        return success_envelope(result.data, result.message)
    errors = tuple(ApiErrorItem(field=item.field or "", message=item.message) for item in result.errors)
    return error_envelope(result.code, result.message, errors)


def paginated_data(page: Page[Any]) -> PaginatedData:
    return PaginatedData(
        items=tuple(page.items),
        pagination=ApiPagination(
            page=page.pagination.page,
            per_page=page.pagination.per_page,
            total=page.total,
            total_pages=page.total_pages,
        ),
    )


def validate_detector_id(value: object) -> ServiceResult[int]:
    detector_id, error = _positive_int(value, "detector_id")
    if error is not None:
        return _validation((error,))
    return ServiceResult.ok(detector_id)


def validate_realtime_devices_query(
    *,
    port_id: object = None,
    controller_id: object = None,
    status: object = None,
    page: object = 1,
    per_page: object = 20,
) -> ServiceResult[RealtimeDevicesQuery]:
    errors: list[ApiErrorItem] = []
    safe_port_id, error = _optional_positive_int(port_id, "port_id")
    _append_error(errors, error)
    safe_controller_id, error = _optional_positive_int(controller_id, "controller_id")
    _append_error(errors, error)
    safe_status, error = _optional_code(status, "status", ALLOWED_DEVICE_STATUSES)
    _append_error(errors, error)
    safe_page, error = _positive_int(page, "page")
    _append_error(errors, error)
    safe_per_page, error = _per_page(per_page)
    _append_error(errors, error)
    if errors:
        return _validation(tuple(errors))
    return ServiceResult.ok(RealtimeDevicesQuery(safe_port_id, safe_controller_id, safe_status, safe_page, safe_per_page))


def validate_alarm_history_query(
    *,
    detector_id: object = None,
    controller_id: object = None,
    alarm_type: object = None,
    status: object = None,
    start_time: object = None,
    end_time: object = None,
    page: object = 1,
    per_page: object = 20,
    sort_by: object = "start_time",
    sort_direction: object = "DESC",
) -> ServiceResult[AlarmHistoryQuery]:
    errors: list[ApiErrorItem] = []
    safe_detector_id, error = _optional_positive_int(detector_id, "detector_id")
    _append_error(errors, error)
    safe_controller_id, error = _optional_positive_int(controller_id, "controller_id")
    _append_error(errors, error)
    safe_alarm_type, error = _optional_code(alarm_type, "alarm_type", ALLOWED_ALARM_TYPES)
    _append_error(errors, error)
    safe_status, error = _optional_code(status, "status", ALLOWED_ALARM_STATUS)
    _append_error(errors, error)
    safe_start, start_error = _optional_time(start_time, "start_time")
    _append_error(errors, start_error)
    safe_end, end_error = _optional_time(end_time, "end_time")
    _append_error(errors, end_error)
    if start_error is None and end_error is None:
        _append_error(errors, _time_range_error(safe_start, safe_end))
    safe_page, error = _positive_int(page, "page")
    _append_error(errors, error)
    safe_per_page, error = _per_page(per_page)
    _append_error(errors, error)
    safe_sort_by, error = _code(sort_by, "sort_by", ALLOWED_HISTORY_SORTS)
    _append_error(errors, error)
    safe_sort_direction, error = _code(str(sort_direction).upper(), "sort_direction", ALLOWED_SORT_DIRECTIONS)
    _append_error(errors, error)
    if errors:
        return _validation(tuple(errors))
    return ServiceResult.ok(
        AlarmHistoryQuery(
            detector_id=safe_detector_id,
            controller_id=safe_controller_id,
            alarm_type=safe_alarm_type,
            status=safe_status,
            start_time=None if safe_start is None else safe_start.isoformat(),
            end_time=None if safe_end is None else safe_end.isoformat(),
            page=safe_page,
            per_page=safe_per_page,
            sort_by=safe_sort_by,
            sort_direction=safe_sort_direction,
        )
    )


def _validation(errors: tuple[ApiErrorItem, ...]) -> ServiceResult[Any]:
    result_errors = tuple(ServiceError(code="validation_error", field=item.field, message=item.message) for item in errors)
    return ServiceResult.fail(code=int(ErrorCode.VALIDATION_ERROR), message=API_VALIDATION_MESSAGE, errors=result_errors)


def _append_error(errors: list[ApiErrorItem], error: ApiErrorItem | None) -> None:
    if error is not None:
        errors.append(error)


def _optional_positive_int(value: object, field: str) -> tuple[int | None, ApiErrorItem | None]:
    if value is None or value == "":
        return None, None
    return _positive_int(value, field)


def _positive_int(value: object, field: str) -> tuple[int, ApiErrorItem | None]:
    if isinstance(value, bool):
        return 0, ApiErrorItem(field, f"{field} 必须为正整数")
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str) and value.strip().isdigit():
        parsed = int(value.strip())
    else:
        return 0, ApiErrorItem(field, f"{field} 必须为正整数")
    if parsed < 1:
        return 0, ApiErrorItem(field, f"{field} 必须大于等于 1")
    return parsed, None


def _per_page(value: object) -> tuple[int, ApiErrorItem | None]:
    parsed, error = _positive_int(value, "per_page")
    if error is not None:
        return 0, error
    if parsed > API_MAX_PER_PAGE:
        return 0, ApiErrorItem("per_page", f"per_page 必须小于等于 {API_MAX_PER_PAGE}")
    return parsed, None


def _optional_code(value: object, field: str, allowed: frozenset[str]) -> tuple[str | None, ApiErrorItem | None]:
    if value is None or value == "":
        return None, None
    return _code(value, field, allowed)


def _code(value: object, field: str, allowed: frozenset[str]) -> tuple[str, ApiErrorItem | None]:
    if not isinstance(value, str):
        return "", ApiErrorItem(field, f"{field} 不支持")
    text = " ".join(value.replace("\r", " ").replace("\n", " ").split())[:80]
    if text not in allowed:
        return "", ApiErrorItem(field, f"{field} 不支持")
    return text, None


def _optional_time(value: object, field: str) -> tuple[datetime | None, ApiErrorItem | None]:
    if value is None or value == "":
        return None, None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None, ApiErrorItem(field, "时间必须为 ISO-8601 格式")
    else:
        return None, ApiErrorItem(field, "时间必须为 ISO-8601 格式")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed, None


def _time_range_error(start: datetime | None, end: datetime | None) -> ApiErrorItem | None:
    if start is None or end is None:
        return None
    if start > end:
        return ApiErrorItem("start_time", "start_time 必须早于或等于 end_time")
    if end - start > timedelta(days=MAX_QUERY_SPAN_DAYS):
        return ApiErrorItem("end_time", f"时间范围不能超过 {MAX_QUERY_SPAN_DAYS} 天")
    return None


def _safe_success_message(message: object) -> str:
    text = _safe_message(message)
    if not text or _contains_sensitive_detail(text):
        return API_OK_MESSAGE
    return text


def _safe_error_message(message: object, *, code: int) -> str:
    fallback = API_VALIDATION_MESSAGE if code == int(ErrorCode.VALIDATION_ERROR) else API_ERROR_FALLBACK_MESSAGE
    text = _safe_message(message)
    if not text or _contains_sensitive_detail(text):
        return fallback
    return text[:256]


def _safe_error_field(field: object) -> str:
    text = _safe_message(field)
    if not text or _contains_sensitive_detail(text):
        return ""
    return text[:128]


def _safe_message(message: object) -> str:
    text = " ".join(str(message).replace("\r", " ").replace("\n", " ").split())
    return text[:512]


def _contains_sensitive_detail(text: str) -> bool:
    return any(
        pattern.search(text)
        for pattern in (_STACK_MARKER, _ABSOLUTE_PATH, _AUTH_DETAIL, _SQL_DETAIL, _SENSITIVE_KEYWORD)
    )


def _to_plain(value: object) -> object:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, StrEnum):
        return value.value
    if is_dataclass(value):
        return {key: _to_plain(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): _to_plain(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_to_plain(item) for item in value]
    return str(value)[:512]
