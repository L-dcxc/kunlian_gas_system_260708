from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.services.errors import ErrorCode
from app.services.permissions import Role
from app.services.user_service import CreateUserCommand, UpdateUserCommand
from app.ui.common.dialogs import RiskConfirmDialog
from app.ui.common.errors import ValidationHint, controlled_error_text
from app.ui.common.permission_hint import PermissionHint
from app.ui.common.safe_text import SafeTextLabel

PASSWORD_POLICY_TEXT = "密码长度需为 8 至 128 位。"
PASSWORD_MISMATCH_TEXT = "两次输入的密码不一致。"
USER_SAVE_FAILED_TEXT = "用户保存失败，请稍后重试。"


@dataclass(frozen=True, slots=True)
class EditableUser:
    id: int
    username: str
    role: str
    is_active: bool


class UserEditorDialog(QDialog):
    def __init__(
        self,
        user_service: object | None = None,
        session: object | None = None,
        parent: QWidget | None = None,
        *,
        user: object | None = None,
        can_manage_users: bool = True,
    ) -> None:
        super().__init__(parent)
        self._user_service = user_service
        self._session = session
        self._user = _coerce_user(user)
        self._submitting = False
        self._blocked_by_permission = not can_manage_users
        self.setWindowTitle("编辑用户" if self._user else "新增用户")
        self.setModal(True)
        self.resize(460, 420)

        self.card = QFrame(self)
        self.card.setProperty("panel", "true")
        self.title_label = SafeTextLabel(self.windowTitle(), selectable=False)
        self.title_label.setProperty("role", "dialogTitle")
        self.permission_hint = PermissionHint()
        self.permission_hint.setVisible(self._blocked_by_permission)

        self.username_label = QLabel("用户名")
        self.username_label.setProperty("role", "fieldLabel")
        self.username_edit = QLineEdit()
        self.username_edit.setMaxLength(80)
        self.username_edit.setPlaceholderText("3 至 80 位，可含字母、数字、_-." )

        self.role_label = QLabel("角色")
        self.role_label.setProperty("role", "fieldLabel")
        self.role_combo = QComboBox()
        self.role_combo.addItem("管理员", Role.ADMIN.value)
        self.role_combo.addItem("操作员", Role.OPERATOR.value)

        self.active_checkbox = QCheckBox("启用账号")
        self.active_checkbox.setChecked(True)

        self.password_label = QLabel("初始密码" if self._user is None else "重置密码")
        self.password_label.setProperty("role", "fieldLabel")
        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_edit.setMaxLength(128)
        self.password_edit.setPlaceholderText("8 至 128 位" if self._user is None else "留空则不修改")
        self.confirm_password_edit = QLineEdit()
        self.confirm_password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.confirm_password_edit.setMaxLength(128)
        self.confirm_password_edit.setPlaceholderText("再次输入密码")

        self.error_hint = ValidationHint()
        self.error_hint.clear()
        self.cancel_button = QPushButton("取消")
        self.submit_button = QPushButton("保存")
        self.submit_button.setProperty("variant", "primary")
        self.cancel_button.clicked.connect(self.reject)
        self.submit_button.clicked.connect(self.submit)
        self.confirm_password_edit.returnPressed.connect(self.submit)
        self.password_edit.textChanged.connect(self._validate_password_confirmation)
        self.confirm_password_edit.textChanged.connect(self._validate_password_confirmation)

        actions = QHBoxLayout()
        actions.addStretch(1)
        actions.addWidget(self.cancel_button)
        actions.addWidget(self.submit_button)

        layout = QVBoxLayout(self.card)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(10)
        for widget in (
            self.title_label,
            self.permission_hint,
            self.username_label,
            self.username_edit,
            self.role_label,
            self.role_combo,
            self.active_checkbox,
            self.password_label,
            self.password_edit,
            self.confirm_password_edit,
            self.error_hint,
        ):
            layout.addWidget(widget)
        layout.addLayout(actions)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.addWidget(self.card)
        self._apply_user()
        self._apply_permission_block()

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
            result = self._save()
            if bool(getattr(result, "success", False)):
                self.accept()
            elif int(getattr(result, "code", 0) or 0) == int(ErrorCode.PERMISSION_DENIED):
                self.show_permission_denied()
            else:
                # Admin uniqueness and duplicate usernames belong to UserService;
                # this dialog only renders the controlled service failure text.
                self.show_error(getattr(result, "message", USER_SAVE_FAILED_TEXT))
        except Exception:
            self.show_error(USER_SAVE_FAILED_TEXT)
        finally:
            self._set_submitting(False)

    def show_permission_denied(self) -> None:
        self.permission_hint.show_denied()
        self.show_error("当前账号无权限执行此操作，已记录权限失败事件。")

    def show_error(self, message: object) -> None:
        self.error_hint.set_safe_text(controlled_error_text(message, fallback=USER_SAVE_FAILED_TEXT))
        self.error_hint.setVisible(True)

    def clear_error(self) -> None:
        for editor in (self.username_edit, self.password_edit, self.confirm_password_edit):
            editor.setProperty("validation", None)
            _repolish(editor)
        self.error_hint.clear()

    def _save(self) -> object:
        if self._user_service is None:
            raise RuntimeError("user service is required")
        if self._user is None:
            return self._user_service.create_user(self._session, self._create_command())
        return self._user_service.update_user(self._session, self._user.id, self._update_command())

    def _create_command(self) -> CreateUserCommand:
        return CreateUserCommand(
            username=self.username_edit.text(),
            password=self.password_edit.text(),
            role=str(self.role_combo.currentData()),
            is_active=self.active_checkbox.isChecked(),
        )

    def _update_command(self) -> UpdateUserCommand:
        password = self.password_edit.text() or None
        return UpdateUserCommand(
            username=self.username_edit.text(),
            role=str(self.role_combo.currentData()),
            is_active=self.active_checkbox.isChecked(),
            password=password,
        )

    def _validate_form(self) -> bool:
        self.clear_error()
        if not self.username_edit.text().strip():
            self.username_edit.setProperty("validation", "error")
            _repolish(self.username_edit)
            self.show_error("用户名不能为空。")
            return False
        password = self.password_edit.text()
        password_required = self._user is None
        if password_required or password:
            if not 8 <= len(password) <= 128:
                self.password_edit.setProperty("validation", "error")
                _repolish(self.password_edit)
                self.show_error(PASSWORD_POLICY_TEXT)
                return False
            return self._validate_password_confirmation(show_when_empty=True)
        return True

    def _validate_password_confirmation(self, show_when_empty: bool = False) -> bool:
        password = self.password_edit.text()
        confirm = self.confirm_password_edit.text()
        mismatch = bool(confirm or show_when_empty) and password != confirm
        self.confirm_password_edit.setProperty("validation", "error" if mismatch else None)
        _repolish(self.confirm_password_edit)
        if mismatch:
            # Password values are never copied into labels or signals; only the
            # mismatch state is exposed to the user.
            self.error_hint.set_safe_text(PASSWORD_MISMATCH_TEXT)
            self.error_hint.setVisible(True)
        elif self.error_hint.text() == PASSWORD_MISMATCH_TEXT:
            self.error_hint.clear()
        return not mismatch

    def _apply_user(self) -> None:
        if self._user is None:
            return
        self.username_edit.setText(self._user.username)
        self.active_checkbox.setChecked(self._user.is_active)
        index = self.role_combo.findData(self._user.role)
        if index >= 0:
            self.role_combo.setCurrentIndex(index)

    def _apply_permission_block(self) -> None:
        enabled = not self._blocked_by_permission
        for widget in (self.username_edit, self.role_combo, self.active_checkbox, self.password_edit, self.confirm_password_edit, self.submit_button):
            widget.setEnabled(enabled)
        if self._blocked_by_permission:
            self.permission_hint.show_denied()

    def _set_submitting(self, submitting: bool) -> None:
        self._submitting = submitting
        enabled = not submitting and not self._blocked_by_permission
        for widget in (self.username_edit, self.role_combo, self.active_checkbox, self.password_edit, self.confirm_password_edit):
            widget.setEnabled(enabled)
        self.submit_button.setEnabled(enabled)
        self.submit_button.setText("保存中..." if submitting else "保存")


class ResetPasswordDialog(QDialog):
    def __init__(self, user_service: object | None, session: object | None, user: object, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._user_service = user_service
        self._session = session
        self._user = _coerce_user(user)
        if self._user is None:
            raise ValueError("user is required")
        self.setWindowTitle("重置密码")
        self.setModal(True)
        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_edit.setMaxLength(128)
        self.confirm_password_edit = QLineEdit()
        self.confirm_password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.confirm_password_edit.setMaxLength(128)
        self.error_hint = ValidationHint()
        self.error_hint.clear()
        self.cancel_button = QPushButton("取消")
        self.submit_button = QPushButton("确认重置")
        self.submit_button.setProperty("variant", "primary")
        self.cancel_button.clicked.connect(self.reject)
        self.submit_button.clicked.connect(self.submit)
        layout = QVBoxLayout(self)
        layout.addWidget(SafeTextLabel(f"账号：{self._user.username}", selectable=True))
        layout.addWidget(QLabel("新密码"))
        layout.addWidget(self.password_edit)
        layout.addWidget(QLabel("确认新密码"))
        layout.addWidget(self.confirm_password_edit)
        layout.addWidget(self.error_hint)
        actions = QHBoxLayout()
        actions.addStretch(1)
        actions.addWidget(self.cancel_button)
        actions.addWidget(self.submit_button)
        layout.addLayout(actions)

    def submit(self) -> None:
        if self.password_edit.text() != self.confirm_password_edit.text():
            self.error_hint.set_validation_error(PASSWORD_MISMATCH_TEXT)
            return
        if not 8 <= len(self.password_edit.text()) <= 128:
            self.error_hint.set_validation_error(PASSWORD_POLICY_TEXT)
            return
        try:
            result = self._user_service.update_user(
                self._session,
                self._user.id,
                UpdateUserCommand(password=self.password_edit.text()),
            )
        except Exception:
            self.error_hint.set_safe_text(USER_SAVE_FAILED_TEXT)
            self.error_hint.setVisible(True)
            return
        if bool(getattr(result, "success", False)):
            self.accept()
        else:
            self.error_hint.set_safe_text(controlled_error_text(getattr(result, "message", USER_SAVE_FAILED_TEXT)))
            self.error_hint.setVisible(True)


class RoleChangeDialog(QDialog):
    def __init__(
        self,
        user_service: object | None,
        session: object | None,
        user: object,
        parent: QWidget | None = None,
        *,
        confirm_danger: object | None = None,
    ) -> None:
        super().__init__(parent)
        self._user_service = user_service
        self._session = session
        self._user = _coerce_user(user)
        if self._user is None:
            raise ValueError("user is required")
        self._confirm_danger = confirm_danger or _confirm_role_change
        self.setWindowTitle("修改角色")
        self.setModal(True)
        self.role_combo = QComboBox()
        self.role_combo.addItem("管理员", Role.ADMIN.value)
        self.role_combo.addItem("操作员", Role.OPERATOR.value)
        index = self.role_combo.findData(self._user.role)
        if index >= 0:
            self.role_combo.setCurrentIndex(index)
        self.error_hint = ValidationHint()
        self.error_hint.clear()
        self.cancel_button = QPushButton("取消")
        self.submit_button = QPushButton("确认修改")
        self.submit_button.setProperty("variant", "primary")
        self.cancel_button.clicked.connect(self.reject)
        self.submit_button.clicked.connect(self.submit)
        layout = QVBoxLayout(self)
        layout.addWidget(SafeTextLabel(f"账号：{self._user.username}", selectable=True))
        layout.addWidget(self.role_combo)
        layout.addWidget(self.error_hint)
        actions = QHBoxLayout()
        actions.addStretch(1)
        actions.addWidget(self.cancel_button)
        actions.addWidget(self.submit_button)
        layout.addLayout(actions)

    def submit(self) -> None:
        next_role = str(self.role_combo.currentData())
        if next_role != self._user.role and not self._confirm_danger(self):
            return
        try:
            result = self._user_service.update_user(self._session, self._user.id, UpdateUserCommand(role=next_role))
        except Exception:
            self.error_hint.set_safe_text(USER_SAVE_FAILED_TEXT)
            self.error_hint.setVisible(True)
            return
        if bool(getattr(result, "success", False)):
            self.accept()
        else:
            self.error_hint.set_safe_text(controlled_error_text(getattr(result, "message", USER_SAVE_FAILED_TEXT)))
            self.error_hint.setVisible(True)


def _coerce_user(user: object | None) -> EditableUser | None:
    if user is None:
        return None
    return EditableUser(
        id=int(_value(user, "id", 0)),
        username=str(_value(user, "username", "")),
        role=str(_value(user, "role", Role.OPERATOR.value)),
        is_active=bool(_value(user, "is_active", True)),
    )


def _value(source: object, key: str, default: object = None) -> object:
    if isinstance(source, dict):
        return source.get(key, default)
    return getattr(source, key, default)


def _confirm_role_change(parent: QWidget) -> bool:
    return RiskConfirmDialog.confirm(
        "确认修改角色",
        "角色变更会影响账号权限，确认后旧会话可能失效。",
        parent,
        confirm_text="确认修改",
    )


def _repolish(widget: QWidget) -> None:
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()
