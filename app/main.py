from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryFile

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.host import LocalApiHost
from app.core.bootstrap import AppContext, create_app_context
from app.core.logging import Redactor, user_safe_error
from app.core.paths import PathSetupError
from app.device.debug.debug_service import DeviceDebugService
from app.services.api_read_service import ApiReadService
from app.services.auth_service import AuthService, SessionStore
from app.services.backup_service import BackupService
from app.services.bigscreen_service import BigscreenService
from app.services.chart_service import ChartService
from app.services.device_config_service import DeviceConfigService
from app.services.device_debug_executor import DeviceDebugExecutor
from app.services.export_service import ExportService
from app.services.file_validation import FileValidator
from app.services.import_export import ImportExportService
from app.services.license_service import LicenseService
from app.services.linkage_service import LinkageService
from app.services.local_api_config_service import LocalApiConfigFacade
from app.services.maintenance_service import MaintenanceService
from app.services.map_config_service import MapConfigService
from app.services.map_service import MapService
from app.services.monitoring_read_service import MonitoringReadService
from app.services.record_service import RecordService
from app.services.user_service import UserService

QT_FONT_LOG_RULE = "qt.qpa.fonts=false"


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    context: AppContext | None = None
    try:
        context = create_app_context(data_dir=args.data_dir)
        _assemble_services(context)
        if args.smoke_shell:
            return _run_shell_smoke(context)
        if args.platform_smoke:
            print("平台运行底座初始化完成。")
            return 0
        return _run_gui(context)
    except PathSetupError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception as exc:
        print(user_safe_error(exc), file=sys.stderr)
        return 1
    finally:
        if context is not None:
            context.shutdown()


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="气体安全报警监控系统")
    parser.add_argument("--data-dir", default=None, help="指定运行数据目录，用于测试或本机隔离启动。")
    parser.add_argument("--platform-smoke", action="store_true", help="仅初始化平台运行底座后退出。")
    parser.add_argument("--smoke-shell", action="store_true", help="创建 Qt Shell 后立即退出，用于无交互烟测。")
    return parser.parse_args(argv)


def _assemble_services(context: AppContext) -> None:
    session_store = SessionStore()
    auth_service = AuthService(context.db, session_store=session_store)
    license_service = LicenseService(context.db, session_store=session_store)
    validator = FileValidator(data_root=context.paths.data_dir)
    import_export = ImportExportService(validator)
    export_service = ExportService()

    services = context.containers.services
    monitoring_read = MonitoringReadService(context.db)
    map_config = MapConfigService(
        context.db,
        session_store,
        validator=validator,
        maps_dir=context.paths.maps_dir,
    )
    map_runtime = MapService(
        context.db,
        session_store,
        validator=validator,
        maps_dir=context.paths.maps_dir,
        state_store=context.state_store,
        monitoring_read_service=monitoring_read,
    )
    api_read = ApiReadService(context.db, context.state_store, context.config)
    api_host = LocalApiHost(api_read, context.config)
    api_config_facade = LocalApiConfigFacade(
        database=context.db,
        session_store=session_store,
        config=context.config,
        config_file=context.paths.config_file,
        on_config_changed=lambda config: setattr(context, "config", config),
        api_host=api_host,
        read_service=api_read,
    )
    device_debug = DeviceDebugService(database=context.db, session_store=session_store)
    device_config = DeviceConfigService(
        context.db,
        session_store,
        import_export=import_export,
    )
    device_debug_executor = DeviceDebugExecutor(
        debug_service=device_debug,
        device_config_service=device_config,
    )

    context.containers.api.update(
        {
            "host": api_host,
            "config_facade": api_config_facade,
            "read_service": api_read,
        }
    )
    services.update(
        {
            "auth": auth_service,
            "license": license_service,
            "users": UserService(context.db, session_store),
            "monitoring_read": monitoring_read,
            "chart": ChartService(context.db, context.state_store),
            "records": RecordService(context.db, session_store=session_store, export_service=export_service),
            "device_config": device_config,
            "device_debug": device_debug,
            "device_debug_executor": device_debug_executor,
            "map_config": map_config,
            "map_runtime": map_runtime,
            "backup": BackupService(
                context.db,
                session_store,
                paths=context.paths,
                runtime_locks=context.runtime_locks,
                scheduler=context.scheduler,
                event_bus=context.event_bus,
            ),
            "maintenance": MaintenanceService(
                context.db,
                session_store=session_store,
                scheduler=context.scheduler,
                event_bus=context.event_bus,
            ),
            "linkage": LinkageService(context.db, session_store=session_store),
            "bigscreen": BigscreenService(context.db, context.state_store),
            "export": export_service,
        }
    )


def _run_gui(context: AppContext) -> int:
    from PySide6.QtWidgets import QApplication, QMessageBox

    from app.ui.login.change_password_dialog import ChangePasswordDialog
    from app.ui.login.license_dialog import LicenseDialog
    from app.ui.login.login_window import LoginWindow
    from app.ui.theme import AppTheme

    app = QApplication.instance() or QApplication([sys.argv[0]])
    AppTheme().apply_to(app)
    auth_service = context.containers.services["auth"]
    license_service = context.containers.services["license"]
    login = LoginWindow(auth_service, license_service)
    windows: dict[str, object] = {"login": login}

    def open_license() -> None:
        dialog = LicenseDialog(license_service, login)
        dialog.activated.connect(lambda _status: login.refresh_license_status())
        dialog.exec()

    def open_change_password(username: str) -> None:
        dialog = ChangePasswordDialog(auth_service, None, login, target_username=username or None)
        dialog.exec()

    def show_initial_admin_pending() -> None:
        QMessageBox.information(login, "初始管理员", "初始管理员创建流程待确认，请联系管理员或供应商。")

    def enter_shell(session: object) -> None:
        shell = _create_shell(context, session)
        windows["shell"] = shell
        shell.destroyed.connect(lambda _obj=None: windows.pop("shell", None))
        shell.show()
        login.close()

    login.licenseRequested.connect(open_license)
    login.changePasswordRequested.connect(open_change_password)
    login.initialAdminRequested.connect(show_initial_admin_pending)
    login.loggedIn.connect(enter_shell)
    login.show()
    return int(app.exec())


def _run_shell_smoke(context: AppContext) -> int:
    # The smoke path creates widgets without entering the blocking GUI event loop,
    # making CI/offscreen startup failures reproducible without manual clicks.
    _prepare_qt_smoke_environment()
    with _sanitized_smoke_stderr():
        from app.services.auth_service import Session
        from PySide6.QtWidgets import QApplication

        from app.ui.theme import AppTheme

        app = QApplication.instance() or QApplication(["gas-alarm-shell-smoke"])
        AppTheme().apply_to(app)
        session = Session(
            session_id="smoke",
            user_id=1,
            username="smoke-admin",
            role="admin",
            permissions=("*",),
            permission_version=1,
            login_at="smoke",
        )
        shell = _create_shell(context, session)
        for entry_key in ("monitor", "map", "debug", "api", "bigscreen"):
            if not shell.open_entry(entry_key):
                raise RuntimeError(f"shell smoke failed to open {entry_key}")
        for window in tuple(getattr(shell, "_window_refs", ())):
            window.close()
            window.deleteLater()
        shell.deleteLater()
        app.processEvents()
    print("Shell smoke 初始化完成。")
    return 0


def _prepare_qt_smoke_environment() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    existing_rules = os.environ.get("QT_LOGGING_RULES", "").strip()
    if not existing_rules:
        os.environ["QT_LOGGING_RULES"] = QT_FONT_LOG_RULE
    elif QT_FONT_LOG_RULE not in {rule.strip() for rule in existing_rules.split(";")}:
        os.environ["QT_LOGGING_RULES"] = f"{existing_rules};{QT_FONT_LOG_RULE}"


@contextmanager
def _sanitized_smoke_stderr() -> Iterator[None]:
    original_stderr_fd = os.dup(2)
    try:
        with TemporaryFile(mode="w+t", encoding="utf-8", errors="replace") as captured:
            sys.stderr.flush()
            os.dup2(captured.fileno(), 2)
            try:
                yield
            finally:
                sys.stderr.flush()
                captured.flush()
                os.dup2(original_stderr_fd, 2)
                captured.seek(0)
                safe_text = Redactor().redact(captured.read()).strip()
                if safe_text:
                    print(safe_text, file=sys.stderr)
    finally:
        os.close(original_stderr_fd)


def _create_shell(context: AppContext, session: object):
    from app.ui.shell.main_window import MainWindowShell
    from app.ui.shell.page_registry import build_default_page_registry

    services = context.containers.services
    return MainWindowShell(
        session,
        build_default_page_registry(),
        auth_service=services.get("auth"),
        license_service=services.get("license"),
        state_store=context.state_store,
        event_bus=context.event_bus,
        services=services,
        devices=context.containers.devices,
        api=context.containers.api,
        config=context.config,
        paths=context.paths,
    )


if __name__ == "__main__":
    raise SystemExit(main())
