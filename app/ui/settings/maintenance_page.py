from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.services.errors import ErrorCode
from app.services.permissions import Permission, role_has_permission
from app.ui.common.data_table import DataTable, TableColumn, TableState
from app.ui.common.errors import ErrorBanner, controlled_error_text
from app.ui.common.permission_hint import PermissionHint
from app.ui.common.safe_text import SafeTextLabel, normalize_plain_text
from app.ui.common.status import repolish
from app.ui.settings.maintenance_dialogs import MaintenancePlanDialog

LOAD_FAILED_TEXT: Final[str] = "维护提醒读取失败，请稍后重试。"
PLAN_LOAD_FAILED_TEXT: Final[str] = "维护计划读取失败，请稍后重试。"
PLAN_TYPE_LABELS: Final[dict[str, str]] = {
    "sensor_life": "传感器寿命",
    "calibration": "校验周期",
    "custom": "自定义",
}
STATUS_LABELS: Final[dict[str, str]] = {
    "active": "进行中",
    "completed": "已完成",
    "cancelled": "已取消",
}
SOURCE_LABELS: Final[dict[str, str]] = {
    "detector.sensor_life": "探测器传感器寿命",
    "detector.calibration": "探测器校验周期",
    "maintenance_plan": "维护计划",
}


@dataclass(frozen=True, slots=True)
class MaintenancePlanRow:
    id: int
    detector_id: int
    plan_type: str
    due_at: str
    remind_days_before: int
    status: str
    notes: str = ""
    detector_position_code: str | None = None
    detector_name: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class MaintenancePanel(QWidget):
    planChanged = Signal()

    def __init__(
        self,
        maintenance_service: object | None = None,
        session: object | None = None,
        parent: QWidget | None = None,
        *,
        can_manage: bool | None = None,
        dialog_factory: object | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = maintenance_service
        self._session = session
        self._can_manage = _can_manage_from_session(session) if can_manage is None else can_manage
        self._dialog_factory = dialog_factory or self._default_dialog_factory
        self._busy = False
        self._plans: tuple[MaintenancePlanRow, ...] = ()
        self._selected_plan_id: int | None = None
        self.reminder_cards: list[MaintenanceReminderCard] = []

        self.title_label = SafeTextLabel("维护提醒", selectable=False)
        self.title_label.setProperty("role", "panelTitle")
        self.subtitle_label = SafeTextLabel("查看传感器寿命、校验周期和维护计划到期提醒。", selectable=False)
        self.subtitle_label.setProperty("role", "muted")
        self.permission_hint = PermissionHint()
        self.permission_hint.setVisible(not self._can_manage)
        self.error_banner = ErrorBanner(); self.error_banner.clear()

        self.refresh_button = QPushButton("刷新")
        self.new_button = QPushButton("新增计划")
        self.new_button.setProperty("variant", "primary")
        self.edit_button = QPushButton("编辑计划")
        self.refresh_button.clicked.connect(self.reload)
        self.new_button.clicked.connect(self.open_create_dialog)
        self.edit_button.clicked.connect(self.open_edit_dialog)

        header_actions = QHBoxLayout()
        header_actions.addWidget(self.new_button)
        header_actions.addWidget(self.edit_button)
        header_actions.addStretch(1)
        header_actions.addWidget(self.refresh_button)

        header = QFrame(self)
        header.setProperty("panel", "true")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(16, 16, 16, 16)
        header_layout.setSpacing(8)
        header_layout.addWidget(self.title_label)
        header_layout.addWidget(self.subtitle_label)
        header_layout.addWidget(self.permission_hint)
        header_layout.addLayout(header_actions)

        self.reminders_panel = self._build_reminders_panel()
        self.plan_table = DataTable(
            [
                TableColumn("id", "ID", 58, Qt.AlignmentFlag.AlignRight),
                TableColumn("detector_label", "探测器", 180),
                TableColumn("position", "点位", 110),
                TableColumn("plan_type_label", "类型", 110),
                TableColumn("due_at", "到期日期", 150),
                TableColumn("remind_days_before", "提前天数", 90, Qt.AlignmentFlag.AlignRight),
                TableColumn("status_label", "状态", 90),
                TableColumn("notes", "备注", 260),
            ]
        )
        self.plan_table.export_button.setVisible(False)
        self.plan_table.retryRequested.connect(self.reload)
        self.plan_table.emptyActionRequested.connect(self.open_create_dialog)
        self.plan_table.table.selectionModel().selectionChanged.connect(self._selection_changed)

        body = QHBoxLayout()
        body.setSpacing(12)
        body.addWidget(self.reminders_panel, 3)
        body.addWidget(self.plan_table, 5)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        layout.addWidget(header)
        layout.addWidget(self.error_banner)
        layout.addLayout(body, 1)
        self._apply_action_state()

    def reload(self) -> None:
        if self._busy:
            return
        self.error_banner.clear()
        self._set_busy(True)
        try:
            self._load_reminders()
            self._load_plans()
        finally:
            self._set_busy(False)

    def open_create_dialog(self) -> None:
        if not self._require_manage_permission():
            return
        dialog = self._dialog_factory(self._service, self._session, self, plan=None, can_manage=True)
        if dialog.exec():
            self.reload()
            self.planChanged.emit()

    def open_edit_dialog(self) -> None:
        if not self._require_manage_permission():
            return
        plan = self.selected_plan()
        if plan is None:
            return
        dialog = self._dialog_factory(self._service, self._session, self, plan=plan, can_manage=True)
        if dialog.exec():
            self.reload()
            self.planChanged.emit()

    def selected_plan(self) -> MaintenancePlanRow | None:
        if self._selected_plan_id is None:
            return None
        return next((plan for plan in self._plans if plan.id == self._selected_plan_id), None)

    def _load_reminders(self) -> None:
        self.reminders_loading_label.setVisible(True)
        self.reminders_progress.setVisible(True)
        self.reminders_empty_label.setVisible(False)
        self.reminders_error.clear()
        self._clear_reminder_cards()
        try:
            result = self._call_due_reminders()
        except Exception:
            self._show_reminder_error(LOAD_FAILED_TEXT)
            return
        if not bool(getattr(result, "success", False)):
            if int(getattr(result, "code", 0) or 0) == int(ErrorCode.PERMISSION_DENIED):
                self.permission_hint.show_denied()
            self._show_reminder_error(getattr(result, "message", LOAD_FAILED_TEXT))
            return
        reminders = tuple(getattr(result, "data", ()) or ())
        self.reminders_loading_label.setVisible(False)
        self.reminders_progress.setVisible(False)
        if not reminders:
            self.reminders_empty_label.setVisible(True)
            return
        for reminder in reminders:
            card = MaintenanceReminderCard(reminder)
            self.reminder_cards.append(card)
            self.reminder_list_layout.addWidget(card)
        self.reminder_list_layout.addStretch(1)

    def _load_plans(self) -> None:
        self._selected_plan_id = None
        self.plan_table.set_state(TableState.LOADING, "正在加载维护计划")
        self._apply_action_state()
        try:
            result = self._call_list_plans()
        except Exception:
            self._show_plan_error(PLAN_LOAD_FAILED_TEXT)
            return
        if not bool(getattr(result, "success", False)):
            if int(getattr(result, "code", 0) or 0) == int(ErrorCode.PERMISSION_DENIED):
                self.permission_hint.show_denied()
            self._show_plan_error(getattr(result, "message", PLAN_LOAD_FAILED_TEXT))
            return
        self._plans = tuple(_coerce_plan(item) for item in (getattr(result, "data", ()) or ()))
        self.plan_table.set_rows([_plan_to_table(plan) for plan in self._plans])
        self.plan_table.set_page(1, len(self._plans), max(1, len(self._plans) or 1))
        self.plan_table.set_state(TableState.READY if self._plans else TableState.EMPTY, "暂无维护计划")
        self._apply_action_state()

    def _call_due_reminders(self) -> object:
        if self._service is None:
            raise RuntimeError("maintenance service is required")
        if hasattr(self._service, "view_due_reminders"):
            return self._service.view_due_reminders(self._session)
        return self._service.list_due_reminders()

    def _call_list_plans(self) -> object:
        if self._service is None or not hasattr(self._service, "list_plans"):
            raise RuntimeError("maintenance plan service is required")
        return self._service.list_plans(self._session)

    def _show_reminder_error(self, message: object) -> None:
        self.reminders_loading_label.setVisible(False)
        self.reminders_progress.setVisible(False)
        self.reminders_empty_label.setVisible(False)
        self.reminders_error.set_error(controlled_error_text(message, fallback=LOAD_FAILED_TEXT), severity="error")

    def _show_plan_error(self, message: object) -> None:
        self._plans = ()
        self.plan_table.set_rows([])
        self.plan_table.set_page(1, 0, 1)
        self.plan_table.set_state(TableState.ERROR, controlled_error_text(message, fallback=PLAN_LOAD_FAILED_TEXT))
        self._apply_action_state()

    def _require_manage_permission(self) -> bool:
        if self._can_manage:
            return True
        # Operators may view reminders, but the UI reports denied write attempts;
        # MaintenanceService still enforces the same boundary on save.
        self.permission_hint.show_denied()
        self.error_banner.show_permission_denied()
        return False

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._apply_action_state()

    def _apply_action_state(self) -> None:
        self.refresh_button.setEnabled(not self._busy)
        self.new_button.setEnabled(self._can_manage and not self._busy)
        self.edit_button.setEnabled(self._can_manage and not self._busy and self._selected_plan_id is not None)

    def _selection_changed(self) -> None:
        indexes = self.plan_table.table.selectionModel().selectedRows()
        if indexes and 0 <= indexes[0].row() < len(self._plans):
            self._selected_plan_id = self._plans[indexes[0].row()].id
        else:
            self._selected_plan_id = None
        self._apply_action_state()

    def _clear_reminder_cards(self) -> None:
        self.reminder_cards.clear()
        while self.reminder_list_layout.count():
            item = self.reminder_list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _build_reminders_panel(self) -> QFrame:
        panel = QFrame(self)
        panel.setProperty("panel", "true")
        title = SafeTextLabel("到期提醒", selectable=False)
        title.setProperty("role", "panelTitle")
        self.reminders_error = ErrorBanner(); self.reminders_error.clear()
        self.reminders_progress = QProgressBar()
        self.reminders_progress.setRange(0, 0)
        self.reminders_progress.setVisible(False)
        self.reminders_loading_label = SafeTextLabel("正在加载维护提醒", selectable=False)
        self.reminders_loading_label.setProperty("role", "muted")
        self.reminders_loading_label.setVisible(False)
        self.reminders_empty_label = SafeTextLabel("暂无到期或超期维护提醒", selectable=False)
        self.reminders_empty_label.setProperty("role", "muted")
        self.reminders_empty_label.setVisible(False)

        self.reminder_container = QWidget(panel)
        self.reminder_list_layout = QVBoxLayout(self.reminder_container)
        self.reminder_list_layout.setContentsMargins(0, 0, 0, 0)
        self.reminder_list_layout.setSpacing(8)
        scroll = QScrollArea(panel)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(self.reminder_container)

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        layout.addWidget(title)
        layout.addWidget(self.reminders_error)
        layout.addWidget(self.reminders_progress)
        layout.addWidget(self.reminders_loading_label)
        layout.addWidget(self.reminders_empty_label)
        layout.addWidget(scroll, 1)
        return panel

    def _default_dialog_factory(self, service: object, session: object, parent: QWidget, **kwargs: object) -> MaintenancePlanDialog:
        return MaintenancePlanDialog(service, session, parent, **kwargs)


class MaintenanceReminderCard(QFrame):
    def __init__(self, reminder: object, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("maintenance", _maintenance_property(reminder))
        self.title_label = SafeTextLabel(_reminder_title(reminder), selectable=True)
        self.title_label.setProperty("role", "panelTitle")
        self.device_label = SafeTextLabel(_detector_label(reminder), selectable=True)
        self.device_label.setProperty("role", "muted")
        self.due_label = SafeTextLabel(_due_text(reminder), selectable=True)
        self.notes_label = SafeTextLabel(_notes_text(reminder), selectable=True, max_chars=1000)
        self.notes_label.setProperty("role", "muted")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(4)
        layout.addWidget(self.title_label)
        layout.addWidget(self.device_label)
        layout.addWidget(self.due_label)
        layout.addWidget(self.notes_label)
        repolish(self)


def _can_manage_from_session(session: object | None) -> bool:
    permissions = tuple(getattr(session, "permissions", ()) or ())
    if "*" in permissions or Permission.MAINTENANCE_MANAGE.value in permissions:
        return True
    role = getattr(session, "role", None)
    if role is None:
        return False
    try:
        return role_has_permission(str(role), Permission.MAINTENANCE_MANAGE.value)
    except ValueError:
        return False


def _coerce_plan(item: object) -> MaintenancePlanRow:
    return MaintenancePlanRow(
        id=int(_value(item, "id", 0)),
        detector_id=int(_value(item, "detector_id", 0)),
        plan_type=str(_value(item, "plan_type", "custom")),
        due_at=str(_value(item, "due_at", "")),
        remind_days_before=int(_value(item, "remind_days_before", 0)),
        status=str(_value(item, "status", "active")),
        notes=str(_value(item, "notes", "") or ""),
        detector_position_code=_optional_text(_value(item, "detector_position_code", None)),
        detector_name=_optional_text(_value(item, "detector_name", None)),
        created_at=_optional_text(_value(item, "created_at", None)),
        updated_at=_optional_text(_value(item, "updated_at", None)),
    )


def _plan_to_table(plan: MaintenancePlanRow) -> dict[str, object]:
    return {
        "id": plan.id,
        "detector_label": plan.detector_name or f"探测器 {plan.detector_id}",
        "position": plan.detector_position_code or "-",
        "plan_type_label": PLAN_TYPE_LABELS.get(plan.plan_type, plan.plan_type),
        "due_at": _date_text(plan.due_at),
        "remind_days_before": plan.remind_days_before,
        "status_label": STATUS_LABELS.get(plan.status, plan.status),
        "notes": plan.notes,
    }


def _maintenance_property(reminder: object) -> str:
    return "overdue" if _value(reminder, "status", "") == "overdue" else "dueSoon"


def _reminder_title(reminder: object) -> str:
    status = "已超期" if _maintenance_property(reminder) == "overdue" else "即将到期"
    plan_type = PLAN_TYPE_LABELS.get(str(_value(reminder, "plan_type", "")), str(_value(reminder, "plan_type", "")))
    source = SOURCE_LABELS.get(str(_value(reminder, "source", "")), str(_value(reminder, "source", "")))
    return normalize_plain_text(f"{status} · {source} · {plan_type}", max_chars=160)


def _detector_label(reminder: object) -> str:
    name = str(_value(reminder, "detector_name", "") or f"探测器 {_value(reminder, 'detector_id', '-')}")
    position = str(_value(reminder, "detector_position_code", "") or "-")
    return normalize_plain_text(f"设备：{name}    点位：{position}", max_chars=240)


def _due_text(reminder: object) -> str:
    due_at = _date_text(str(_value(reminder, "due_at", "")))
    days = int(_value(reminder, "days_until_due", 0) or 0)
    if days < 0:
        return f"到期：{due_at}，已超期 {abs(days)} 天"
    return f"到期：{due_at}，剩余 {days} 天"


def _notes_text(reminder: object) -> str:
    notes = str(_value(reminder, "notes", "") or "")
    return f"备注：{notes}" if notes else "备注：-"


def _date_text(value: str) -> str:
    text = normalize_plain_text(value, max_chars=80)
    return text[:10] if len(text) >= 10 else text or "-"


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _value(source: object, key: str, default: object = None) -> object:
    if isinstance(source, dict):
        return source.get(key, default)
    return getattr(source, key, default)


MaintenancePage = MaintenancePanel

__all__ = ["MaintenancePanel", "MaintenancePage", "MaintenancePlanRow", "MaintenanceReminderCard"]
