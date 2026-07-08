from __future__ import annotations

from dataclasses import dataclass

from app.device.protocols.base import CRCByteOrder, ValidationErrorCode, ValidationResult, bytes_to_hex

CRC16_INITIAL = 0xFFFF
CRC16_POLYNOMIAL = 0xA001
CRC_FIELD_BYTES = 2


@dataclass(frozen=True, slots=True)
class CRCCheckResult:
    ok: bool
    expected: bytes
    actual: bytes
    expected_hex: str
    actual_hex: str


def compute_modbus_crc16(payload: bytes) -> int:
    if not isinstance(payload, bytes):
        raise ValueError("payload must be bytes")
    if not payload:
        raise ValueError("payload is required")
    crc = CRC16_INITIAL
    for byte in payload:
        crc ^= byte
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ CRC16_POLYNOMIAL
            else:
                crc >>= 1
            crc &= 0xFFFF
    return crc


def format_crc16(crc: int, byte_order: CRCByteOrder | str) -> bytes:
    if isinstance(crc, bool) or not isinstance(crc, int) or crc < 0 or crc > 0xFFFF:
        raise ValueError("crc must be between 0 and 0xFFFF")
    order = CRCByteOrder(byte_order)
    high = (crc >> 8) & 0xFF
    low = crc & 0xFF
    # Protocol 1 documents specify CRCH/CRCL, while Protocol 2 follows standard
    # Modbus RTU wire order. Keep byte order explicit instead of normalizing it.
    if order is CRCByteOrder.HIGH_BYTE_FIRST:
        return bytes((high, low))
    return bytes((low, high))


def append_crc16(payload: bytes, byte_order: CRCByteOrder | str) -> bytes:
    return payload + format_crc16(compute_modbus_crc16(payload), byte_order)


def split_frame_crc(frame: bytes) -> tuple[bytes, bytes]:
    if not isinstance(frame, bytes):
        raise ValueError("frame must be bytes")
    if len(frame) <= CRC_FIELD_BYTES:
        raise ValueError("frame must include payload and crc")
    return frame[:-CRC_FIELD_BYTES], frame[-CRC_FIELD_BYTES:]


def check_frame_crc(frame: bytes, byte_order: CRCByteOrder | str) -> CRCCheckResult:
    payload, actual = split_frame_crc(frame)
    expected = format_crc16(compute_modbus_crc16(payload), byte_order)
    return CRCCheckResult(
        ok=expected == actual,
        expected=expected,
        actual=actual,
        expected_hex=bytes_to_hex(expected),
        actual_hex=bytes_to_hex(actual),
    )


def validation_result_for_crc(frame: bytes, byte_order: CRCByteOrder | str) -> ValidationResult:
    """Return diagnostics only; callers must avoid parsing when ok is false."""

    result = check_frame_crc(frame, byte_order)
    if result.ok:
        return ValidationResult.success(
            raw_frame=frame,
            crc_expected=result.expected_hex,
            crc_actual=result.actual_hex,
        )
    return ValidationResult.failure(
        error_code=ValidationErrorCode.CRC_MISMATCH,
        message="crc mismatch",
        raw_frame=frame,
        crc_expected=result.expected_hex,
        crc_actual=result.actual_hex,
    )
