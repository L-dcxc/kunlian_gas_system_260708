from __future__ import annotations

import unittest

from app.device.channels.base import ChannelErrorCode, TransactResult
from app.device.debug.debug_service import DebugReadCommand, DeviceDebugService
from app.device.protocols.base import CRCByteOrder, ValidationErrorCode, bytes_to_hex
from app.device.protocols.crc import append_crc16, compute_modbus_crc16, format_crc16
from app.device.protocols.factory import ProtocolAdapterFactory, create_protocol_adapter, load_protocol_adapter
from app.device.protocols.protocol_1 import Protocol1Adapter
from app.device.protocols.protocol_2 import Protocol2Adapter
from app.services.models import DeviceSourceType, DeviceStatus, ProtocolMode


class FakeChannel:
    def __init__(self, result: TransactResult) -> None:
        self.result = result
        self.sent = b""
        self.timeout_ms: int | None = None

    def transact(self, payload: bytes, timeout_ms: int | None = None) -> TransactResult:
        self.sent = payload
        self.timeout_ms = timeout_ms
        return self.result


def _frame(payload_hex: str, order: CRCByteOrder) -> bytes:
    return append_crc16(bytes.fromhex(payload_hex), order)


def _p1_probe_response(address: int, registers: list[int]) -> bytes:
    payload = bytes((address, 0x03, len(registers) * 2)) + b"".join(item.to_bytes(2, "big") for item in registers)
    return append_crc16(payload, CRCByteOrder.HIGH_BYTE_FIRST)


def _p1_controller_response(address: int, registers: list[int]) -> bytes:
    return _p1_probe_response(address, registers)


def _p2_response(address: int, registers: list[int]) -> bytes:
    payload = bytes((address, 0x03, len(registers) * 2)) + b"".join(item.to_bytes(2, "big") for item in registers)
    return append_crc16(payload, CRCByteOrder.LOW_BYTE_FIRST)


class ProtocolAdaptationDebugTests(unittest.TestCase):
    def test_protocol_1_crc_high_byte_first_request_and_probe_alarm_parse(self) -> None:
        adapter = Protocol1Adapter()
        request = adapter.build_read_request(
            source_type=DeviceSourceType.PROBE,
            port_id=1,
            unit_address=5,
            start_register=0,
            register_count=15,
            detector_id=5,
        )
        payload = bytes.fromhex("05 03 00 00 00 0F")
        crc = compute_modbus_crc16(payload)
        self.assertEqual(request.payload, payload + format_crc16(crc, CRCByteOrder.HIGH_BYTE_FIRST))
        self.assertNotEqual(request.payload[-2:], format_crc16(crc, CRCByteOrder.LOW_BYTE_FIRST))

        registers = [0x003C, 0x1200, 0x0001, 0x0014] + [0] * 11
        response = _p1_probe_response(5, registers)
        validation = adapter.validate_response(request, response)
        readings = adapter.parse_response(request, response)

        self.assertTrue(validation.ok)
        self.assertEqual(readings[0].status, DeviceStatus.ALARM_HIGH)
        self.assertEqual(readings[0].alarm_level, 2)
        self.assertEqual(readings[0].concentration, 60.0)
        self.assertEqual(readings[0].unit, "%LEL")

    def test_protocol_1_probe_fault_invalid_and_bad_frame_do_not_parse(self) -> None:
        adapter = Protocol1Adapter()
        request = adapter.build_read_request(
            source_type="probe",
            port_id=1,
            unit_address=1,
            start_register=0,
            register_count=15,
        )
        fault = _p1_probe_response(1, [0x0001, 0x2000, 0x0001, 0x0014] + [0] * 11)
        invalid = _p1_probe_response(1, [0x0001, 0x8000, 0x0001, 0x0014] + [0] * 11)
        bad_crc = fault[:-1] + bytes([fault[-1] ^ 0xFF])
        short = _p1_probe_response(1, [0x0001])

        self.assertEqual(adapter.parse_response(request, fault)[0].status, DeviceStatus.FAULT)
        self.assertEqual(adapter.parse_response(request, invalid)[0].status, DeviceStatus.INVALID)
        self.assertEqual(
            adapter.validate_response(bad_request := request, bad_crc).error_code,
            ValidationErrorCode.CRC_MISMATCH,
        )
        self.assertEqual(adapter.parse_response(bad_request, bad_crc), [])
        self.assertEqual(adapter.validate_response(request, short).error_code, ValidationErrorCode.LENGTH_MISMATCH)
        self.assertEqual(adapter.parse_response(request, short), [])

    def test_protocol_1_controller_boundaries_status_and_address_function_errors(self) -> None:
        adapter = Protocol1Adapter()
        with self.assertRaises(ValueError):
            adapter.build_read_request(
                source_type="controller",
                port_id=1,
                unit_address=1,
                start_register=0,
                register_count=21,
            )
        with self.assertRaises(ValueError):
            adapter.build_read_request(
                source_type="controller",
                port_id=1,
                unit_address=1,
                start_register=0,
                register_count=3,
            )

        request = adapter.build_read_request(
            source_type="controller",
            port_id=1,
            unit_address=1,
            start_register=0,
            register_count=2,
            detector_id=7,
            controller_id=3,
        )
        normal = _p1_controller_response(1, [0, 12])
        alarm = _p1_controller_response(1, [2, 30])
        unknown = _p1_controller_response(1, [9, 30])
        wrong_address = _p1_controller_response(2, [0, 12])
        wrong_function = _frame("01 04 04 00 00 00 0C", CRCByteOrder.HIGH_BYTE_FIRST)

        self.assertEqual(adapter.parse_response(request, normal)[0].status, DeviceStatus.NORMAL)
        self.assertEqual(adapter.parse_response(request, alarm)[0].status, DeviceStatus.ALARM_LOW)
        self.assertEqual(adapter.validate_response(request, unknown).error_code, ValidationErrorCode.UNKNOWN_STATUS)
        self.assertEqual(adapter.parse_response(request, unknown), [])
        self.assertEqual(
            adapter.validate_response(request, wrong_address).error_code,
            ValidationErrorCode.ADDRESS_MISMATCH,
        )
        self.assertEqual(
            adapter.validate_response(request, wrong_function).error_code,
            ValidationErrorCode.FUNCTION_MISMATCH,
        )

    def test_protocol_2_crc_low_byte_first_request_and_status_mappings(self) -> None:
        adapter = Protocol2Adapter()
        request = adapter.build_read_request(
            source_type="probe",
            port_id=1,
            unit_address=1,
            start_register=0,
            register_count=4,
        )
        self.assertEqual(request.payload, bytes.fromhex("01 03 00 00 00 04 44 09"))

        cases = {
            0: DeviceStatus.OFFLINE,
            1: DeviceStatus.FAULT,
            2: DeviceStatus.NORMAL,
            3: DeviceStatus.ALARM_LOW,
            4: DeviceStatus.ALARM_HIGH,
            5: DeviceStatus.OVER_RANGE,
        }
        for raw_status, expected_status in cases.items():
            with self.subTest(raw_status=raw_status):
                response = _p2_response(1, [raw_status, 2, 0x4120, 0x0000])
                reading = adapter.parse_response(request, response)[0]
                self.assertEqual(reading.status, expected_status)
                self.assertEqual(reading.concentration, 10.0)
                self.assertEqual(reading.unit, "%LEL")

    def test_protocol_2_rejects_crc_length_address_function_unknown_status_and_bad_float(self) -> None:
        adapter = Protocol2Adapter()
        request = adapter.build_read_request(
            source_type="probe",
            port_id=1,
            unit_address=1,
            start_register=0,
            register_count=4,
        )
        valid = _p2_response(1, [2, 2, 0x4120, 0x0000])
        bad_crc = valid[:-1] + bytes([valid[-1] ^ 0xFF])
        short = _p2_response(1, [2, 2])
        wrong_address = _p2_response(2, [2, 2, 0x4120, 0x0000])
        wrong_function = _frame("01 04 08 00 02 00 02 41 20 00 00", CRCByteOrder.LOW_BYTE_FIRST)
        unknown = _p2_response(1, [9, 2, 0x4120, 0x0000])
        nan_value = _p2_response(1, [2, 2, 0x7FC0, 0x0000])
        inf_value = _p2_response(1, [2, 2, 0x7F80, 0x0000])

        self.assertEqual(adapter.validate_response(request, bad_crc).error_code, ValidationErrorCode.CRC_MISMATCH)
        self.assertEqual(adapter.validate_response(request, short).error_code, ValidationErrorCode.LENGTH_MISMATCH)
        self.assertEqual(
            adapter.validate_response(request, wrong_address).error_code,
            ValidationErrorCode.ADDRESS_MISMATCH,
        )
        self.assertEqual(
            adapter.validate_response(request, wrong_function).error_code,
            ValidationErrorCode.FUNCTION_MISMATCH,
        )
        self.assertEqual(adapter.validate_response(request, unknown).error_code, ValidationErrorCode.UNKNOWN_STATUS)
        self.assertEqual(adapter.validate_response(request, nan_value).error_code, ValidationErrorCode.INVALID_DATA)
        self.assertEqual(adapter.validate_response(request, inf_value).error_code, ValidationErrorCode.INVALID_DATA)
        for frame in (bad_crc, short, wrong_address, wrong_function, unknown, nan_value, inf_value):
            self.assertEqual(adapter.parse_response(request, frame), [])
        with self.assertRaises(ValueError):
            adapter.build_read_request(
                source_type="probe",
                port_id=1,
                unit_address=1,
                start_register=0,
                register_count=2,
            )

    def test_factory_loads_single_mode_and_rejects_unknown_or_mixed_mode(self) -> None:
        self.assertIsInstance(create_protocol_adapter("protocol_1"), Protocol1Adapter)
        self.assertIsInstance(load_protocol_adapter(mode="protocol_2"), Protocol2Adapter)
        with self.assertRaises(ValueError):
            create_protocol_adapter("protocol_x")

        factory = ProtocolAdapterFactory(mode_provider=lambda: "protocol_1")
        first = factory.get_adapter()
        self.assertIsInstance(first, Protocol1Adapter)
        with self.assertRaises(ValueError):
            factory.get_adapter("protocol_2")

    def test_debug_service_builds_sends_validates_and_keeps_read_only_boundary(self) -> None:
        service = DeviceDebugService()
        command = DebugReadCommand(
            mode="protocol_2",
            source_type="probe",
            port_id=1,
            unit_address=1,
            start_register=0,
            register_count=4,
            detector_id=1,
            timeout_ms=1500,
        )
        built = service.build_read_request(command)
        self.assertTrue(built.success)
        self.assertEqual(built.data.request_hex, "01 03 00 00 00 04 44 09")

        response = _p2_response(1, [4, 2, 0x422C, 0x0000])
        channel = FakeChannel(TransactResult.success(response, elapsed_ms=3))
        sent = service.send_read_request(command, channel)
        self.assertTrue(sent.success)
        self.assertEqual(bytes_to_hex(channel.sent), built.data.request_hex)
        self.assertTrue(sent.data.crc_ok)
        self.assertEqual(sent.data.readings[0].status, DeviceStatus.ALARM_HIGH)

        bad = service.validate_response(command, response[:-1] + bytes([response[-1] ^ 0xFF]))
        self.assertTrue(bad.success)
        self.assertFalse(bad.data.crc_ok)
        self.assertEqual(bad.data.error_code, ValidationErrorCode.CRC_MISMATCH.value)
        self.assertEqual(bad.data.readings, ())

        write_command = DebugReadCommand(
            mode="protocol_1",
            source_type="probe",
            port_id=1,
            unit_address=1,
            start_register=0,
            register_count=1,
            function_code=0x06,
        )
        rejected = service.build_read_request(write_command)
        self.assertFalse(rejected.success)
        self.assertEqual(rejected.code, 400)
        self.assertIn("仅允许读 03", rejected.message)

    def test_debug_service_channel_failure_returns_diagnostic_without_readings(self) -> None:
        service = DeviceDebugService()
        command = DebugReadCommand(
            mode="protocol_2",
            source_type="probe",
            port_id=1,
            unit_address=1,
            start_register=0,
            register_count=4,
        )
        channel = FakeChannel(TransactResult.failure(ChannelErrorCode.TIMEOUT, "通讯超时\nTraceback: hidden", 99))
        result = service.send_read_request(command, channel)
        self.assertTrue(result.success)
        self.assertIsNone(result.data.crc_ok)
        self.assertEqual(result.data.readings, ())
        self.assertNotIn("\n", result.data.validation_message)
        self.assertEqual(result.data.error_code, ChannelErrorCode.TIMEOUT.value)


if __name__ == "__main__":
    unittest.main()
