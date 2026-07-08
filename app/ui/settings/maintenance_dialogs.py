from __future__ import annotations

from datetime import datetime
from typing import Final

from PySide6.QtCore import QDate
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.services.errors import ErrorCode
from app.services.maintenance_service import MaintenancePlanCommand
from app.ui.common.errors import ValidationHint, controlled_error_text
from app.ui.common.permission_hint import PermissionHint
from app.ui.common.safe_text import SafeTextLabel
from app.ui.common.status import repolish

PLAN_TYPES: Final[tuple[tuple[str, str], ...]] = (
    ("传感器寿命", "sensor_life"),
    ("校验周期", "calibration"),
    ("自定义", "custom"),
)
PLAN_STATUSES: Final[tuple[tuple[str, str], ...]] = (
    ("进行中", "active"),
    ("已完成", "completed"),
    ("已取消", "cancelled"),
)
PLAN_TYPE_VALUES: Final[frozenset[str]] = frozenset(value for _, value in PLAN_TYPES)
PLAN_STATUS_VALUES: Final[frozenset[str]] = frozenset(value for _, value in PLAN_STATUSES)
MAX_NOTES_LENGTH: Final[int] = 1000
SAVE_FAILED_TEXT: Final[str] = "维护计划保存失败，请稍后重试。"


class MaintenancePlanDialog(QDialog):
    def __init__(
        self,
        maintenance_service: object | None = None,
        session: object | None = None,
        parent: QWidget | None = None,
        *,
        plan: object | None = None,
        can_manage: bool = True,
    ) -> None:
        super().__init__(parent)
        self._service = maintenance_service
        self._session = session
        self._plan = plan
        self._submitting = False
        self._blocked_by_permission = not can_manage
        self.setWindowTitle("编辑维护计划" if self._plan is not None else "新增维护计划")
        self.setModal(True)
        self.resize(520, 460)

        self.card = QFrame(self)
        self.card.setProperty("panel", "true")
        self.title_label = SafeTextLabel(self.windowTitle(), selectable=False)
        self.title_label.setProperty("role", "dialogTitle")
        self.permission_hint = PermissionHint()
        self.permission_hint.setVisible(self._blocked_by_permission)

        self.detector_id_spin = _spin(0, 2_147_483_647, 1)
        self.plan_type_combo = QComboBox()
        for label, value in PLAN_TYPES:
            self.plan_type_combo.addItem(label, value)
        self.due_at_edit = QDateEdit()
        self.due_at_edit.setCalendarPopup(True)
        self.due_at_edit.setDisplayFormat("yyyy-MM-dd")
        self.due_at_edit.setDate(QDate.currentDate())
        self.remind_days_spin = _spin(0, 3650, 7)
        self.status_combo = QComboBox()
        for label, value in PLAN_STATUSES:
            self.status_combo.addItem(label, value)
        self.notes_edit = QPlainTextEdit()
        self.notes_edit.setPlaceholderText("维护说明、处理要求或现场备注")
        self.notes_edit.setMaximumHeight(110)

        self.error_hint = ValidationHint()
        self.error_hint.clear()
        self.cancel_button = QPushButton("取消")
        self.submit_button = QPushButton("保存")
        self.submit_button.setProperty("variant", "primary")
        self.cancel_button.clicked.connect(self.reject)
        self.submit_button.clicked.connect(self.submit)

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(10)
        _add_field(grid, 0, "探测器ID", self.detector_id_spin)
        _add_field(grid, 1, "计划类型", self.plan_type_combo)
        _add_field(grid, 2, "到期日期", self.due_at_edit)
        _add_field(grid, 3, "提前提醒(天)", self.remind_days_spin)
        _add_field(grid, 4, "状态", self.status_combo)
        _add_field(grid, 5, "备注", self.notes_edit)

        actions = QHBoxLayout()
        actions.addStretch(1)
        actions.addWidget(self.cancel_button)
        actions.addWidget(self.submit_button)

        layout = QVBoxLayout(self.card)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)
        layout.addWidget(self.title_label)
        layout.addWidget(self.permission_hint)
        layout.addLayout(grid)
        layout.addWidget(self.error_hint)
        layout.addLayout(actions)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.addWidget(self.card)
        self._apply_plan()
        self._apply_permission_state()

    def submit(self) -> None:
        if self._submitting:
            return
        if self._blocked_by_permission:
            self.show_permission_denied()
            return
        if not self.validate_form():
            return
        self._set_submitting(True)
        try:
            result = self._save()
        except Exception:
            self.show_error(SAVE_FAILED_TEXT)
            self._set_submitting(False)
            return
        if bool(getattr(result, "success", False)):
            self.accept()
            self._set_submitting(False)
            return
        if int(getattr(result, "code", 0) or 0) == int(ErrorCode.PERMISSION_DENIED):
            self.show_permission_denied()
        else:
            # Service messages may contain database or platform details; render a
            # controlled plain-text message and leave authoritative checks in service.
            self.show_error(getattr(result, "message", SAVE_FAILED_TEXT))
        self._set_submitting(False)

    def validate_form(self) -> bool:
        self.clear_validation()
        if self.detector_id_spin.value() <= 0:
            return self._field_error(self.detector_id_spin, "detector_id 必须为正整数")
        if self.plan_type_combo.currentData() not in PLAN_TYPE_VALUES:
            return self._field_error(self.plan_type_combo, "计划类型不受支持")
        if not self.due_at_edit.date().isValid():
            return self._field_error(self.due_at_edit, "到期日期无效")
        if not 0 <= self.remind_days_spin.value() <= 3650:
            return self._field_error(self.remind_days_spin, "提前提醒天数必须在 0..3650 范围内")
        if self.status_combo.currentData() not in PLAN_STATUS_VALUES:
            return self._field_error(self.status_combo, "计划状态不受支持")
        if len(self.notes_edit.toPlainText()) > MAX_NOTES_LENGTH:
            return self._field_error(self.notes_edit, "备注长度不能超过 1000 字")
        return True

    def clear_validation(self) -> None:
        self.error_hint.clear()
        for widget in (
            self.detector_id_spin,
            self.plan_type_combo,
            self.due_at_edit,
            self.remind_days_spin,
            self.status_combo,
            self.notes_edit,
        ):
            widget.setProperty("validation", None)
            repolish(widget)

    def show_permission_denied(self) -> None:
        self.permission_hint.show_denied()
        self.show_error("当前账号无权限执行此操作，已记录权限失败事件。")

    def show_error(self, message: object) -> None:
        self.error_hint.set_safe_text(controlled_error_text(message, fallback=SAVE_FAILED_TEXT))
        self.error_hint.setVisible(True)

    def _save(self) -> object:
        if self._service is None:
            raise RuntimeError("maintenance service is required")
        if self._plan is None:
            return self._service.create_plan(self._session, self._command())
        return self._service.update_plan(self._session, int(_value(self._plan, "id", 0)), self._command())

    def _command(self) -> MaintenancePlanCommand:
        return MaintenancePlanCommand(
            detector_id=self.detector_id_spin.value(),
            plan_type=str(self.plan_type_combo.currentData()),
            due_at=self.due_at_edit.date().toPython().isoformat(),
            remind_days_before=self.remind_days_spin.value(),
            status=str(self.status_combo.currentData()),
            notes=self.notes_edit.toPlainText(),
        )

    def _apply_plan(self) -> None:
        if self._plan is None:
            return
        self.detector_id_spin.setValue(int(_value(self._plan, "detector_id", 1) or 1))
        _set_combo(self.plan_type_combo, _value(self._plan, "plan_type", "custom"))
        due_date = _qdate_from_iso(_value(self._plan, "due_at", None))
        if due_date.isValid():
            self.due_at_edit.setDate(due_date)
        self.remind_days_spin.setValue(int(_value(self._plan, "remind_days_before", 7) or 7))
        _set_combo(self.status_combo, _value(self._plan, "status", "active"))
        self.notes_edit.setPlainText(str(_value(self._plan, "notes", "") or ""))

    def _apply_permission_state(self) -> None:
        enabled = not self._blocked_by_permission
        # UI disables write controls for operators, while MaintenanceService remains
        # the authoritative permission boundary for create/update calls.
        self.detector_id_spin.setReadOnly(not enabled)
        self.plan_type_combo.setEnabled(enabled)
        self.due_at_edit.setReadOnly(not enabled)
        self.remind_days_spin.setReadOnly(not enabled)
        self.status_combo.setEnabled(enabled)
        self.notes_edit.setReadOnly(not enabled)
        self.submit_button.setEnabled(enabled)
        if self._blocked_by_permission:
            self.permission_hint.show_denied()

    def _set_submitting(self, submitting: bool) -> None:
        self._submitting = submitting
        enabled = not submitting and not self._blocked_by_permission
        self.detector_id_spin.setReadOnly(not enabled)
        self.plan_type_combo.setEnabled(enabled)
        self.due_at_edit.setReadOnly(not enabled)
        self.remind_days_spin.setReadOnly(not enabled)
        self.status_combo.setEnabled(enabled)
        self.notes_edit.setReadOnly(not enabled)
        self.submit_button.setEnabled(enabled)
        self.submit_button.setText("保存中..." if submitting else "保存")

    def _field_error(self, widget: QWidget, message: str) -> bool:
        widget.setProperty("validation", "error")
        repolish(widget)
        self.error_hint.set_validation_error(message)
        widget.setFocus()
        return False


def _spin(minimum: int, maximum: int, value: int) -> QSpinBox:
    spin = QSpinBox()
    spin.setRange(minimum, maximum)
    spin.setValue(value)
    return spin


def _add_field(grid: QGridLayout, row: int, label: str, widget: QWidget) -> None:
    label_widget = QLabel(label)
    label_widget.setProperty("role", "fieldLabel")
    grid.addWidget(label_widget, row, 0)
    grid.addWidget(widget, row, 1)


def _set_combo(combo: QComboBox, value: object) -> None:
    index = combo.findData(value)
    if index >= 0:
        combo.setCurrentIndex(index)


def _qdate_from_iso(value: object) -> QDate:
    if isinstance(value, datetime):
        return QDate(value.year, value.month, value.day)
    if not isinstance(value, str) or not value.strip():
        return QDate()
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            return QDate.fromString(text[:10], "yyyy-MM-dd")
        except Exception:
            return QDate()
    return QDate(parsed.year, parsed.month, parsed.day)


def _value(source: object, key: str, default: object = None) -> object:
    if isinstance(source, dict):
        return source.get(key, default)
    return getattr(source, key, default)


__all__ = ["MaintenancePlanDialog"]
