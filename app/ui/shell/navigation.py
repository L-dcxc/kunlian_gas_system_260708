from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QPushButton, QWidget

from app.ui.common.errors import permission_denied_text
from app.ui.shell.page_registry import PageEntry

KEY_ROLE = 256
ALLOWED_ROLE = KEY_ROLE + 1
WINDOW_ROLE = KEY_ROLE + 2
PAGE_SIZE = 6


@dataclass(frozen=True, slots=True)
class NavigationItem:
    entry: PageEntry
    allowed: bool
    hidden: bool = False


class ShellNavigation(QFrame):
    pageRequested = Signal(str)
    permissionDenied = Signal(str)

    def __init__(
        self,
        entries: tuple[PageEntry, ...],
        session: object | None,
        parent: QWidget | None = None,
        *,
        hide_restricted: bool = False,
        page_size: int = PAGE_SIZE,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ShellNavBar")
        self._entries = entries
        self._session = session
        self._hide_restricted = hide_restricted
        self._page_size = max(1, page_size)
        self._items: tuple[NavigationItem, ...] = ()
        self._page = 0
        self._current_key: str | None = None

        self.prev_button = QPushButton("‹")
        self.prev_button.setObjectName("ShellNavPageButton")
        self.prev_button.setToolTip("上一组页面")
        self.next_button = QPushButton("›")
        self.next_button.setObjectName("ShellNavPageButton")
        self.next_button.setToolTip("下一组页面")
        self.prev_button.clicked.connect(self.previous_page)
        self.next_button.clicked.connect(self.next_page)

        self._page_buttons = tuple(QPushButton() for _ in range(self._page_size))
        for button in self._page_buttons:
            button.setObjectName("ShellNavButton")
            button.clicked.connect(lambda _checked=False, ref=button: self._button_clicked(ref))

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(6)
        layout.addWidget(self.prev_button)
        for button in self._page_buttons:
            layout.addWidget(button)
        layout.addWidget(self.next_button)

        self.apply_permissions()

    def set_session(self, session: object | None) -> None:
        self._session = session
        self.apply_permissions()

    def apply_permissions(self) -> None:
        items: list[NavigationItem] = []
        for entry in self._entries:
            allowed = has_permission(self._session, entry.permission)
            items.append(NavigationItem(entry=entry, allowed=allowed, hidden=self._hide_restricted and not allowed))
        self._items = tuple(items)
        self._page = min(self._page, max(0, self.page_count() - 1))
        self._render_page()

    def page_count(self) -> int:
        visible_count = len(self._visible_items())
        return max(1, (visible_count + self._page_size - 1) // self._page_size)

    def current_page(self) -> int:
        return self._page

    def visible_button_texts(self) -> tuple[str, ...]:
        return tuple(button.text() for button in self._page_buttons if isinstance(button.property("entryKey"), str))

    def select_first_allowed(self) -> str | None:
        for index, item in enumerate(self._visible_items()):
            if item.allowed and item.entry.kind != "window":
                self._page = index // self._page_size
                self._current_key = item.entry.key
                self._render_page()
                self.pageRequested.emit(item.entry.key)
                return item.entry.key
        return None

    def request_key(self, key: str) -> bool:
        visible = self._visible_items()
        for index, item in enumerate(visible):
            if item.entry.key != key:
                continue
            if not item.allowed:
                self.permissionDenied.emit(key)
                return False
            self._page = index // self._page_size
            if item.entry.kind != "window":
                self._current_key = key
            self._render_page()
            self.pageRequested.emit(key)
            return True
        return False

    def next_page(self) -> None:
        if self._page + 1 >= self.page_count():
            return
        self._page += 1
        self._render_page()

    def previous_page(self) -> None:
        if self._page <= 0:
            return
        self._page -= 1
        self._render_page()

    def button_for_key(self, key: str) -> QPushButton | None:
        for button in self._page_buttons:
            if button.property("entryKey") == key:
                return button
        return None

    def _button_clicked(self, button: QPushButton) -> None:
        key = button.property("entryKey")
        if isinstance(key, str):
            self.request_key(key)

    def _render_page(self) -> None:
        visible = self._visible_items()
        start = self._page * self._page_size
        page_items = visible[start : start + self._page_size]
        for button, item in zip(self._page_buttons, page_items, strict=False):
            title = item.entry.title if item.allowed else f"[锁] {item.entry.title}"
            button.setText(title)
            button.setToolTip("" if item.allowed else permission_denied_text())
            button.setProperty("entryKey", item.entry.key)
            button.setProperty("allowed", "true" if item.allowed else "false")
            button.setProperty("active", "true" if item.entry.key == self._current_key else "false")
            button.setEnabled(True)
            button.setVisible(True)
            _repolish(button)
        for button in self._page_buttons[len(page_items) :]:
            button.setText("")
            button.setProperty("entryKey", None)
            button.setVisible(False)
        total_pages = self.page_count()
        self.prev_button.setEnabled(self._page > 0)
        self.next_button.setEnabled(self._page + 1 < total_pages)
        self.prev_button.setToolTip(f"上一组页面 ({self._page + 1}/{total_pages})")
        self.next_button.setToolTip(f"下一组页面 ({self._page + 1}/{total_pages})")

    def _visible_items(self) -> tuple[NavigationItem, ...]:
        return tuple(item for item in self._items if not item.hidden)


def has_permission(session: object | None, permission: str | None) -> bool:
    if permission is None:
        return True
    permissions = getattr(session, "permissions", ()) or ()
    return "*" in permissions or permission in permissions


def _repolish(widget: QWidget) -> None:
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()
