from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum

MAX_PAYLOAD_BYTES = 4096
MAX_ERROR_MESSAGE_LENGTH = 256


class ChannelType(StrEnum):
    SERIAL = "serial"
    TCP = "tcp"


class TcpFrameMode(StrEnum):
    RTU_OVER_TCP = "rtu_over_tcp"


class Parity(StrEnum):
    NONE = "none"
    EVEN = "even"
    ODD = "odd"


class ChannelErrorCode(StrEnum):
    NOT_OPEN = "not_open"
    OPEN_FAILED = "open_failed"
    CLOSE_FAILED = "close_failed"
    TIMEOUT = "timeout"
    CONNECTION_FAILED = "connection_failed"
    INVALID_PAYLOAD = "invalid_payload"
    IO_ERROR = "io_error"


class ChannelError(RuntimeError):
    def __init__(self, code: ChannelErrorCode | str, message: str) -> None:
        self.code = ChannelErrorCode(code)
        self.message = _stable_message(message)
        super().__init__(self.message)


@dataclass(frozen=True, slots=True)
class SerialParameters:
    port_name: str
    baud_rate: int = 9600
    data_bits: int = 8
    stop_bits: int = 1
    parity: Parity = Parity.NONE

    def __post_init__(self) -> None:
        port_name = _safe_text(self.port_name, "port_name", max_length=64)
        object.__setattr__(self, "port_name", port_name)
        if (
            isinstance(self.baud_rate, bool)
            or not isinstance(self.baud_rate, int)
            or self.baud_rate < 1200
            or self.baud_rate > 115200
        ):
            raise ValueError("baud_rate must be between 1200 and 115200")
        if self.data_bits not in {7, 8}:
            raise ValueError("data_bits must be 7 or 8")
        if self.stop_bits not in {1, 2}:
            raise ValueError("stop_bits must be 1 or 2")
        object.__setattr__(self, "parity", Parity(self.parity))


@dataclass(frozen=True, slots=True)
class TcpParameters:
    host: str
    port: int
    frame_mode: TcpFrameMode = TcpFrameMode.RTU_OVER_TCP
    connect_timeout_ms: int = 3000

    def __post_init__(self) -> None:
        host = _safe_text(self.host, "host", max_length=255)
        object.__setattr__(self, "host", host)
        if isinstance(self.port, bool) or not isinstance(self.port, int) or self.port < 1 or self.port > 65535:
            raise ValueError("port must be between 1 and 65535")
        object.__setattr__(self, "frame_mode", TcpFrameMode(self.frame_mode))
        _validate_timeout_ms(self.connect_timeout_ms, "connect_timeout_ms")


@dataclass(frozen=True, slots=True)
class ChannelConfig:
    port_id: int
    channel_type: ChannelType
    serial: SerialParameters | None = None
    tcp: TcpParameters | None = None
    timeout_ms: int = 1000
    retry_count: int = 0
    max_payload_bytes: int = MAX_PAYLOAD_BYTES
    labels: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if isinstance(self.port_id, bool) or not isinstance(self.port_id, int) or self.port_id <= 0:
            raise ValueError("port_id must be a positive integer")
        channel_type = ChannelType(self.channel_type)
        object.__setattr__(self, "channel_type", channel_type)
        if channel_type is ChannelType.SERIAL and self.serial is None:
            raise ValueError("serial parameters are required for serial channel")
        if channel_type is ChannelType.TCP and self.tcp is None:
            raise ValueError("tcp parameters are required for tcp channel")
        _validate_timeout_ms(self.timeout_ms, "timeout_ms")
        if (
            isinstance(self.retry_count, bool)
            or not isinstance(self.retry_count, int)
            or self.retry_count < 0
            or self.retry_count > 10
        ):
            raise ValueError("retry_count must be between 0 and 10")
        if (
            isinstance(self.max_payload_bytes, bool)
            or not isinstance(self.max_payload_bytes, int)
            or self.max_payload_bytes < 1
            or self.max_payload_bytes > MAX_PAYLOAD_BYTES
        ):
            raise ValueError(f"max_payload_bytes must be between 1 and {MAX_PAYLOAD_BYTES}")
        object.__setattr__(self, "labels", tuple(_safe_text(item, "label", max_length=64) for item in self.labels))


@dataclass(frozen=True, slots=True)
class TransactResult:
    ok: bool
    payload: bytes = b""
    error_code: ChannelErrorCode | None = None
    message: str = ""
    elapsed_ms: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.payload, bytes):
            raise ValueError("payload must be bytes")
        if len(self.payload) > MAX_PAYLOAD_BYTES:
            raise ValueError("payload is too large")
        if self.error_code is not None:
            object.__setattr__(self, "error_code", ChannelErrorCode(self.error_code))
        object.__setattr__(self, "message", _stable_message(self.message))
        if self.elapsed_ms is not None and (
            isinstance(self.elapsed_ms, bool) or not isinstance(self.elapsed_ms, int) or self.elapsed_ms < 0
        ):
            raise ValueError("elapsed_ms must be greater than or equal to 0")
        if self.ok and self.error_code is not None:
            raise ValueError("successful transaction cannot include error_code")
        if not self.ok and self.error_code is None:
            raise ValueError("failed transaction requires error_code")

    @classmethod
    def success(cls, payload: bytes, elapsed_ms: int | None = None) -> TransactResult:
        return cls(ok=True, payload=payload, elapsed_ms=elapsed_ms)

    @classmethod
    def failure(
        cls,
        error_code: ChannelErrorCode | str,
        message: str,
        elapsed_ms: int | None = None,
    ) -> TransactResult:
        return cls(ok=False, error_code=ChannelErrorCode(error_code), message=message, elapsed_ms=elapsed_ms)


class Channel(ABC):
    """Common channel boundary for acquisition and device debug.

    Concrete serial/socket implementations live behind this interface so UI and
    business pages never receive handles to pyserial objects or sockets.
    """

    config: ChannelConfig

    @abstractmethod
    def open(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def close(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def transact(self, payload: bytes, timeout_ms: int | None = None) -> TransactResult:
        raise NotImplementedError


def validate_outbound_payload(payload: bytes, max_payload_bytes: int = MAX_PAYLOAD_BYTES) -> bytes:
    if not isinstance(payload, bytes):
        raise ValueError("payload must be bytes")
    if not payload:
        raise ValueError("payload is required")
    if len(payload) > max_payload_bytes:
        raise ValueError("payload is too large")
    return payload


def _validate_timeout_ms(value: int, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1 or value > 60_000:
        raise ValueError(f"{field_name} must be between 1 and 60000")


def _safe_text(value: str, field_name: str, max_length: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} is required")
    normalized = " ".join(value.replace("\r", " ").replace("\n", " ").split())
    if len(normalized) > max_length:
        raise ValueError(f"{field_name} is too long")
    return normalized


def _stable_message(value: str) -> str:
    if value == "":
        return ""
    if not isinstance(value, str):
        raise ValueError("message must be a string")
    normalized = " ".join(value.replace("\r", " ").replace("\n", " ").split())
    return normalized[:MAX_ERROR_MESSAGE_LENGTH]
