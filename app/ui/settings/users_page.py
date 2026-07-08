from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QComboBox, QFrame, QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from app.services.errors import ErrorCode
from app.services.models import Pagination
from app.services.permissions import Permission, Role, role_has_permission
from app.services.user_service import UpdateUserCommand
from app.ui.common.data_table import DataTable, TableColumn, TableState
from app.ui.common.dialogs import RiskConfirmDialog
from app.ui.common.errors import controlled_error_text
from app.ui.common.filter_panel import FilterPanel
from app.ui.common.permission_hint import PermissionHint
from app.ui.common.safe_text import SafeTextLabel
from app.ui.settings.user_dialogs import UserEditorDialog

DEFAULT_PER_PAGE = 20
LOAD_FAILED_TEXT = "用户列表加载失败，请稍后重试。"
ACTION_FAILED_TEXT = "用户操作失败，请稍后重试。"


@dataclass(frozen=True, slots=True)
class UserRow:
    id: int
    username: str
    role: str
    is_active: bool
    created_at: str = ""
    updated_at: str = ""
    remark: str = ""


@dataclass(frozen=True, slots=True)
class UserListQuery:
    role: str | None
    is_active: bool | None
    page: int
    per_page: int


class UserManagementPage(QWidget):
    userChanged = Signal()

    def __init__(
        self,
        user_service: object | None = None,
        session: object | None = None,
        parent: QWidget | None = None,
        *,
        can_manage_users: bool | None = None,
        confirm_danger: object | None = None,
        dialog_factory: object | None = None,
    ) -> None:
        super().__init__(parent)
        self._user_service = user_service
        self._session = session
        self._page = 1
        self._per_page = DEFAULT_PER_PAGE
        self._total = 0
        self._rows: tuple[UserRow, ...] = ()
        self._selected_user_id: int | None = None
        self._confirm_danger = confirm_danger or _confirm_disable_user
        self._dialog_factory = dialog_factory or self._default_dialog_factory
        self._can_manage_users = _can_manage_from_session(session) if can_manage_users is None else can_manage_users

        self.title_label = SafeTextLabel("用户管理", selectable=False)
        self.title_label.setProperty("role", "panelTitle")
        self.subtitle_label = SafeTextLabel("管理本机账号、角色和启用状态。", selectable=False)
        self.subtitle_label.setProperty("role", "muted")

        self.permission_hint = PermissionHint()
        self.permission_hint.setVisible(not self._can_manage_users)

        self.filter_panel = FilterPanel("用户筛选")
        self.role_filter = QComboBox()
        self.role_filter.addItem("全部角色", None)
        self.role_filter.addItem("管理员", Role.ADMIN.value)
        self.role_filter.addItem("操作员", Role.OPERATOR.value)
        self.status_filter = QComboBox()
        self.status_filter.addItem("全部状态", None)
        self.status_filter.addItem("启用", True)
        self.status_filter.addItem("禁用", False)
        self.filter_panel.add_field("role", "角色", self.role_filter)
        self.filter_panel.add_field("status", "状态", self.status_filter)
        self.filter_panel.searchRequested.connect(self.apply_filters)
        self.filter_panel.resetRequested.connect(self.reset_filters)

        self.table = DataTable(
            [
                TableColumn("id", "ID", width=64, alignment=Qt.AlignmentFlag.AlignRight),
                TableColumn("username", "用户名", width=180),
                TableColumn("role_label", "角色", width=110),
                TableColumn("status_label", "状态", width=90),
                TableColumn("created_at", "创建时间", width=180),
                TableColumn("updated_at", "更新时间", width=180),
                TableColumn("remark", "备注", width=220),
            ]
        )
        self.table.retryRequested.connect(self.reload)
        self.table.pageChanged.connect(self._change_page)
        self.table.emptyActionRequested.connect(self.open_create_dialog)
        self.table.table.selectionModel().selectionChanged.connect(self._selection_changed)

        self.new_button = QPushButton("新增用户")
        self.new_button.setProperty("variant", "primary")
        self.edit_button = QPushButton("编辑")
        self.enable_button = QPushButton("启用")
        self.disable_button = QPushButton("禁用")
        self.refresh_button = QPushButton("刷新")
        self.new_button.clicked.connect(self.open_create_dialog)
        self.edit_button.clicked.connect(self.open_edit_dialog)
        self.enable_button.clicked.connect(lambda: self.set_selected_active(True))
        self.disable_button.clicked.connect(lambda: self.set_selected_active(False))
        self.refresh_button.clicked.connect(self.reload)

        actions = QHBoxLayout()
        actions.addWidget(self.new_button)
        actions.addWidget(self.edit_button)
        actions.addWidget(self.enable_button)
        actions.addWidget(self.disable_button)
        actions.addStretch(1)
        actions.addWidget(self.refresh_button)

        header = QFrame(self)
        header.setProperty("panel", "true")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(16, 16, 16, 16)
        header_layout.setSpacing(8)
        header_layout.addWidget(self.title_label)
        header_layout.addWidget(self.subtitle_label)
        header_layout.addWidget(self.permission_hint)
        header_layout.addLayout(actions)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        layout.addWidget(header)
        layout.addWidget(self.filter_panel)
        layout.addWidget(self.table, 1)

        self._apply_permission_state()
        self._apply_action_state()

    def reload(self) -> None:
        self.load_users(page=self._page, per_page=self._per_page)

    def apply_filters(self) -> None:
        self.load_users(page=1, per_page=self._per_page)

    def reset_filters(self) -> None:
        self.role_filter.setCurrentIndex(0)
        self.status_filter.setCurrentIndex(0)
        self.load_users(page=1, per_page=self._per_page)

    def load_users(self, *, page: int = 1, per_page: int = DEFAULT_PER_PAGE) -> None:
        self._page = page
        self._per_page = per_page
        self._selected_user_id = None
        self.table.set_state(TableState.LOADING, "正在加载用户")
        self.filter_panel.set_querying(True)
        self._apply_action_state()
        try:
            result = self._call_list_users(UserListQuery(
                role=self.role_filter.currentData(),
                is_active=self.status_filter.currentData(),
                page=page,
                per_page=per_page,
            ))
        except Exception:
            self._show_load_error(LOAD_FAILED_TEXT)
            return
        finally:
            self.filter_panel.set_querying(False)

        if not bool(getattr(result, "success", False)):
            if int(getattr(result, "code", 0) or 0) == int(ErrorCode.PERMISSION_DENIED):
                self.permission_hint.show_denied()
            self._show_load_error(getattr(result, "message", LOAD_FAILED_TEXT))
            return
        rows, total = _extract_page(getattr(result, "data", None))
        self._rows = tuple(_coerce_user_row(item) for item in rows)
        self._total = total
        self.table.set_rows([_row_to_table(item) for item in self._rows])
        self.table.set_page(page, total=total, per_page=per_page)
        self.table.set_state(TableState.EMPTY if not self._rows else TableState.READY, "暂无匹配用户")
        self._apply_action_state()

    def open_create_dialog(self) -> None:
        if not self._require_manage_permission():
            return
        dialog = self._dialog_factory(self._user_service, self._session, self, user=None, can_manage_users=True)
        if dialog.exec():
            self.reload()
            self.userChanged.emit()

    def open_edit_dialog(self) -> None:
        if not self._require_manage_permission():
            return
        user = self.selected_user()
        if user is None:
            return
        dialog = self._dialog_factory(self._user_service, self._session, self, user=user, can_manage_users=True)
        if dialog.exec():
            self.reload()
            self.userChanged.emit()

    def set_selected_active(self, is_active: bool) -> None:
        if not self._require_manage_permission():
            return
        user = self.selected_user()
        if user is None:
            return
        if user.is_active == is_active:
            return
        if not is_active and not self._confirm_danger(self, user):
            # Dangerous account changes default to Cancel; service invariants are
            # still authoritative after the user explicitly confirms.
            return
        try:
            if is_active:
                result = self._user_service.update_user(self._session, user.id, UpdateUserCommand(is_active=True))
            elif hasattr(self._user_service, "disable_user"):
                result = self._user_service.disable_user(self._session, user.id)
            else:
                result = self._user_service.update_user(self._session, user.id, UpdateUserCommand(is_active=False))
        except Exception:
            self._show_load_error(ACTION_FAILED_TEXT)
            return
        if bool(getattr(result, "success", False)):
            self.reload()
            self.userChanged.emit()
            return
        if int(getattr(result, "code", 0) or 0) == int(ErrorCode.PERMISSION_DENIED):
            self.permission_hint.show_denied()
        self._show_load_error(getattr(result, "message", ACTION_FAILED_TEXT))

    def selected_user(self) -> UserRow | None:
        if self._selected_user_id is None:
            return None
        for row in self._rows:
            if row.id == self._selected_user_id:
                return row
        return None

    def _call_list_users(self, query: UserListQuery) -> object:
        if self._user_service is None:
            raise RuntimeError("user service is required")
        pagination = Pagination(page=query.page, per_page=query.per_page)
        list_method = getattr(self._user_service, "list_users", None) or getattr(self._user_service, "list", None)
        if list_method is None:
            raise RuntimeError("user list service is unavailable")
        try:
            return list_method(self._session, role=query.role, is_active=query.is_active, pagination=pagination)
        except TypeError:
            return list_method(self._session, query)

    def _change_page(self, page: int, per_page: int) -> None:
        self.load_users(page=page, per_page=per_page)

    def _selection_changed(self) -> None:
        indexes = self.table.table.selectionModel().selectedRows()
        if not indexes:
            self._selected_user_id = None
        else:
            row = indexes[0].row()
            self._selected_user_id = self._rows[row].id if 0 <= row < len(self._rows) else None
        self._apply_action_state()

    def _require_manage_permission(self) -> bool:
        if self._can_manage_users:
            return True
        self.permission_hint.show_denied()
        self.table.set_state(TableState.ERROR, "当前账号无权限执行此操作，已记录权限失败事件。")
        return False

    def _apply_permission_state(self) -> None:
        for button in (self.new_button, self.edit_button, self.enable_button, self.disable_button):
            button.setVisible(self._can_manage_users)
        self.permission_hint.setVisible(not self._can_manage_users)

    def _apply_action_state(self) -> None:
        selected = self.selected_user()
        has_selection = selected is not None and self._can_manage_users and self.table.state() is TableState.READY
        self.edit_button.setEnabled(has_selection)
        self.enable_button.setEnabled(has_selection and not selected.is_active if selected else False)
        self.disable_button.setEnabled(has_selection and selected.is_active if selected else False)
        self.new_button.setEnabled(self._can_manage_users and self.table.state() is not TableState.LOADING)
        self.refresh_button.setEnabled(self.table.state() is not TableState.LOADING)

    def _show_load_error(self, message: object) -> None:
        self._rows = ()
        self._selected_user_id = None
        self.table.set_rows([])
        self.table.set_page(self._page, total=0, per_page=self._per_page)
        self.table.set_state(TableState.ERROR, controlled_error_text(message, fallback=LOAD_FAILED_TEXT))
        self._apply_action_state()

    def _default_dialog_factory(
        self,
        user_service: object | None,
        session: object | None,
        parent: QWidget,
        *,
        user: object | None,
        can_manage_users: bool,
    ) -> UserEditorDialog:
        return UserEditorDialog(
            user_service=user_service,
            session=session,
            parent=parent,
            user=user,
            can_manage_users=can_manage_users,
        )


def _can_manage_from_session(session: object | None) -> bool:
    role = getattr(session, "role", None)
    if role is None:
        return False
    try:
        return role_has_permission(str(role), Permission.USER_MANAGE.value)
    except ValueError:
        return False


def _extract_page(data: object) -> tuple[Sequence[object], int]:
    if data is None:
        return (), 0
    if isinstance(data, tuple) and len(data) == 2:
        rows, total = data
        return tuple(rows), int(total)
    items = getattr(data, "items", None)
    total = getattr(data, "total", None)
    if items is not None and total is not None:
        return tuple(items), int(total)
    if isinstance(data, list):
        return tuple(data), len(data)
    return (), 0


def _coerce_user_row(item: object) -> UserRow:
    return UserRow(
        id=int(_value(item, "id", 0)),
        username=str(_value(item, "username", "")),
        role=str(_value(item, "role", Role.OPERATOR.value)),
        is_active=bool(_value(item, "is_active", False)),
        created_at=str(_value(item, "created_at", "")),
        updated_at=str(_value(item, "updated_at", "")),
        remark=str(_value(item, "remark", _value(item, "notes", ""))),
    )


def _row_to_table(row: UserRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "username": row.username,
        "role_label": "管理员" if row.role == Role.ADMIN.value else "操作员",
        "status_label": "启用" if row.is_active else "禁用",
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "remark": row.remark,
    }


def _value(source: object, key: str, default: object = None) -> object:
    if isinstance(source, dict):
        return source.get(key, default)
    return getattr(source, key, default)


def _confirm_disable_user(parent: QWidget, user: UserRow) -> bool:
    return RiskConfirmDialog.confirm(
        "确认禁用账号",
        f"将禁用账号：{user.username}。若这是唯一管理员，服务层会拒绝执行。",
        parent,
        confirm_text="确认禁用",
    )
