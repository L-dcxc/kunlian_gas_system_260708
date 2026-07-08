from __future__ import annotations

from typing import Any, Callable, Final

from PySide6.QtWidgets import QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from app.services.errors import ErrorCode
from app.services.linkage_service import ManualLinkageCommand
from app.services.permissions import Permission, role_has_permission
from app.ui.common.dialogs import RiskConfirmDialog
from app.ui.common.errors import ErrorBanner, ValidationHint, controlled_error_text, permission_denied_text
from app.ui.common.permission_hint import PermissionHint
from app.ui.common.safe_text import SafeTextLabel, normalize_plain_text
from app.ui.common.status import repolish

MANUAL_FAILED_TEXT: Final[str] = "手动联动执行失败，请稍后重试。"
DEFAULT_REASON: Final[str] = "手动联动模拟执行。"
ACTION_RE = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_:-.")
ConfirmManual = Callable[[QWidget, str, str, str], bool]


class LinkageControlPanel(QFrame):
    def __init__(
        self,
        linkage_service: object | None = None,
        session: object | None = None,
        parent: QWidget | None = None,
        *,
        can_control: bool | None = None,
        confirm_manual: ConfirmManual | None = None,
    ) -> None:
        super().__init__(parent)
        self.setProperty("panel", "true")
        self._service = linkage_service
        self._session = session
        self._can_control = _can_manual_control_from_session(session) if can_control is None else can_control
        self._confirm_manual = confirm_manual or _confirm_manual_linkage
        self._busy = False
        self._objects: tuple[dict[str, Any], ...] = ()

        self.error_banner = ErrorBanner(); self.error_banner.clear()
        self.permission_hint = PermissionHint(); self.permission_hint.setVisible(not self._can_control)
        self.title_label = SafeTextLabel("手动联动控制", selectable=False)
        self.title_label.setProperty("role", "panelTitle")
        self.subtitle_label = SafeTextLabel("真实 IO 协议和点表未确认，当前仅执行模拟联动并记录操作。", selectable=True)
        self.subtitle_label.setProperty("role", "warningText")
        self.object_combo = QComboBox()
        self.action_edit = QLineEdit("open")
        self.action_edit.setMaxLength(80)
        self.reason_edit = QLineEdit(DEFAULT_REASON)
        self.reason_edit.setMaxLength(300)
        self.validation_hint = ValidationHint(); self.validation_hint.clear()
        self.refresh_button = QPushButton("刷新对象")
        self.control_button = QPushButton("模拟联动")
        self.control_button.setProperty("variant", "primary")
        self.control_button.setProperty("linkage", "manual")
        self.result_card = QFrame()
        self.result_card.setProperty("linkageResult", "success")
        self.result_title = SafeTextLabel("", selectable=False)
        self.result_detail = SafeTextLabel("", selectable=True)
        result_layout = QVBoxLayout(self.result_card)
        result_layout.setContentsMargins(12, 10, 12, 10)
        result_layout.addWidget(self.result_title)
        result_layout.addWidget(self.result_detail)
        self.result_card.setVisible(False)

        self.refresh_button.clicked.connect(self.reload_objects)
        self.control_button.clicked.connect(self.manual_control)

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(10)
        self._add_field(grid, 0, "联动对象", self.object_combo)
        self._add_field(grid, 1, "动作码", self.action_edit)
        self._add_field(grid, 2, "原因", self.reason_edit)

        actions = QHBoxLayout()
        actions.addWidget(self.control_button)
        actions.addWidget(self.refresh_button)
        actions.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        layout.addWidget(self.error_banner)
        layout.addWidget(self.permission_hint)
        layout.addWidget(self.title_label)
        layout.addWidget(self.subtitle_label)
        layout.addLayout(grid)
        layout.addWidget(self.validation_hint)
        layout.addLayout(actions)
        layout.addWidget(self.result_card)
        self._apply_state()

    def reload_objects(self) -> None:
        if self._busy:
            return
        self.error_banner.clear()
        try:
            self._objects = tuple(dict(row) for row in (self._service.list_objects() if self._service else ()))
        except Exception:
            self._objects = ()
            self.error_banner.set_error("联动对象读取失败，请稍后重试。")
        self._reload_object_combo()
        self._apply_state()

    def manual_control(self) -> None:
        if self._busy:
            return
        if not self._require_permission() or not self.validate_form():
            return
        object_id = int(self.object_combo.currentData())
        object_name = self.object_combo.currentText()
        action = self.action_edit.text().strip()
        reason = self.reason_edit.text().strip() or DEFAULT_REASON
        if not self._confirm_manual(self, object_name, action, reason):
            return
        if self._service is None or not hasattr(self._service, "manual_control"):
            self._show_result(False, object_name, action, reason, "联动服务未配置")
            return
        self._set_busy(True)
        try:
            result = self._service.manual_control(self._session, ManualLinkageCommand(object_id=object_id, action=action, message=reason))
        except Exception:
            self._set_busy(False)
            self._show_result(False, object_name, action, reason, MANUAL_FAILED_TEXT)
            return
        self._set_busy(False)
        if bool(getattr(result, "success", False)):
            self._show_result(True, object_name, action, reason, getattr(result, "data", None))
            return
        if int(getattr(result, "code", 0) or 0) == int(ErrorCode.PERMISSION_DENIED):
            self.permission_hint.show_denied()
            self._show_result(False, object_name, action, reason, permission_denied_text())
        else:
            self._show_result(False, object_name, action, reason, getattr(result, "message", MANUAL_FAILED_TEXT))

    def validate_form(self) -> bool:
        self.clear_validation()
        if self.object_combo.currentData() is None:
            return self._field_error(self.object_combo, "必须选择联动对象")
        action = self.action_edit.text().strip()
        if not action:
            return self._field_error(self.action_edit, "动作码不能为空")
        if len(action) > 80 or any(char not in ACTION_RE for char in action):
            return self._field_error(self.action_edit, "动作码仅允许字母、数字、下划线、冒号、点和短横线，长度不超过 80")
        if len(self.reason_edit.text().strip()) > 300:
            return self._field_error(self.reason_edit, "原因长度不能超过 300")
        return True

    def clear_validation(self) -> None:
        self.validation_hint.clear()
        for widget in (self.object_combo, self.action_edit, self.reason_edit):
            widget.setProperty("validation", None)
            repolish(widget)

    def _reload_object_combo(self) -> None:
        current = self.object_combo.currentData()
        self.object_combo.clear()
        self.object_combo.addItem("请选择联动对象", None)
        for row in self._objects:
            if not bool(row.get("is_enabled", True)):
                continue
            suffix = "（模拟）" if str(row.get("adapter_type", "simulated")) == "simulated" else "（真实 IO 待确认，不可用）"
            self.object_combo.addItem(f"{row.get('name', '联动对象')} {suffix}", int(row.get("id", 0)))
            if str(row.get("adapter_type", "simulated")) != "simulated":
                self.object_combo.model().item(self.object_combo.count() - 1).setEnabled(False)
        index = self.object_combo.findData(current)
        self.object_combo.setCurrentIndex(index if index >= 0 else 0)

    def _require_permission(self) -> bool:
        if self._can_control:
            return True
        self.permission_hint.show_denied()
        self.error_banner.show_permission_denied()
        return False

    def _field_error(self, widget: QWidget, message: str) -> bool:
        widget.setProperty("validation", "error")
        repolish(widget)
        self.validation_hint.set_validation_error(message)
        widget.setFocus()
        return False

    def _show_result(self, success: bool, object_name: object, action: object, reason: object, result: object) -> None:
        # The result card may include service/user text. Keep it plain and pass
        # failures through the common redactor before rendering.
        self.result_card.setProperty("linkageResult", "success" if success else "error")
        self.result_title.set_safe_text("模拟联动已执行" if success else "联动失败")
        detail_result = _success_result_text(result) if success else controlled_error_text(result, fallback=MANUAL_FAILED_TEXT)
        self.result_detail.set_safe_text(
            "\n".join(
                (
                    f"对象：{normalize_plain_text(object_name, max_chars=120)}",
                    f"动作：{normalize_plain_text(action, max_chars=80)}",
                    f"原因：{normalize_plain_text(reason, max_chars=300)}",
                    f"结果：{detail_result}",
                )
            )
        )
        self.result_card.setVisible(True)
        repolish(self.result_card)

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._apply_state()

    def _apply_state(self) -> None:
        can_use = self._can_control and not self._busy
        self.object_combo.setEnabled(can_use)
        self.action_edit.setReadOnly(not can_use)
        self.reason_edit.setReadOnly(not can_use)
        self.refresh_button.setEnabled(not self._busy)
        self.control_button.setEnabled(can_use and self.object_combo.currentData() is not None)

    @staticmethod
    def _add_field(grid: QGridLayout, row: int, label: str, widget: QWidget) -> None:
        label_widget = QLabel(label)
        label_widget.setProperty("role", "fieldLabel")
        grid.addWidget(label_widget, row, 0)
        grid.addWidget(widget, row, 1)


def _success_result_text(data: object) -> str:
    if data is None:
        return "模拟执行成功"
    if isinstance(data, dict):
        result = data.get("result") or data.get("message") or "模拟执行成功"
    else:
        result = getattr(data, "result", None) or getattr(data, "message", None) or "模拟执行成功"
    return normalize_plain_text(result, max_chars=240)


def _can_manual_control_from_session(session: object | None) -> bool:
    permissions = tuple(getattr(session, "permissions", ()) or ())
    if "*" in permissions or Permission.LINKAGE_MANUAL_CONTROL.value in permissions:
        return True
    role = getattr(session, "role", None)
    if role is None:
        return False
    try:
        return role_has_permission(str(role), Permission.LINKAGE_MANUAL_CONTROL.value)
    except ValueError:
        return False


def _confirm_manual_linkage(parent: QWidget, object_name: str, action: str, reason: str) -> bool:
    return RiskConfirmDialog.confirm(
        "确认手动联动",
        f"即将对联动对象执行模拟动作：{object_name}\n动作：{action}\n原因：{reason}",
        parent,
        confirm_text="确认模拟联动",
    )


__all__ = ["LinkageControlPanel"]
