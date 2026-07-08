from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping

from app.services.models import DeviceReading, DeviceSourceType, ProtocolMode

MAX_FRAME_BYTES = 4096
MAX_RAW_HEX_CHARS = 512
MAX_MESSAGE_LENGTH = 256


class CRCByteOrder(StrEnum):
    HIGH_BYTE_FIRST = "high_byte_first"
    LOW_BYTE_FIRST = "low_byte_first"


class ValidationErrorCode(StrEnum):
    EMPTY_RESPONSE = "empty_response"
    FRAME_TOO_LARGE = "frame_too_large"
    LENGTH_MISMATCH = "length_mismatch"
    ADDRESS_MISMATCH = "address_mismatch"
    FUNCTION_MISMATCH = "function_mismatch"
    CRC_MISMATCH = "crc_mismatch"
    UNKNOWN_STATUS = "unknown_status"
    INVALID_DATA = "invalid_data"


@dataclass(frozen=True, slots=True)
class PollTarget:
    detector_id: int
    detector_address: int | None = None
    controller_id: int | None = None
    controller_address: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "detector_id", _positive_int(self.detector_id, "detector_id"))
        _validate_optional_positive_int(self.controller_id, "controller_id")
        _validate_optional_address(self.detector_address, "detector_address")
        _validate_optional_address(self.controller_address, "controller_address")


@dataclass(frozen=True, slots=True)
class PollBuildContext:
    protocol: ProtocolMode
    source_type: DeviceSourceType
    port_id: int
    targets: tuple[PollTarget, ...]
    default_timeout_ms: int = 1000
    labels: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "protocol", ProtocolMode(self.protocol))
        object.__setattr__(self, "source_type", DeviceSourceType(self.source_type))
        object.__setattr__(self, "port_id", _positive_int(self.port_id, "port_id"))
        object.__setattr__(self, "targets", tuple(self.targets))
        if not self.targets:
            raise ValueError("targets are required")
        _validate_timeout_ms(self.default_timeout_ms, "default_timeout_ms")
        object.__setattr__(self, "labels", tuple(_safe_text(item, max_length=64) for item in self.labels))


@dataclass(frozen=True, slots=True)
class PollRequest:
    protocol: ProtocolMode
    source_type: DeviceSourceType
    port_id: int
    unit_address: int
    function_code: int
    payload: bytes
    timeout_ms: int = 1000
    expected_response_min_bytes: int = 5
    crc_byte_order: CRCByteOrder = CRCByteOrder.LOW_BYTE_FIRST
    detector_id: int | None = None
    controller_id: int | None = None
    label: str = ""
    metadata: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "protocol", ProtocolMode(self.protocol))
        object.__setattr__(self, "source_type", DeviceSourceType(self.source_type))
        object.__setattr__(self, "port_id", _positive_int(self.port_id, "port_id"))
        _validate_address(self.unit_address, "unit_address")
        _validate_function_code(self.function_code)
        if not isinstance(self.payload, bytes) or not self.payload:
            raise ValueError("payload is required")
        if len(self.payload) > MAX_FRAME_BYTES:
            raise ValueError("payload is too large")
        _validate_timeout_ms(self.timeout_ms, "timeout_ms")
        if (
            isinstance(self.expected_response_min_bytes, bool)
            or not isinstance(self.expected_response_min_bytes, int)
            or self.expected_response_min_bytes < 1
        ):
            raise ValueError("expected_response_min_bytes must be positive")
        object.__setattr__(self, "crc_byte_order", CRCByteOrder(self.crc_byte_order))
        _validate_optional_positive_int(self.detector_id, "detector_id")
        _validate_optional_positive_int(self.controller_id, "controller_id")
        object.__setattr__(self, "label", _safe_text(self.label, max_length=128) if self.label else "")
        object.__setattr__(self, "metadata", _freeze_metadata(self.metadata))


@dataclass(frozen=True, slots=True)
class ValidationResult:
    ok: bool
    error_code: ValidationErrorCode | str | None = None
    message: str = ""
    crc_expected: str | None = None
    crc_actual: str | None = None
    raw_hex: str = ""

    def __post_init__(self) -> None:
        if self.error_code is not None:
            object.__setattr__(self, "error_code", ValidationErrorCode(self.error_code))
        object.__setattr__(self, "message", _stable_message(self.message))
        if self.crc_expected is not None:
            object.__setattr__(self, "crc_expected", _safe_text(self.crc_expected, max_length=32))
        if self.crc_actual is not None:
            object.__setattr__(self, "crc_actual", _safe_text(self.crc_actual, max_length=32))
        object.__setattr__(self, "raw_hex", normalize_hex_display(self.raw_hex, max_chars=MAX_RAW_HEX_CHARS))
        if self.ok and self.error_code is not None:
            raise ValueError("successful validation cannot include error_code")
        if not self.ok and self.error_code is None:
            raise ValueError("failed validation requires error_code")

    @classmethod
    def success(
        cls,
        raw_frame: bytes | None = None,
        raw_hex: str = "",
        crc_expected: str | None = None,
        crc_actual: str | None = None,
        message: str = "ok",
    ) -> ValidationResult:
        return cls(
            ok=True,
            message=message,
            crc_expected=crc_expected,
            crc_actual=crc_actual,
            raw_hex=bytes_to_hex(raw_frame) if raw_frame is not None else raw_hex,
        )

    @classmethod
    def failure(
        cls,
        error_code: ValidationErrorCode | str,
        message: str,
        raw_frame: bytes | None = None,
        raw_hex: str = "",
        crc_expected: str | None = None,
        crc_actual: str | None = None,
    ) -> ValidationResult:
        return cls(
            ok=False,
            error_code=ValidationErrorCode(error_code),
            message=message,
            crc_expected=crc_expected,
            crc_actual=crc_actual,
            raw_hex=bytes_to_hex(raw_frame) if raw_frame is not None else raw_hex,
        )


class ProtocolAdapter(ABC):
    """Shared adapter boundary reused by acquisition and device debug.

    Device frames are untrusted until validate_response returns ok=True. Concrete
    adapters own CRC, length, address, function and status checks before parsing.
    """

    mode: ProtocolMode

    @abstractmethod
    def build_poll_requests(self, context: PollBuildContext) -> list[PollRequest]:
        raise NotImplementedError

    @abstractmethod
    def validate_response(self, request: PollRequest, response: bytes) -> ValidationResult:
        raise NotImplementedError

    @abstractmethod
    def parse_response(self, request: PollRequest, response: bytes) -> list[DeviceReading]:
        raise NotImplementedError


def bytes_to_hex(payload: bytes, max_chars: int = MAX_RAW_HEX_CHARS) -> str:
    if not isinstance(payload, bytes):
        raise ValueError("payload must be bytes")
    return normalize_hex_display(" ".join(f"{byte:02X}" for byte in payload), max_chars=max_chars)


def normalize_hex_display(raw_hex: str, max_chars: int = MAX_RAW_HEX_CHARS) -> str:
    if not isinstance(raw_hex, str):
        raise ValueError("raw_hex must be a string")
    cleaned = " ".join(raw_hex.replace("\r", " ").replace("\n", " ").split()).upper()
    if len(cleaned) <= max_chars:
        return cleaned
    return f"{cleaned[:max_chars]}..."


def _freeze_metadata(metadata: Mapping[str, str]) -> Mapping[str, str]:
    if not isinstance(metadata, Mapping):
        raise ValueError("metadata must be a mapping")
    cleaned: dict[str, str] = {}
    for key, value in metadata.items():
        cleaned[_safe_text(str(key), max_length=64)] = _safe_text(str(value), max_length=128)
    return MappingProxyType(cleaned)


def _positive_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field_name} must be a positive integer")
    return value


def _validate_optional_positive_int(value: int | None, field_name: str) -> None:
    if value is not None:
        _positive_int(value, field_name)


def _validate_address(value: object, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > 255:
        raise ValueError(f"{field_name} must be between 0 and 255")


def _validate_optional_address(value: int | None, field_name: str) -> None:
    if value is not None:
        _validate_address(value, field_name)


def _validate_function_code(value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1 or value > 127:
        raise ValueError("function_code must be between 1 and 127")


def _validate_timeout_ms(value: int, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1 or value > 60_000:
        raise ValueError(f"{field_name} must be between 1 and 60000")


def _safe_text(value: str, max_length: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("text value is required")
    normalized = " ".join(value.replace("\r", " ").replace("\n", " ").split())
    if len(normalized) > max_length:
        return normalized[:max_length]
    return normalized


def _stable_message(value: str) -> str:
    if value == "":
        return ""
    if not isinstance(value, str):
        raise ValueError("message must be a string")
    return _safe_text(value, max_length=MAX_MESSAGE_LENGTH)
