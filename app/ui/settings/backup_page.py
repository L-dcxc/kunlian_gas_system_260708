from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.services.errors import ErrorCode
from app.services.permissions import Permission, role_has_permission
from app.ui.common.errors import ErrorBanner, controlled_error_text
from app.ui.common.permission_hint import PermissionHint
from app.ui.common.safe_text import SafeTextLabel, normalize_plain_text
from app.ui.common.status import repolish
from app.ui.settings.backup_schedule_panel import BackupSchedulePanel
from app.ui.settings.restore_panel import RestorePanel

BACKUP_FAILED_TEXT = "备份失败，请稍后重试。"
BUSY_TEXT = "正在打包数据库、地图、配置"

PathProvider = Callable[[], Path | str | None]


class BackupRestorePage(QWidget):
    backupCompleted = Signal()
    restoreCompleted = Signal()

    def __init__(
        self,
        backup_service: object | None = None,
        session: object | None = None,
        parent: QWidget | None = None,
        *,
        can_manage: bool | None = None,
        manual_directory_provider: PathProvider | None = None,
        restore_file_provider: PathProvider | None = None,
        confirm_restore: object | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = backup_service
        self._session = session
        self._can_manage = _can_backup_from_session(session) if can_manage is None else can_manage

        self.error_banner = ErrorBanner(); self.error_banner.clear()
        self.permission_hint = PermissionHint(); self.permission_hint.setVisible(not self._can_manage)
        self.title_label = SafeTextLabel("备份恢复", selectable=False)
        self.title_label.setProperty("role", "panelTitle")

        self.manual_panel = ManualBackupPanel(
            backup_service,
            session,
            can_manage=self._can_manage,
            directory_provider=manual_directory_provider,
        )
        self.schedule_panel = BackupSchedulePanel(backup_service, session, can_manage=self._can_manage)
        self.restore_panel = RestorePanel(
            backup_service,
            session,
            can_manage=self._can_manage,
            file_provider=restore_file_provider,
            confirm_restore=confirm_restore,
        )
        self.manual_panel.backupCompleted.connect(self.backupCompleted.emit)
        self.restore_panel.restoreCompleted.connect(self.restoreCompleted.emit)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        layout.addWidget(self.error_banner)
        layout.addWidget(self.title_label)
        layout.addWidget(self.permission_hint)
        layout.addWidget(self.manual_panel)
        layout.addWidget(self.schedule_panel)
        layout.addWidget(self.restore_panel)
        layout.addStretch(1)

    def reload(self) -> None:
        self.schedule_panel.reload()

    def refresh_acquisition_state(self, running: bool) -> None:
        self.restore_panel.refresh_acquisition_state(running)


class ManualBackupPanel(QFrame):
    backupCompleted = Signal()

    def __init__(
        self,
        backup_service: object | None,
        session: object | None,
        parent: QWidget | None = None,
        *,
        can_manage: bool,
        directory_provider: PathProvider | None = None,
    ) -> None:
        super().__init__(parent)
        self.setProperty("panel", "true")
        self._service = backup_service
        self._session = session
        self._can_manage = can_manage
        self._directory_provider = directory_provider or self._choose_directory
        self._target_directory: Path | None = None
        self._busy = False

        self.title_label = SafeTextLabel("手动备份", selectable=False)
        self.title_label.setProperty("role", "panelTitle")
        self.target_label = SafeTextLabel("未选择目录，将使用服务默认受控备份目录。", selectable=True)
        self.target_label.setProperty("role", "muted")
        self.choose_button = QPushButton("选择目录")
        self.backup_button = QPushButton("立即备份")
        self.backup_button.setProperty("variant", "primary")
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setVisible(False)
        self.busy_label = SafeTextLabel("", selectable=False)
        self.busy_label.setProperty("role", "muted")
        self.result_card = _result_card()
        self.result_title = SafeTextLabel("", selectable=False)
        self.result_detail = SafeTextLabel("", selectable=True)
        result_layout = QVBoxLayout(self.result_card)
        result_layout.setContentsMargins(12, 10, 12, 10)
        result_layout.addWidget(self.result_title)
        result_layout.addWidget(self.result_detail)
        self.result_card.setVisible(False)

        self.choose_button.clicked.connect(self.choose_target_directory)
        self.backup_button.clicked.connect(self.create_manual_backup)

        actions = QHBoxLayout()
        actions.addWidget(self.choose_button)
        actions.addWidget(self.backup_button)
        actions.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        layout.addWidget(self.title_label)
        layout.addWidget(self.target_label)
        layout.addLayout(actions)
        layout.addWidget(self.progress)
        layout.addWidget(self.busy_label)
        layout.addWidget(self.result_card)
        self._apply_state()

    def choose_target_directory(self) -> None:
        if not self._require_permission():
            return
        path = _provider_path(self._directory_provider)
        if path is None:
            return
        self._target_directory = path
        self.target_label.set_safe_text(f"目录：{_display_path(path)}")

    def create_manual_backup(self) -> None:
        if self._busy or not self._require_permission():
            return
        if self._service is None or not hasattr(self._service, "create_manual_backup"):
            self._show_result(False, "备份服务未配置")
            return
        target = self._target_directory or Path("backups")
        self._set_busy(True)
        try:
            result = self._service.create_manual_backup(self._session, target)
        except Exception:
            self._set_busy(False)
            self._show_result(False, BACKUP_FAILED_TEXT)
            return
        self._set_busy(False)
        if bool(getattr(result, "success", False)):
            self._show_success(getattr(result, "data", None))
            self.backupCompleted.emit()
            return
        if int(getattr(result, "code", 0) or 0) == int(ErrorCode.PERMISSION_DENIED):
            self._show_result(False, "当前账号无权限执行此操作，已记录权限失败事件。")
        else:
            self._show_result(False, getattr(result, "message", BACKUP_FAILED_TEXT))

    def _show_success(self, data: object) -> None:
        file_name = _safe_file_name(getattr(data, "file_name", "")) if data is not None else "-"
        created_at = _safe_metadata_text(getattr(data, "created_at", "-")) if data is not None else "-"
        size_text = _size_text(getattr(data, "size_bytes", None)) if data is not None else "-"
        self._show_result(True, f"文件：{file_name}\n时间：{created_at}\n大小：{size_text}")

    def _show_result(self, success: bool, message: object) -> None:
        # Result cards intentionally show service/file values as plain text only;
        # internal paths, stack traces and secrets are filtered before display.
        self.result_card.setProperty("backupResult", "success" if success else "error")
        self.result_title.set_safe_text("备份成功" if success else "备份失败")
        text = (
            normalize_plain_text(message, max_chars=256)
            if success
            else controlled_error_text(message, fallback=BACKUP_FAILED_TEXT)
        )
        self.result_detail.set_safe_text(text)
        self.result_card.setVisible(True)
        repolish(self.result_card)

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.progress.setVisible(busy)
        if busy:
            self.progress.setRange(0, 0)
            self.busy_label.set_safe_text(BUSY_TEXT)
            self.result_card.setVisible(False)
        else:
            self.progress.setRange(0, 100)
            self.progress.setValue(0)
            self.busy_label.set_safe_text("")
        self._apply_state()

    def _apply_state(self) -> None:
        self.choose_button.setEnabled(self._can_manage and not self._busy)
        self.backup_button.setEnabled(self._can_manage and not self._busy)

    def _require_permission(self) -> bool:
        if self._can_manage:
            return True
        self._show_result(False, "当前账号无权限执行此操作，已记录权限失败事件。")
        return False

    def _choose_directory(self) -> Path | None:
        path = QFileDialog.getExistingDirectory(self, "选择备份目录", "")
        return Path(path) if path else None


def _result_card() -> QFrame:
    frame = QFrame()
    frame.setProperty("backupResult", "success")
    return frame


def _provider_path(provider: PathProvider) -> Path | None:
    value = provider() if callable(provider) else provider
    if value in {None, ""}:
        return None
    return Path(value)


def _safe_file_name(value: object) -> str:
    text = normalize_plain_text(value, max_chars=180).replace("\\", "/")
    name = text.rsplit("/", 1)[-1].strip()
    return controlled_error_text(name, fallback="-", max_chars=180) or "-"


def _safe_metadata_text(value: object) -> str:
    return controlled_error_text(value, fallback="-", max_chars=80)


def _display_path(path: Path) -> str:
    # Avoid exposing absolute runtime locations in the UI; the service still
    # receives the selected Path and performs authoritative containment checks.
    name = _safe_file_name(path)
    return name if name not in {".", ""} else "受控备份目录"


def _size_text(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return "-"
    if value >= 1024 * 1024:
        return f"{value / (1024 * 1024):.1f} MB"
    if value >= 1024:
        return f"{value / 1024:.1f} KB"
    return f"{value} B"


def _can_backup_from_session(session: object | None) -> bool:
    permissions = tuple(getattr(session, "permissions", ()) or ())
    if "*" in permissions or Permission.BACKUP_RESTORE.value in permissions:
        return True
    role = getattr(session, "role", None)
    if role is None:
        return False
    try:
        return role_has_permission(str(role), Permission.BACKUP_RESTORE.value)
    except ValueError:
        return False


__all__ = ["BackupRestorePage", "ManualBackupPanel"]
