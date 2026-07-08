from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from app.services.errors import ErrorCode
from app.ui.common.errors import ValidationHint, controlled_error_text
from app.ui.common.permission_hint import PermissionHint
from app.ui.common.safe_text import SafeTextLabel

PASSWORD_MISMATCH_TEXT = "两次输入的新密码不一致。"
PASSWORD_POLICY_TEXT = "新密码长度需为 8 至 128 位。"
PASSWORD_CHANGE_SUCCESS_TEXT = "密码修改成功。"


class ChangePasswordDialog(QDialog):
    passwordChanged = Signal()
    passwordChangeFailed = Signal(str)

    def __init__(
        self,
        auth_service: object | None = None,
        session: object | None = None,
        parent: QWidget | None = None,
        *,
        target_username: str | None = None,
        can_modify_target: bool = True,
        force_change: bool = False,
    ) -> None:
        super().__init__(parent)
        self._auth_service = auth_service
        self._session = session
        self._submitting = False
        self._blocked_by_permission = not can_modify_target
        self._force_change = force_change

        self.setWindowTitle("修改密码")
        self.setModal(True)
        self.resize(460, 360)

        self.card = QFrame(self)
        self.card.setObjectName("ChangePasswordCard")
        self.card.setProperty("panel", "true")

        self.title_label = SafeTextLabel("首次登录修改密码" if force_change else "修改密码", selectable=False)
        self.title_label.setProperty("role", "dialogTitle")
        if force_change:
            target_text = "首次登录必须修改默认密码，完成后才能进入系统。"
        else:
            target_text = f"目标账号：{target_username}" if target_username else "仅支持当前登录账号修改本人密码。"
        self.target_label = SafeTextLabel(target_text, selectable=True)
        self.target_label.setProperty("role", "muted")

        self.permission_hint = PermissionHint()
        self.permission_hint.setVisible(self._blocked_by_permission)

        self.old_password_label = QLabel("原密码")
        self.old_password_label.setProperty("role", "fieldLabel")
        self.old_password_edit = QLineEdit()
        self.old_password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.old_password_edit.setMaxLength(256)
        self.old_password_edit.setPlaceholderText("请输入原密码")

        self.new_password_label = QLabel("新密码")
        self.new_password_label.setProperty("role", "fieldLabel")
        self.new_password_edit = QLineEdit()
        self.new_password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.new_password_edit.setMaxLength(128)
        self.new_password_edit.setPlaceholderText("8 至 128 位")

        self.confirm_password_label = QLabel("确认新密码")
        self.confirm_password_label.setProperty("role", "fieldLabel")
        self.confirm_password_edit = QLineEdit()
        self.confirm_password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.confirm_password_edit.setMaxLength(128)
        self.confirm_password_edit.setPlaceholderText("再次输入新密码")

        self.error_hint = ValidationHint()
        self.error_hint.clear()
        self.success_label = SafeTextLabel("", selectable=False)
        self.success_label.setProperty("status", "normal")
        self.success_label.setVisible(False)

        self.cancel_button = QPushButton("取消")
        self.submit_button = QPushButton("确认修改")
        self.submit_button.setProperty("variant", "primary")
        self.cancel_button.clicked.connect(self.reject)
        self.submit_button.clicked.connect(self.submit)
        self.confirm_password_edit.returnPressed.connect(self.submit)
        self.new_password_edit.textChanged.connect(self._validate_confirmation)
        self.confirm_password_edit.textChanged.connect(self._validate_confirmation)

        actions = QHBoxLayout()
        actions.addStretch(1)
        actions.addWidget(self.cancel_button)
        actions.addWidget(self.submit_button)

        layout = QVBoxLayout(self.card)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(10)
        layout.addWidget(self.title_label)
        layout.addWidget(self.target_label)
        layout.addWidget(self.permission_hint)
        layout.addWidget(self.old_password_label)
        layout.addWidget(self.old_password_edit)
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

        self._apply_permission_block()
        self.cancel_button.setVisible(not force_change)

    def reject(self) -> None:
        if self._force_change:
            self.show_error("请先修改默认密码后再进入系统。")
            return
        super().reject()

    def submit(self) -> None:
        if self._submitting:
            return
        if self._blocked_by_permission:
            self.show_permission_denied()
            return
        if not self._validate_form():
            return
        self._set_submitting(True)
        try:
            if self._auth_service is None or not hasattr(self._auth_service, "change_password"):
                self.show_error("密码修改失败，请稍后重试。")
                return
            result = self._auth_service.change_password(
                self._session,
                self.old_password_edit.text(),
                self.new_password_edit.text(),
            )
            if bool(getattr(result, "success", False)):
                self.clear_error()
                self.success_label.set_safe_text(PASSWORD_CHANGE_SUCCESS_TEXT)
                self.success_label.setVisible(True)
                self.passwordChanged.emit()
                self.accept()
            elif int(getattr(result, "code", 0) or 0) == int(ErrorCode.PERMISSION_DENIED):
                self.show_permission_denied()
            else:
                self.show_error(getattr(result, "message", "密码修改失败，请稍后重试。"))
        except Exception:
            self.show_error("密码修改失败，请稍后重试。")
        finally:
            self._set_submitting(False)

    def show_permission_denied(self) -> None:
        self.permission_hint.show_denied()
        self.show_error("当前账号无权限执行此操作，已记录权限失败事件。")
        self.passwordChangeFailed.emit(self.permission_hint.message_label.text())

    def show_error(self, message: object) -> None:
        self.error_hint.set_safe_text(controlled_error_text(message, fallback="密码修改失败，请稍后重试。"))
        self.error_hint.setVisible(True)

    def clear_error(self) -> None:
        for editor in (self.old_password_edit, self.new_password_edit, self.confirm_password_edit):
            editor.setProperty("validation", None)
            _repolish(editor)
        self.error_hint.clear()
        self.success_label.set_safe_text("")
        self.success_label.setVisible(False)

    def _validate_form(self) -> bool:
        self.clear_error()
        has_all_passwords = all(
            (self.old_password_edit.text(), self.new_password_edit.text(), self.confirm_password_edit.text())
        )
        if not has_all_passwords:
            self.show_error("请完整填写密码信息。")
            return False
        if not 8 <= len(self.new_password_edit.text()) <= 128:
            self.new_password_edit.setProperty("validation", "error")
            _repolish(self.new_password_edit)
            self.show_error(PASSWORD_POLICY_TEXT)
            return False
        return self._validate_confirmation(show_when_empty=True)

    def _validate_confirmation(self, show_when_empty: bool = False) -> bool:
        new_password = self.new_password_edit.text()
        confirm_password = self.confirm_password_edit.text()
        mismatch = bool(confirm_password or show_when_empty) and new_password != confirm_password
        self.confirm_password_edit.setProperty("validation", "error" if mismatch else None)
        _repolish(self.confirm_password_edit)
        if mismatch:
            # Only mismatch state is shown; neither password value is ever copied
            # into labels, logs, signals or errors.
            self.error_hint.set_safe_text(PASSWORD_MISMATCH_TEXT)
            self.error_hint.setVisible(True)
        elif self.error_hint.text() == PASSWORD_MISMATCH_TEXT:
            self.error_hint.clear()
        self.submit_button.setEnabled(not mismatch and not self._blocked_by_permission and not self._submitting)
        return not mismatch

    def _apply_permission_block(self) -> None:
        for widget in (self.old_password_edit, self.new_password_edit, self.confirm_password_edit, self.submit_button):
            widget.setEnabled(not self._blocked_by_permission)
        if self._blocked_by_permission:
            self.permission_hint.show_denied()

    def _set_submitting(self, submitting: bool) -> None:
        self._submitting = submitting
        enabled = not submitting and not self._blocked_by_permission
        for editor in (self.old_password_edit, self.new_password_edit, self.confirm_password_edit):
            editor.setEnabled(enabled)
        self.submit_button.setEnabled(enabled)
        self.submit_button.setText("修改中..." if submitting else "确认修改")


def _repolish(widget: QWidget) -> None:
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()
