from __future__ import annotations

import os
import unittest
from dataclasses import dataclass

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel, QWidget

from app.services.models import AcquisitionState, AcquisitionStatus, DeviceStatus, ServiceResult
from app.ui.shell.alert_bar import GlobalAlertBar
from app.ui.shell.main_window import MainWindowShell
from app.ui.shell.navigation import ALLOWED_ROLE, KEY_ROLE, ShellNavigation
from app.ui.shell.page_registry import PageEntry, PageFactoryContext, PageRegistry
from app.ui.shell.status_bar import API_PORT_IN_USE_MESSAGE, ShellStatusBar
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

    def require_app_exit(self, session: object) -> ServiceResult[None]:
        self.calls += 1
        if self.allowed:
            return ServiceResult.ok(None)
        return ServiceResult.fail(403, "当前账号无权限执行此操作，已记录权限失败事件。")


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

        restricted = nav.item(1)
        self.assertFalse(bool(restricted.data(ALLOWED_ROLE)))
        self.assertIn("[锁]", restricted.text())
        nav.setCurrentRow(1)

        self.assertEqual(denied, ["settings"])

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

    def test_close_event_requires_auth_service_exit_permission(self) -> None:
        registry = PageRegistry((PageEntry("monitor", "实时监控", "monitor.view", lambda ctx: QWidget()),))
        denied_auth = FakeAuthService(False)
        denied_shell = MainWindowShell(FakeSession(), registry, auth_service=denied_auth, license_service=FakeLicenseService())
        denied_shell.show()
        denied_shell.close()
        self.app.processEvents()
        self.assertEqual(denied_auth.calls, 1)
        self.assertTrue(denied_shell.isVisible())
        self.assertIn("无权限", denied_shell.message_bar.text())

        allowed_auth = FakeAuthService(True)
        allowed_shell = MainWindowShell(FakeSession(), registry, auth_service=allowed_auth, license_service=FakeLicenseService())
        allowed_shell.show()
        allowed_shell.close()
        self.app.processEvents()
        self.assertEqual(allowed_auth.calls, 1)
        self.assertFalse(allowed_shell.isVisible())
        denied_shell._closing_allowed = True
        denied_shell.close()


if __name__ == "__main__":
    unittest.main()
