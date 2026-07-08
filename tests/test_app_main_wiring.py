from __future__ import annotations

# ruff: noqa: E402

import json
import tempfile
import unittest

from tests.qt_test_harness import configure_qt_test_environment

configure_qt_test_environment()

from PySide6.QtWidgets import QApplication

from app.core.bootstrap import create_app_context
from app.db.repositories.user_repository import UserRepository
from app.db.unit_of_work import UnitOfWork
from app.device.debug.debug_service import DebugReadCommand
from app.main import _assemble_services, _create_shell
from app.services.auth_service import Session, hash_password
from app.ui.bigscreen.bigscreen_window import BigscreenWindow
from app.ui.map.map_page import MapMonitoringPage
from app.ui.monitor.monitor_page import MonitoringPage
from app.ui.settings.device_debug_page import DeviceDebugPage
from app.ui.settings.local_api_page import LocalApiSettingsCommand, LocalApiSettingsPage
from app.ui.theme import AppTheme


class AppMainWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        AppTheme().apply_to(cls.app)

    def tearDown(self) -> None:
        self.app.processEvents()

    def test_default_shell_monitor_and_map_use_real_view_models_from_main_services(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            context = create_app_context(temp_dir)
            try:
                _assemble_services(context)
                shell = _create_shell(context, _admin_session())
                try:
                    self.assertTrue(shell.open_entry("monitor"))
                    monitor = shell.stack.currentWidget()
                    self.assertIsInstance(monitor, MonitoringPage)
                    self.assertIs(monitor.view_model._read_service, context.containers.services["monitoring_read"])
                    self.assertIs(monitor.view_model._state_store, context.state_store)
                    self.assertIs(monitor.view_model._event_bus, context.event_bus)

                    self.assertTrue(shell.open_entry("map"))
                    map_page = shell.stack.currentWidget()
                    self.assertIsInstance(map_page, MapMonitoringPage)
                    self.assertIs(map_page.view_model._service, context.containers.services["map_runtime"])
                    self.assertIs(map_page.view_model._state_store, context.state_store)
                    self.assertIs(map_page.view_model._event_bus, context.event_bus)
                finally:
                    shell.close()
                    shell.deleteLater()
                    self.app.processEvents()
            finally:
                context.shutdown()

    def test_default_shell_registry_backend_entries_are_satisfied_by_main_container(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            context = create_app_context(temp_dir)
            try:
                _assemble_services(context)
                services = context.containers.services
                api = context.containers.api
                for key in ("device_config", "device_debug", "device_debug_executor", "bigscreen"):
                    self.assertIn(key, services)
                    self.assertIsNotNone(services[key])
                for key in ("host", "config_facade", "read_service"):
                    self.assertIn(key, api)
                    self.assertIsNotNone(api[key])

                shell = _create_shell(context, _admin_session())
                try:
                    self.assertTrue(shell.open_entry("debug"))
                    debug_page = shell.stack.currentWidget()
                    self.assertIsInstance(debug_page, DeviceDebugPage)
                    self.assertIs(debug_page._service, services["device_debug"])
                    self.assertIsNotNone(debug_page._send_executor)

                    self.assertTrue(shell.open_entry("api"))
                    api_page = shell.stack.currentWidget()
                    self.assertIsInstance(api_page, LocalApiSettingsPage)
                    self.assertIs(api_page._host, api["host"])
                    self.assertIs(api_page._config_facade, api["config_facade"])

                    self.assertTrue(shell.open_entry("bigscreen"))
                    self.assertTrue(shell._window_refs)
                    self.assertIsInstance(shell._window_refs[-1], BigscreenWindow)
                    for window in tuple(shell._window_refs):
                        window.close()
                        window.deleteLater()
                finally:
                    shell.close()
                    shell.deleteLater()
                    self.app.processEvents()
            finally:
                context.shutdown()

    def test_local_api_config_facade_persists_and_refreshes_host_and_read_service(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            context = create_app_context(temp_dir)
            try:
                _assemble_services(context)
                session = _seed_and_login_admin(context)
                facade = context.containers.api["config_facade"]
                host = context.containers.api["host"]
                read_service = context.containers.api["read_service"]

                result = facade.save_api_config(
                    session,
                    LocalApiSettingsCommand(enabled=True, bind_address="127.0.0.1", port=9091),
                )

                self.assertTrue(result.success)
                self.assertTrue(context.config.api.enabled)
                self.assertEqual(context.config.api.port, 9091)
                self.assertEqual(host.port, 9091)
                self.assertTrue(read_service.health().data.api_enabled)
                config_text = context.paths.config_file.read_text(encoding="utf-8")
                saved = json.loads(config_text)
                self.assertEqual(saved["api"]["bind_address"], "127.0.0.1")
                self.assertEqual(saved["api"]["port"], 9091)
                self.assertFalse(saved["api"]["cors_enabled"])
            finally:
                context.shutdown()

    def test_local_api_config_facade_rejects_lan_bind_without_polluting_runtime_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            context = create_app_context(temp_dir)
            try:
                _assemble_services(context)
                session = _seed_and_login_admin(context)
                facade = context.containers.api["config_facade"]
                host = context.containers.api["host"]
                read_service = context.containers.api["read_service"]

                rejected = facade.save_api_config(
                    session,
                    LocalApiSettingsCommand(enabled=True, bind_address="0.0.0.0", port=9092),
                )

                self.assertFalse(rejected.success)
                self.assertEqual(rejected.code, 400)
                self.assertEqual(context.config.api.bind_address, "127.0.0.1")
                self.assertFalse(context.config.api.enabled)
                self.assertEqual(host.bind_address, "127.0.0.1")
                self.assertFalse(read_service.health().data.api_enabled)
                config_text = context.paths.config_file.read_text(encoding="utf-8")
                saved = json.loads(config_text)
                self.assertFalse(saved["api"]["enabled"])
                self.assertEqual(saved["api"]["bind_address"], "127.0.0.1")
                self.assertFalse(saved["api"]["cors_enabled"])
            finally:
                context.shutdown()

    def test_device_debug_executor_keeps_read_only_and_unconfigured_port_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            context = create_app_context(temp_dir)
            try:
                _assemble_services(context)
                session = _seed_and_login_admin(context)
                executor = context.containers.services["device_debug_executor"]

                write_attempt = executor.send_debug_read(
                    session,
                    DebugReadCommand(
                        mode="protocol_1",
                        source_type="probe",
                        port_id=999,
                        unit_address=1,
                        start_register=0,
                        register_count=1,
                        function_code=0x06,
                    ),
                )
                self.assertFalse(write_attempt.success)
                self.assertEqual(write_attempt.code, 400)
                self.assertIn("仅允许读 03", write_attempt.message)

                missing_port = executor.send_debug_read(
                    session,
                    DebugReadCommand(
                        mode="protocol_2",
                        source_type="probe",
                        port_id=999,
                        unit_address=1,
                        start_register=0,
                        register_count=4,
                    ),
                )
                self.assertTrue(missing_port.success)
                self.assertEqual(missing_port.message, "调试请求未发送")
                self.assertEqual(missing_port.data.error_code, "port_not_configured")
                self.assertEqual(missing_port.data.validation_message, "调试端口未配置或未启用")
                self.assertNotIn("Traceback", missing_port.data.validation_message)
                self.assertEqual(missing_port.data.response_hex, "")
            finally:
                context.shutdown()


def _admin_session() -> Session:
    return Session(
        session_id="admin-session",
        user_id=1,
        username="admin",
        role="admin",
        permissions=("*",),
        permission_version=1,
        login_at="2026-01-01T00:00:00+00:00",
    )


def _seed_and_login_admin(context) -> Session:
    password_hash, password_salt = hash_password("AdminPass123")
    with UnitOfWork(context.db) as uow:
        user = UserRepository(uow).find_active_by_username("admin")
        if user is None:
            UserRepository(uow).create_user(
                username="admin",
                password_hash=password_hash,
                password_salt=password_salt,
                role="admin",
            )
        else:
            UserRepository(uow).update_user(
                int(user["id"]),
                password_hash=password_hash,
                password_salt=password_salt,
                must_change_password=False,
            )
        uow.commit()
    result = context.containers.services["auth"].login("admin", "AdminPass123")
    if not result.success or result.data is None:
        raise AssertionError("admin login failed")
    return result.data


if __name__ == "__main__":
    unittest.main()
