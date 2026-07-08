from __future__ import annotations

# ruff: noqa: E402

import os
import unittest
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app.config.defaults import ApiConfig
from app.services.auth_service import Session
from app.services.errors import ErrorCode
from app.services.models import ServiceResult
from app.services.permissions import Role
from app.ui.settings.api_docs_panel import ApiDocsPanel
from app.ui.settings.local_api_page import (
    API_PORT_IN_USE_MESSAGE,
    LocalApiSettingsCommand,
    LocalApiSettingsPage,
    READONLY_BOUNDARY_TEXT,
)
from app.ui.theme import AppTheme


class FakeApiHost:
    def __init__(self, *, running: bool = False, alert: str | None = None, start_result: object | None = None) -> None:
        self._running = running
        self._alert = alert
        self.start_result = start_result
        self.start_calls: list[tuple[object, ...]] = []
        self.stop_calls = 0
        self.bind_address = "127.0.0.1"
        self.port = 8765

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def last_alert(self) -> str | None:
        return self._alert

    def start(self, *args: object) -> object:
        self.start_calls.append(args)
        if self.start_result is not None:
            if bool(getattr(self.start_result, "started", False)):
                self._running = True
                self._alert = None
            return self.start_result
        self._running = True
        self._alert = None
        return SimpleNamespace(started=True, message="")

    def stop(self, *args: object) -> None:
        self.stop_calls += 1
        self._running = False
        self._alert = None


class FakeApiConfigFacade:
    def __init__(self, config: ApiConfig | None = None) -> None:
        self.config = config or ApiConfig(enabled=True, bind_address="127.0.0.1", port=8765)
        self.save_calls: list[tuple[object, LocalApiSettingsCommand]] = []
        self.save_result: ServiceResult[ApiConfig] | None = None

    def get_api_config(self) -> ServiceResult[ApiConfig]:
        return ServiceResult.ok(self.config)

    def save_api_config(self, session: object, command: LocalApiSettingsCommand) -> ServiceResult[ApiConfig]:
        self.save_calls.append((session, command))
        if self.save_result is not None:
            return self.save_result
        self.config = ApiConfig(enabled=command.enabled, bind_address=command.bind_address, port=command.port)
        return ServiceResult.ok(self.config)


class UiLocalApiPageTests(unittest.TestCase):
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
            ("monitor.view",),
            1,
            "2026-01-01T00:00:00+00:00",
        )

    def test_page_initializes_status_config_and_docs(self) -> None:
        host = FakeApiHost(running=False)
        facade = FakeApiConfigFacade(ApiConfig(enabled=True, bind_address="127.0.0.1", port=9000))
        page = LocalApiSettingsPage(host, facade, session=self.admin_session())

        self.assertEqual(page.status_card.property("status"), "stopped")
        self.assertIn("127.0.0.1:9000", page.endpoint_label.text())
        self.assertEqual(page.desktop_status_label.text(), "桌面主程序状态：正常")
        self.assertIsInstance(page.docs_panel, ApiDocsPanel)
        self.assertIn(READONLY_BOUNDARY_TEXT, page.readonly_notice.text())
        self.assertIn("[待确认]", page.pending_notice.text())

    def test_running_stopped_error_and_starting_states(self) -> None:
        running_page = LocalApiSettingsPage(FakeApiHost(running=True), FakeApiConfigFacade(), session=self.admin_session())
        self.assertEqual(running_page.status_card.property("status"), "running")
        self.assertIn("已运行", running_page.status_label.text())
        self.assertIn("127.0.0.1:8765", running_page.endpoint_label.text())

        stopped_page = LocalApiSettingsPage(FakeApiHost(running=False), FakeApiConfigFacade(), session=self.admin_session())
        self.assertEqual(stopped_page.status_card.property("status"), "stopped")
        self.assertIn("已停止", stopped_page.status_label.text())

        busy_page = LocalApiSettingsPage(
            FakeApiHost(running=False, alert="OSError: address already in use"),
            FakeApiConfigFacade(),
            session=self.admin_session(),
        )
        self.assertEqual(busy_page.status_card.property("status"), "error")
        self.assertIn(API_PORT_IN_USE_MESSAGE, busy_page.last_error_label.text())
        self.assertEqual(busy_page.desktop_status_label.text(), "桌面主程序状态：正常")

        stopped_page.set_starting(True)
        self.assertFalse(stopped_page.start_button.isEnabled())
        self.assertEqual(stopped_page.start_button.text(), "启动中...")

    def test_start_stop_and_save_call_only_injected_facades(self) -> None:
        host = FakeApiHost(running=False)
        facade = FakeApiConfigFacade(ApiConfig(enabled=True, bind_address="127.0.0.1", port=8765))
        page = LocalApiSettingsPage(host, facade, session=self.admin_session())
        page.port_input.setValue(9010)

        page.save_config()
        self.assertEqual(facade.save_calls[-1][1].port, 9010)
        self.assertEqual(facade.config.port, 9010)

        page.start_api()
        self.assertTrue(host.start_calls)
        self.assertEqual(page.status_card.property("status"), "running")
        self.assertEqual(facade.save_calls[-1][1].bind_address, "127.0.0.1")

        page.stop_api()
        self.assertEqual(host.stop_calls, 1)
        self.assertEqual(page.status_card.property("status"), "stopped")

    def test_port_validation_and_busy_fixed_message(self) -> None:
        host = FakeApiHost(start_result=SimpleNamespace(started=False, message=API_PORT_IN_USE_MESSAGE))
        facade = FakeApiConfigFacade(ApiConfig(enabled=True, bind_address="127.0.0.1", port=8765))
        page = LocalApiSettingsPage(host, facade, session=self.admin_session())

        page.port_input.setValue(0)
        page.start_api()
        self.assertEqual(page.port_input.property("validation"), "error")
        self.assertIn("端口必须为 1-65535", page.validation_hint.text())
        self.assertFalse(host.start_calls)

        page.port_input.setValue(8765)
        page.start_api()
        self.assertIn(API_PORT_IN_USE_MESSAGE, page.last_error_label.text())
        self.assertIn(API_PORT_IN_USE_MESSAGE, page.error_banner.label.text())
        self.assertEqual(page.desktop_status_label.text(), "桌面主程序状态：正常")

    def test_no_permission_is_readonly_and_does_not_execute_actions(self) -> None:
        host = FakeApiHost(running=True)
        facade = FakeApiConfigFacade()
        page = LocalApiSettingsPage(host, facade, session=self.operator_session())

        self.assertFalse(page.permission_hint.isHidden())
        self.assertFalse(page.enabled_check.isEnabled())
        self.assertTrue(page.port_input.isReadOnly())
        self.assertFalse(page.save_button.isEnabled())
        self.assertFalse(page.start_button.isEnabled())
        self.assertFalse(page.stop_button.isEnabled())

        page.save_config()
        page.start_api()
        page.stop_api()
        self.assertFalse(facade.save_calls)
        self.assertFalse(host.start_calls)
        self.assertEqual(host.stop_calls, 0)
        self.assertIn("当前账号无权限执行此操作", page.error_banner.label.text())

    def test_readonly_docs_click_does_not_call_host_or_network(self) -> None:
        host = FakeApiHost(running=False)
        page = LocalApiSettingsPage(host, FakeApiConfigFacade(), session=self.admin_session())
        initial_start_calls = len(host.start_calls)
        initial_stop_calls = host.stop_calls

        page.docs_panel.endpoint_list.setCurrentRow(3)

        self.assertEqual(len(host.start_calls), initial_start_calls)
        self.assertEqual(host.stop_calls, initial_stop_calls)
        self.assertIn("GET /api/v1/alarms/active", page.docs_panel.detail_title.text())
        self.assertIn("success", page.docs_panel.example_preview.toPlainText())
        self.assertIn("code", page.docs_panel.example_preview.toPlainText())
        self.assertIn("message", page.docs_panel.example_preview.toPlainText())
        self.assertIn("data", page.docs_panel.example_preview.toPlainText())

    def test_examples_have_no_real_sensitive_values_and_errors_are_redacted(self) -> None:
        docs = ApiDocsPanel()
        for row in range(docs.endpoint_list.count()):
            docs.endpoint_list.setCurrentRow(row)
            text = docs.example_preview.toPlainText().lower()
            self.assertIn('"success"', text)
            self.assertNotIn("password", text)
            self.assertNotIn("secret", text)
            self.assertNotIn("token", text)
            self.assertNotIn("c:\\", text)
            self.assertNotIn("machine", text)

        facade = FakeApiConfigFacade()
        facade.save_result = ServiceResult.fail(
            code=int(ErrorCode.INTERNAL_ERROR),
            message=r"sqlite3.OperationalError: SELECT secret FROM users at C:\internal\app.sqlite3 token=abc123",
        )
        page = LocalApiSettingsPage(FakeApiHost(), facade, session=self.admin_session())
        page.save_config()

        shown = page.error_banner.label.text().lower()
        self.assertIn("本地 api 设置保存失败", shown)
        self.assertNotIn("select secret", shown)
        self.assertNotIn("c:\\internal", shown)
        self.assertNotIn("abc123", shown)


if __name__ == "__main__":
    unittest.main()
