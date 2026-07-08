from __future__ import annotations

import re

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QWidget

from app.ui.common.safe_text import SafeTextLabel, normalize_plain_text

DEFAULT_ERROR_MESSAGE = "操作失败，请稍后重试。"
VALIDATION_FAILED_MESSAGE = "输入内容校验失败，请检查后重试。"
PERMISSION_DENIED_MESSAGE = "当前账号无权限执行此操作，已记录权限失败事件。"
LICENSE_FAILED_MESSAGE = "授权校验失败，请联系管理员或供应商。"
API_PORT_BUSY_MESSAGE = "本地 API 启动失败：端口被占用。桌面监控不受影响。"

_STACK_MARKER = re.compile(r"(?i)(traceback|\bfile\s+\".*?\"|\bline\s+\d+|stack trace)")
_ABSOLUTE_PATH = re.compile(r"(?i)([A-Z]:\\[^\s,;]+|/[A-Za-z0-9_.-]+(?:/[^\s,;]+)+)")
_SENSITIVE_VALUE = re.compile(
    r"(?i)(password|passwd|pwd|authorization[_ -]?code|auth[_ -]?code|license[_ -]?code|"
    r"machine[_ -]?id|machine[_ -]?code|hardware[_ -]?id|api[_ -]?token|token|secret|key)\s*[:=]\s*[^\s,;]+"
)
_AUTH_DETAIL = re.compile(r"(?i)(license algorithm|authorization algorithm|machine fingerprint|signature key)")
_SQL_DETAIL = re.compile(
    r"(?i)(sqlite3?\.\w+|sqlalchemy|operationalerror|integrityerror|programmingerror|"
    r"\bselect\b.+\bfrom\b|\binsert\b.+\binto\b|\bupdate\b.+\bset\b|\bdelete\b.+\bfrom\b|\bdrop\s+table\b)"
)


def controlled_error_text(message: object, *, fallback: str = DEFAULT_ERROR_MESSAGE, max_chars: int = 256) -> str:
    text = "" if message is None else str(message)
    if not text.strip() or _STACK_MARKER.search(text) or _AUTH_DETAIL.search(text) or _SQL_DETAIL.search(text):
        return fallback
    text = _SENSITIVE_VALUE.sub(lambda match: f"{match.group(1)}=<已隐藏>", text)
    text = _ABSOLUTE_PATH.sub("<路径已隐藏>", text)
    return _one_line(normalize_plain_text(text, max_chars=max_chars)) or fallback


def validation_failed_text(detail: object | None = None) -> str:
    if detail is None:
        return VALIDATION_FAILED_MESSAGE
    return controlled_error_text(detail, fallback=VALIDATION_FAILED_MESSAGE)


def permission_denied_text() -> str:
    return PERMISSION_DENIED_MESSAGE


class ErrorBanner(QFrame):
    def __init__(self, message: object = "", parent: QWidget | None = None, *, severity: str = "error") -> None:
        super().__init__(parent)
        self.setProperty("role", "errorBanner")
        self._label = SafeTextLabel(parent=self, selectable=True)
        self._label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.addWidget(self._label)
        self.set_error(message, severity=severity)

    @property
    def label(self) -> SafeTextLabel:
        return self._label

    def set_error(self, message: object, *, severity: str = "error") -> None:
        safe_severity = severity if severity in {"error", "warning", "permission"} else "error"
        self.setProperty("severity", safe_severity)
        self._label.set_safe_text(controlled_error_text(message))
        self.setVisible(bool(self._label.text()))
        self._repolish()

    def show_validation_error(self, detail: object | None = None) -> None:
        self.setProperty("severity", "warning")
        self._label.set_safe_text(validation_failed_text(detail))
        self.setVisible(True)
        self._repolish()

    def show_permission_denied(self) -> None:
        self.setProperty("severity", "permission")
        self._label.set_safe_text(permission_denied_text())
        self.setVisible(True)
        self._repolish()

    def clear(self) -> None:
        self._label.set_safe_text("")
        self.setVisible(False)

    def _repolish(self) -> None:
        style = self.style()
        style.unpolish(self)
        style.polish(self)
        self.update()


class ValidationHint(SafeTextLabel):
    def __init__(self, message: object = "", parent: QWidget | None = None) -> None:
        super().__init__(validation_failed_text(message) if message else "", parent=parent, selectable=False)
        self.setProperty("role", "validationError")

    def set_validation_error(self, message: object | None = None) -> None:
        self.set_safe_text(validation_failed_text(message))
        self.setVisible(True)

    def clear(self) -> None:
        self.set_safe_text("")
        self.setVisible(False)


def _one_line(value: str) -> str:
    return " ".join(value.replace("\r", " ").replace("\n", " ").split())
