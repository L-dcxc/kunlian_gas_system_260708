from __future__ import annotations

from datetime import datetime, timezone

from app.device.protocols.base import (
    CRCByteOrder,
    MAX_FRAME_BYTES,
    PollBuildContext,
    PollRequest,
    PollTarget,
    ProtocolAdapter,
    ValidationErrorCode,
    ValidationResult,
    bytes_to_hex,
)
from app.device.protocols.crc import append_crc16, check_frame_crc
from app.services.models import DeviceReading, DeviceSourceType, DeviceStatus, ProtocolMode

READ_HOLDING_REGISTERS = 0x03
PROTOCOL_1_PROBE_REGISTER_COUNT = 15
PROTOCOL_1_CONTROLLER_MAX_REGISTERS = 20

_PROTOCOL_1_GAS_TYPES = {
    0: "可燃气体",
    1: "一氧化碳CO",
    2: "硫化氢H2S",
    3: "氨气NH3",
    4: "氯气Cl2",
    5: "氧气O2",
    6: "甲烷CH4",
    7: "氢气H2",
    46: "二氧化硫 SO2",
}
_PROTOCOL_1_UNITS = {
    0: "无",
    1: "%LEL",
    2: "ppm",
    3: "%Vol",
    4: "℃",
    5: "%RH",
    6: "%",
}


class Protocol1Adapter(ProtocolAdapter):
    mode = ProtocolMode.PROTOCOL_1

    def build_poll_requests(self, context: PollBuildContext) -> list[PollRequest]:
        if context.protocol is not self.mode:
            raise ValueError("protocol_1 adapter received a different protocol context")
        return [self._request_for_target(context, target) for target in context.targets]

    def build_read_request(
        self,
        *,
        source_type: DeviceSourceType | str,
        port_id: int,
        unit_address: int,
        start_register: int,
        register_count: int,
        timeout_ms: int = 1000,
        detector_id: int | None = None,
        controller_id: int | None = None,
        controller_address: int | None = None,
        label: str = "",
    ) -> PollRequest:
        source = DeviceSourceType(source_type)
        _validate_address(unit_address, "unit_address")
        _validate_register_range(start_register, register_count, source)
        payload = bytes(
            (
                unit_address,
                READ_HOLDING_REGISTERS,
                (start_register >> 8) & 0xFF,
                start_register & 0xFF,
                (register_count >> 8) & 0xFF,
                register_count & 0xFF,
            )
        )
        # Protocol 1 documents explicitly put CRC high byte before low byte;
        # this differs from standard Modbus RTU and remains adapter-local.
        frame = append_crc16(payload, CRCByteOrder.HIGH_BYTE_FIRST)
        return PollRequest(
            protocol=self.mode,
            source_type=source,
            port_id=port_id,
            unit_address=unit_address,
            function_code=READ_HOLDING_REGISTERS,
            payload=frame,
            timeout_ms=timeout_ms,
            expected_response_min_bytes=3 + register_count * 2 + 2,
            crc_byte_order=CRCByteOrder.HIGH_BYTE_FIRST,
            detector_id=detector_id,
            controller_id=controller_id,
            label=label,
            metadata={
                "start_register": str(start_register),
                "register_count": str(register_count),
                "controller_address": "none" if controller_address is None else str(controller_address),
            },
        )

    def validate_response(self, request: PollRequest, response: bytes) -> ValidationResult:
        base = _validate_response_base(request, response, CRCByteOrder.HIGH_BYTE_FIRST)
        if not base.ok:
            return base
        register_count = _request_register_count(request)
        registers = _registers(response[3:-2])
        if request.source_type is DeviceSourceType.CONTROLLER:
            if register_count > PROTOCOL_1_CONTROLLER_MAX_REGISTERS or register_count % 2 != 0:
                return _failure(ValidationErrorCode.INVALID_DATA, "controller register count is invalid", response)
            for status_register in registers[0::2]:
                if status_register not in {0, 1, 2}:
                    return _failure(ValidationErrorCode.UNKNOWN_STATUS, "unknown controller detector status", response)
        return base

    def parse_response(self, request: PollRequest, response: bytes) -> list[DeviceReading]:
        validation = self.validate_response(request, response)
        if not validation.ok:
            return []
        registers = _registers(response[3:-2])
        if request.source_type is DeviceSourceType.PROBE:
            return [self._parse_probe(request, registers, response)]
        return self._parse_controller(request, registers, response)

    def _request_for_target(self, context: PollBuildContext, target: PollTarget) -> PollRequest:
        if context.source_type is DeviceSourceType.PROBE:
            unit_address = _required_address(target.detector_address, "detector_address")
            return self.build_read_request(
                source_type=context.source_type,
                port_id=context.port_id,
                unit_address=unit_address,
                start_register=0,
                register_count=PROTOCOL_1_PROBE_REGISTER_COUNT,
                timeout_ms=context.default_timeout_ms,
                detector_id=target.detector_id,
                label=f"probe {unit_address}",
            )
        controller_address = _required_address(target.controller_address, "controller_address")
        detector_number = _required_address(target.detector_address, "detector_address")
        start_register = (detector_number - 1) * 2
        return self.build_read_request(
            source_type=context.source_type,
            port_id=context.port_id,
            unit_address=controller_address,
            start_register=start_register,
            register_count=2,
            timeout_ms=context.default_timeout_ms,
            detector_id=target.detector_id,
            controller_id=target.controller_id,
            controller_address=controller_address,
            label=f"controller {controller_address} detector {detector_number}",
        )

    def _parse_probe(self, request: PollRequest, registers: list[int], frame: bytes) -> DeviceReading:
        value = _signed_magnitude(registers[0])
        state1 = (registers[1] >> 8) & 0xFF if len(registers) > 1 else 0
        gas_code = (registers[2] >> 8) & 0xFF if len(registers) > 2 else None
        unit_code = registers[2] & 0xFF if len(registers) > 2 else None
        decimal_places = (registers[3] >> 8) & 0xFF if len(registers) > 3 else 0
        status, alarm_level = _probe_status(state1)
        if decimal_places > 6 or status is DeviceStatus.INVALID:
            concentration = None
            alarm_level = None
            status = DeviceStatus.INVALID
        else:
            concentration = value / (10**decimal_places)
        return DeviceReading(
            protocol=self.mode,
            source_type=DeviceSourceType.PROBE,
            port_id=request.port_id,
            controller_id=None,
            detector_id=request.detector_id or request.unit_address,
            controller_address=None,
            detector_address=request.unit_address,
            status=status,
            concentration=concentration,
            gas_type=_PROTOCOL_1_GAS_TYPES.get(gas_code) if gas_code is not None else None,
            unit=_PROTOCOL_1_UNITS.get(unit_code) if unit_code is not None else None,
            alarm_level=alarm_level,
            raw_status=state1,
            raw_value=bytes_to_hex(frame),
            timestamp=datetime.now(timezone.utc),
        )

    def _parse_controller(self, request: PollRequest, registers: list[int], frame: bytes) -> list[DeviceReading]:
        start_register = _request_start_register(request)
        start_detector_number = (start_register // 2) + 1
        readings: list[DeviceReading] = []
        for index in range(0, len(registers), 2):
            detector_number = start_detector_number + (index // 2)
            status_code = registers[index]
            concentration = float(registers[index + 1])
            status, alarm_level = _controller_status(status_code)
            readings.append(
                DeviceReading(
                    protocol=self.mode,
                    source_type=DeviceSourceType.CONTROLLER,
                    port_id=request.port_id,
                    controller_id=request.controller_id,
                    detector_id=request.detector_id or detector_number,
                    controller_address=request.unit_address,
                    detector_address=detector_number,
                    status=status,
                    concentration=concentration,
                    gas_type=None,
                    unit=None,
                    alarm_level=alarm_level,
                    raw_status=status_code,
                    raw_value=bytes_to_hex(frame),
                    timestamp=datetime.now(timezone.utc),
                )
            )
        return readings


def _validate_response_base(request: PollRequest, response: bytes, crc_order: CRCByteOrder) -> ValidationResult:
    if not isinstance(response, bytes):
        return ValidationResult.failure(ValidationErrorCode.INVALID_DATA, "response must be bytes", raw_hex="")
    if not response:
        return ValidationResult.failure(ValidationErrorCode.EMPTY_RESPONSE, "empty response", raw_frame=response)
    if len(response) > MAX_FRAME_BYTES:
        return ValidationResult.failure(ValidationErrorCode.FRAME_TOO_LARGE, "frame too large", raw_frame=response)
    if len(response) < 5:
        return _failure(ValidationErrorCode.LENGTH_MISMATCH, "response frame is too short", response)
    if response[0] != request.unit_address:
        return _failure(ValidationErrorCode.ADDRESS_MISMATCH, "address mismatch", response)
    if response[1] != request.function_code:
        return _failure(ValidationErrorCode.FUNCTION_MISMATCH, "function code mismatch", response)
    crc = check_frame_crc(response, crc_order)
    if not crc.ok:
        return ValidationResult.failure(
            ValidationErrorCode.CRC_MISMATCH,
            "crc mismatch",
            raw_frame=response,
            crc_expected=crc.expected_hex,
            crc_actual=crc.actual_hex,
        )
    byte_count = response[2]
    expected_byte_count = _request_register_count(request) * 2
    if len(response) != 3 + byte_count + 2 or byte_count != expected_byte_count or byte_count % 2 != 0:
        return _failure(ValidationErrorCode.LENGTH_MISMATCH, "response byte count mismatch", response)
    return ValidationResult.success(
        raw_frame=response,
        crc_expected=crc.expected_hex,
        crc_actual=crc.actual_hex,
    )


def _validate_register_range(start_register: int, register_count: int, source_type: DeviceSourceType) -> None:
    if (
        isinstance(start_register, bool)
        or not isinstance(start_register, int)
        or start_register < 0
        or start_register > 65535
    ):
        raise ValueError("start_register must be between 0 and 65535")
    if isinstance(register_count, bool) or not isinstance(register_count, int) or register_count < 1:
        raise ValueError("register_count must be positive")
    if start_register + register_count > 65536:
        raise ValueError("register range is out of bounds")
    if source_type is DeviceSourceType.CONTROLLER:
        if register_count > PROTOCOL_1_CONTROLLER_MAX_REGISTERS or register_count % 2 != 0:
            raise ValueError("protocol_1 controller reads require an even count no greater than 20")
        if start_register % 2 != 0:
            raise ValueError("protocol_1 controller reads must start at an even detector register boundary")


def _registers(payload: bytes) -> list[int]:
    return [int.from_bytes(payload[index : index + 2], "big") for index in range(0, len(payload), 2)]


def _probe_status(state1: int) -> tuple[DeviceStatus, int | None]:
    if state1 & 0x80:
        return DeviceStatus.INVALID, None
    if state1 & 0x40:
        return DeviceStatus.WARMING, None
    if state1 & 0x20:
        return DeviceStatus.FAULT, None
    alarm_bits = state1 & 0x0F
    if alarm_bits:
        level = max(index + 1 for index in range(4) if alarm_bits & (1 << index))
        return (DeviceStatus.ALARM_HIGH if state1 & 0x10 else DeviceStatus.ALARM_LOW), level
    return DeviceStatus.NORMAL, None


def _controller_status(value: int) -> tuple[DeviceStatus, int | None]:
    if value == 0:
        return DeviceStatus.NORMAL, None
    if value == 1:
        return DeviceStatus.FAULT, None
    return DeviceStatus.ALARM_LOW, 1


def _signed_magnitude(value: int) -> int:
    magnitude = value & 0x7FFF
    return -magnitude if value & 0x8000 else magnitude


def _request_register_count(request: PollRequest) -> int:
    return _metadata_int(request, "register_count")


def _request_start_register(request: PollRequest) -> int:
    return _metadata_int(request, "start_register")


def _metadata_int(request: PollRequest, key: str) -> int:
    try:
        value = int(request.metadata[key])
    except (KeyError, ValueError) as exc:
        raise ValueError(f"request metadata missing {key}") from exc
    return value


def _required_address(value: int | None, field_name: str) -> int:
    if value is None:
        raise ValueError(f"{field_name} is required")
    _validate_address(value, field_name)
    if value == 0:
        raise ValueError(f"{field_name} must not be broadcast address")
    return value


def _validate_address(value: int, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > 255:
        raise ValueError(f"{field_name} must be between 0 and 255")


def _failure(code: ValidationErrorCode, message: str, frame: bytes) -> ValidationResult:
    return ValidationResult.failure(code, message, raw_frame=frame)
