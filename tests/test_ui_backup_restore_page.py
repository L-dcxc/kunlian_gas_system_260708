from __future__ import annotations

# ruff: noqa: E402

import os
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app.services.auth_service import Session
from app.services.backup_service import BackupResult, BackupSettingsView, RestoreConfirm, RestoreResult
from app.services.errors import ErrorCode
from app.services.models import ServiceResult
from app.services.permissions import Role
from app.ui.settings.backup_page import BackupRestorePage, ManualBackupPanel
from app.ui.settings.backup_schedule_panel import BackupSchedulePanel
from app.ui.settings.restore_panel import RestorePanel
from app.ui.theme import AppTheme


class FakeBackupService:
    def __init__(self) -> None:
        self.manual_calls: list[tuple[object, Path]] = []
        self.settings_calls: list[tuple[object, object]] = []
        self.restore_calls: list[tuple[object, Path, RestoreConfirm]] = []
        self.manual_result: ServiceResult[BackupResult] = ServiceResult.ok(
            BackupResult(
                file_name=r"E:\secret\backup_20260101_020000.zip",
                relative_path="backups/backup_20260101_020000.zip",
                size_bytes=2048,
                sha256="0" * 64,
                created_at="2026-01-01T02:00:00+00:00",
                schema_version="0001",
            )
        )
        self.settings = BackupSettingsView(
            scheduled_enabled=True,
            interval_hours=24,
            backup_time="02:00",
            target_directory="backups/daily",
            keep_last=7,
            failure_notify_enabled=True,
        )
        self.update_result: ServiceResult[BackupSettingsView] | None = None
        self.restore_result: ServiceResult[RestoreResult] = ServiceResult.ok(
            RestoreResult(
                restored_files=("db/app.sqlite3", "config/config.json"),
                pre_restore_backup=None,
                restart_required=True,
                message="数据恢复完成，请重启或重新加载应用数据。",
            )
        )
        self.check_result: ServiceResult[RestoreResult] = ServiceResult.fail(
            code=int(ErrorCode.VALIDATION_ERROR),
            message="数据恢复需要显式确认",
        )

    def create_manual_backup(self, session: object, target_dir: Path) -> ServiceResult[BackupResult]:
        self.manual_calls.append((session, Path(target_dir)))
        return self.manual_result

    def get_settings(self) -> ServiceResult[BackupSettingsView]:
        return ServiceResult.ok(self.settings)

    def update_settings(self, session: object, command: object) -> ServiceResult[BackupSettingsView]:
        self.settings_calls.append((session, command))
        if self.update_result is not None:
            return self.update_result
        self.settings = BackupSettingsView(
            scheduled_enabled=command.scheduled_enabled,
            interval_hours=command.interval_hours,
            backup_time=command.backup_time,
            target_directory=str(command.target_directory),
            keep_last=command.keep_last,
            failure_notify_enabled=command.failure_notify_enabled,
        )
        return ServiceResult.ok(self.settings)

    def restore_from_backup(
        self,
        session: object,
        backup_file: Path,
        confirm: RestoreConfirm,
    ) -> ServiceResult[RestoreResult]:
        self.restore_calls.append((session, Path(backup_file), confirm))
        return self.restore_result if confirm.confirmed else self.check_result


class UiBackupRestorePageTests(unittest.TestCase):
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

    def test_page_initializes_composed_backup_schedule_restore_panels(self) -> None:
        page = BackupRestorePage(FakeBackupService(), self.admin_session())

        self.assertIsInstance(page.manual_panel, ManualBackupPanel)
        self.assertIsInstance(page.schedule_panel, BackupSchedulePanel)
        self.assertIsInstance(page.restore_panel, RestorePanel)
        self.assertFalse(page.permission_hint.isVisible())

    def test_manual_backup_busy_success_failure_and_plain_filename(self) -> None:
        service = FakeBackupService()
        panel = ManualBackupPanel(
            service,
            self.admin_session(),
            can_manage=True,
            directory_provider=lambda: Path(r"E:\secret\runtime\backups"),
        )

        panel.choose_target_directory()
        self.assertIn("backups", panel.target_label.text())
        self.assertNotIn(r"E:\secret", panel.target_label.text())

        panel._set_busy(True)
        self.assertFalse(panel.backup_button.isEnabled())
        self.assertIn("正在打包数据库、地图、配置", panel.busy_label.text())
        panel._set_busy(False)

        panel.create_manual_backup()
        self.assertEqual(len(service.manual_calls), 1)
        self.assertEqual(panel.result_card.property("backupResult"), "success")
        self.assertIn("backup_20260101_020000.zip", panel.result_detail.text())
        self.assertNotIn(r"E:\secret", panel.result_detail.text())

        service.manual_result = ServiceResult.ok(
            BackupResult(
                file_name=r"E:\secret\password=abc.zip",
                relative_path="backups/password=abc.zip",
                size_bytes=128,
                sha256="1" * 64,
                created_at=r"Traceback File \"E:\secret\clock.py\", line 2",
                schema_version="0001",
            )
        )
        panel.create_manual_backup()
        self.assertNotIn(r"E:\secret", panel.result_detail.text())
        self.assertNotIn("password=abc", panel.result_detail.text())
        self.assertNotIn("Traceback", panel.result_detail.text())

        service.manual_result = ServiceResult.fail(
            code=int(ErrorCode.INTERNAL_ERROR),
            message=r"Traceback File \"E:\secret\app.py\", line 2 password=abc",
        )
        panel.create_manual_backup()
        self.assertEqual(panel.result_card.property("backupResult"), "error")
        self.assertIn("备份失败", panel.result_detail.text())
        self.assertNotIn("Traceback", panel.result_detail.text())
        self.assertNotIn("password=abc", panel.result_detail.text())

    def test_schedule_form_validation_readonly_and_save_calls_service_only(self) -> None:
        service = FakeBackupService()
        readonly = BackupSchedulePanel(service, self.operator_session(), can_manage=False)

        self.assertTrue(readonly.time_edit.isReadOnly())
        self.assertTrue(readonly.directory_edit.isReadOnly())
        self.assertFalse(readonly.save_button.isEnabled())
        readonly.save_settings()
        self.assertEqual(service.settings_calls, [])
        self.assertIn("无权限", readonly.error_banner.label.text())

        panel = BackupSchedulePanel(service, self.admin_session(), can_manage=True)
        panel.time_edit.setText("25:99")
        self.assertFalse(panel.validate_form())
        self.assertIn("HH:MM", panel.validation_hint.text())

        panel.time_edit.setText("03:30")
        panel.interval_spin.setValue(6)
        panel.keep_spin.setValue(3)
        panel.directory_edit.setText("backups/daily")
        panel.enabled_check.setChecked(True)
        panel.save_settings()

        self.assertEqual(len(service.settings_calls), 1)
        command = service.settings_calls[0][1]
        self.assertEqual(command.interval_hours, 6)
        self.assertEqual(command.backup_time, "03:30")
        self.assertEqual(command.keep_last, 3)
        self.assertEqual(command.target_directory, "backups/daily")
        self.assertIn("已保存", panel.status_label.text())

        service.update_result = ServiceResult.fail(
            code=int(ErrorCode.INTERNAL_ERROR),
            message="sqlite3.OperationalError: SELECT password FROM users",
        )
        panel.save_settings()
        self.assertIn("备份设置保存失败", panel.error_banner.label.text())
        self.assertNotIn("sqlite", panel.error_banner.label.text().lower())
        self.assertNotIn("SELECT", panel.error_banner.label.text())
        self.assertNotIn("password", panel.error_banner.label.text().lower())

    def test_restore_running_acquisition_disables_button_and_shows_hint(self) -> None:
        service = FakeBackupService()
        panel = RestorePanel(
            service,
            self.admin_session(),
            can_manage=True,
            file_provider=lambda: Path(r"E:\secret\backup.zip"),
            confirm_restore=lambda parent, file_name: True,
        )
        panel.select_restore_file()

        panel.refresh_acquisition_state(True)

        self.assertFalse(panel.restore_button.isEnabled())
        self.assertIn("请先停止采集服务", panel.hint_label.text())
        panel.restore_selected()
        self.assertEqual(service.restore_calls, [])

    def test_restore_file_check_cancel_and_confirm_flow(self) -> None:
        service = FakeBackupService()
        confirms: list[str] = []
        panel = RestorePanel(
            service,
            self.admin_session(),
            can_manage=True,
            file_provider=lambda: Path(r"E:\secret\valid_backup.zip"),
            confirm_restore=lambda parent, file_name: confirms.append(file_name) or False,
        )
        panel.select_restore_file()
        self.assertIn("valid_backup.zip", panel.file_label.text())
        self.assertNotIn(r"E:\secret", panel.file_label.text())

        panel.check_backup_file()
        self.assertEqual(len(service.restore_calls), 1)
        self.assertFalse(service.restore_calls[-1][2].confirmed)
        self.assertEqual(panel.result_card.property("backupResult"), "success")

        panel.restore_selected()
        self.assertEqual(confirms, ["valid_backup.zip"])
        self.assertEqual(len(service.restore_calls), 1)

        panel._confirm_restore = lambda parent, file_name: True
        panel.restore_selected()
        self.assertEqual(len(service.restore_calls), 2)
        self.assertTrue(service.restore_calls[-1][2].confirmed)
        self.assertIn("重启", panel.result_detail.text())

    def test_restore_validation_failure_is_controlled_without_path_traversal_detail(self) -> None:
        service = FakeBackupService()
        service.check_result = ServiceResult.fail(
            code=int(ErrorCode.VALIDATION_ERROR),
            message=r"备份文件包含非法路径 ../config/config.json E:\secret\app.sqlite3",
        )
        panel = RestorePanel(
            service,
            self.admin_session(),
            can_manage=True,
            file_provider=lambda: Path("bad.zip"),
            confirm_restore=lambda parent, file_name: True,
        )

        panel.select_restore_file()
        panel.check_backup_file()

        self.assertEqual(panel.result_card.property("backupResult"), "error")
        self.assertIn("备份文件结构无效", panel.result_detail.text())
        self.assertNotIn("../", panel.result_detail.text())
        self.assertNotIn("E:\\secret", panel.result_detail.text())

        service.check_result = ServiceResult.fail(
            code=int(ErrorCode.VALIDATION_ERROR),
            message="sqlite3.OperationalError: SELECT secret FROM backup_manifest",
        )
        panel.check_backup_file()
        self.assertNotIn("sqlite", panel.result_detail.text().lower())
        self.assertNotIn("SELECT", panel.result_detail.text())
        self.assertNotIn("secret", panel.result_detail.text().lower())


if __name__ == "__main__":
    unittest.main()
