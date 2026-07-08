from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum

from app.device.protocols.base import ValidationResult, bytes_to_hex, normalize_hex_display
from app.services.models import DeviceReading

DEFAULT_DEBUG_HEX_LIMIT = 512
MAX_DEBUG_MESSAGE_LENGTH = 256


class DebugParseStatus(StrEnum):
    NOT_RUN = "not_run"
    SUCCESS = "success"
    VALIDATION_FAILED = "validation_failed"
    PARSE_FAILED = "parse_failed"
    TIMEOUT = "timeout"
    CHANNEL_ERROR = "channel_error"


@dataclass(frozen=True, slots=True)
class DebugFrame:
    raw_hex: str
    truncated: bool = False

    @classmethod
    def from_bytes(cls, payload: bytes, limit: int = DEFAULT_DEBUG_HEX_LIMIT) -> DebugFrame:
        rendered = bytes_to_hex(payload, max_chars=limit)
        return cls(raw_hex=rendered, truncated=rendered.endswith("..."))

    @classmethod
    def from_hex(cls, raw_hex: str, limit: int = DEFAULT_DEBUG_HEX_LIMIT) -> DebugFrame:
        rendered = normalize_hex_display(raw_hex, max_chars=limit)
        return cls(raw_hex=rendered, truncated=rendered.endswith("..."))


@dataclass(frozen=True, slots=True)
class DebugCrcResult:
    ok: bool | None
    expected_hex: str | None = None
    actual_hex: str | None = None
    error_code: str | None = None

    @classmethod
    def from_validation(cls, validation: ValidationResult) -> DebugCrcResult:
        return cls(
            ok=validation.ok if validation.crc_expected or validation.crc_actual else None,
            expected_hex=validation.crc_expected,
            actual_hex=validation.crc_actual,
            error_code=str(validation.error_code.value) if validation.error_code is not None else None,
        )


@dataclass(frozen=True, slots=True)
class DebugParseResult:
    status: DebugParseStatus
    readings: tuple[DeviceReading, ...] = field(default_factory=tuple)
    error_code: str | None = None
    message: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", DebugParseStatus(self.status))
        object.__setattr__(self, "readings", tuple(self.readings))
        if self.error_code is not None:
            object.__setattr__(self, "error_code", _stable_text(self.error_code, max_length=64))
        message = _stable_text(self.message, max_length=MAX_DEBUG_MESSAGE_LENGTH) if self.message else ""
        object.__setattr__(self, "message", message)


@dataclass(frozen=True, slots=True)
class DebugExchange:
    request_hex: str
    response_hex: str
    crc: DebugCrcResult
    parse: DebugParseResult
    error_reason: str = ""
    elapsed_ms: int | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "request_hex",
            normalize_hex_display(self.request_hex, max_chars=DEFAULT_DEBUG_HEX_LIMIT),
        )
        object.__setattr__(
            self,
            "response_hex",
            normalize_hex_display(self.response_hex, max_chars=DEFAULT_DEBUG_HEX_LIMIT),
        )
        error_reason = _stable_text(self.error_reason, max_length=MAX_DEBUG_MESSAGE_LENGTH) if self.error_reason else ""
        object.__setattr__(self, "error_reason", error_reason)
        if self.elapsed_ms is not None and (
            isinstance(self.elapsed_ms, bool) or not isinstance(self.elapsed_ms, int) or self.elapsed_ms < 0
        ):
            raise ValueError("elapsed_ms must be greater than or equal to 0")
        if not isinstance(self.created_at, datetime):
            raise ValueError("created_at must be a datetime")

    @classmethod
    def from_validation(
        cls,
        request: bytes,
        response: bytes,
        validation: ValidationResult,
        readings: tuple[DeviceReading, ...] | list[DeviceReading] = (),
        elapsed_ms: int | None = None,
    ) -> DebugExchange:
        if validation.ok:
            parse = DebugParseResult(status=DebugParseStatus.SUCCESS, readings=tuple(readings))
            error_reason = ""
        else:
            parse = DebugParseResult(
                status=DebugParseStatus.VALIDATION_FAILED,
                error_code=str(validation.error_code.value) if validation.error_code is not None else None,
                message=validation.message,
            )
            error_reason = validation.message
        return cls(
            request_hex=bytes_to_hex(request),
            response_hex=bytes_to_hex(response) if response else "",
            crc=DebugCrcResult.from_validation(validation),
            parse=parse,
            error_reason=error_reason,
            elapsed_ms=elapsed_ms,
        )


def _stable_text(value: str, max_length: int) -> str:
    if not isinstance(value, str):
        raise ValueError("text value must be a string")
    normalized = " ".join(value.replace("\r", " ").replace("\n", " ").split())
    return normalized[:max_length]
