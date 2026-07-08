from __future__ import annotations

# ruff: noqa: E402

import os
import unittest
from dataclasses import dataclass

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLineEdit

from app.services.errors import ErrorCode
from app.services.models import ServiceResult
from app.ui.login import ChangePasswordDialog, LicenseDialog, LoginWindow
from app.ui.login.change_password_dialog import PASSWORD_MISMATCH_TEXT
from app.ui.login.license_dialog import LICENSE_FAILED_MESSAGE
from app.ui.login.login_window import LOGIN_FAILED_TEXT
from app.ui.theme import AppTheme


@dataclass(frozen=True)
class FakeLicenseStatus:
    status: str = "active"
    machine_fingerprint_hash: str = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    activated_at: str | None = "2026-01-01T00:00:00+00:00"
    expires_at: str | None = None

    @property
    def is_active(self) -> bool:
        return self.status == "active"

    @property
    def can_enter_main_system(self) -> bool:
        return self.is_active


class FailingLicenseService:
    def __init__(self) -> None:
        self.calls = 0
        self.button_enabled_during_call: bool | None = None
        self.dialog: LicenseDialog | None = None

    def get_license_status(self) -> FakeLicenseStatus:
        return FakeLicenseStatus(status="unlicensed")

    def activate(self, code: str) -> ServiceResult[FakeLicenseStatus]:
        self.calls += 1
        if self.dialog is not None:
            self.button_enabled_during_call = self.dialog.validate_button.isEnabled()
        return ServiceResult.fail(
            code=int(ErrorCode.PERMISSION_DENIED),
            message='Traceback File "E:\\secret\\app.db", line 7 signature key leaked machine_fingerprint=raw',
        )


class ActiveLicenseService:
    def get_license_status(self) -> FakeLicenseStatus:
        return FakeLicenseStatus(status="active")


class FailingAuthService:
    def __init__(self) -> None:
        self.calls = 0
        self.window: LoginWindow | None = None
        self.button_enabled_during_call: bool | None = None

    def login(self, username: str, password: str) -> ServiceResult[object]:
        self.calls += 1
        if self.window is not None:
            self.button_enabled_during_call = self.window.login_button.isEnabled()
        return ServiceResult.fail(
            code=int(ErrorCode.PERMISSION_DENIED),
            message="user not found password=secret db=E:\\secret\\app.db",
        )


class PermissionDeniedAuthService:
    def change_password(self, session: object, old_password: str, new_password: str) -> ServiceResult[None]:
        return ServiceResult.fail(
            code=int(ErrorCode.PERMISSION_DENIED),
            message="permission_code=USER_EDIT_OTHER password=secret",
        )


class UiLoginLicenseTests(unittest.TestCase):
    app: QApplication

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        AppTheme().apply_to(cls.app)

    def test_license_dialog_masks_machine_identifier_and_controls_activation_failure(self) -> None:
        service = FailingLicenseService()
        dialog = LicenseDialog(service)
        service.dialog = dialog
        dialog.authorization_code_edit.setPlainText("bad-license-code")

        dialog.submit()

        self.assertEqual(service.calls, 1)
        self.assertFalse(service.button_enabled_during_call)
        self.assertEqual(dialog.error_hint.text(), LICENSE_FAILED_MESSAGE)
        self.assertNotIn("Traceback", dialog.error_hint.text())
        self.assertNotIn("signature", dialog.error_hint.text().lower())
        self.assertNotIn("0123456789abcdef0123456789abcdef", dialog.machine_label.text())
        self.assertIn("01234567", dialog.machine_label.text())
        self.assertIn("cdef", dialog.machine_label.text())

    def test_login_window_failure_is_unified_password_hidden_and_submit_disabled_during_call(self) -> None:
        auth = FailingAuthService()
        window = LoginWindow(auth_service=auth, license_service=ActiveLicenseService())
        auth.window = window
        window.username_edit.setText("operator")
        window.password_edit.setText("wrong-password")

        window.submit()

        self.assertEqual(auth.calls, 1)
        self.assertFalse(auth.button_enabled_during_call)
        self.assertEqual(window.error_hint.text(), LOGIN_FAILED_TEXT)
        self.assertNotIn("secret", window.error_hint.text())
        self.assertEqual(window.password_edit.echoMode(), QLineEdit.EchoMode.Password)
        self.assertTrue(window.login_button.isEnabled())

    def test_login_window_blocks_unlicensed_main_system_by_default(self) -> None:
        window = LoginWindow(auth_service=FailingAuthService(), license_service=None)
        window.username_edit.setText("admin")
        window.password_edit.setText("password")

        window.submit()

        self.assertIn("未授权", window.error_hint.text())
        self.assertEqual(window.password_edit.echoMode(), QLineEdit.EchoMode.Password)

    def test_change_password_realtime_mismatch_and_password_echo_modes(self) -> None:
        dialog = ChangePasswordDialog(auth_service=PermissionDeniedAuthService(), session=object())
        dialog.old_password_edit.setText("old-password")
        dialog.new_password_edit.setText("new-password-1")
        dialog.confirm_password_edit.setText("new-password-2")

        self.assertEqual(dialog.error_hint.text(), PASSWORD_MISMATCH_TEXT)
        self.assertEqual(dialog.confirm_password_edit.property("validation"), "error")
        self.assertFalse(dialog.submit_button.isEnabled())
        self.assertEqual(dialog.old_password_edit.echoMode(), QLineEdit.EchoMode.Password)
        self.assertEqual(dialog.new_password_edit.echoMode(), QLineEdit.EchoMode.Password)
        self.assertEqual(dialog.confirm_password_edit.echoMode(), QLineEdit.EchoMode.Password)

    def test_change_password_permission_denied_shows_lock_hint_without_sensitive_details(self) -> None:
        dialog = ChangePasswordDialog(
            auth_service=PermissionDeniedAuthService(),
            session=object(),
            target_username="other-user",
            can_modify_target=False,
        )

        self.assertFalse(dialog.submit_button.isEnabled())
        self.assertFalse(dialog.permission_hint.isHidden())
        self.assertIn("🔒", dialog.permission_hint.icon_label.text())
        self.assertIn("已记录", dialog.permission_hint.message_label.text())

    def test_change_password_service_permission_denied_is_controlled(self) -> None:
        dialog = ChangePasswordDialog(auth_service=PermissionDeniedAuthService(), session=object())
        dialog.old_password_edit.setText("old-password")
        dialog.new_password_edit.setText("new-password-1")
        dialog.confirm_password_edit.setText("new-password-1")

        dialog.submit()

        self.assertFalse(dialog.permission_hint.isHidden())
        self.assertIn("已记录", dialog.permission_hint.message_label.text())
        self.assertNotIn("USER_EDIT_OTHER", dialog.error_hint.text())
        self.assertNotIn("secret", dialog.error_hint.text())


if __name__ == "__main__":
    unittest.main()
