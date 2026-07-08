from __future__ import annotations

# ruff: noqa: E402

import os
import unittest
from dataclasses import dataclass

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog

from app.services.auth_service import Session
from app.services.errors import ErrorCode
from app.services.maintenance_service import MaintenancePlanView, MaintenanceReminderView
from app.services.models import ServiceResult
from app.services.permissions import Role
from app.ui.settings.maintenance_dialogs import MaintenancePlanDialog
from app.ui.settings.maintenance_page import MaintenancePanel, MaintenanceReminderCard
from app.ui.theme import AppTheme


@dataclass(frozen=True)
class FakePlan:
    id: int
    detector_id: int
    plan_type: str = "custom"
    due_at: str = "2026-02-01T00:00:00+00:00"
    remind_days_before: int = 7
    status: str = "active"
    notes: str = ""
    detector_position_code: str | None = "A-001"
    detector_name: str | None = "探测器"


class FakeDialog(QDialog):
    def __init__(self, result: int = QDialog.DialogCode.Accepted) -> None:
        super().__init__()
        self._result = result

    def exec(self) -> int:  # noqa: A003
        return self._result


class FakeMaintenanceService:
    def __init__(self) -> None:
        self.reminders: tuple[MaintenanceReminderView, ...] = ()
        self.plans: tuple[object, ...] = ()
        self.view_calls: list[object] = []
        self.list_calls: list[object] = []
        self.create_calls: list[tuple[object, object]] = []
        self.update_calls: list[tuple[object, int, object]] = []
        self.reminder_result: ServiceResult[tuple[MaintenanceReminderView, ...]] | None = None
        self.plan_result: ServiceResult[tuple[object, ...]] | None = None
        self.save_result: ServiceResult[MaintenancePlanView] = ServiceResult.ok(
            MaintenancePlanView(
                id=1,
                detector_id=10,
                plan_type="custom",
                due_at="2026-02-01T00:00:00+00:00",
                remind_days_before=7,
                status="active",
                notes="saved",
            )
        )

    def view_due_reminders(self, session: object) -> ServiceResult[tuple[MaintenanceReminderView, ...]]:
        self.view_calls.append(session)
        return self.reminder_result or ServiceResult.ok(self.reminders)

    def list_plans(self, session: object) -> ServiceResult[tuple[object, ...]]:
        self.list_calls.append(session)
        return self.plan_result or ServiceResult.ok(self.plans)

    def create_plan(self, session: object, command: object) -> ServiceResult[MaintenancePlanView]:
        self.create_calls.append((session, command))
        return self.save_result

    def update_plan(self, session: object, plan_id: int, command: object) -> ServiceResult[MaintenancePlanView]:
        self.update_calls.append((session, plan_id, command))
        return self.save_result


class UiMaintenancePageTests(unittest.TestCase):
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
            permissions=("maintenance.view",),
            permission_version=1,
            login_at="2026-01-01T00:00:00+00:00",
        )

    def test_page_initializes_and_reload_renders_due_soon_overdue_states(self) -> None:
        service = FakeMaintenanceService()
        service.reminders = (
            MaintenanceReminderView(
                source="detector.sensor_life",
                detector_id=10,
                detector_position_code="A-001",
                detector_name="甲烷探头",
                plan_type="sensor_life",
                due_at="2026-01-03T00:00:00+00:00",
                remind_days_before=30,
                status="due_soon",
                days_until_due=2,
                notes="",
            ),
            MaintenanceReminderView(
                source="maintenance_plan",
                detector_id=11,
                detector_position_code="B-001",
                detector_name="氢气探头",
                plan_type="custom",
                due_at="2025-12-30T00:00:00+00:00",
                remind_days_before=7,
                status="overdue",
                days_until_due=-2,
                plan_id=8,
                notes="replace sensor",
            ),
        )
        service.plans = (FakePlan(8, 11, notes="replace sensor"),)
        page = MaintenancePanel(service, self.admin_session())

        page.reload()

        self.assertEqual(service.view_calls, [page._session])
        self.assertEqual(service.list_calls, [page._session])
        self.assertEqual(len(page.reminder_cards), 2)
        self.assertEqual(page.reminder_cards[0].property("maintenance"), "dueSoon")
        self.assertEqual(page.reminder_cards[1].property("maintenance"), "overdue")
        self.assertEqual(page.plan_table.state().value, "ready")
        self.assertFalse(page.refresh_button.isEnabled() is False)

    def test_empty_loading_and_controlled_service_error_states(self) -> None:
        service = FakeMaintenanceService()
        page = MaintenancePanel(service, self.admin_session())

        page._set_busy(True)
        self.assertFalse(page.refresh_button.isEnabled())
        page._set_busy(False)
        page.reload()

        self.assertFalse(page.reminders_empty_label.isHidden())
        self.assertEqual(page.plan_table.state().value, "empty")

        service.reminder_result = ServiceResult.fail(
            code=int(ErrorCode.INTERNAL_ERROR),
            message='Traceback File "E:\\secret\\db.py", line 1 SELECT password FROM users',
        )
        page.reload()
        self.assertFalse(page.reminders_error.isHidden())
        self.assertIn("维护提醒读取失败", page.reminders_error.label.text())
        self.assertNotIn("E:\\secret", page.reminders_error.label.text())
        self.assertNotIn("SELECT", page.reminders_error.label.text())
        self.assertNotIn("password", page.reminders_error.label.text().lower())

    def test_reminder_and_plan_user_text_is_plain_text(self) -> None:
        reminder = MaintenanceReminderView(
            source="maintenance_plan",
            detector_id=12,
            detector_position_code="<i>A-001</i>",
            detector_name="<b>探头</b>",
            plan_type="custom",
            due_at="2026-01-03T00:00:00+00:00",
            remind_days_before=3,
            status="due_soon",
            days_until_due=1,
            notes="<img src=x onerror=alert(1)>",
        )
        card = MaintenanceReminderCard(reminder)

        self.assertEqual(card.device_label.textFormat(), Qt.TextFormat.PlainText)
        self.assertIn("<b>探头</b>", card.device_label.text())
        self.assertIn("<img", card.notes_label.text())

        service = FakeMaintenanceService()
        service.plans = (FakePlan(1, 12, notes="<script>alert(1)</script>", detector_name="<b>探头</b>"),)
        page = MaintenancePanel(service, self.admin_session())
        page.reload()
        model = page.plan_table.model()
        self.assertEqual(model.data(model.index(0, 1), Qt.ItemDataRole.DisplayRole), "<b>探头</b>")
        self.assertEqual(model.data(model.index(0, 7), Qt.ItemDataRole.DisplayRole), "<script>alert(1)</script>")

    def test_operator_cannot_open_or_save_plan(self) -> None:
        service = FakeMaintenanceService()
        page = MaintenancePanel(service, self.operator_session())

        self.assertFalse(page.new_button.isEnabled())
        page.open_create_dialog()
        self.assertEqual(service.create_calls, [])
        self.assertIn("无权限", page.permission_hint.message_label.text())

        dialog = MaintenancePlanDialog(service, self.operator_session(), can_manage=False)
        self.assertFalse(dialog.submit_button.isEnabled())
        dialog.submit()
        self.assertEqual(service.create_calls, [])
        self.assertIn("无权限", dialog.error_hint.text())

    def test_form_validation_and_create_save_calls_service_only(self) -> None:
        service = FakeMaintenanceService()
        dialog = MaintenancePlanDialog(service, self.admin_session())

        dialog.detector_id_spin.setValue(0)
        dialog.submit()
        self.assertIn("正整数", dialog.error_hint.text())
        self.assertEqual(service.create_calls, [])

        dialog.detector_id_spin.setValue(12)
        dialog.notes_edit.setPlainText("x" * 1001)
        dialog.submit()
        self.assertIn("1000", dialog.error_hint.text())
        self.assertEqual(service.create_calls, [])

        dialog.notes_edit.setPlainText("inspect detector")
        dialog.remind_days_spin.setValue(30)
        dialog.plan_type_combo.setCurrentIndex(dialog.plan_type_combo.findData("custom"))
        dialog.status_combo.setCurrentIndex(dialog.status_combo.findData("active"))
        dialog.submit()

        self.assertEqual(len(service.create_calls), 1)
        command = service.create_calls[0][1]
        self.assertEqual(command.detector_id, 12)
        self.assertEqual(command.plan_type, "custom")
        self.assertEqual(command.remind_days_before, 30)
        self.assertEqual(command.status, "active")
        self.assertEqual(command.notes, "inspect detector")

    def test_edit_save_calls_update_and_service_failure_is_controlled(self) -> None:
        service = FakeMaintenanceService()
        plan = FakePlan(9, 12, notes="old")
        dialog = MaintenancePlanDialog(service, self.admin_session(), plan=plan)
        dialog.detector_id_spin.setValue(12)
        dialog.notes_edit.setPlainText("new")

        dialog.submit()

        self.assertEqual(len(service.update_calls), 1)
        self.assertEqual(service.update_calls[0][1], 9)

        service.save_result = ServiceResult.fail(
            code=int(ErrorCode.INTERNAL_ERROR),
            message='sqlite3.OperationalError: SELECT password FROM users at E:\\secret\\app.sqlite3',
        )
        dialog = MaintenancePlanDialog(service, self.admin_session())
        dialog.detector_id_spin.setValue(12)
        dialog.submit()

        self.assertIn("维护计划保存失败", dialog.error_hint.text())
        self.assertNotIn("sqlite", dialog.error_hint.text().lower())
        self.assertNotIn("SELECT", dialog.error_hint.text())
        self.assertNotIn("password", dialog.error_hint.text().lower())
        self.assertNotIn("E:\\secret", dialog.error_hint.text())


if __name__ == "__main__":
    unittest.main()
