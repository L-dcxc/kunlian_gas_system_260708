from __future__ import annotations

from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from app.ui.common.errors import ValidationHint, controlled_error_text
from app.ui.common.safe_text import SafeTextLabel

LOGIN_FAILED_TEXT = "账号或密码错误"
UNLICENSED_BLOCK_TEXT = "软件未授权，请先完成授权。"
INITIAL_ADMIN_HINT_TEXT = "请创建初始管理员账号"


class LoginWindow(QWidget):
    loggedIn = Signal(object)
    loginFailed = Signal(str)
    licenseRequested = Signal()
    changePasswordRequested = Signal(str)
    initialAdminRequested = Signal()

    def __init__(
        self,
        auth_service: object | None = None,
        license_service: object | None = None,
        parent: QWidget | None = None,
        *,
        product_name: str = "气体安全报警监控系统",
        initialization_required: bool = False,
    ) -> None:
        super().__init__(parent)
        self._auth_service = auth_service
        self._license_service = license_service
        self._submitting = False

        self.setWindowTitle("系统登录")
        self.resize(480, 420)

        self.card = QFrame(self)
        self.card.setObjectName("LoginCard")
        self.card.setProperty("panel", "true")

        self.title_label = SafeTextLabel(product_name, selectable=False)
        self.title_label.setObjectName("ProductTitle")
        self.subtitle_label = SafeTextLabel("请输入账号和密码进入监控系统。", selectable=False)
        self.subtitle_label.setProperty("role", "muted")

        self.license_state_label = SafeTextLabel("授权状态待检查", selectable=False)
        self.license_state_label.setObjectName("LicenseState")
        self.refresh_license_status()

        self.initialization_hint = SafeTextLabel(INITIAL_ADMIN_HINT_TEXT, selectable=False)
        self.initialization_hint.setProperty("role", "warningText")
        self.initialization_hint.setVisible(initialization_required)
        self.initial_admin_button = QPushButton("创建初始管理员账号")
        self.initial_admin_button.setVisible(initialization_required)
        self.initial_admin_button.clicked.connect(self.initialAdminRequested.emit)

        self.username_label = QLabel("账号")
        self.username_label.setProperty("role", "fieldLabel")
        self.username_edit = QLineEdit()
        self.username_edit.setPlaceholderText("请输入账号")
        self.username_edit.setMaxLength(80)
        self.username_edit.returnPressed.connect(self.submit)

        self.password_label = QLabel("密码")
        self.password_label.setProperty("role", "fieldLabel")
        self.password_edit = QLineEdit()
        self.password_edit.setPlaceholderText("请输入密码")
        self.password_edit.setMaxLength(256)
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_edit.returnPressed.connect(self.submit)

        self.error_hint = ValidationHint()
        self.error_hint.clear()

        self.login_button = QPushButton("登录")
        self.login_button.setProperty("variant", "primary")
        self.login_button.clicked.connect(self.submit)
        self.license_button = QPushButton("软件授权")
        self.license_button.clicked.connect(self.licenseRequested.emit)
        self.change_password_button = QPushButton("修改密码")
        self.change_password_button.clicked.connect(self._request_change_password)

        actions = QHBoxLayout()
        actions.addWidget(self.license_button)
        actions.addWidget(self.change_password_button)
        actions.addStretch(1)
        actions.addWidget(self.login_button)

        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(24, 24, 24, 24)
        card_layout.setSpacing(12)
        card_layout.addWidget(self.title_label)
        card_layout.addWidget(self.subtitle_label)
        card_layout.addWidget(self.license_state_label)
        card_layout.addWidget(self.initialization_hint)
        card_layout.addWidget(self.initial_admin_button)
        card_layout.addWidget(self.username_label)
        card_layout.addWidget(self.username_edit)
        card_layout.addWidget(self.password_label)
        card_layout.addWidget(self.password_edit)
        card_layout.addWidget(self.error_hint)
        card_layout.addLayout(actions)

        root = QVBoxLayout(self)
        root.setContentsMargins(32, 32, 32, 32)
        root.addStretch(1)
        root.addWidget(self.card, 0, Qt.AlignmentFlag.AlignCenter)
        root.addStretch(1)

    def set_initialization_required(self, required: bool) -> None:
        self.initialization_hint.setVisible(required)
        self.initial_admin_button.setVisible(required)

    def refresh_license_status(self) -> None:
        status = self._get_license_status()
        if status is None:
            self._set_license_state("授权状态待检查", "warning")
            return
        if bool(getattr(status, "can_enter_main_system", False)):
            self._set_license_state("授权状态：已授权", "valid")
        else:
            self._set_license_state("授权状态：未授权", "invalid")

    def submit(self) -> None:
        if self._submitting:
            return
        self.clear_error()
        if not self._can_enter_main_system():
            self.show_error(UNLICENSED_BLOCK_TEXT, field="license")
            return
        username = self.username_edit.text().strip()
        password = self.password_edit.text()
        if not username or not password:
            self.show_error("请输入账号和密码。")
            return
        self._set_submitting(True)
        try:
            if self._auth_service is None or not hasattr(self._auth_service, "login"):
                self.show_login_failed()
                return
            result = self._auth_service.login(username, password)
            if bool(getattr(result, "success", False)):
                self.clear_error()
                self.loggedIn.emit(getattr(result, "data", None))
            else:
                self.show_login_failed()
        except Exception:
            self.show_login_failed()
        finally:
            self._set_submitting(False)

    def show_login_failed(self) -> None:
        # Authentication failures must not distinguish missing accounts, bad
        # passwords, disabled users, service exceptions or permission details.
        self.show_error(LOGIN_FAILED_TEXT, field="password", fixed=True)
        self.loginFailed.emit(LOGIN_FAILED_TEXT)

    def show_error(self, message: object, *, field: str = "password", fixed: bool = False) -> None:
        text = str(message) if fixed else controlled_error_text(message, fallback="输入内容校验失败，请检查后重试。")
        if field == "license":
            self.license_state_label.setProperty("status", "invalid")
            _repolish(self.license_state_label)
        else:
            self.password_edit.setProperty("validation", "error")
            _repolish(self.password_edit)
        self.error_hint.set_safe_text(text)
        self.error_hint.setVisible(True)

    def clear_error(self) -> None:
        self.password_edit.setProperty("validation", None)
        self.error_hint.clear()
        _repolish(self.password_edit)

    def _request_change_password(self) -> None:
        self.changePasswordRequested.emit(self.username_edit.text().strip())

    def _get_license_status(self) -> object | None:
        if self._license_service is None or not hasattr(self._license_service, "get_license_status"):
            return None
        try:
            return self._license_service.get_license_status()
        except Exception:
            return None

    def _can_enter_main_system(self) -> bool:
        if self._license_service is None:
            # [待确认] Demo mode is not confirmed; login UI defaults to blocking
            # when no license service/status can prove main-system access.
            return False
        status = self._get_license_status()
        return bool(getattr(status, "can_enter_main_system", False))

    def _set_license_state(self, text: str, status: str) -> None:
        self.license_state_label.set_safe_text(text)
        self.license_state_label.setProperty("status", status)
        _repolish(self.license_state_label)

    def _set_submitting(self, submitting: bool) -> None:
        self._submitting = submitting
        # Synchronous AuthService calls return quickly in tests, but the button
        # still enters a disabled loading state to prevent Enter/click repeats.
        self.login_button.setEnabled(not submitting)
        self.username_edit.setEnabled(not submitting)
        self.password_edit.setEnabled(not submitting)
        self.login_button.setText("登录中..." if submitting else "登录")


def _repolish(widget: QWidget) -> None:
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()
