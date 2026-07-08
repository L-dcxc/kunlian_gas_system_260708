from __future__ import annotations

# ruff: noqa: E402

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from app.services.auth_service import Session
from app.services.errors import ErrorCode
from app.services.models import ServiceResult
from app.services.permissions import Role
from app.ui.settings.linkage_control_panel import LinkageControlPanel
from app.ui.settings.linkage_page import LinkagePanel
from app.ui.settings.linkage_records_panel import LinkageRecordsPanel
from app.ui.theme import AppTheme


class FakeLinkageService:
    def __init__(self) -> None:
        self.objects: tuple[dict[str, object], ...] = (
            {
                "id": 1,
                "object_type": "relay",
                "name": "排风机<1>",
                "location": "一层",
                "adapter_type": "simulated",
                "is_enabled": True,
            },
            {
                "id": 2,
                "object_type": "relay",
                "name": "真实 IO",
                "location": "二层",
                "adapter_type": "real",
                "is_enabled": True,
            },
        )
        self.rules: tuple[dict[str, object], ...] = (
            {
                "id": 5,
                "name": "高报开风机",
                "object_id": 1,
                "detector_id": 12,
                "alarm_type": "alarm_high",
                "alarm_level": 2,
                "action": "open",
                "trigger_delay_sec": 3,
                "recovery_action": "close",
                "is_enabled": True,
            },
        )
        self.records: tuple[dict[str, object], ...] = ()
        self.save_object_calls: list[tuple[object, object]] = []
        self.delete_object_calls: list[tuple[object, int]] = []
        self.save_rule_calls: list[tuple[object, object]] = []
        self.delete_rule_calls: list[tuple[object, int]] = []
        self.manual_calls: list[tuple[object, object]] = []
        self.object_result: ServiceResult[dict[str, object]] | None = None
        self.rule_result: ServiceResult[dict[str, object]] | None = None
        self.manual_result: ServiceResult[dict[str, object]] | None = None

    def list_objects(self) -> tuple[dict[str, object], ...]:
        return self.objects

    def list_rules(self) -> tuple[dict[str, object], ...]:
        return self.rules

    def save_object(self, session: object, command: object) -> ServiceResult[dict[str, object]]:
        self.save_object_calls.append((session, command))
        if self.object_result is not None:
            return self.object_result
        return ServiceResult.ok({"id": command.id or 3, "name": command.name})

    def delete_object(self, session: object, object_id: int) -> ServiceResult[None]:
        self.delete_object_calls.append((session, object_id))
        return ServiceResult.ok(None)

    def save_rule(self, session: object, command: object) -> ServiceResult[dict[str, object]]:
        self.save_rule_calls.append((session, command))
        if self.rule_result is not None:
            return self.rule_result
        return ServiceResult.ok({"id": command.id or 9, "name": command.name})

    def delete_rule(self, session: object, rule_id: int) -> ServiceResult[None]:
        self.delete_rule_calls.append((session, rule_id))
        return ServiceResult.ok(None)

    def manual_control(self, session: object, command: object) -> ServiceResult[dict[str, object]]:
        self.manual_calls.append((session, command))
        if self.manual_result is not None:
            return self.manual_result
        return ServiceResult.ok({"id": 20, "result": "simulated_success", "message": "ok"})

    def list_records(self, session: object) -> ServiceResult[tuple[dict[str, object], ...]]:
        return ServiceResult.ok(self.records)


class UiLinkagePageTests(unittest.TestCase):
    app: QApplication

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        AppTheme().apply_to(cls.app)

    def admin_session(self) -> Session:
        return Session(
            session_id="admin-session",
            user_id=1,
            username="admin",
            role=Role.ADMIN.value,
            permissions=("*",),
            permission_version=1,
            login_at="2026-01-01T00:00:00+00:00",
        )

    def operator_session(self) -> Session:
        return Session(
            session_id="operator-session",
            user_id=2,
            username="operator",
            role=Role.OPERATOR.value,
            permissions=("monitor.view",),
            permission_version=1,
            login_at="2026-01-01T00:00:00+00:00",
        )

    def test_page_reload_renders_objects_rules_and_real_io_is_not_selectable(self) -> None:
        service = FakeLinkageService()
        page = LinkagePanel(service, self.admin_session())

        page.reload()

        self.assertEqual(page.object_table.state().value, "ready")
        self.assertEqual(page.rule_table.state().value, "ready")
        self.assertIn("真实", page.simulation_notice.text())
        self.assertEqual(page.object_adapter_combo.findData("simulated"), 0)
        self.assertFalse(page.object_adapter_combo.model().item(page.object_adapter_combo.findData("real")).isEnabled())
        self.assertEqual(page.rule_object_combo.count(), 2)
        self.assertEqual(page.rule_object_combo.itemData(1), 1)

        model = page.object_table.model()
        self.assertEqual(model.data(model.index(0, 1), Qt.ItemDataRole.DisplayRole), "排风机<1>")

    def test_object_form_validation_and_save_forces_simulated_service_command(self) -> None:
        service = FakeLinkageService()
        page = LinkagePanel(service, self.admin_session())
        page.reload()

        page.object_name_edit.clear()
        page.save_object()
        self.assertIn("不能为空", page.object_validation_hint.text())
        self.assertEqual(service.save_object_calls, [])

        page.object_type_edit.setText("relay")
        page.object_name_edit.setText("新风机")
        page.object_location_edit.setText("三层")
        page.object_adapter_combo.setCurrentIndex(page.object_adapter_combo.findData("real"))
        page.save_object()
        self.assertIn("simulated", page.object_validation_hint.text())
        self.assertEqual(service.save_object_calls, [])

        page.object_adapter_combo.setCurrentIndex(page.object_adapter_combo.findData("simulated"))
        page.save_object()
        self.assertEqual(len(service.save_object_calls), 1)
        command = service.save_object_calls[0][1]
        self.assertEqual(command.adapter_type, "simulated")
        self.assertEqual(command.name, "新风机")

    def test_rule_form_validation_and_save_calls_service_only(self) -> None:
        service = FakeLinkageService()
        page = LinkagePanel(service, self.admin_session())
        page.reload()

        page.new_rule()
        page.rule_name_edit.setText("规则")
        page.rule_object_combo.setCurrentIndex(1)
        page.rule_detector_id_edit.setText("-1")
        page.save_rule()
        self.assertIn("正整数", page.rule_validation_hint.text())
        self.assertEqual(service.save_rule_calls, [])

        page.rule_detector_id_edit.setText("12")
        page.rule_action_edit.setText("open;rm")
        page.save_rule()
        self.assertIn("动作码", page.rule_validation_hint.text())
        self.assertEqual(service.save_rule_calls, [])

        page.rule_action_edit.setText("open")
        page.rule_alarm_combo.setCurrentIndex(page.rule_alarm_combo.findData("alarm_high"))
        page.rule_alarm_level_enabled.setChecked(True)
        page.rule_alarm_level_spin.setValue(2)
        page.rule_delay_spin.setValue(60)
        page.save_rule()
        self.assertEqual(len(service.save_rule_calls), 1)
        command = service.save_rule_calls[0][1]
        self.assertEqual(command.object_id, 1)
        self.assertEqual(command.detector_id, 12)
        self.assertEqual(command.alarm_type, "alarm_high")
        self.assertEqual(command.alarm_level, 2)
        self.assertEqual(command.trigger_delay_sec, 60)

    def test_operator_is_readonly_and_permission_denied_is_visible(self) -> None:
        service = FakeLinkageService()
        page = LinkagePanel(service, self.operator_session())

        self.assertFalse(page.save_object_button.isEnabled())
        self.assertTrue(page.object_name_edit.isReadOnly())
        page.save_object()

        self.assertEqual(service.save_object_calls, [])
        self.assertIn("无权限", page.error_banner.label.text())
        self.assertIn("已记录", page.permission_hint.message_label.text())

    def test_manual_control_requires_confirm_has_busy_state_and_sanitizes_result(self) -> None:
        service = FakeLinkageService()
        confirms: list[tuple[str, str, str]] = []
        panel = LinkageControlPanel(
            service,
            self.admin_session(),
            confirm_manual=lambda parent, obj, action, reason: confirms.append((obj, action, reason)) or True,
        )
        panel.reload_objects()
        panel.object_combo.setCurrentIndex(1)
        panel.action_edit.setText("open")
        panel.reason_edit.setText("现场确认")

        panel._set_busy(True)
        self.assertFalse(panel.control_button.isEnabled())
        panel._set_busy(False)
        panel.manual_control()

        self.assertEqual(len(service.manual_calls), 1)
        self.assertEqual(confirms[0][1], "open")
        self.assertEqual(panel.result_card.property("linkageResult"), "success")
        self.assertIn("对象：", panel.result_detail.text())
        self.assertIn("原因：现场确认", panel.result_detail.text())

        service.manual_result = ServiceResult.fail(
            code=int(ErrorCode.INTERNAL_ERROR),
            message='Traceback File "E:\\secret\\linkage.py", line 2 SELECT token FROM t password=abc',
        )
        panel.manual_control()
        self.assertEqual(panel.result_card.property("linkageResult"), "error")
        self.assertNotIn("Traceback", panel.result_detail.text())
        self.assertNotIn("E:\\secret", panel.result_detail.text())
        self.assertNotIn("SELECT", panel.result_detail.text())
        self.assertNotIn("password=abc", panel.result_detail.text())

    def test_operator_manual_control_disabled_and_reports_permission_failure(self) -> None:
        service = FakeLinkageService()
        panel = LinkageControlPanel(service, self.operator_session())
        panel.reload_objects()

        self.assertFalse(panel.control_button.isEnabled())
        panel.manual_control()

        self.assertEqual(service.manual_calls, [])
        self.assertIn("无权限", panel.error_banner.label.text())
        self.assertIn("已记录", panel.permission_hint.message_label.text())

    def test_records_panel_dedupes_automatic_alarm_and_sanitizes_failures(self) -> None:
        service = FakeLinkageService()
        service.records = (
            {
                "id": 1,
                "object_id": 1,
                "object_name": "排风机",
                "rule_id": 5,
                "alarm_record_id": 100,
                "action": "open",
                "trigger_reason": "automatic_alarm",
                "result": "simulated_success",
                "message": "自动联动模拟触发。",
                "created_at": "2026-01-01T00:00:00+00:00",
            },
            {
                "id": 2,
                "object_id": 1,
                "object_name": "排风机",
                "rule_id": 5,
                "alarm_record_id": 100,
                "action": "open",
                "trigger_reason": "automatic_alarm",
                "result": "simulated_success",
                "message": "重复触发",
                "created_at": "2026-01-01T00:00:01+00:00",
            },
            {
                "id": 3,
                "object_id": 1,
                "object_name": "排风机",
                "rule_id": 6,
                "alarm_record_id": 101,
                "action": "open",
                "trigger_reason": "automatic_alarm",
                "result": "simulated_fail",
                "message": r"sqlite3.OperationalError SELECT secret FROM linkage E:\secret\db.sqlite3",
                "created_at": "2026-01-01T00:00:02+00:00",
            },
        )
        panel = LinkageRecordsPanel(service, self.admin_session())

        panel.reload()

        self.assertEqual(panel.table.model().rowCount(), 2)
        model = panel.table.model()
        statuses = [model.data(model.index(row, 5), Qt.ItemDataRole.DisplayRole) for row in range(model.rowCount())]
        self.assertIn("已触发", statuses)
        self.assertIn("失败", statuses)
        self.assertIn("联动失败", panel.status_detail.text())
        self.assertNotIn("sqlite", panel.status_detail.text().lower())
        self.assertNotIn("SELECT", panel.status_detail.text())
        self.assertNotIn("E:\\secret", panel.status_detail.text())

    def test_records_panel_without_facade_shows_controlled_empty_state(self) -> None:
        class NoRecordFacade:
            pass

        panel = LinkageRecordsPanel(NoRecordFacade(), self.admin_session())
        panel.reload()

        self.assertEqual(panel.table.state().value, "empty")
        self.assertIn("未配置", panel.table._empty_message.text())


if __name__ == "__main__":
    unittest.main()
