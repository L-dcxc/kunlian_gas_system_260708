from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFileDialog, QFrame, QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from app.services.backup_service import RestoreConfirm
from app.services.errors import ErrorCode
from app.ui.common.dialogs import RiskConfirmDialog
from app.ui.common.errors import ErrorBanner, controlled_error_text
from app.ui.common.permission_hint import PermissionHint
from app.ui.common.safe_text import SafeTextLabel, normalize_plain_text
from app.ui.common.status import repolish

RESTORE_FAILED_TEXT = "恢复失败，请使用安全备份并联系管理员。"
RESTORE_VALID_CONFIRM_TEXT = "备份文件结构已通过初步校验，恢复前仍需确认覆盖风险。"
STOP_ACQUISITION_TEXT = "请先停止采集服务"
RISK_TEXT = "恢复将覆盖当前数据；恢复前需停止采集服务；建议先执行一次手动备份。"

PathProvider = Callable[[], Path | str | None]


class RestorePanel(QFrame):
    restoreCompleted = Signal()

    def __init__(
        self,
        backup_service: object | None = None,
        session: object | None = None,
        parent: QWidget | None = None,
        *,
        can_manage: bool = True,
        file_provider: PathProvider | None = None,
        confirm_restore: object | None = None,
    ) -> None:
        super().__init__(parent)
        self.setProperty("panel", "true")
        self._service = backup_service
        self._session = session
        self._can_manage = can_manage
        self._file_provider = file_provider or self._choose_file
        self._confirm_restore = confirm_restore or _confirm_restore_dialog
        self._backup_file: Path | None = None
        self._busy = False
        self._acquisition_running = False

        self.error_banner = ErrorBanner(); self.error_banner.clear()
        self.permission_hint = PermissionHint(); self.permission_hint.setVisible(not can_manage)
        self.title_label = SafeTextLabel("数据恢复", selectable=False)
        self.title_label.setProperty("role", "panelTitle")
        self.risk_label = SafeTextLabel(RISK_TEXT, selectable=True)
        self.risk_label.setProperty("role", "warningText")
        self.file_label = SafeTextLabel("未选择备份文件", selectable=True)
        self.file_label.setProperty("role", "muted")
        self.browse_button = QPushButton("选择备份文件")
        self.check_button = QPushButton("恢复前检查")
        self.restore_button = QPushButton("确认恢复")
        self.restore_button.setProperty("variant", "danger")
        self.hint_label = SafeTextLabel("恢复将覆盖当前数据", selectable=False)
        self.hint_label.setProperty("role", "warningText")
        self.result_card = QFrame()
        self.result_card.setProperty("backupResult", "success")
        self.result_title = SafeTextLabel("", selectable=False)
        self.result_detail = SafeTextLabel("", selectable=True)
        result_layout = QVBoxLayout(self.result_card)
        result_layout.setContentsMargins(12, 10, 12, 10)
        result_layout.addWidget(self.result_title)
        result_layout.addWidget(self.result_detail)
        self.result_card.setVisible(False)

        self.browse_button.clicked.connect(self.select_restore_file)
        self.check_button.clicked.connect(self.check_backup_file)
        self.restore_button.clicked.connect(self.restore_selected)

        actions = QHBoxLayout()
        actions.addWidget(self.browse_button)
        actions.addWidget(self.check_button)
        actions.addWidget(self.restore_button)
        actions.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        layout.addWidget(self.error_banner)
        layout.addWidget(self.permission_hint)
        layout.addWidget(self.title_label)
        layout.addWidget(self.risk_label)
        layout.addWidget(self.file_label)
        layout.addLayout(actions)
        layout.addWidget(self.hint_label)
        layout.addWidget(self.result_card)
        self._apply_state()

    def refresh_acquisition_state(self, running: bool) -> None:
        self._acquisition_running = bool(running)
        self.hint_label.set_safe_text(STOP_ACQUISITION_TEXT if running else "恢复将覆盖当前数据")
        self._apply_state()

    def select_restore_file(self) -> None:
        if not self._require_permission():
            return
        path = _provider_path(self._file_provider)
        if path is None:
            return
        self._backup_file = path
        self.file_label.set_safe_text(f"文件：{_safe_file_name(path)}")
        self.result_card.setVisible(False)
        self._apply_state()

    def check_backup_file(self) -> None:
        if not self._require_permission() or self._backup_file is None:
            self._show_result(False, "请选择备份文件")
            return
        if self._backup_file.suffix.lower() != ".zip":
            self._show_result(False, "备份文件类型无效")
            return
        if self._service is None or not hasattr(self._service, "restore_from_backup"):
            self._show_result(False, "备份服务未配置")
            return
        # The UI asks the service to validate with confirmed=False instead of
        # inspecting zip internals; package structure and traversal checks stay
        # inside the backup service boundary.
        try:
            result = self._service.restore_from_backup(
                self._session,
                self._backup_file,
                RestoreConfirm(confirmed=False),
            )
        except Exception:
            self._show_result(False, "备份文件校验失败")
            return
        if bool(getattr(result, "success", False)):
            self._show_result(True, RESTORE_VALID_CONFIRM_TEXT)
            return
        if int(getattr(result, "code", 0) or 0) == int(ErrorCode.PERMISSION_DENIED):
            self.error_banner.show_permission_denied()
            return
        message = str(getattr(result, "message", ""))
        if "显式确认" in message:
            self._show_result(True, RESTORE_VALID_CONFIRM_TEXT)
        else:
            self._show_result(False, message or "备份文件校验失败")

    def restore_selected(self) -> None:
        if self._busy or not self._require_permission():
            return
        if self._acquisition_running:
            self.hint_label.set_safe_text(STOP_ACQUISITION_TEXT)
            self._apply_state()
            return
        if self._backup_file is None:
            self._show_result(False, "请选择备份文件")
            return
        if self._backup_file.suffix.lower() != ".zip":
            self._show_result(False, "备份文件类型无效")
            return
        confirmed = bool(self._confirm_restore(self, _safe_file_name(self._backup_file)))
        if not confirmed:
            return
        if self._service is None or not hasattr(self._service, "restore_from_backup"):
            self._show_result(False, "备份服务未配置")
            return
        self._set_busy(True)
        try:
            result = self._service.restore_from_backup(
                self._session,
                self._backup_file,
                RestoreConfirm(confirmed=True),
            )
        except Exception:
            self._set_busy(False)
            self._show_result(False, RESTORE_FAILED_TEXT)
            return
        self._set_busy(False)
        if bool(getattr(result, "success", False)):
            data = getattr(result, "data", None)
            message = getattr(data, "message", None) or getattr(result, "message", "恢复完成，请重启或重新加载应用数据。")
            restored_count = len(tuple(getattr(data, "restored_files", ()) or ())) if data is not None else 0
            self._show_result(True, f"{message}\n恢复文件数：{restored_count}")
            self.restoreCompleted.emit()
            return
        if int(getattr(result, "code", 0) or 0) == int(ErrorCode.PERMISSION_DENIED):
            self.error_banner.show_permission_denied()
        else:
            self._show_result(False, getattr(result, "message", RESTORE_FAILED_TEXT))

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._apply_state()

    def _show_result(self, success: bool, message: object) -> None:
        # Restore errors can originate from untrusted archives; display only the
        # public reason after path/stack/secret redaction.
        self.result_card.setProperty("backupResult", "success" if success else "error")
        self.result_title.set_safe_text("检查通过" if success else "恢复检查失败")
        text = normalize_plain_text(message, max_chars=256) if success else _restore_error_text(message)
        self.result_detail.set_safe_text(text)
        self.result_card.setVisible(True)
        repolish(self.result_card)

    def _require_permission(self) -> bool:
        if self._can_manage:
            return True
        self.permission_hint.show_denied()
        self.error_banner.show_permission_denied()
        return False

    def _apply_state(self) -> None:
        can_use = self._can_manage and not self._busy
        self.browse_button.setEnabled(can_use)
        self.check_button.setEnabled(can_use and self._backup_file is not None)
        self.restore_button.setEnabled(can_use and self._backup_file is not None and not self._acquisition_running)
        if self._acquisition_running:
            self.restore_button.setToolTip(STOP_ACQUISITION_TEXT)
        else:
            self.restore_button.setToolTip("")

    def _choose_file(self) -> Path | None:
        path, _ = QFileDialog.getOpenFileName(self, "选择备份文件", "", "备份文件 (*.zip);;所有文件 (*)")
        return Path(path) if path else None


def _confirm_restore_dialog(parent: QWidget, file_name: str) -> bool:
    message = f"即将恢复备份文件：{file_name}\n{RISK_TEXT}"
    return RiskConfirmDialog.confirm("确认恢复备份", message, parent, confirm_text="确认风险并恢复")


def _provider_path(provider: PathProvider) -> Path | None:
    value = provider() if callable(provider) else provider
    if value in {None, ""}:
        return None
    return Path(value)


def _restore_error_text(message: object) -> str:
    raw = "" if message is None else str(message)
    lowered = raw.lower()
    if ".." in raw or "../" in raw or "路径" in raw or "path" in lowered or "traversal" in lowered:
        return "备份文件结构无效"
    return controlled_error_text(raw, fallback=RESTORE_FAILED_TEXT)


def _safe_file_name(value: object) -> str:
    text = normalize_plain_text(value, max_chars=180).replace("\\", "/")
    name = text.rsplit("/", 1)[-1].strip()
    return controlled_error_text(name, fallback="-", max_chars=180) or "-"


__all__ = ["RestorePanel"]
