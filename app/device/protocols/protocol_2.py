from __future__ import annotations

import math
import struct
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
PROTOCOL_2_REGISTERS_PER_DETECTOR = 4
MAX_PROTOCOL_2_REGISTERS = 124
MAX_REASONABLE_CONCENTRATION = 1_000_000.0

_PROTOCOL_2_UNITS = {
    0: "%RH",
    1: "PPM",
    2: "%LEL",
    3: "%VOL",
    4: "℃",
}
_PROTOCOL_2_GAS_TYPES = {
    0: "湿度",
    1: "PPM",
    2: "可燃气",
    3: "体积浓度",
    4: "温度",
}


class Protocol2Adapter(ProtocolAdapter):
    mode = ProtocolMode.PROTOCOL_2

    def build_poll_requests(self, context: PollBuildContext) -> list[PollRequest]:
        if context.protocol is not self.mode:
            raise ValueError("protocol_2 adapter received a different protocol context")
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
        _validate_register_range(start_register, register_count)
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
        # Protocol 2 follows standard Modbus RTU wire order: CRC low byte first.
        frame = append_crc16(payload, CRCByteOrder.LOW_BYTE_FIRST)
        return PollRequest(
            protocol=self.mode,
            source_type=source,
            port_id=port_id,
            unit_address=unit_address,
            function_code=READ_HOLDING_REGISTERS,
            payload=frame,
            timeout_ms=timeout_ms,
            expected_response_min_bytes=3 + register_count * 2 + 2,
            crc_byte_order=CRCByteOrder.LOW_BYTE_FIRST,
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
        base = _validate_response_base(request, response, CRCByteOrder.LOW_BYTE_FIRST)
        if not base.ok:
            return base
        registers = _registers(response[3:-2])
        if len(registers) % PROTOCOL_2_REGISTERS_PER_DETECTOR != 0:
            return _failure(ValidationErrorCode.LENGTH_MISMATCH, "protocol_2 detector block length mismatch", response)
        for offset in range(0, len(registers), PROTOCOL_2_REGISTERS_PER_DETECTOR):
            status = registers[offset]
            unit_code = registers[offset + 1]
            if status not in _STATUS_MAP:
                return _failure(ValidationErrorCode.UNKNOWN_STATUS, "unknown protocol_2 status", response)
            if unit_code not in _PROTOCOL_2_UNITS:
                return _failure(ValidationErrorCode.INVALID_DATA, "unknown gas unit", response)
            concentration = _float32_from_registers(registers[offset + 2], registers[offset + 3])
            if not _valid_concentration(concentration):
                return _failure(ValidationErrorCode.INVALID_DATA, "invalid concentration", response)
        return base

    def parse_response(self, request: PollRequest, response: bytes) -> list[DeviceReading]:
        validation = self.validate_response(request, response)
        if not validation.ok:
            return []
        registers = _registers(response[3:-2])
        start_detector_number = (_request_start_register(request) // PROTOCOL_2_REGISTERS_PER_DETECTOR) + 1
        readings: list[DeviceReading] = []
        for offset in range(0, len(registers), PROTOCOL_2_REGISTERS_PER_DETECTOR):
            detector_number = start_detector_number + (offset // PROTOCOL_2_REGISTERS_PER_DETECTOR)
            status_code = registers[offset]
            unit_code = registers[offset + 1]
            concentration = _float32_from_registers(registers[offset + 2], registers[offset + 3])
            status, alarm_level = _STATUS_MAP[status_code]
            readings.append(
                DeviceReading(
                    protocol=self.mode,
                    source_type=request.source_type,
                    port_id=request.port_id,
                    controller_id=request.controller_id,
                    detector_id=request.detector_id or detector_number,
                    controller_address=(
                        request.unit_address if request.source_type is DeviceSourceType.CONTROLLER else None
                    ),
                    detector_address=(
                        detector_number if request.source_type is DeviceSourceType.CONTROLLER else request.unit_address
                    ),
                    status=status,
                    concentration=concentration,
                    gas_type=_PROTOCOL_2_GAS_TYPES.get(unit_code),
                    unit=_PROTOCOL_2_UNITS.get(unit_code),
                    alarm_level=alarm_level,
                    raw_status=status_code,
                    raw_value=bytes_to_hex(response),
                    timestamp=datetime.now(timezone.utc),
                )
            )
        return readings

    def _request_for_target(self, context: PollBuildContext, target: PollTarget) -> PollRequest:
        if context.source_type is DeviceSourceType.PROBE:
            unit_address = _required_address(target.detector_address, "detector_address")
            return self.build_read_request(
                source_type=context.source_type,
                port_id=context.port_id,
                unit_address=unit_address,
                start_register=0,
                register_count=PROTOCOL_2_REGISTERS_PER_DETECTOR,
                timeout_ms=context.default_timeout_ms,
                detector_id=target.detector_id,
                label=f"probe {unit_address}",
            )
        controller_address = _required_address(target.controller_address, "controller_address")
        detector_number = _required_address(target.detector_address, "detector_address")
        start_register = (detector_number - 1) * PROTOCOL_2_REGISTERS_PER_DETECTOR
        return self.build_read_request(
            source_type=context.source_type,
            port_id=context.port_id,
            unit_address=controller_address,
            start_register=start_register,
            register_count=PROTOCOL_2_REGISTERS_PER_DETECTOR,
            timeout_ms=context.default_timeout_ms,
            detector_id=target.detector_id,
            controller_id=target.controller_id,
            controller_address=controller_address,
            label=f"controller {controller_address} detector {detector_number}",
        )


# Status 0 combines disconnected/shielded in the provided document. The adapter
# maps it to OFFLINE so monitoring treats it as an abnormal unavailable reading.
_STATUS_MAP: dict[int, tuple[DeviceStatus, int | None]] = {
    0: (DeviceStatus.OFFLINE, None),
    1: (DeviceStatus.FAULT, None),
    2: (DeviceStatus.NORMAL, None),
    3: (DeviceStatus.ALARM_LOW, 1),
    4: (DeviceStatus.ALARM_HIGH, 2),
    5: (DeviceStatus.OVER_RANGE, None),
}


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


def _validate_register_range(start_register: int, register_count: int) -> None:
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
    if register_count > MAX_PROTOCOL_2_REGISTERS:
        raise ValueError("protocol_2 register_count is too large")
    if (
        start_register % PROTOCOL_2_REGISTERS_PER_DETECTOR != 0
        or register_count % PROTOCOL_2_REGISTERS_PER_DETECTOR != 0
    ):
        raise ValueError("protocol_2 reads must align to 4-register detector blocks")


def _registers(payload: bytes) -> list[int]:
    return [int.from_bytes(payload[index : index + 2], "big") for index in range(0, len(payload), 2)]


def _float32_from_registers(high_register: int, low_register: int) -> float:
    # The protocol stores IEEE754 float32 as high word/high byte first, so big
    # endian byte composition is required before Python float conversion.
    payload = high_register.to_bytes(2, "big") + low_register.to_bytes(2, "big")
    return float(struct.unpack(">f", payload)[0])


def _valid_concentration(value: float) -> bool:
    return math.isfinite(value) and abs(value) <= MAX_REASONABLE_CONCENTRATION


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
