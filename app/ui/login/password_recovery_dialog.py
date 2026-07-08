from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from app.ui.common.errors import ValidationHint, controlled_error_text
from app.ui.common.safe_text import SafeTextLabel

RECOVERY_SUCCESS_TEXT = "密码已重置，请使用新密码登录。"


class PasswordRecoveryDialog(QDialog):
    passwordRecovered = Signal(str)
    passwordRecoveryFailed = Signal(str)

    def __init__(
        self,
        auth_service: object | None = None,
        parent: QWidget | None = None,
        *,
        username: str | None = None,
    ) -> None:
        super().__init__(parent)
        self._auth_service = auth_service
        self._submitting = False

        self.setWindowTitle("厂家密码找回")
        self.setModal(True)
        self.resize(460, 380)

        self.card = QFrame(self)
        self.card.setObjectName("PasswordRecoveryCard")
        self.card.setProperty("panel", "true")

        self.title_label = SafeTextLabel("厂家密码找回", selectable=False)
        self.title_label.setProperty("role", "dialogTitle")
        self.subtitle_label = SafeTextLabel("输入账号、厂家密码和新密码后重置登录密码。", selectable=False)
        self.subtitle_label.setProperty("role", "muted")

        self.username_label = QLabel("账号")
        self.username_label.setProperty("role", "fieldLabel")
        self.username_edit = QLineEdit()
        self.username_edit.setMaxLength(80)
        self.username_edit.setText(username or "")
        self.username_edit.setPlaceholderText("请输入要找回的账号")

        self.factory_password_label = QLabel("厂家密码")
        self.factory_password_label.setProperty("role", "fieldLabel")
        self.factory_password_edit = QLineEdit()
        self.factory_password_edit.setMaxLength(256)
        self.factory_password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.factory_password_edit.setPlaceholderText("请输入厂家密码")

        self.new_password_label = QLabel("新密码")
        self.new_password_label.setProperty("role", "fieldLabel")
        self.new_password_edit = QLineEdit()
        self.new_password_edit.setMaxLength(128)
        self.new_password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.new_password_edit.setPlaceholderText("8 至 128 位")

        self.confirm_password_label = QLabel("确认新密码")
        self.confirm_password_label.setProperty("role", "fieldLabel")
        self.confirm_password_edit = QLineEdit()
        self.confirm_password_edit.setMaxLength(128)
        self.confirm_password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.confirm_password_edit.setPlaceholderText("再次输入新密码")

        self.error_hint = ValidationHint()
        self.error_hint.clear()
        self.success_label = SafeTextLabel("", selectable=False)
        self.success_label.setProperty("status", "normal")
        self.success_label.setVisible(False)

        self.cancel_button = QPushButton("取消")
        self.submit_button = QPushButton("重置密码")
        self.submit_button.setProperty("variant", "primary")
        self.cancel_button.clicked.connect(self.reject)
        self.submit_button.clicked.connect(self.submit)
        self.confirm_password_edit.returnPressed.connect(self.submit)

        actions = QHBoxLayout()
        actions.addStretch(1)
        actions.addWidget(self.cancel_button)
        actions.addWidget(self.submit_button)

        layout = QVBoxLayout(self.card)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(10)
        layout.addWidget(self.title_label)
        layout.addWidget(self.subtitle_label)
        layout.addWidget(self.username_label)
        layout.addWidget(self.username_edit)
        layout.addWidget(self.factory_password_label)
        layout.addWidget(self.factory_password_edit)
        layout.addWidget(self.new_password_label)
        layout.addWidget(self.new_password_edit)
        layout.addWidget(self.confirm_password_label)
        layout.addWidget(self.confirm_password_edit)
        layout.addWidget(self.error_hint)
        layout.addWidget(self.success_label)
        layout.addLayout(actions)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.addWidget(self.card)

    def submit(self) -> None:
        if self._submitting or not self._validate_form():
            return
        self._set_submitting(True)
        try:
            if self._auth_service is None or not hasattr(self._auth_service, "recover_password_with_factory_password"):
                self.show_error("密码重置失败，请稍后重试。")
                return
            result = self._auth_service.recover_password_with_factory_password(
                self.username_edit.text().strip(),
                self.factory_password_edit.text(),
                self.new_password_edit.text(),
            )
            if bool(getattr(result, "success", False)):
                username = self.username_edit.text().strip()
                self.clear_error()
                self.success_label.set_safe_text(RECOVERY_SUCCESS_TEXT)
                self.success_label.setVisible(True)
                self.passwordRecovered.emit(username)
                self.accept()
            else:
                self.show_error(getattr(result, "message", "密码重置失败，请稍后重试。"))
        except Exception:
            self.show_error("密码重置失败，请稍后重试。")
        finally:
            self._set_submitting(False)

    def show_error(self, message: object) -> None:
        self.error_hint.set_safe_text(controlled_error_text(message, fallback="密码重置失败，请稍后重试。"))
        self.error_hint.setVisible(True)

    def clear_error(self) -> None:
        self.error_hint.clear()
        self.success_label.set_safe_text("")
        self.success_label.setVisible(False)

    def _validate_form(self) -> bool:
        self.clear_error()
        if not self.username_edit.text().strip() or not self.factory_password_edit.text():
            self.show_error("请填写账号和厂家密码。")
            return False
        new_password = self.new_password_edit.text()
        if not 8 <= len(new_password) <= 128:
            self.show_error("新密码长度需为 8 至 128 位。")
            return False
        if new_password != self.confirm_password_edit.text():
            self.show_error("两次输入的新密码不一致。")
            return False
        return True

    def _set_submitting(self, submitting: bool) -> None:
        self._submitting = submitting
        for editor in (
            self.username_edit,
            self.factory_password_edit,
            self.new_password_edit,
            self.confirm_password_edit,
        ):
            editor.setEnabled(not submitting)
        self.cancel_button.setEnabled(not submitting)
        self.submit_button.setEnabled(not submitting)
        self.submit_button.setText("重置中..." if submitting else "重置密码")
