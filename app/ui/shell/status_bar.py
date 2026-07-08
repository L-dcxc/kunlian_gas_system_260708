from __future__ import annotations

from datetime import datetime
from typing import Any

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QFrame, QHBoxLayout, QWidget

from app.services.models import AcquisitionStatus
from app.ui.common.errors import controlled_error_text
from app.ui.common.safe_text import SafeTextLabel
from app.ui.common.status import repolish

ACQUISITION_STATUS_STATE_KEY = "acquisition.status"
API_STATUS_STATE_KEY = "api.status"
API_PORT_IN_USE_MESSAGE = "本地 API 启动失败：端口被占用。桌面监控不受影响。"
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


class ShellStatusBar(QFrame):
    def __init__(
        self,
        session: object | None = None,
        license_service: object | None = None,
        state_store: object | None = None,
        parent: QWidget | None = None,
        *,
        clock_interval_ms: int = 1000,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ShellTopBar")
        self._session = session
        self._license_service = license_service
        self._state_store = state_store

        self.license_label = _status_label("授权：待检查", "warning")
        self.user_label = _status_label("用户：未登录", "offline")
        self.acquisition_label = _status_label("采集：未启动", "offline")
        self.api_label = _status_label("API：未启动", "offline")
        self.time_label = _status_label("时间：--", "offline")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 8, 16, 8)
        layout.setSpacing(12)
        for label in (self.license_label, self.user_label, self.acquisition_label, self.api_label):
            layout.addWidget(label)
        layout.addStretch(1)
        layout.addWidget(self.time_label)

        self._clock_timer = QTimer(self)
        self._clock_timer.setInterval(clock_interval_ms)
        self._clock_timer.timeout.connect(self.refresh)
        self._clock_timer.start()
        self.refresh()

    def set_session(self, session: object | None) -> None:
        self._session = session
        self.refresh()

    def set_api_status(self, status: object, message: object | None = None) -> None:
        text, severity = _api_status_text(status, message)
        self._set_label(self.api_label, text, severity)

    def refresh(self) -> None:
        self._refresh_license()
        self._refresh_user()
        self._refresh_acquisition()
        self._refresh_api()
        self._set_label(self.time_label, f"时间：{datetime.now().strftime(TIME_FORMAT)}", "offline")

    def _refresh_license(self) -> None:
        if self._license_service is None or not hasattr(self._license_service, "get_license_status"):
            self._set_label(self.license_label, "授权：待检查", "warning")
            return
        try:
            status = self._license_service.get_license_status()
        except Exception:
            self._set_label(self.license_label, "授权：校验失败", "warning")
            return
        if bool(getattr(status, "is_active", False)):
            self._set_label(self.license_label, "授权：已授权", "normal")
        else:
            message = controlled_error_text(getattr(status, "message", "未授权"), fallback="未授权")
            self._set_label(self.license_label, f"授权：{message}", "warning")

    def _refresh_user(self) -> None:
        username = getattr(self._session, "username", None)
        role = getattr(self._session, "role", None)
        if username:
            self._set_label(self.user_label, f"用户：{username} ({role or '未知角色'})", "normal")
        else:
            self._set_label(self.user_label, "用户：未登录", "offline")

    def _refresh_acquisition(self) -> None:
        state = _state_value(self._state_store, ACQUISITION_STATUS_STATE_KEY)
        status = getattr(state, "status", state)
        message = getattr(state, "message", "")
        text, severity = _acquisition_status_text(status, message)
        self._set_label(self.acquisition_label, text, severity)

    def _refresh_api(self) -> None:
        state = _state_value(self._state_store, API_STATUS_STATE_KEY)
        if state is None:
            self._set_label(self.api_label, "API：未启动", "offline")
            return
        status = getattr(state, "status", state)
        message = getattr(state, "message", "")
        self.set_api_status(status, message)

    def _set_label(self, label: SafeTextLabel, text: object, status: str) -> None:
        label.set_safe_text(text)
        label.setProperty("status", status)
        repolish(label)


def _status_label(text: str, status: str) -> SafeTextLabel:
    label = SafeTextLabel(text, selectable=True, max_chars=256)
    label.setProperty("role", "statusBadge")
    label.setProperty("status", status)
    return label


def _state_value(state_store: object | None, key: str) -> Any:
    if state_store is None or not hasattr(state_store, "get_value"):
        return None
    try:
        return state_store.get_value(key)
    except Exception:
        return None


def _acquisition_status_text(status: object, message: object = "") -> tuple[str, str]:
    value = _status_value(status)
    detail = controlled_error_text(message, fallback="", max_chars=120) if message else ""
    suffix = f"：{detail}" if detail else ""
    if value == AcquisitionStatus.RUNNING.value:
        return f"采集：运行中{suffix}", "running"
    if value == AcquisitionStatus.RECONNECTING.value:
        return f"采集：重连中{suffix}", "warning"
    if value == AcquisitionStatus.ERROR.value:
        return f"采集：异常{suffix}", "warning"
    if value == AcquisitionStatus.STOPPED.value:
        return "采集：已停止", "offline"
    return "采集：未启动", "offline"


def _api_status_text(status: object, message: object | None = None) -> tuple[str, str]:
    raw_message = str(message or "")
    if raw_message == API_PORT_IN_USE_MESSAGE or "address already in use" in raw_message.lower() or "占用" in raw_message:
        return API_PORT_IN_USE_MESSAGE, "warning"
    value = _status_value(status).lower()
    if value in {"running", "started", "enabled"}:
        return "API：运行中", "running"
    if value in {"starting"}:
        return "API：启动中", "warning"
    if value in {"error", "failed"}:
        detail = controlled_error_text(message, fallback="启动失败", max_chars=120) if message else "启动失败"
        return f"API：{detail}", "warning"
    return "API：未启动", "offline"


def _status_value(status: object) -> str:
    value = getattr(status, "value", status)
    return str(value or "")
