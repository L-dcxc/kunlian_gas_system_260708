from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.config.defaults import ApiConfig
from app.services.errors import ErrorCode
from app.services.permissions import Permission, role_has_permission
from app.ui.common.errors import ErrorBanner, ValidationHint, controlled_error_text
from app.ui.common.permission_hint import PermissionHint
from app.ui.common.safe_text import SafeTextLabel
from app.ui.common.status import repolish
from app.ui.settings.api_docs_panel import ApiDocsPanel

PORT_MIN = 1
PORT_MAX = 65535
DEFAULT_API_BIND_ADDRESS = "127.0.0.1"
API_PORT_IN_USE_MESSAGE = "本地 API 启动失败：端口被占用。桌面监控不受影响。"
SAVE_FAILED_TEXT = "本地 API 设置保存失败，请稍后重试。"
START_FAILED_TEXT = "本地 API 启动失败。桌面监控不受影响。"
STOP_FAILED_TEXT = "本地 API 停止失败。桌面监控不受影响。"
READONLY_BOUNDARY_TEXT = "本地 API 不提供配置变更、采集控制、备份恢复、联动控制接口"
PENDING_EXPOSURE_TEXT = "LAN 绑定、API token、IP 白名单：[待确认]。当前页面不提供启用入口。"
DESKTOP_OK_TEXT = "桌面主程序状态：正常"


@dataclass(frozen=True, slots=True)
class LocalApiSettingsCommand:
    enabled: bool
    bind_address: str
    port: int


class LocalApiSettingsPage(QWidget):
    startRequested = Signal(int)
    stopRequested = Signal()
    configSaved = Signal(int)

    def __init__(
        self,
        api_host: object | None = None,
        api_config_facade: object | None = None,
        session: object | None = None,
        parent: QWidget | None = None,
        *,
        can_configure: bool | None = None,
        docs_panel: ApiDocsPanel | None = None,
    ) -> None:
        super().__init__(parent)
        self._host = api_host
        self._config_facade = api_config_facade
        self._session = session
        self._can_configure = _can_configure_from_session(session) if can_configure is None else can_configure
        self._starting = False
        self._config = ApiConfig(bind_address=DEFAULT_API_BIND_ADDRESS)

        self.error_banner = ErrorBanner(); self.error_banner.clear()
        self.permission_hint = PermissionHint(); self.permission_hint.setVisible(not self._can_configure)
        self.title_label = SafeTextLabel("本地 API 设置", selectable=False)
        self.title_label.setProperty("role", "panelTitle")
        self.subtitle_label = SafeTextLabel("配置桌面应用内置的本机只读 HTTP API。", selectable=False)
        self.subtitle_label.setProperty("role", "muted")

        self.status_card = QFrame()
        self.status_card.setObjectName("ApiStatus")
        self.status_card.setProperty("panel", "true")
        self.status_card.setProperty("status", "stopped")
        self.status_label = SafeTextLabel("已停止", selectable=False)
        self.status_label.setProperty("role", "metricValue")
        self.endpoint_label = SafeTextLabel("127.0.0.1:8765", selectable=True)
        self.endpoint_label.setProperty("role", "muted")
        self.desktop_status_label = SafeTextLabel(DESKTOP_OK_TEXT, selectable=False)
        self.desktop_status_label.setProperty("status", "normal")
        self.last_error_label = SafeTextLabel("最近错误：无", selectable=True)
        self.last_error_label.setProperty("role", "muted")
        self.start_button = QPushButton("启动 API")
        self.start_button.setProperty("variant", "primary")
        self.stop_button = QPushButton("停止 API")
        self.stop_button.setProperty("variant", "danger")
        self.start_button.clicked.connect(self.start_api)
        self.stop_button.clicked.connect(self.stop_api)

        self.enabled_check = QCheckBox("启用本地 API")
        self.bind_label = SafeTextLabel(DEFAULT_API_BIND_ADDRESS, selectable=True)
        self.port_input = QSpinBox()
        self.port_input.setRange(0, PORT_MAX)
        self.port_input.setValue(self._config.port)
        self.port_input.setKeyboardTracking(False)
        self.readonly_notice = SafeTextLabel(READONLY_BOUNDARY_TEXT, selectable=True)
        self.readonly_notice.setProperty("role", "readonlyNotice")
        self.pending_notice = SafeTextLabel(PENDING_EXPOSURE_TEXT, selectable=True)
        self.pending_notice.setProperty("role", "muted")
        self.validation_hint = ValidationHint(); self.validation_hint.clear()
        self.save_button = QPushButton("保存配置")
        self.save_button.setProperty("variant", "primary")
        self.save_button.clicked.connect(self.save_config)

        self.docs_panel = docs_panel or ApiDocsPanel()
        self._build_layout()
        self.reload()
        self._apply_permission_state()

    def reload(self) -> None:
        self.error_banner.clear()
        self.clear_validation()
        loaded = self._load_config()
        if loaded is not None:
            self._fill_config(loaded)
        self.refresh_status()

    def set_starting(self, starting: bool) -> None:
        self._starting = starting
        self.status_card.setProperty("status", "starting" if starting else self.status_card.property("status"))
        self.status_label.set_safe_text("启动中" if starting else self.status_label.text())
        self.start_button.setEnabled(False if starting else self._can_configure and self.enabled_check.isChecked())
        self.start_button.setText("启动中..." if starting else "启动 API")
        self.stop_button.setEnabled(False if starting else self._can_configure and self._host_running())
        repolish(self.status_card)

    def update_status(self, status: str, *, message: object | None = None) -> None:
        safe_status = status if status in {"running", "stopped", "error", "starting"} else "stopped"
        self.status_card.setProperty("status", safe_status)
        if safe_status == "running":
            self.status_label.set_safe_text("已运行")
            self.status_label.setProperty("status", "normal")
        elif safe_status == "starting":
            self.status_label.set_safe_text("启动中")
            self.status_label.setProperty("status", "running")
        elif safe_status == "error":
            self.status_label.set_safe_text("启动失败")
            self.status_label.setProperty("status", "highAlarm")
        else:
            self.status_label.set_safe_text("已停止")
            self.status_label.setProperty("status", "offline")
        self.endpoint_label.set_safe_text(f"{self._config.bind_address}:{self._config.port}")
        self.desktop_status_label.set_safe_text(DESKTOP_OK_TEXT)
        error_text = _api_error_text(message)
        self.last_error_label.set_safe_text(f"最近错误：{error_text or '无'}")
        if safe_status == "error" and error_text:
            self.error_banner.set_error(error_text)
        elif safe_status != "error":
            self.error_banner.clear()
        repolish(self.status_label)
        repolish(self.status_card)
        self._sync_action_buttons()

    def refresh_status(self) -> None:
        if self._starting:
            self.update_status("starting")
            return
        alert = _call_or_value(self._host, "last_alert")
        if alert:
            self.update_status("error", message=alert)
            return
        self.update_status("running" if self._host_running() else "stopped")

    def validate_form(self) -> bool:
        self.clear_validation()
        # Client-side range checks make mistakes visible early; the injected
        # config/host facade remains authoritative before any runtime exposure.
        port = self._port_text_value()
        if port is None or port < PORT_MIN or port > PORT_MAX:
            return self._field_error(self.port_input, "端口必须为 1-65535")
        return True

    def clear_validation(self) -> None:
        self.validation_hint.clear()
        self.port_input.setProperty("validation", None)
        repolish(self.port_input)

    def save_config(self) -> None:
        if not self._require_permission() or not self.validate_form():
            return
        self._save_config(show_success=True)

    def start_api(self) -> None:
        if not self._require_permission() or not self.validate_form():
            return
        if not self.enabled_check.isChecked():
            self.enabled_check.setFocus()
            self.error_banner.show_validation_error("请先启用本地 API")
            return
        self.set_starting(True)
        if not self._save_config(show_success=False):
            self.set_starting(False)
            self.refresh_status()
            return
        command = self._command()
        self.startRequested.emit(command.port)
        try:
            result = _call_with_supported_args(_host_method(self._host, ("start_api", "start")), self._session, command, command.port)
        except Exception:
            result = _Failure(START_FAILED_TEXT)
        self.set_starting(False)
        if _result_success(result, success_attr="started"):
            self.error_banner.clear()
            self.refresh_status()
            if not self._host_running():
                self.update_status("running")
            return
        self.update_status("error", message=_result_message(result, START_FAILED_TEXT))

    def stop_api(self) -> None:
        if not self._require_permission():
            return
        self.stopRequested.emit()
        self.stop_button.setEnabled(False)
        try:
            result = _call_with_supported_args(_host_method(self._host, ("stop_api", "stop")), self._session)
        except Exception:
            result = _Failure(STOP_FAILED_TEXT)
        if _result_success(result):
            self.error_banner.clear()
            self.refresh_status()
            if self._host_running():
                self._sync_action_buttons()
            else:
                self.update_status("stopped")
            return
        if _result_code(result) == int(ErrorCode.PERMISSION_DENIED):
            self.error_banner.show_permission_denied()
        else:
            self.error_banner.set_error(controlled_error_text(_result_message(result, STOP_FAILED_TEXT), fallback=STOP_FAILED_TEXT))
        self._sync_action_buttons()

    def _build_layout(self) -> None:
        status_grid = QGridLayout(self.status_card)
        status_grid.setContentsMargins(16, 16, 16, 16)
        status_grid.setHorizontalSpacing(12)
        status_grid.setVerticalSpacing(8)
        _add_field(status_grid, 0, "状态", self.status_label)
        _add_field(status_grid, 1, "绑定", self.endpoint_label)
        _add_field(status_grid, 2, "桌面主程序", self.desktop_status_label)
        _add_field(status_grid, 3, "最近错误", self.last_error_label)
        actions = QHBoxLayout()
        actions.addWidget(self.start_button)
        actions.addWidget(self.stop_button)
        actions.addStretch(1)
        status_grid.addLayout(actions, 4, 1)

        config_card = QFrame()
        config_card.setProperty("panel", "true")
        config_layout = QGridLayout(config_card)
        config_layout.setContentsMargins(16, 16, 16, 16)
        config_layout.setHorizontalSpacing(12)
        config_layout.setVerticalSpacing(10)
        _add_field(config_layout, 0, "启用", self.enabled_check)
        _add_field(config_layout, 1, "绑定地址", self.bind_label)
        _add_field(config_layout, 2, "端口", self.port_input)
        config_layout.addWidget(self.validation_hint, 3, 1)
        config_layout.addWidget(self.readonly_notice, 4, 0, 1, 2)
        config_layout.addWidget(self.pending_notice, 5, 0, 1, 2)
        config_layout.addWidget(self.save_button, 6, 1)

        header = QFrame()
        header.setProperty("panel", "true")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(16, 16, 16, 16)
        header_layout.setSpacing(8)
        header_layout.addWidget(self.title_label)
        header_layout.addWidget(self.subtitle_label)
        header_layout.addWidget(self.permission_hint)

        top = QHBoxLayout()
        top.setSpacing(12)
        top.addWidget(self.status_card, 1)
        top.addWidget(config_card, 1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        layout.addWidget(self.error_banner)
        layout.addWidget(header)
        layout.addLayout(top)
        layout.addWidget(self.docs_panel, 1)

    def _load_config(self) -> object | None:
        method = _facade_method(self._config_facade, ("get_api_config", "get_config", "load_api_config"))
        if method is None:
            return self._config
        try:
            result = _call_with_supported_args(method, self._session)
        except Exception:
            self.error_banner.set_error("本地 API 设置读取失败")
            return None
        if _result_success(result):
            return getattr(result, "data", result)
        self.error_banner.set_error(controlled_error_text(_result_message(result, "本地 API 设置读取失败")))
        return None

    def _save_config(self, *, show_success: bool) -> bool:
        command = self._command()
        method = _facade_method(self._config_facade, ("save_api_config", "update_api_config", "save_config", "update_config"))
        if method is None:
            self._fill_config(command)
            if show_success:
                self.configSaved.emit(command.port)
            return True
        self.save_button.setEnabled(False)
        try:
            result = _call_with_supported_args(method, self._session, command)
        except Exception:
            result = _Failure(SAVE_FAILED_TEXT)
        self.save_button.setEnabled(self._can_configure)
        if _result_success(result):
            data = getattr(result, "data", None)
            self._fill_config(data if data is not None else command)
            self.error_banner.clear()
            self.configSaved.emit(command.port)
            if show_success:
                self.last_error_label.set_safe_text("最近错误：无")
            return True
        if _result_code(result) == int(ErrorCode.PERMISSION_DENIED):
            self.error_banner.show_permission_denied()
        else:
            self.error_banner.set_error(controlled_error_text(_result_message(result, SAVE_FAILED_TEXT), fallback=SAVE_FAILED_TEXT))
        return False

    def _fill_config(self, data: object) -> None:
        enabled = bool(_data_get(data, "enabled", self._config.enabled))
        port = _safe_port(_data_get(data, "port", self._config.port), self._config.port)
        bind_address = _loopback_bind(_data_get(data, "bind_address", self._config.bind_address))
        self._config = ApiConfig(enabled=enabled, bind_address=bind_address, port=port, cors_enabled=False)
        self.enabled_check.setChecked(enabled)
        self.bind_label.set_safe_text(bind_address)
        self.port_input.setValue(port)
        self.endpoint_label.set_safe_text(f"{bind_address}:{port}")
        self._sync_action_buttons()

    def _command(self) -> LocalApiSettingsCommand:
        return LocalApiSettingsCommand(
            enabled=self.enabled_check.isChecked(),
            bind_address=DEFAULT_API_BIND_ADDRESS,
            port=self._port_text_value() or self.port_input.value(),
        )

    def _port_text_value(self) -> int | None:
        text = self.port_input.lineEdit().text().strip() if self.port_input.lineEdit() is not None else str(self.port_input.value())
        try:
            return int(text)
        except (TypeError, ValueError):
            return None

    def _field_error(self, widget: QWidget, message: str) -> bool:
        widget.setProperty("validation", "error")
        repolish(widget)
        self.validation_hint.set_validation_error(message)
        widget.setFocus()
        return False

    def _require_permission(self) -> bool:
        if self._can_configure:
            return True
        self.permission_hint.show_denied()
        self.error_banner.show_permission_denied()
        return False

    def _apply_permission_state(self) -> None:
        self.permission_hint.setVisible(not self._can_configure)
        # UI read-only mode is only an affordance; the injected facade must still
        # enforce permission before saving config or changing the API host state.
        self.enabled_check.setEnabled(self._can_configure)
        self.port_input.setReadOnly(not self._can_configure)
        self.save_button.setEnabled(self._can_configure)
        self._sync_action_buttons()

    def _sync_action_buttons(self) -> None:
        running = self._host_running()
        enabled = self._can_configure and self.enabled_check.isChecked() and not self._starting
        self.start_button.setEnabled(enabled and not running)
        self.stop_button.setEnabled(self._can_configure and running and not self._starting)
        self.start_button.setText("启动中..." if self._starting else "启动 API")

    def _host_running(self) -> bool:
        return bool(_call_or_value(self._host, "is_running"))


def _add_field(grid: QGridLayout, row: int, label: str, widget: QWidget) -> None:
    label_widget = QLabel(label)
    label_widget.setProperty("role", "fieldLabel")
    grid.addWidget(label_widget, row, 0)
    grid.addWidget(widget, row, 1)


def _can_configure_from_session(session: object | None) -> bool:
    permissions = tuple(getattr(session, "permissions", ()) or ())
    if "*" in permissions or Permission.SYSTEM_SETTINGS.value in permissions:
        return True
    role = getattr(session, "role", None)
    if role is None:
        return False
    try:
        return role_has_permission(str(role), Permission.SYSTEM_SETTINGS.value)
    except ValueError:
        return False


def _facade_method(facade: object | None, names: tuple[str, ...]) -> object | None:
    if facade is None:
        return None
    for name in names:
        method = getattr(facade, name, None)
        if callable(method):
            return method
    return None


def _host_method(host: object | None, names: tuple[str, ...]) -> object | None:
    return _facade_method(host, names)


def _call_with_supported_args(method: object | None, *args: object) -> object:
    if method is None:
        return _Failure("本地 API 服务未配置")
    variants = (args, args[1:], args[2:], ())
    last_error: TypeError | None = None
    for variant in variants:
        try:
            return method(*variant)  # type: ignore[misc]
        except TypeError as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    return method()  # type: ignore[misc]


def _call_or_value(obj: object | None, name: str) -> object | None:
    if obj is None:
        return None
    value = getattr(obj, name, None)
    return value() if callable(value) else value


def _result_success(result: object, *, success_attr: str = "success") -> bool:
    if result is None:
        return True
    if hasattr(result, success_attr):
        return bool(getattr(result, success_attr))
    if success_attr != "success" and hasattr(result, "success"):
        return bool(getattr(result, "success"))
    return True


def _result_code(result: object) -> int:
    try:
        return int(getattr(result, "code", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _result_message(result: object, fallback: str) -> str:
    return str(getattr(result, "message", "") or fallback)


def _data_get(data: object, key: str, default: object) -> object:
    if data is None:
        return default
    if isinstance(data, dict):
        return data.get(key, default)
    return getattr(data, key, default)


def _safe_port(value: object, default: int) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError):
        return default
    if PORT_MIN <= port <= PORT_MAX:
        return port
    return default


def _loopback_bind(value: object) -> str:
    text = str(value or DEFAULT_API_BIND_ADDRESS)
    return text if text in {"127.0.0.1", "localhost", "::1"} else DEFAULT_API_BIND_ADDRESS


def _api_error_text(message: object | None) -> str:
    if message in {None, ""}:
        return ""
    text = str(message)
    if "address already in use" in text.lower() or ("端口" in text and "占用" in text):
        return API_PORT_IN_USE_MESSAGE
    return controlled_error_text(text, fallback=START_FAILED_TEXT)


class _Failure:
    def __init__(self, message: str) -> None:
        self.success = False
        self.started = False
        self.code = int(ErrorCode.INTERNAL_ERROR)
        self.message = message


__all__ = ["LocalApiSettingsCommand", "LocalApiSettingsPage"]
