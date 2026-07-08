from __future__ import annotations

# ruff: noqa: E402

import os
import unittest
from dataclasses import dataclass
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog

from app.services.auth_service import Session
from app.services.errors import ErrorCode
from app.services.models import ServiceResult
from app.services.permissions import Role
from app.ui.settings.controllers_page import ControllersPage
from app.ui.settings.detectors_page import DetectorsPage
from app.ui.settings.device_config_page import DeviceConfigPage
from app.ui.settings.gas_types_page import GasTypesPage
from app.ui.settings.import_result_dialog import ImportResultDialog
from app.ui.settings.ports_page import PortsPage
from app.ui.settings.protocol_settings_page import PROTOCOL_RESTART_TEXT, ProtocolSettingsPage
from app.ui.theme import AppTheme


@dataclass(frozen=True)
class FakeImportError:
    row_number: int
    field: str
    message: str


@dataclass(frozen=True)
class FakeImportData:
    imported_count: int
    errors: tuple[FakeImportError, ...]


class FakeResultDialog(QDialog):
    def __init__(self, **kwargs: object) -> None:
        super().__init__()
        self.kwargs = kwargs

    def exec(self) -> int:  # noqa: A003
        return QDialog.DialogCode.Accepted


class FakeDeviceConfigService:
    def __init__(self) -> None:
        self.ports = [
            {"id": 1, "name": "COM1", "channel_type": "serial", "serial_port_name": "COM1", "baud_rate": 9600, "data_bits": 8, "parity": "N", "stop_bits": 1, "poll_interval_ms": 1000, "timeout_ms": 1500, "failure_threshold": 3, "reconnect_interval_ms": 3000, "is_enabled": True},
        ]
        self.controllers = [{"id": 2, "port_id": 1, "name": "CTRL-1", "address": 1, "model": "M", "detector_count": 4, "is_enabled": True}]
        self.gas_types = [{"id": 3, "name": "甲烷", "unit": "%LEL", "range_min": 0, "range_max": 100, "default_alarm_low": 20, "default_alarm_high": 50, "is_enabled": True}]
        self.detectors = [{"id": 4, "port_id": 1, "controller_id": 2, "position_code": "A-001", "name": "探测器", "protocol_address": 2, "register_index": 0, "gas_type_id": 3, "unit": "%LEL", "range_min": 0, "range_max": 100, "alarm_low": 20, "alarm_high": 50, "alarm_type": "low_high", "sound_enabled": True, "store_interval_sec": 60, "sensor_life_until": "2027-01-01", "calibration_cycle_days": 365, "is_enabled": True}]
        self.save_result: ServiceResult[dict[str, object]] = ServiceResult.ok({"id": 99})
        self.protocol_mode = "protocol_1"
        self.export_calls = 0

    def list_ports(self) -> tuple[dict[str, object], ...]:
        return tuple(self.ports)

    def list_controllers(self) -> tuple[dict[str, object], ...]:
        return tuple(self.controllers)

    def list_gas_types(self) -> tuple[dict[str, object], ...]:
        return tuple(self.gas_types)

    def list_detectors(self) -> tuple[dict[str, object], ...]:
        return tuple(self.detectors)

    def save_port(self, session: object, command: object) -> ServiceResult[dict[str, object]]:
        return self.save_result

    def save_controller(self, session: object, command: object) -> ServiceResult[dict[str, object]]:
        return self.save_result

    def save_gas_type(self, session: object, command: object) -> ServiceResult[dict[str, object]]:
        return self.save_result

    def save_detector(self, session: object, command: object) -> ServiceResult[dict[str, object]]:
        return self.save_result

    def delete_port(self, session: object, item_id: int) -> ServiceResult[None]:
        return ServiceResult.ok(None)

    def delete_controller(self, session: object, item_id: int) -> ServiceResult[None]:
        return ServiceResult.ok(None)

    def delete_gas_type(self, session: object, item_id: int) -> ServiceResult[None]:
        return ServiceResult.ok(None)

    def delete_detector(self, session: object, item_id: int) -> ServiceResult[None]:
        return ServiceResult.ok(None)

    def get_protocol_mode(self) -> str:
        return self.protocol_mode

    def set_protocol_mode(self, session: object, mode: str) -> ServiceResult[object]:
        self.protocol_mode = mode
        return ServiceResult.ok(message="协议模式已切换，请重启采集或软件后生效。")

    def import_detectors(self, session: object, source: Path) -> ServiceResult[FakeImportData]:
        return ServiceResult.ok(FakeImportData(1, (FakeImportError(5, "name", "名称不能为空 <b>x</b>"),)))

    def export_detectors(self, destination: Path) -> ServiceResult[Path]:
        self.export_calls += 1
        return ServiceResult.ok(destination)

    def export_detector_template(self, destination: Path) -> ServiceResult[Path]:
        self.export_calls += 1
        return ServiceResult.ok(destination)


class UiDeviceConfigurationTests(unittest.TestCase):
    app: QApplication

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        AppTheme().apply_to(cls.app)

    def admin_session(self) -> Session:
        return Session("admin-session", 1, "admin", Role.ADMIN.value, ("*",), 1, "2026-01-01T00:00:00+00:00")

    def operator_session(self) -> Session:
        return Session("operator-session", 2, "operator", Role.OPERATOR.value, ("monitor.view",), 1, "2026-01-01T00:00:00+00:00")

    def test_device_config_page_category_navigation_and_operator_readonly_hint(self) -> None:
        service = FakeDeviceConfigService()
        page = DeviceConfigPage(service, session=self.admin_session())

        page.set_category("detectors")

        self.assertEqual(page.current_category(), "detectors")
        self.assertEqual(page.stack.count(), 5)
        self.assertFalse(page.new_button.isHidden())

        readonly = DeviceConfigPage(service, session=self.operator_session())
        self.assertFalse(readonly.permission_hint.isHidden())
        self.assertTrue(readonly.new_button.isHidden())
        self.assertIn("无权限", readonly.permission_hint.message_label.text())

    def test_ports_validation_and_controlled_service_error(self) -> None:
        service = FakeDeviceConfigService()
        page = PortsPage(service, self.admin_session())
        page.new_record()
        page.name_edit.clear()

        page.save_current()

        self.assertIn("端口名称不能为空", page.validation_hint.text())
        service.save_result = ServiceResult.fail(
            code=int(ErrorCode.CONFLICT),
            message="端口重复 Traceback File \"E:\\secret\\db.sqlite3\", line 1 password=abc",
        )
        page.name_edit.setText("COM2")
        page.save_current()
        self.assertNotIn("E:\\secret", page.error_banner.label.text())
        self.assertNotIn("abc", page.error_banner.label.text())

    def test_controller_detector_and_gas_type_frontend_validation(self) -> None:
        service = FakeDeviceConfigService()
        controllers = ControllersPage(service, self.admin_session())
        controllers.reload()
        controllers.port_combo.setCurrentIndex(0)
        controllers.name_edit.setText("CTRL")
        controllers.save_current()
        self.assertIn("必须选择端口", controllers.validation_hint.text())

        gas = GasTypesPage(service, self.admin_session())
        gas.range_min_edit.setText("100")
        gas.range_max_edit.setText("1")
        gas.name_edit.setText("氢气")
        gas.unit_edit.setText("ppm")
        gas.save_current()
        self.assertIn("量程下限", gas.validation_hint.text())

        detectors = DetectorsPage(service, self.admin_session())
        detectors.reload()
        detectors.port_combo.setCurrentIndex(detectors.port_combo.findData(1))
        detectors.gas_combo.setCurrentIndex(detectors.gas_combo.findData(3))
        detectors.position_edit.setText("D-1")
        detectors.name_edit.setText("Detector")
        detectors.unit_edit.setText("ppm")
        detectors.range_min_edit.setText("0")
        detectors.range_max_edit.setText("100")
        detectors.alarm_low_edit.setText("90")
        detectors.alarm_high_edit.setText("20")
        detectors.save_current()
        self.assertIn("低报阈值不能高于高报阈值", detectors.validation_hint.text())

    def test_protocol_switch_prompt_import_errors_and_plain_filename(self) -> None:
        service = FakeDeviceConfigService()
        dialogs: list[FakeResultDialog] = []

        def dialog_factory(**kwargs: object) -> FakeResultDialog:
            dialog = FakeResultDialog(**kwargs)
            dialogs.append(dialog)
            return dialog

        page = ProtocolSettingsPage(
            service,
            self.admin_session(),
            import_path_provider=lambda: Path("<b>bad.csv"),
            export_path_provider=lambda: Path("detectors.csv"),
            template_path_provider=lambda: Path("template.csv"),
            import_result_dialog_factory=dialog_factory,
        )
        page.protocol_combo.setCurrentIndex(page.protocol_combo.findData("protocol_2"))

        page.save_protocol_mode()
        self.assertIn("重启采集", PROTOCOL_RESTART_TEXT)
        self.assertIn("重启采集", page.error_banner.label.text())

        page.import_detectors()
        page.export_config()
        self.assertEqual(page.import_file_label.text(), "文件：<b>bad.csv")
        self.assertEqual(page.import_file_label.textFormat(), Qt.TextFormat.PlainText)
        self.assertEqual(dialogs[0].kwargs["source_name"], "<b>bad.csv")
        self.assertEqual(service.export_calls, 1)
        self.assertTrue(page.export_button.isEnabled())

    def test_import_result_dialog_displays_row_field_reason_as_plain_text(self) -> None:
        dialog = ImportResultDialog(imported_count=2, errors=(FakeImportError(7, "position_code", "<script>bad</script>"),), source_name="<b>file.csv</b>")

        self.assertEqual(dialog.source_label.text(), "文件：<b>file.csv</b>")
        self.assertEqual(dialog.source_label.textFormat(), Qt.TextFormat.PlainText)
        self.assertEqual(dialog.table.model().data(dialog.table.model().index(0, 0), Qt.ItemDataRole.DisplayRole), "7")
        self.assertEqual(dialog.table.model().data(dialog.table.model().index(0, 1), Qt.ItemDataRole.DisplayRole), "position_code")
        self.assertEqual(dialog.table.model().data(dialog.table.model().index(0, 2), Qt.ItemDataRole.DisplayRole), "<script>bad</script>")


if __name__ == "__main__":
    unittest.main()
