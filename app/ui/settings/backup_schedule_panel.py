from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.services.backup_service import BackupSettingsCommand
from app.services.errors import ErrorCode
from app.ui.common.errors import ErrorBanner, ValidationHint, controlled_error_text
from app.ui.common.permission_hint import PermissionHint
from app.ui.common.safe_text import SafeTextLabel
from app.ui.common.status import repolish

SAVE_FAILED_TEXT = "备份设置保存失败，请稍后重试。"
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
PathProvider = Callable[[], Path | str | None]


class BackupSchedulePanel(QFrame):
    def __init__(
        self,
        backup_service: object | None = None,
        session: object | None = None,
        parent: QWidget | None = None,
        *,
        can_manage: bool = True,
        directory_provider: PathProvider | None = None,
    ) -> None:
        super().__init__(parent)
        self.setProperty("panel", "true")
        self._service = backup_service
        self._session = session
        self._can_manage = can_manage
        self._directory_provider = directory_provider or self._choose_directory

        self.error_banner = ErrorBanner(); self.error_banner.clear()
        self.permission_hint = PermissionHint(); self.permission_hint.setVisible(not can_manage)
        self.title_label = SafeTextLabel("定时备份", selectable=False)
        self.title_label.setProperty("role", "panelTitle")
        self.enabled_check = QCheckBox("启用定时备份")
        self.interval_spin = _spin(1, 720, 24)
        self.time_edit = QLineEdit("02:00")
        self.time_edit.setMaxLength(5)
        self.directory_edit = QLineEdit("backups")
        self.directory_edit.setMaxLength(260)
        self.keep_spin = _spin(1, 365, 10)
        self.failure_notify_check = QCheckBox("备份失败时显示提醒")
        self.failure_notify_check.setChecked(True)
        self.choose_button = QPushButton("选择目录")
        self.save_button = QPushButton("保存设置")
        self.save_button.setProperty("variant", "primary")
        self.validation_hint = ValidationHint(); self.validation_hint.clear()
        self.status_label = SafeTextLabel("", selectable=True)
        self.status_label.setProperty("status", "normal")

        self.choose_button.clicked.connect(self.choose_directory)
        self.save_button.clicked.connect(self.save_settings)

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(10)
        self._add_field(grid, 0, "状态", self.enabled_check)
        self._add_field(grid, 1, "周期(小时)", self.interval_spin)
        self._add_field(grid, 2, "执行时间", self.time_edit)
        self._add_field(grid, 3, "备份目录", self.directory_edit)
        grid.addWidget(self.choose_button, 3, 2)
        self._add_field(grid, 4, "保留份数", self.keep_spin)
        self._add_field(grid, 5, "失败提醒", self.failure_notify_check)

        actions = QHBoxLayout()
        actions.addWidget(self.save_button)
        actions.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        layout.addWidget(self.error_banner)
        layout.addWidget(self.permission_hint)
        layout.addWidget(self.title_label)
        layout.addLayout(grid)
        layout.addWidget(self.validation_hint)
        layout.addLayout(actions)
        layout.addWidget(self.status_label)
        self._apply_permission_state()

    def reload(self) -> None:
        if self._service is None or not hasattr(self._service, "get_settings"):
            return
        try:
            result = self._service.get_settings()
        except Exception:
            self.error_banner.set_error("备份设置读取失败")
            return
        if not bool(getattr(result, "success", False)):
            self.error_banner.set_error(getattr(result, "message", "备份设置读取失败"))
            return
        self._fill(getattr(result, "data", None))

    def choose_directory(self) -> None:
        if not self._require_permission():
            return
        path = _provider_path(self._directory_provider)
        if path is None:
            return
        self.directory_edit.setText(str(path))

    def save_settings(self) -> None:
        if not self._require_permission() or not self.validate_form():
            return
        if self._service is None or not hasattr(self._service, "update_settings"):
            self.error_banner.set_error("备份服务未配置")
            return
        self.save_button.setEnabled(False)
        try:
            result = self._service.update_settings(self._session, self._command())
        except Exception:
            self.save_button.setEnabled(self._can_manage)
            self.error_banner.set_error(SAVE_FAILED_TEXT)
            return
        self.save_button.setEnabled(self._can_manage)
        if bool(getattr(result, "success", False)):
            self.error_banner.clear()
            self.status_label.set_safe_text("备份设置已保存。")
            self._fill(getattr(result, "data", None))
            return
        if int(getattr(result, "code", 0) or 0) == int(ErrorCode.PERMISSION_DENIED):
            self.error_banner.show_permission_denied()
        else:
            self.error_banner.set_error(controlled_error_text(getattr(result, "message", SAVE_FAILED_TEXT), fallback=SAVE_FAILED_TEXT))

    def validate_form(self) -> bool:
        self.clear_validation()
        if not TIME_RE.match(self.time_edit.text().strip()):
            return self._field_error(self.time_edit, "执行时间格式必须为 HH:MM")
        if not self.directory_edit.text().strip():
            return self._field_error(self.directory_edit, "备份目录不能为空")
        if any(ch in self.directory_edit.text() for ch in ("\x00", "\r", "\n")):
            return self._field_error(self.directory_edit, "备份目录包含无效字符")
        return True

    def clear_validation(self) -> None:
        self.validation_hint.clear()
        for widget in (self.time_edit, self.directory_edit):
            widget.setProperty("validation", None)
            repolish(widget)

    def _field_error(self, widget: QWidget, message: str) -> bool:
        widget.setProperty("validation", "error")
        repolish(widget)
        self.validation_hint.set_validation_error(message)
        widget.setFocus()
        return False

    def _command(self) -> BackupSettingsCommand:
        return BackupSettingsCommand(
            scheduled_enabled=self.enabled_check.isChecked(),
            interval_hours=self.interval_spin.value(),
            backup_time=self.time_edit.text().strip(),
            target_directory=self.directory_edit.text().strip(),
            keep_last=self.keep_spin.value(),
            failure_notify_enabled=self.failure_notify_check.isChecked(),
        )

    def _fill(self, data: object) -> None:
        if data is None:
            return
        self.enabled_check.setChecked(bool(getattr(data, "scheduled_enabled", False)))
        self.interval_spin.setValue(int(getattr(data, "interval_hours", 24) or 24))
        self.time_edit.setText(str(getattr(data, "backup_time", "02:00") or "02:00"))
        self.directory_edit.setText(str(getattr(data, "target_directory", "backups") or "backups"))
        self.keep_spin.setValue(int(getattr(data, "keep_last", 10) or 10))
        self.failure_notify_check.setChecked(bool(getattr(data, "failure_notify_enabled", True)))

    def _require_permission(self) -> bool:
        if self._can_manage:
            return True
        self.permission_hint.show_denied()
        self.error_banner.show_permission_denied()
        return False

    def _apply_permission_state(self) -> None:
        # Read-only mode is a UI affordance only; BackupService remains the
        # authoritative permission boundary for saving settings.
        self.enabled_check.setEnabled(self._can_manage)
        self.interval_spin.setReadOnly(not self._can_manage)
        self.time_edit.setReadOnly(not self._can_manage)
        self.directory_edit.setReadOnly(not self._can_manage)
        self.keep_spin.setReadOnly(not self._can_manage)
        self.failure_notify_check.setEnabled(self._can_manage)
        self.choose_button.setEnabled(self._can_manage)
        self.save_button.setEnabled(self._can_manage)

    def _choose_directory(self) -> Path | None:
        path = QFileDialog.getExistingDirectory(self, "选择定时备份目录", "")
        return Path(path) if path else None

    @staticmethod
    def _add_field(grid: QGridLayout, row: int, label: str, widget: QWidget) -> None:
        label_widget = QLabel(label)
        label_widget.setProperty("role", "fieldLabel")
        grid.addWidget(label_widget, row, 0)
        grid.addWidget(widget, row, 1)


def _spin(minimum: int, maximum: int, value: int) -> QSpinBox:
    spin = QSpinBox()
    spin.setRange(minimum, maximum)
    spin.setValue(value)
    return spin


def _provider_path(provider: PathProvider) -> Path | None:
    value = provider() if callable(provider) else provider
    if value in {None, ""}:
        return None
    return Path(value)


__all__ = ["BackupSchedulePanel"]
