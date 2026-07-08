from __future__ import annotations

import re

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QWidget

from app.ui.common.errors import permission_denied_text
from app.ui.common.safe_text import SafeTextLabel, normalize_plain_text

_PERMISSION_CODE = re.compile(r"(?i)\b(permission|perm|acl|role|policy)[_-]?(code|id)?\s*[:=]\s*[^\s,;]+")


class PermissionHint(QFrame):
    def __init__(self, message: object | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("role", "permissionHint")
        self.icon_label = SafeTextLabel("🔒", selectable=False)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.message_label = SafeTextLabel(selectable=True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)
        layout.addWidget(self.icon_label)
        layout.addWidget(self.message_label, 1)
        self.set_message(message)

    def set_message(self, message: object | None = None) -> None:
        if message is None:
            self.message_label.set_safe_text(permission_denied_text())
            return
        text = normalize_plain_text(message, max_chars=256)
        text = _PERMISSION_CODE.sub("权限标识=<已隐藏>", text)
        if "已记录" not in text:
            text = f"{text} 已记录权限失败事件。"
        self.message_label.set_safe_text(text)

    def show_denied(self) -> None:
        self.set_message(None)
        self.setVisible(True)
