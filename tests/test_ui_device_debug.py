from __future__ import annotations

# ruff: noqa: E402

import os
import unittest
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from app.device.debug.debug_service import DebugFrameResult, DebugReadCommand, DeviceDebugService
from app.device.debug.models import DebugCrcResult, DebugParseResult, DebugParseStatus
from app.device.protocols.base import CRCByteOrder
from app.device.protocols.crc import append_crc16
from app.services.auth_service import Session
from app.services.errors import ErrorCode
from app.services.models import ServiceResult
from app.services.permissions import Role
from app.ui.common.hex_viewer import HEX_VIEWER_MAX_CHARS
from app.ui.settings.device_debug_page import DeviceDebugPage
from app.ui.theme import AppTheme


class FakeConfigService:
    def list_ports(self) -> tuple[dict[str, object], ...]:
        return (
            {
                "id": 1,
                "name": "COM1",
                "channel_type": "serial",
                "serial_port_name": "COM1",
                "is_enabled": True,
            },
        )


class FakeDebugFacade:
    def __init__(self, send_result: ServiceResult[DebugFrameResult] | None = None) -> None:
        self.service = DeviceDebugService()
        self.send_result = send_result
        self.sent_commands: list[DebugReadCommand] = []

    def build_read_request(self, command: DebugReadCommand, session: object | None = None) -> ServiceResult[DebugFrameResult]:
        return self.service.build_read_request(command)

    def send_debug_read(self, session: object | None, command: DebugReadCommand) -> ServiceResult[DebugFrameResult]:
        self.sent_commands.append(command)
        if self.send_result is not None:
            return self.send_result
        return self.service.validate_response(command, _p2_response(1, [2, 2, 0x4120, 0x0000]))


class UiDeviceDebugTests(unittest.TestCase):
    app: QApplication

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        AppTheme().apply_to(cls.app)

    def admin_session(self) -> Session:
        return Session("admin-session", 1, "admin", Role.ADMIN.value, ("*",), 1, "2026-01-01T00:00:00+00:00")

    def operator_session(self) -> Session:
        return Session(
            "operator-session",
            2,
            "operator",
            Role.OPERATOR.value,
            ("device_debug.view",),
            1,
            "2026-01-01T00:00:00+00:00",
        )

    def test_request_validation_and_read_only_request_generation(self) -> None:
        page = DeviceDebugPage(FakeDebugFacade(), FakeConfigService(), session=self.admin_session())
        page.protocol_combo.setCurrentIndex(page.protocol_combo.findData("protocol_2"))
        page.register_count_spin.setValue(3)

        page.generate_request()

        self.assertIn("协议 2", page.validation_hint.text())
        self.assertEqual(page.register_count_spin.property("validation"), "error")
        page.register_count_spin.setValue(4)
        page.generate_request()
        self.assertEqual(page.function_combo.currentData(), 3)
        self.assertIn("01 03", page.send_hex.text())
        self.assertFalse(page.send_button.text() == "发送中...")

    def test_send_state_and_success_result_display(self) -> None:
        facade = FakeDebugFacade()
        page = DeviceDebugPage(facade, FakeConfigService(), session=self.admin_session())
        page.protocol_combo.setCurrentIndex(page.protocol_combo.findData("protocol_2"))
        page.set_sending(True)
        self.assertFalse(page.send_button.isEnabled())
        self.assertEqual(page.send_button.text(), "发送中...")
        page.set_sending(False)

        page.send_read()

        self.assertTrue(facade.sent_commands)
        self.assertEqual(page.result_badge.property("debugResult"), "ok")
        self.assertIn("解析成功", page.result_badge.text())
        self.assertEqual(page.concentration_label.text(), "10")
        self.assertEqual(page.unit_label.text(), "%LEL")
        self.assertTrue(page.send_button.isEnabled())
        self.assertGreater(len(page.frame_log.rows()), 0)

    def test_crc_and_error_result_are_diagnostic_without_valid_reading(self) -> None:
        service = DeviceDebugService()
        command = DebugReadCommand(
            mode="protocol_2",
            source_type="probe",
            port_id=1,
            unit_address=1,
            start_register=0,
            register_count=4,
        )
        bad_response = bytearray(_p2_response(1, [2, 2, 0x4120, 0x0000]))
        bad_response[-1] ^= 0xFF
        bad_result = service.validate_response(command, bytes(bad_response))
        self.assertTrue(bad_result.success)
        facade = FakeDebugFacade(bad_result)
        page = DeviceDebugPage(facade, FakeConfigService(), session=self.admin_session())

        page.send_read()

        self.assertEqual(page.result_badge.property("debugResult"), "error")
        self.assertIn("CRC", page.result_badge.text())
        self.assertIn("失败", page.crc_label.text())
        self.assertEqual(page.concentration_label.text(), "-")
        self.assertIn("crc mismatch", page.error_reason_label.text())

    def test_hex_truncation_copy_viewer_and_frame_log_clear(self) -> None:
        long_hex = "A" * (HEX_VIEWER_MAX_CHARS + 20)
        exchange = SimpleNamespace(
            request_hex=long_hex,
            response_hex=long_hex,
            crc=DebugCrcResult(ok=None),
            parse=DebugParseResult(status=DebugParseStatus.CHANNEL_ERROR, message="连接失败"),
            error_reason="连接失败",
        )
        result = ServiceResult.ok(
            SimpleNamespace(
                request_hex=long_hex,
                response_hex=long_hex,
                crc_ok=None,
                validation_message="连接失败",
                readings=(),
                error_code="channel_error",
                exchange=exchange,
            )
        )
        page = DeviceDebugPage(FakeDebugFacade(result), FakeConfigService(), session=self.admin_session())

        page.send_read()

        self.assertTrue(page.send_hex.is_truncated())
        self.assertTrue(page.recv_hex.is_truncated())
        self.assertEqual(page.send_hex.viewer.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByMouse, Qt.TextInteractionFlag.TextSelectableByMouse)
        self.assertIn("已截断", page.send_hex.truncation_label.text())
        self.assertGreater(len(page.frame_log.rows()), 0)
        page.frame_log.clear()
        self.assertEqual(len(page.frame_log.rows()), 0)

    def test_without_permission_buttons_disabled_and_permission_message_controlled(self) -> None:
        page = DeviceDebugPage(FakeDebugFacade(), FakeConfigService(), session=self.operator_session(), can_debug=False)

        self.assertFalse(page.send_button.isEnabled())
        self.assertFalse(page.generate_button.isEnabled())
        self.assertFalse(page.permission_hint.isHidden())
        page.send_read()
        self.assertIn("无权限", page.error_banner.label.text())
        self.assertNotIn("permission_code", page.error_banner.label.text().lower())

    def test_service_failure_is_controlled_and_does_not_leak_stack_or_path(self) -> None:
        failure = ServiceResult.fail(
            code=int(ErrorCode.SERVICE_UNAVAILABLE),
            message='连接失败 Traceback File "C:\\secret\\driver.py", line 1 password=abc',
        )
        page = DeviceDebugPage(FakeDebugFacade(failure), FakeConfigService(), session=self.admin_session())

        page.send_read()

        self.assertIn("操作失败", page.error_banner.label.text())
        self.assertNotIn("C:\\secret", page.error_banner.label.text())
        self.assertNotIn("abc", page.error_banner.label.text())


def _p2_response(address: int, registers: list[int]) -> bytes:
    payload = bytes((address, 0x03, len(registers) * 2)) + b"".join(item.to_bytes(2, "big") for item in registers)
    return append_crc16(payload, CRCByteOrder.LOW_BYTE_FIRST)


if __name__ == "__main__":
    unittest.main()
