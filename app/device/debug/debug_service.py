from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.db.connection import Database
from app.device.channels.base import Channel, ChannelErrorCode
from app.device.debug.models import DebugCrcResult, DebugExchange, DebugParseResult, DebugParseStatus
from app.device.protocols.base import PollRequest, ValidationResult, bytes_to_hex
from app.device.protocols.factory import ProtocolAdapterFactory
from app.services.auth_service import Session, SessionStore
from app.services.errors import ErrorCode
from app.services.models import DeviceReading, DeviceSourceType, ProtocolMode, ServiceResult
from app.services.permissions import Permission

READ_HOLDING_REGISTERS = 0x03
WRITE_SINGLE_REGISTER = 0x06


@dataclass(frozen=True, slots=True)
class DebugReadCommand:
    source_type: DeviceSourceType | str
    port_id: int
    unit_address: int
    start_register: int
    register_count: int
    mode: ProtocolMode | str | None = None
    function_code: int = READ_HOLDING_REGISTERS
    timeout_ms: int = 1000
    detector_id: int | None = None
    controller_id: int | None = None
    controller_address: int | None = None
    label: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_type", DeviceSourceType(self.source_type))
        if self.mode is not None:
            object.__setattr__(self, "mode", ProtocolMode(self.mode))
        _positive_int(self.port_id, "port_id")
        _address(self.unit_address, "unit_address")
        _non_negative_int(self.start_register, "start_register")
        _positive_int(self.register_count, "register_count")
        _positive_int(self.timeout_ms, "timeout_ms")
        if self.detector_id is not None:
            _positive_int(self.detector_id, "detector_id")
        if self.controller_id is not None:
            _positive_int(self.controller_id, "controller_id")
        if self.controller_address is not None:
            _address(self.controller_address, "controller_address")
        if isinstance(self.function_code, bool) or not isinstance(self.function_code, int):
            raise ValueError("function_code must be an integer")
        if self.label:
            object.__setattr__(self, "label", _stable_text(self.label, 128))


@dataclass(frozen=True, slots=True)
class DebugFrameResult:
    request_hex: str
    response_hex: str | None = None
    crc_ok: bool | None = None
    validation_message: str = ""
    readings: tuple[DeviceReading, ...] = field(default_factory=tuple)
    error_code: str | None = None
    exchange: DebugExchange | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "request_hex", _stable_hex(self.request_hex))
        if self.response_hex is not None:
            object.__setattr__(self, "response_hex", _stable_hex(self.response_hex))
        object.__setattr__(self, "validation_message", _stable_text(self.validation_message, 256))
        object.__setattr__(self, "readings", tuple(self.readings))
        if self.error_code is not None:
            object.__setattr__(self, "error_code", _stable_text(self.error_code, 64))


class DeviceDebugService:
    def __init__(
        self,
        *,
        adapter_factory: ProtocolAdapterFactory | None = None,
        database: Database | None = None,
        session_store: SessionStore | None = None,
    ) -> None:
        self._adapter_factory = adapter_factory or ProtocolAdapterFactory(database=database)
        self._database = database
        self._session_store = session_store

    def build_read_request(
        self,
        command: DebugReadCommand,
        session_or_id: Session | str | None = None,
    ) -> ServiceResult[DebugFrameResult]:
        permission = self._check_permission(session_or_id)
        if permission is not None:
            return permission
        try:
            request = self._request(command)
        except ValueError as exc:
            return _validation(str(exc))
        result = DebugFrameResult(
            request_hex=bytes_to_hex(request.payload),
            validation_message="request built",
        )
        return ServiceResult.ok(result)

    def validate_response(
        self,
        command: DebugReadCommand,
        response: bytes,
        session_or_id: Session | str | None = None,
    ) -> ServiceResult[DebugFrameResult]:
        permission = self._check_permission(session_or_id)
        if permission is not None:
            return permission
        try:
            request = self._request(command)
            adapter = self._adapter(command)
            validation = adapter.validate_response(request, response)
            readings = adapter.parse_response(request, response) if validation.ok else []
        except ValueError as exc:
            return _validation(str(exc))
        except Exception:
            return _internal_error()
        return ServiceResult.ok(_result_from_validation(request, response, validation, readings))

    def send_read_request(
        self,
        command: DebugReadCommand,
        channel: Channel,
        session_or_id: Session | str | None = None,
    ) -> ServiceResult[DebugFrameResult]:
        permission = self._check_permission(session_or_id)
        if permission is not None:
            return permission
        try:
            request = self._request(command)
            adapter = self._adapter(command)
        except ValueError as exc:
            return _validation(str(exc))
        transact = channel.transact(request.payload, timeout_ms=request.timeout_ms)
        if not transact.ok:
            result = _result_from_channel_failure(request, transact.error_code, transact.message, transact.elapsed_ms)
            return ServiceResult.ok(result, message="调试请求已发送但通讯失败")
        try:
            validation = adapter.validate_response(request, transact.payload)
            readings = adapter.parse_response(request, transact.payload) if validation.ok else []
            result = _result_from_validation(request, transact.payload, validation, readings, transact.elapsed_ms)
            return ServiceResult.ok(result)
        except ValueError as exc:
            return _validation(str(exc))
        except Exception:
            return _internal_error()

    def _adapter(self, command: DebugReadCommand) -> Any:
        return self._adapter_factory.get_adapter(command.mode)

    def _request(self, command: DebugReadCommand) -> PollRequest:
        if command.function_code == WRITE_SINGLE_REGISTER:
            # Protocol 1 mentions function 06, but write/control semantics and
            # permissions are not confirmed; debug remains a read-only surface.
            raise ValueError("功能码 06 写寄存器待确认，设备调试仅允许读 03")
        if command.function_code != READ_HOLDING_REGISTERS:
            raise ValueError("设备调试仅允许读功能码 03")
        adapter = self._adapter(command)
        return adapter.build_read_request(
            source_type=command.source_type,
            port_id=command.port_id,
            unit_address=command.unit_address,
            start_register=command.start_register,
            register_count=command.register_count,
            timeout_ms=command.timeout_ms,
            detector_id=command.detector_id,
            controller_id=command.controller_id,
            controller_address=command.controller_address,
            label=command.label,
        )

    def _check_permission(self, session_or_id: Session | str | None) -> ServiceResult[DebugFrameResult] | None:
        if session_or_id is None:
            return None
        if self._database is None or self._session_store is None:
            return ServiceResult.fail(code=int(ErrorCode.PERMISSION_DENIED), message="调试权限校验未配置")
        try:
            self._session_store.require_permission(
                self._database,
                session_or_id,
                Permission.SYSTEM_SETTINGS.value,
                "设备调试读请求",
            )
        except Exception as exc:
            return ServiceResult.fail(code=int(ErrorCode.PERMISSION_DENIED), message=str(exc))
        return None


def _result_from_validation(
    request: PollRequest,
    response: bytes,
    validation: ValidationResult,
    readings: list[DeviceReading] | tuple[DeviceReading, ...],
    elapsed_ms: int | None = None,
) -> DebugFrameResult:
    exchange = DebugExchange.from_validation(
        request=request.payload,
        response=response,
        validation=validation,
        readings=readings,
        elapsed_ms=elapsed_ms,
    )
    return DebugFrameResult(
        request_hex=exchange.request_hex,
        response_hex=exchange.response_hex,
        crc_ok=exchange.crc.ok,
        validation_message=validation.message or "ok",
        readings=tuple(readings) if validation.ok else (),
        error_code=str(validation.error_code.value) if validation.error_code is not None else None,
        exchange=exchange,
    )


def _result_from_channel_failure(
    request: PollRequest,
    code: ChannelErrorCode | None,
    message: str,
    elapsed_ms: int | None,
) -> DebugFrameResult:
    stable_message = _stable_text(message or "通讯失败", 256)
    parse = DebugParseResult(
        status=DebugParseStatus.TIMEOUT if code is ChannelErrorCode.TIMEOUT else DebugParseStatus.CHANNEL_ERROR,
        error_code=str(code.value) if code is not None else None,
        message=stable_message,
    )
    exchange = DebugExchange(
        request_hex=bytes_to_hex(request.payload),
        response_hex="",
        crc=DebugCrcResult(ok=None),
        parse=parse,
        error_reason=stable_message,
        elapsed_ms=elapsed_ms,
    )
    return DebugFrameResult(
        request_hex=exchange.request_hex,
        response_hex="",
        crc_ok=None,
        validation_message=stable_message,
        error_code=str(code.value) if code is not None else "channel_error",
        exchange=exchange,
    )


def _validation(message: str) -> ServiceResult[DebugFrameResult]:
    return ServiceResult.fail(code=int(ErrorCode.VALIDATION_ERROR), message=_stable_text(message, 256))


def _internal_error() -> ServiceResult[DebugFrameResult]:
    return ServiceResult.fail(code=int(ErrorCode.INTERNAL_ERROR), message="设备调试处理失败")


def _positive_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field_name} must be a positive integer")
    return value


def _non_negative_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be greater than or equal to 0")
    return value


def _address(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > 255:
        raise ValueError(f"{field_name} must be between 0 and 255")
    return value


def _stable_text(value: str, max_length: int) -> str:
    if not isinstance(value, str):
        raise ValueError("text value must be a string")
    return " ".join(value.replace("\r", " ").replace("\n", " ").split())[:max_length]


def _stable_hex(value: str) -> str:
    from app.device.protocols.base import normalize_hex_display

    return normalize_hex_display(value, max_chars=512)
