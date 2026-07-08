from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.ui.common.errors import LICENSE_FAILED_MESSAGE, ValidationHint, controlled_error_text
from app.ui.common.safe_text import SafeTextLabel, normalize_plain_text

AUTHORIZATION_FILE_MAX_CHARS = 4096
AUTHORIZATION_FILE_ERROR_MESSAGE = "授权文件不可用或已损坏"


class LicenseDialog(QDialog):
    activated = Signal(object)
    activationFailed = Signal(str)

    def __init__(
        self,
        license_service: object | None = None,
        parent: QWidget | None = None,
        *,
        actor: object | None = None,
        product_name: str = "气体安全报警监控系统",
    ) -> None:
        super().__init__(parent)
        self._license_service = license_service
        self._actor = actor
        self._submitting = False
        self._selected_file: Path | None = None
        self._machine_identifier = ""

        self.setWindowTitle("软件授权")
        self.setModal(True)
        self.resize(520, 430)

        self.card = QFrame(self)
        self.card.setObjectName("LicenseCard")
        self.card.setProperty("panel", "true")

        self.title_label = SafeTextLabel(product_name, selectable=False)
        self.title_label.setObjectName("ProductTitle")
        self.subtitle_label = SafeTextLabel("请输入授权码或导入授权文件完成授权。", selectable=False)
        self.subtitle_label.setProperty("role", "muted")

        self.status_label = SafeTextLabel("授权状态待检查", selectable=False)
        self.status_label.setObjectName("LicenseState")
        self.machine_label = SafeTextLabel("机器标识：<待检查>", selectable=True)
        self.machine_label.setProperty("role", "muted")
        self.copy_machine_button = QPushButton("复制机器码")
        self.copy_machine_button.clicked.connect(self.copy_machine_identifier)
        self.expires_label = SafeTextLabel("", selectable=False)
        self.expires_label.setProperty("role", "muted")

        self.code_label = QLabel("授权码")
        self.code_label.setProperty("role", "fieldLabel")
        self.authorization_code_edit = QPlainTextEdit()
        self.authorization_code_edit.setPlaceholderText("粘贴供应商提供的授权码")
        self.authorization_code_edit.setTabChangesFocus(True)
        self.authorization_code_edit.setMaximumHeight(96)
        self.error_hint = ValidationHint()
        self.error_hint.clear()

        self.file_label = SafeTextLabel("未选择授权文件", selectable=True, max_chars=256)
        self.file_label.setProperty("role", "muted")
        self.import_button = QPushButton("导入授权文件")
        self.validate_button = QPushButton("校验授权")
        self.validate_button.setProperty("variant", "primary")
        self.cancel_button = QPushButton("取消")

        self.import_button.clicked.connect(self.choose_authorization_file)
        self.validate_button.clicked.connect(self.submit)
        self.cancel_button.clicked.connect(self.reject)

        actions = QHBoxLayout()
        actions.addWidget(self.import_button)
        actions.addStretch(1)
        actions.addWidget(self.cancel_button)
        actions.addWidget(self.validate_button)

        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(24, 24, 24, 24)
        card_layout.setSpacing(12)
        card_layout.addWidget(self.title_label)
        card_layout.addWidget(self.subtitle_label)
        card_layout.addWidget(self.status_label)
        card_layout.addWidget(self.machine_label)
        card_layout.addWidget(self.copy_machine_button)
        card_layout.addWidget(self.expires_label)
        card_layout.addWidget(self.code_label)
        card_layout.addWidget(self.authorization_code_edit)
        card_layout.addWidget(self.error_hint)
        card_layout.addWidget(self.file_label)
        card_layout.addLayout(actions)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.addWidget(self.card)

        self.refresh_status()

    def refresh_status(self) -> None:
        status = self._get_status()
        if status is None:
            self._set_status_text("授权状态待检查", "warning")
            self.machine_label.set_safe_text("机器标识：<不可用>")
            self._machine_identifier = ""
            self.copy_machine_button.setEnabled(False)
            self.expires_label.set_safe_text("")
            return

        state = str(getattr(status, "status", "unknown"))
        if bool(getattr(status, "is_active", False)):
            self._set_status_text("授权状态：已授权", "valid")
        elif state == "expired":
            self._set_status_text("授权状态：已过期", "invalid")
        else:
            self._set_status_text("授权状态：未授权", "invalid")
        self._machine_identifier = normalize_plain_text(
            getattr(status, "machine_fingerprint_hash", ""),
            max_chars=128,
        ).strip()
        self.copy_machine_button.setEnabled(bool(self._machine_identifier))
        self.machine_label.set_safe_text(f"机器标识：{_mask_machine_identifier(self._machine_identifier)}")
        expires_at = getattr(status, "expires_at", None)
        self.expires_label.set_safe_text(f"到期时间：{expires_at}" if expires_at else "")
        self.expires_label.setVisible(bool(expires_at))

    def choose_authorization_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择授权文件", "", "授权文件 (*.lic *.txt *.json);;所有文件 (*)")
        if path:
            self.import_authorization_file(path)

    def import_authorization_file(self, file_path: str | Path) -> bool:
        path = Path(file_path)
        try:
            if not path.is_file():
                raise OSError("license file is not available")
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            self.show_authorization_file_error()
            return False
        if not content.strip() or len(content) > AUTHORIZATION_FILE_MAX_CHARS:
            self.show_authorization_file_error()
            return False
        self._selected_file = path
        self.authorization_code_edit.setPlainText(content.strip())
        self.file_label.set_safe_text(f"已导入：{path.name}")
        self.clear_error()
        return True

    def submit(self) -> None:
        if self._submitting:
            return
        code = self.authorization_code_edit.toPlainText().strip()
        if not code:
            self.show_validation_error("请输入授权码或导入授权文件。")
            return
        self._set_submitting(True)
        try:
            if self._license_service is None or not hasattr(self._license_service, "activate"):
                self.show_activation_failed()
                return
            result = self._activate(code)
            if bool(getattr(result, "success", False)):
                self.clear_error()
                status = getattr(result, "data", None)
                if status is not None:
                    self._apply_status(status)
                else:
                    self.refresh_status()
                self.activated.emit(status)
                self.accept()
            else:
                self.show_activation_failed()
        except Exception:
            self.show_activation_failed()
        finally:
            self._set_submitting(False)

    def show_authorization_file_error(self) -> None:
        self.show_validation_error(AUTHORIZATION_FILE_ERROR_MESSAGE)
        self.file_label.set_safe_text(AUTHORIZATION_FILE_ERROR_MESSAGE)

    def show_activation_failed(self) -> None:
        # License-service errors may include algorithm, signature or machine details;
        # the authorization UI always collapses them to the approved public message.
        self.authorization_code_edit.setProperty("validation", "error")
        self.error_hint.set_safe_text(LICENSE_FAILED_MESSAGE)
        self.error_hint.setVisible(True)
        _repolish(self.authorization_code_edit)
        self.activationFailed.emit(LICENSE_FAILED_MESSAGE)

    def copy_machine_identifier(self) -> bool:
        if not self._machine_identifier:
            return False
        QApplication.clipboard().setText(self._machine_identifier)
        self.file_label.set_safe_text("机器码已复制")
        return True

    def show_validation_error(self, message: object) -> None:
        self.authorization_code_edit.setProperty("validation", "error")
        self.error_hint.set_validation_error(controlled_error_text(message, fallback="输入内容校验失败，请检查后重试。"))
        _repolish(self.authorization_code_edit)

    def clear_error(self) -> None:
        self.authorization_code_edit.setProperty("validation", None)
        self.error_hint.clear()
        _repolish(self.authorization_code_edit)

    def _activate(self, code: str) -> Any:
        activate = getattr(self._license_service, "activate")
        if self._actor is None:
            return activate(code)
        return activate(code, self._actor)

    def _get_status(self) -> object | None:
        if self._license_service is None or not hasattr(self._license_service, "get_license_status"):
            return None
        try:
            return self._license_service.get_license_status()
        except Exception:
            return None

    def _apply_status(self, status: object) -> None:
        is_active = bool(getattr(status, "is_active", False))
        self._set_status_text("授权状态：已授权" if is_active else "授权状态：未授权", "valid" if is_active else "invalid")
        self._machine_identifier = normalize_plain_text(
            getattr(status, "machine_fingerprint_hash", ""),
            max_chars=128,
        ).strip()
        self.copy_machine_button.setEnabled(bool(self._machine_identifier))
        self.machine_label.set_safe_text(f"机器标识：{_mask_machine_identifier(self._machine_identifier)}")
        expires_at = getattr(status, "expires_at", None)
        self.expires_label.set_safe_text(f"到期时间：{expires_at}" if expires_at else "")
        self.expires_label.setVisible(bool(expires_at))

    def _set_status_text(self, text: str, status: str) -> None:
        self.status_label.set_safe_text(text)
        self.status_label.setProperty("status", status)
        _repolish(self.status_label)

    def _set_submitting(self, submitting: bool) -> None:
        self._submitting = submitting
        # The service call is synchronous in the current backend; disabling both
        # submit and import buttons prevents double activation while it is running.
        self.validate_button.setEnabled(not submitting)
        self.import_button.setEnabled(not submitting)
        self.validate_button.setText("校验中..." if submitting else "校验授权")


def _mask_machine_identifier(value: object) -> str:
    text = normalize_plain_text(value, max_chars=128).strip()
    if len(text) < 16:
        return "<已隐藏>"
    # Show only a digest prefix/suffix so one-machine-one-code binding can be
    # checked visually without exposing the full machine fingerprint material.
    return f"{text[:8]}...{text[-4:]}"


def _repolish(widget: QWidget) -> None:
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()
