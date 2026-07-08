from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone

from app.device.channels.base import (
    ChannelConfig,
    ChannelErrorCode,
    ChannelType,
    TcpFrameMode,
    TcpParameters,
    TransactResult,
    validate_outbound_payload,
)
from app.device.debug.models import DebugExchange, DebugFrame, DebugParseStatus
from app.device.protocols.base import (
    CRCByteOrder,
    PollBuildContext,
    PollRequest,
    PollTarget,
    ProtocolAdapter,
    ValidationErrorCode,
    ValidationResult,
    bytes_to_hex,
)
from app.device.protocols.crc import append_crc16, compute_modbus_crc16, format_crc16, validation_result_for_crc
from app.services.models import DeviceReading, DeviceSourceType, DeviceStatus, Pagination, ProtocolMode, ServiceResult


class FakeAdapter(ProtocolAdapter):
    mode = ProtocolMode.PROTOCOL_2

    def build_poll_requests(self, context: PollBuildContext) -> list[PollRequest]:
        target = context.targets[0]
        payload = bytes([target.detector_address or 1, 0x03, 0x00, 0x00, 0x00, 0x04])
        return [
            PollRequest(
                protocol=context.protocol,
                source_type=context.source_type,
                port_id=context.port_id,
                unit_address=target.detector_address or 1,
                function_code=0x03,
                payload=append_crc16(payload, CRCByteOrder.LOW_BYTE_FIRST),
                crc_byte_order=CRCByteOrder.LOW_BYTE_FIRST,
                detector_id=target.detector_id,
                controller_id=target.controller_id,
            )
        ]

    def validate_response(self, request: PollRequest, response: bytes) -> ValidationResult:
        return validation_result_for_crc(response, request.crc_byte_order)

    def parse_response(self, request: PollRequest, response: bytes) -> list[DeviceReading]:
        validation = self.validate_response(request, response)
        if not validation.ok:
            return []
        return [
            DeviceReading(
                protocol=request.protocol,
                source_type=request.source_type,
                port_id=request.port_id,
                controller_id=request.controller_id,
                detector_id=request.detector_id or 1,
                controller_address=None,
                detector_address=request.unit_address,
                status=DeviceStatus.NORMAL,
                concentration=12.3,
                gas_type="可燃气",
                unit="%LEL",
                alarm_level=None,
                raw_status=0,
                raw_value=bytes_to_hex(response),
                timestamp=datetime.now(timezone.utc),
            )
        ]


class SharedDeviceContractTests(unittest.TestCase):
    def test_device_reading_statuses_are_explicit_and_frozen(self) -> None:
        expected = {
            "normal",
            "alarm_low",
            "alarm_high",
            "fault",
            "offline",
            "disabled",
            "over_range",
            "warming",
            "invalid",
        }
        self.assertEqual({item.value for item in DeviceStatus}, expected)

        reading = DeviceReading(
            protocol=ProtocolMode.PROTOCOL_1,
            source_type=DeviceSourceType.PROBE,
            port_id=1,
            controller_id=None,
            detector_id=2,
            controller_address=None,
            detector_address=1,
            status=DeviceStatus.INVALID,
            concentration=None,
            gas_type=None,
            unit=None,
            alarm_level=None,
            raw_status="unknown",
            raw_value="bad frame",
        )
        self.assertFalse(hasattr(reading, "register_index"))
        with self.assertRaises(FrozenInstanceError):
            reading.status = DeviceStatus.NORMAL  # type: ignore[misc]
        with self.assertRaises(ValueError):
            DeviceReading(
                protocol=ProtocolMode.PROTOCOL_1,
                source_type=DeviceSourceType.PROBE,
                port_id=1,
                controller_id=None,
                detector_id=2,
                controller_address=None,
                detector_address=1,
                status="mystery",
                concentration=None,
                gas_type=None,
                unit=None,
                alarm_level=None,
                raw_status=None,
                raw_value=None,
            )

    def test_service_result_and_pagination_validate_bounds(self) -> None:
        self.assertEqual(Pagination(page=2, per_page=10).offset, 10)
        with self.assertRaises(ValueError):
            Pagination(page=0)
        result = ServiceResult.ok(data={"count": 1})
        self.assertTrue(result.success)
        with self.assertRaises(ValueError):
            ServiceResult.fail(code=0, message="bad")

    def test_channel_contracts_keep_tcp_as_rtu_over_tcp_and_errors_controlled(self) -> None:
        config = ChannelConfig(
            port_id=1,
            channel_type=ChannelType.TCP,
            tcp=TcpParameters(host="127.0.0.1", port=1502),
        )
        self.assertEqual(config.tcp.frame_mode, TcpFrameMode.RTU_OVER_TCP)
        result = TransactResult.failure(ChannelErrorCode.TIMEOUT, "timeout\nwith stack? ")
        self.assertEqual(result.message, "timeout with stack?")
        self.assertEqual(validate_outbound_payload(b"\x01"), b"\x01")
        with self.assertRaises(ValueError):
            validate_outbound_payload(b"")

    def test_crc_calculation_and_byte_order_are_explicit(self) -> None:
        payload = bytes.fromhex("01 03 00 00 00 04")
        crc = compute_modbus_crc16(payload)
        self.assertEqual(crc, 0x0944)
        self.assertEqual(format_crc16(crc, CRCByteOrder.LOW_BYTE_FIRST), bytes.fromhex("44 09"))
        self.assertEqual(format_crc16(crc, CRCByteOrder.HIGH_BYTE_FIRST), bytes.fromhex("09 44"))
        self.assertEqual(append_crc16(payload, CRCByteOrder.LOW_BYTE_FIRST), bytes.fromhex("01 03 00 00 00 04 44 09"))

    def test_validation_result_contains_raw_hex_and_crc_diagnostics(self) -> None:
        valid_frame = bytes.fromhex("01 03 00 00 00 04 44 09")
        valid = validation_result_for_crc(valid_frame, CRCByteOrder.LOW_BYTE_FIRST)
        self.assertTrue(valid.ok)
        self.assertEqual(valid.crc_expected, "44 09")
        self.assertEqual(valid.raw_hex, "01 03 00 00 00 04 44 09")

        invalid = validation_result_for_crc(bytes.fromhex("01 03 00 00 00 04 09 44"), CRCByteOrder.LOW_BYTE_FIRST)
        self.assertFalse(invalid.ok)
        self.assertEqual(invalid.error_code, ValidationErrorCode.CRC_MISMATCH)
        self.assertEqual(invalid.crc_expected, "44 09")
        self.assertEqual(invalid.crc_actual, "09 44")

    def test_debug_models_limit_hex_and_hide_internal_details(self) -> None:
        frame = DebugFrame.from_bytes(bytes(range(64)), limit=32)
        self.assertTrue(frame.truncated)
        self.assertTrue(frame.raw_hex.endswith("..."))

        validation = ValidationResult.failure(
            error_code=ValidationErrorCode.CRC_MISMATCH,
            message="crc mismatch\nTraceback: internal",
            raw_frame=bytes.fromhex("01 03 00 00 00 04 09 44"),
            crc_expected="44 09",
            crc_actual="09 44",
        )
        exchange = DebugExchange.from_validation(
            request=bytes.fromhex("01 03"),
            response=bytes.fromhex("01 03 00"),
            validation=validation,
        )
        self.assertEqual(exchange.parse.status, DebugParseStatus.VALIDATION_FAILED)
        self.assertNotIn("\n", exchange.error_reason)
        self.assertIn("crc mismatch", exchange.error_reason)

    def test_fake_adapter_reuses_contract_for_validation_and_parse(self) -> None:
        adapter = FakeAdapter()
        context = PollBuildContext(
            protocol=ProtocolMode.PROTOCOL_2,
            source_type=DeviceSourceType.PROBE,
            port_id=1,
            targets=(PollTarget(detector_id=1, detector_address=1),),
        )
        request = adapter.build_poll_requests(context)[0]
        response_payload = bytes.fromhex("01 03 00 00 00 04")
        response = append_crc16(response_payload, request.crc_byte_order)
        validation = adapter.validate_response(request, response)
        readings = adapter.parse_response(request, response)
        debug = DebugExchange.from_validation(
            request=request.payload,
            response=response,
            validation=validation,
            readings=readings,
        )

        self.assertTrue(validation.ok)
        self.assertEqual(readings[0].status, DeviceStatus.NORMAL)
        self.assertEqual(debug.parse.status, DebugParseStatus.SUCCESS)


if __name__ == "__main__":
    unittest.main()
