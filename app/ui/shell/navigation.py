from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QListWidget, QListWidgetItem, QWidget

from app.ui.common.errors import permission_denied_text
from app.ui.shell.page_registry import PageEntry

KEY_ROLE = int(Qt.ItemDataRole.UserRole)
ALLOWED_ROLE = KEY_ROLE + 1
WINDOW_ROLE = KEY_ROLE + 2


class ShellNavigation(QListWidget):
    pageRequested = Signal(str)
    permissionDenied = Signal(str)

    def __init__(
        self,
        entries: tuple[PageEntry, ...],
        session: object | None,
        parent: QWidget | None = None,
        *,
        hide_restricted: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ShellNav")
        self._entries = entries
        self._session = session
        self._hide_restricted = hide_restricted
        self._populate()
        self.currentItemChanged.connect(self._current_item_changed)

    def set_session(self, session: object | None) -> None:
        self._session = session
        self.apply_permissions()

    def apply_permissions(self) -> None:
        for index in range(self.count()):
            item = self.item(index)
            entry = self._entry_for_item(item)
            allowed = has_permission(self._session, entry.permission)
            item.setData(ALLOWED_ROLE, allowed)
            item.setFlags(_allowed_flags(item.flags(), allowed))
            item.setHidden(self._hide_restricted and not allowed)
            item.setToolTip("" if allowed else permission_denied_text())
            item.setText(entry.title if allowed else f"[锁] {entry.title}")

    def select_first_allowed(self) -> str | None:
        for index in range(self.count()):
            item = self.item(index)
            if not item.isHidden() and bool(item.data(ALLOWED_ROLE)) and not bool(item.data(WINDOW_ROLE)):
                self.setCurrentRow(index)
                return str(item.data(KEY_ROLE))
        return None

    def request_key(self, key: str) -> bool:
        for index in range(self.count()):
            item = self.item(index)
            if item.data(KEY_ROLE) == key:
                if not bool(item.data(ALLOWED_ROLE)):
                    self.permissionDenied.emit(key)
                    return False
                self.setCurrentRow(index)
                self.pageRequested.emit(key)
                return True
        return False

    def _populate(self) -> None:
        for entry in self._entries:
            item = QListWidgetItem(entry.title)
            item.setData(KEY_ROLE, entry.key)
            item.setData(WINDOW_ROLE, entry.kind == "window")
            self.addItem(item)
        # Navigation filtering is only a UI affordance; protected services still
        # perform their own permission checks before any sensitive state changes.
        self.apply_permissions()

    def _current_item_changed(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        if current is None:
            return
        key = str(current.data(KEY_ROLE))
        if not bool(current.data(ALLOWED_ROLE)):
            self.permissionDenied.emit(key)
            return
        self.pageRequested.emit(key)

    def _entry_for_item(self, item: QListWidgetItem) -> PageEntry:
        key = str(item.data(KEY_ROLE))
        for entry in self._entries:
            if entry.key == key:
                return entry
        raise KeyError(key)


def has_permission(session: object | None, permission: str | None) -> bool:
    if permission is None:
        return True
    permissions = getattr(session, "permissions", ()) or ()
    return "*" in permissions or permission in permissions


def _allowed_flags(flags: Qt.ItemFlag, allowed: bool) -> Qt.ItemFlag:
    if allowed:
        return flags | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
    return (flags | Qt.ItemFlag.ItemIsSelectable) & ~Qt.ItemFlag.ItemIsEnabled
