from __future__ import annotations

# ruff: noqa: E402

import os
import unittest
from dataclasses import dataclass

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog, QLineEdit

from app.services.auth_service import Session
from app.services.errors import ErrorCode
from app.services.models import ServiceResult
from app.services.permissions import Role
from app.services.user_service import UserView
from app.ui.settings.user_dialogs import UserEditorDialog
from app.ui.settings.users_page import UserManagementPage
from app.ui.theme import AppTheme


@dataclass(frozen=True)
class FakeUser:
    id: int
    username: str
    role: str
    is_active: bool
    created_at: str = "2026-01-01T00:00:00+00:00"
    updated_at: str = "2026-01-01T00:00:00+00:00"
    remark: str = ""


class FakeDialog(QDialog):
    def __init__(self, result: int = QDialog.DialogCode.Accepted) -> None:
        super().__init__()
        self._result = result

    def exec(self) -> int:  # noqa: A003
        return self._result


class ListingUserService:
    def __init__(self, rows: list[FakeUser]) -> None:
        self.rows = rows
        self.queries: list[dict[str, object]] = []
        self.disable_calls: list[int] = []
        self.update_calls: list[tuple[int, object]] = []
        self.disable_result: ServiceResult[None] = ServiceResult.ok(None)

    def list_users(self, session: object, *, role: str | None, is_active: bool | None, pagination: object) -> ServiceResult[tuple[list[FakeUser], int]]:
        self.queries.append({"role": role, "is_active": is_active, "page": pagination.page, "per_page": pagination.per_page})
        filtered = [item for item in self.rows if (role is None or item.role == role) and (is_active is None or item.is_active == is_active)]
        return ServiceResult.ok((filtered, len(filtered)))

    def disable_user(self, session: object, user_id: int) -> ServiceResult[None]:
        self.disable_calls.append(user_id)
        return self.disable_result

    def update_user(self, session: object, user_id: int, command: object) -> ServiceResult[UserView]:
        self.update_calls.append((user_id, command))
        return ServiceResult.ok(None)


class CreateSecondAdminService:
    def __init__(self) -> None:
        self.command: object | None = None

    def create_user(self, session: object, command: object) -> ServiceResult[None]:
        self.command = command
        return ServiceResult.fail(
            code=int(ErrorCode.CONFLICT),
            message="管理员账号只能有一个 password=secret123",
        )


class UiUserManagementTests(unittest.TestCase):
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

    def test_user_table_renders_user_text_as_plain_model_data(self) -> None:
        service = ListingUserService([
            FakeUser(1, "<b>admin</b>", Role.ADMIN.value, True, remark="<img src=x>"),
        ])
        page = UserManagementPage(service, self.admin_session())

        page.load_users()

        self.assertEqual(page.table.state().value, "ready")
        self.assertEqual(page.table.model().data(page.table.model().index(0, 1), Qt.ItemDataRole.DisplayRole), "<b>admin</b>")
        created_at = page.table.model().data(page.table.model().index(0, 4), Qt.ItemDataRole.DisplayRole)
        self.assertNotIn("T", created_at)
        self.assertNotIn("+00:00", created_at)
        self.assertEqual(page.table.model().data(page.table.model().index(0, 6), Qt.ItemDataRole.DisplayRole), "<img src=x>")
        self.assertEqual(page.table.table.editTriggers(), page.table.table.EditTrigger.NoEditTriggers)

    def test_role_and_status_filters_are_sent_to_service(self) -> None:
        service = ListingUserService([
            FakeUser(1, "admin", Role.ADMIN.value, True),
            FakeUser(2, "operator", Role.OPERATOR.value, False),
        ])
        page = UserManagementPage(service, self.admin_session())
        page.role_filter.setCurrentIndex(page.role_filter.findData(Role.OPERATOR.value))
        page.status_filter.setCurrentIndex(page.status_filter.findData(False))

        page.apply_filters()

        self.assertEqual(service.queries[-1]["role"], Role.OPERATOR.value)
        self.assertEqual(service.queries[-1]["is_active"], False)
        self.assertEqual(page.table.model().data(page.table.model().index(0, 1), Qt.ItemDataRole.DisplayRole), "operator")

    def test_operator_entry_hides_actions_and_shows_permission_hint(self) -> None:
        page = UserManagementPage(ListingUserService([]), self.operator_session())

        self.assertTrue(page.permission_hint.isVisible() or not page.permission_hint.isHidden())
        self.assertFalse(page.new_button.isVisible())

        page.open_create_dialog()

        self.assertIn("无权限", page.permission_hint.message_label.text())
        self.assertIn("已记录", page.permission_hint.message_label.text())

    def test_create_second_admin_failure_is_controlled_and_password_hidden(self) -> None:
        service = CreateSecondAdminService()
        dialog = UserEditorDialog(service, self.admin_session())
        dialog.username_edit.setText("admin2")
        dialog.role_combo.setCurrentIndex(dialog.role_combo.findData(Role.ADMIN.value))
        dialog.password_edit.setText("AdminPass123")
        dialog.confirm_password_edit.setText("AdminPass123")

        dialog.submit()

        self.assertIn("管理员账号只能有一个", dialog.error_hint.text())
        self.assertNotIn("secret123", dialog.error_hint.text())
        self.assertEqual(dialog.password_edit.echoMode(), QLineEdit.EchoMode.Password)
        self.assertEqual(dialog.confirm_password_edit.echoMode(), QLineEdit.EchoMode.Password)
        self.assertNotIn("AdminPass123", dialog.error_hint.text())

    def test_disable_only_admin_uses_danger_confirm_then_shows_controlled_failure(self) -> None:
        service = ListingUserService([FakeUser(1, "admin", Role.ADMIN.value, True)])
        service.disable_result = ServiceResult.fail(
            code=int(ErrorCode.CONFLICT),
            message="不能禁用或删除唯一管理员 db=E:\\secret\\app.sqlite3",
        )
        confirmed: list[str] = []
        page = UserManagementPage(
            service,
            self.admin_session(),
            confirm_danger=lambda parent, user: confirmed.append(user.username) or True,
        )
        page.load_users()
        page.table.table.selectRow(0)
        self.app.processEvents()

        page.set_selected_active(False)

        self.assertEqual(confirmed, ["admin"])
        self.assertEqual(service.disable_calls, [1])
        self.assertIn("唯一管理员", page.table._error_message.text())
        self.assertNotIn("E:\\secret", page.table._error_message.text())

    def test_password_mismatch_and_echo_do_not_expose_password(self) -> None:
        dialog = UserEditorDialog(CreateSecondAdminService(), self.admin_session())
        dialog.username_edit.setText("operator2")
        dialog.password_edit.setText("OperatorPass123")
        dialog.confirm_password_edit.setText("DifferentPass123")

        self.assertIn("不一致", dialog.error_hint.text())
        self.assertNotIn("OperatorPass123", dialog.error_hint.text())
        self.assertEqual(dialog.password_edit.echoMode(), QLineEdit.EchoMode.Password)
        self.assertEqual(dialog.confirm_password_edit.echoMode(), QLineEdit.EchoMode.Password)


if __name__ == "__main__":
    unittest.main()
