from __future__ import annotations

import os
import unittest
from dataclasses import dataclass

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QWidget

from app.services.models import AcquisitionState, AcquisitionStatus, DeviceStatus, ServiceResult
from app.services.permissions import Permission
from app.ui.shell.alert_bar import GlobalAlertBar
from app.ui.shell.main_window import MainWindowShell
from app.ui.shell.navigation import ShellNavigation
from app.ui.shell.page_registry import PageEntry, PageFactoryContext, PageRegistry, build_default_page_registry
from app.ui.shell.status_bar import API_PORT_IN_USE_MESSAGE, ShellStatusBar
from app.ui.settings.users_page import UserManagementPage
from app.ui.theme import AppTheme


@dataclass(frozen=True, slots=True)
class FakeSession:
    session_id: str = "s1"
    user_id: int = 1
    username: str = "operator"
    role: str = "operator"
    permissions: tuple[str, ...] = ("monitor.view",)
    permission_version: int = 1
    login_at: str = "now"


class FakeAuthService:
    def __init__(self, allowed: bool) -> None:
        self.allowed = allowed
        self.calls = 0
        self.logout_calls = 0

    def require_app_exit(self, session: object) -> ServiceResult[None]:
        self.calls += 1
        if self.allowed:
            return ServiceResult.ok(None)
        return ServiceResult.fail(403, "当前账号无权限执行此操作，已记录权限失败事件。")

    def logout(self, session: object) -> None:
        self.logout_calls += 1


class FakeLicenseService:
    def __init__(self, active: bool = True) -> None:
        self.active = active

    def get_license_status(self) -> object:
        return type("License", (), {"is_active": self.active, "message": "ok"})()


class FakeStateStore:
    def __init__(self) -> None:
        self.values: dict[str, object] = {}

    def get_value(self, key: str, default: object = None) -> object:
        return self.values.get(key, default)

    def set_value(self, key: str, value: object) -> None:
        self.values[key] = value


class FakeUserService:
    def __init__(self) -> None:
        self.calls = 0

    def list_users(self, session: object, *, role: str | None, is_active: bool | None, pagination: object) -> ServiceResult[tuple[list[object], int]]:
        self.calls += 1
        return ServiceResult.ok(([], 0))


class DesktopShellTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        AppTheme().apply_to(cls.app)

    def tearDown(self) -> None:
        self.app.processEvents()

    def test_navigation_marks_restricted_entry_locked_and_emits_permission_message(self) -> None:
        entries = (
            PageEntry("monitor", "实时监控", "monitor.view", lambda ctx: QWidget()),
            PageEntry("settings", "系统配置", "system.settings", lambda ctx: QWidget()),
        )
        nav = ShellNavigation(entries, FakeSession())
        denied: list[str] = []
        nav.permissionDenied.connect(denied.append)

        restricted = nav.button_for_key("settings")
        self.assertIsNotNone(restricted)
        self.assertEqual(restricted.property("allowed"), "false")
        self.assertIn("[锁]", restricted.text())
        restricted.click()

        self.assertEqual(denied, ["settings"])

    def test_top_navigation_pages_entries_six_at_a_time(self) -> None:
        entries = tuple(
            PageEntry(f"page_{index}", f"页面{index}", "monitor.view", lambda ctx: QWidget())
            for index in range(8)
        )
        nav = ShellNavigation(entries, FakeSession(), page_size=6)

        self.assertEqual(nav.page_count(), 2)
        self.assertEqual(nav.visible_button_texts(), ("页面0", "页面1", "页面2", "页面3", "页面4", "页面5"))

        nav.next_page()

        self.assertEqual(nav.current_page(), 1)
        self.assertEqual(nav.visible_button_texts(), ("页面6", "页面7"))

    def test_default_registry_exposes_user_management_page(self) -> None:
        registry = build_default_page_registry()
        entry = registry.get("users")
        user_service = FakeUserService()

        page = registry.create(
            "users",
            PageFactoryContext(
                session=FakeSession(role="admin", permissions=("*",)),
                services={"users": user_service},
            ),
        )

        self.assertEqual(entry.title, "账号管理")
        self.assertEqual(entry.permission, Permission.USER_MANAGE.value)
        self.assertIsInstance(page, UserManagementPage)
        self.assertEqual(user_service.calls, 1)

    def test_main_window_lazy_loads_pages_and_opens_bigscreen_as_window(self) -> None:
        created: list[str] = []

        def page_factory(ctx: PageFactoryContext) -> QWidget:
            created.append("monitor")
            widget = QLabel("monitor-page")
            widget.setObjectName("MonitorFakePage")
            return widget

        def bigscreen_factory(ctx: PageFactoryContext) -> QWidget:
            created.append("bigscreen")
            widget = QWidget()
            widget.setObjectName("BigscreenFakeWindow")
            return widget

        registry = PageRegistry(
            (
                PageEntry("monitor", "实时监控", "monitor.view", page_factory),
                PageEntry("bigscreen", "大屏展示", "monitor.view", bigscreen_factory, "window"),
            )
        )
        shell = MainWindowShell(
            FakeSession(),
            registry,
            auth_service=FakeAuthService(True),
            license_service=FakeLicenseService(),
        )

        self.assertEqual(created, ["monitor"])
        first_index = shell.stack.currentIndex()
        shell.open_entry("monitor")
        self.assertEqual(shell.stack.currentIndex(), first_index)
        self.assertEqual(created, ["monitor"])

        shell.open_entry("bigscreen")
        self.assertEqual(created, ["monitor", "bigscreen"])
        self.assertEqual(shell.stack.currentIndex(), first_index)
        self.assertEqual(len(shell._window_refs), 1)
        shell.close()

    def test_status_bar_renders_safe_state_and_api_port_conflict_text(self) -> None:
        state_store = FakeStateStore()
        state_store.set_value(
            "acquisition.status",
            AcquisitionState(status=AcquisitionStatus.RECONNECTING, message="第 2 次"),
        )
        status_bar = ShellStatusBar(FakeSession(), FakeLicenseService(), state_store, clock_interval_ms=60_000)
        status_bar.set_api_status("error", "sqlite3.OperationalError at C:/secret.db")
        self.assertNotIn("C:/secret", status_bar.api_label.text())

        status_bar.set_api_status("error", "address already in use")
        self.assertEqual(status_bar.api_label.text(), API_PORT_IN_USE_MESSAGE)
        self.assertIn("重连中", status_bar.acquisition_label.text())

    def test_global_alert_bar_only_pins_high_risk_active_alerts(self) -> None:
        state_store = FakeStateStore()
        bar = GlobalAlertBar(state_store)
        self.assertEqual(bar.property("active"), "false")

        state_store.set_value("alarms.active", ({"status": DeviceStatus.ALARM_HIGH, "name": "A区探头", "concentration": 42, "unit": "%LEL"},))
        bar.refresh()
        self.assertEqual(bar.property("active"), "true")
        self.assertIn("高报", bar.label.text())

        state_store.set_value("alarms.active", ({"status": DeviceStatus.OFFLINE, "name": "离线探头"},))
        bar.refresh()
        self.assertEqual(bar.property("active"), "false")

    def test_close_event_allows_operator_exit_without_permission_gate(self) -> None:
        registry = PageRegistry((PageEntry("monitor", "实时监控", "monitor.view", lambda ctx: QWidget()),))
        denied_auth = FakeAuthService(False)
        denied_shell = MainWindowShell(FakeSession(), registry, auth_service=denied_auth, license_service=FakeLicenseService())
        denied_shell.show()
        denied_shell.close()
        self.app.processEvents()
        self.assertEqual(denied_auth.calls, 0)
        self.assertEqual(denied_auth.logout_calls, 1)
        self.assertFalse(denied_shell.isVisible())

    def test_logout_button_emits_switch_account_and_permission_message_auto_hides(self) -> None:
        registry = PageRegistry(
            (
                PageEntry("monitor", "实时监控", "monitor.view", lambda ctx: QWidget()),
                PageEntry("settings", "系统配置", "system.settings", lambda ctx: QWidget()),
            )
        )
        auth = FakeAuthService(False)
        shell = MainWindowShell(
            FakeSession(),
            registry,
            auth_service=auth,
            license_service=FakeLicenseService(),
            message_timeout_ms=20,
        )
        logged_out: list[object] = []
        shell.logoutRequested.connect(logged_out.append)
        shell.show()
        self.app.processEvents()

        shell.navigation.request_key("settings")
        self.assertTrue(shell.message_bar.isVisible())
        self.assertIn("无权限", shell.message_bar.text())
        QTest.qWait(40)
        self.app.processEvents()
        self.assertFalse(shell.message_bar.isVisible())

        shell.logout_button.click()
        self.app.processEvents()
        self.assertEqual(logged_out, [shell.session])
        self.assertEqual(auth.logout_calls, 1)
        self.assertFalse(shell.isVisible())


if __name__ == "__main__":
    unittest.main()
