from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from math import ceil
from typing import Generic, TypeVar

T = TypeVar("T")
MAX_PER_PAGE = 100
MAX_TEXT_LENGTH = 512


class ProtocolMode(StrEnum):
    PROTOCOL_1 = "protocol_1"
    PROTOCOL_2 = "protocol_2"


class DeviceSourceType(StrEnum):
    CONTROLLER = "controller"
    PROBE = "probe"


class DeviceStatus(StrEnum):
    NORMAL = "normal"
    ALARM_LOW = "alarm_low"
    ALARM_HIGH = "alarm_high"
    FAULT = "fault"
    OFFLINE = "offline"
    DISABLED = "disabled"
    OVER_RANGE = "over_range"
    WARMING = "warming"
    INVALID = "invalid"


class AcquisitionStatus(StrEnum):
    NOT_STARTED = "not_started"
    RUNNING = "running"
    ERROR = "error"
    RECONNECTING = "reconnecting"
    STOPPED = "stopped"


@dataclass(frozen=True, slots=True)
class ServiceError:
    code: str
    message: str
    field: str | None = None

    def __post_init__(self) -> None:
        _require_non_empty_text(self.code, "code", max_length=64)
        object.__setattr__(self, "message", _safe_text(self.message, max_length=MAX_TEXT_LENGTH))
        if self.field is not None:
            object.__setattr__(self, "field", _safe_text(self.field, max_length=128))


@dataclass(frozen=True, slots=True)
class ServiceResult(Generic[T]):
    success: bool
    code: int
    message: str
    data: T | None = None
    errors: tuple[ServiceError, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "message", _safe_text(self.message, max_length=MAX_TEXT_LENGTH))
        object.__setattr__(self, "errors", tuple(self.errors))
        if self.success and self.errors:
            raise ValueError("successful result cannot include errors")

    @classmethod
    def ok(cls, data: T | None = None, message: str = "ok") -> ServiceResult[T]:
        return cls(success=True, code=0, message=message, data=data)

    @classmethod
    def fail(
        cls,
        code: int,
        message: str,
        errors: tuple[ServiceError, ...] | list[ServiceError] = (),
    ) -> ServiceResult[T]:
        if code == 0:
            raise ValueError("failure code must be non-zero")
        return cls(success=False, code=code, message=message, errors=tuple(errors))


@dataclass(frozen=True, slots=True)
class Pagination:
    page: int = 1
    per_page: int = 20

    def __post_init__(self) -> None:
        if isinstance(self.page, bool) or not isinstance(self.page, int) or self.page < 1:
            raise ValueError("page must be greater than or equal to 1")
        if (
            isinstance(self.per_page, bool)
            or not isinstance(self.per_page, int)
            or self.per_page < 1
            or self.per_page > MAX_PER_PAGE
        ):
            raise ValueError(f"per_page must be between 1 and {MAX_PER_PAGE}")

    @property
    def limit(self) -> int:
        return self.per_page

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.per_page


@dataclass(frozen=True, slots=True)
class Page(Generic[T]):
    items: tuple[T, ...]
    pagination: Pagination
    total: int

    def __post_init__(self) -> None:
        if isinstance(self.total, bool) or not isinstance(self.total, int) or self.total < 0:
            raise ValueError("total must be greater than or equal to 0")
        object.__setattr__(self, "items", tuple(self.items))

    @property
    def total_pages(self) -> int:
        if self.total == 0:
            return 0
        return ceil(self.total / self.pagination.per_page)


@dataclass(frozen=True, slots=True)
class DeviceReadingFilter:
    port_id: int | None = None
    controller_id: int | None = None
    detector_ids: tuple[int, ...] | None = None
    statuses: tuple[DeviceStatus, ...] | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None

    def __post_init__(self) -> None:
        _validate_optional_positive_int(self.port_id, "port_id")
        _validate_optional_positive_int(self.controller_id, "controller_id")
        if self.detector_ids is not None:
            detector_ids = tuple(_positive_int(item, "detector_id") for item in self.detector_ids)
            object.__setattr__(self, "detector_ids", detector_ids)
        if self.statuses is not None:
            object.__setattr__(self, "statuses", tuple(_enum(DeviceStatus, item, "status") for item in self.statuses))
        _validate_optional_datetime(self.start_time, "start_time")
        _validate_optional_datetime(self.end_time, "end_time")
        if self.start_time is not None and self.end_time is not None and self.start_time > self.end_time:
            raise ValueError("start_time must not be after end_time")


@dataclass(frozen=True, slots=True)
class AcquisitionState:
    status: AcquisitionStatus
    message: str = ""
    active_port_ids: tuple[int, ...] = field(default_factory=tuple)
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", _enum(AcquisitionStatus, self.status, "status"))
        object.__setattr__(self, "message", _safe_text(self.message, max_length=MAX_TEXT_LENGTH))
        object.__setattr__(
            self,
            "active_port_ids",
            tuple(_positive_int(item, "port_id") for item in self.active_port_ids),
        )
        _validate_optional_datetime(self.updated_at, "updated_at")


@dataclass(frozen=True, slots=True)
class DeviceReading:
    protocol: ProtocolMode
    source_type: DeviceSourceType
    port_id: int
    controller_id: int | None
    detector_id: int
    controller_address: int | None
    detector_address: int | None
    status: DeviceStatus
    concentration: float | None
    gas_type: str | None
    unit: str | None
    alarm_level: int | None
    raw_status: int | str | None
    raw_value: str | None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        object.__setattr__(self, "protocol", _enum(ProtocolMode, self.protocol, "protocol"))
        object.__setattr__(self, "source_type", _enum(DeviceSourceType, self.source_type, "source_type"))
        object.__setattr__(self, "port_id", _positive_int(self.port_id, "port_id"))
        _validate_optional_positive_int(self.controller_id, "controller_id")
        object.__setattr__(self, "detector_id", _positive_int(self.detector_id, "detector_id"))
        _validate_optional_address(self.controller_address, "controller_address")
        _validate_optional_address(self.detector_address, "detector_address")
        object.__setattr__(self, "status", _enum(DeviceStatus, self.status, "status"))
        if self.concentration is not None and (
            isinstance(self.concentration, bool) or not isinstance(self.concentration, int | float)
        ):
            raise ValueError("concentration must be numeric")
        if self.gas_type is not None:
            object.__setattr__(self, "gas_type", _safe_text(self.gas_type, max_length=128))
        if self.unit is not None:
            object.__setattr__(self, "unit", _safe_text(self.unit, max_length=32))
        if self.alarm_level is not None and (
            isinstance(self.alarm_level, bool) or not isinstance(self.alarm_level, int) or self.alarm_level < 0
        ):
            raise ValueError("alarm_level must be greater than or equal to 0")
        if isinstance(self.raw_status, str):
            object.__setattr__(self, "raw_status", _safe_text(self.raw_status, max_length=128))
        if self.raw_value is not None:
            object.__setattr__(self, "raw_value", _safe_text(self.raw_value, max_length=MAX_TEXT_LENGTH))
        _validate_optional_datetime(self.timestamp, "timestamp")


@dataclass(frozen=True, slots=True)
class ErrorDetail:
    code: str
    message: str
    target: str | None = None

    def __post_init__(self) -> None:
        _require_non_empty_text(self.code, "code", max_length=64)
        object.__setattr__(self, "message", _safe_text(self.message, max_length=MAX_TEXT_LENGTH))
        if self.target is not None:
            object.__setattr__(self, "target", _safe_text(self.target, max_length=128))


def _enum(enum_type: type[T], value: object, field_name: str) -> T:
    try:
        return enum_type(value)  # type: ignore[call-arg]
    except ValueError as exc:
        raise ValueError(f"unsupported {field_name}") from exc


def _positive_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field_name} must be a positive integer")
    return value


def _validate_optional_positive_int(value: int | None, field_name: str) -> None:
    if value is not None:
        _positive_int(value, field_name)


def _validate_optional_address(value: int | None, field_name: str) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > 255:
        raise ValueError(f"{field_name} must be between 0 and 255")


def _validate_optional_datetime(value: datetime | None, field_name: str) -> None:
    if value is not None and not isinstance(value, datetime):
        raise ValueError(f"{field_name} must be a datetime")


def _require_non_empty_text(value: str, field_name: str, max_length: int) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} is required")
    _safe_text(value, max_length=max_length)


def _safe_text(value: str, max_length: int) -> str:
    if not isinstance(value, str):
        raise ValueError("text value must be a string")
    normalized = " ".join(value.replace("\r", " ").replace("\n", " ").split())
    if len(normalized) > max_length:
        return normalized[:max_length]
    return normalized
