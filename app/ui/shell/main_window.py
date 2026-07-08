from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QMainWindow, QPushButton, QStackedWidget, QVBoxLayout, QWidget

from app.ui.common.errors import controlled_error_text, permission_denied_text
from app.ui.common.safe_text import SafeTextLabel
from app.ui.shell.alert_bar import GlobalAlertBar
from app.ui.shell.navigation import ShellNavigation
from app.ui.shell.page_registry import PageEntry, PageFactoryContext, PageRegistry, build_default_page_registry, placeholder_page
from app.ui.shell.status_bar import ShellStatusBar

SYSTEM_TITLE = "气体安全报警监控系统"


class MainWindowShell(QMainWindow):
    logoutRequested = Signal(object)

    def __init__(
        self,
        session: object,
        page_registry: PageRegistry | None = None,
        parent: QWidget | None = None,
        *,
        auth_service: object | None = None,
        license_service: object | None = None,
        state_store: object | None = None,
        event_bus: object | None = None,
        services: dict[str, object] | None = None,
        devices: dict[str, object] | None = None,
        api: dict[str, object] | None = None,
        config: object | None = None,
        paths: object | None = None,
        hide_restricted_navigation: bool = False,
        message_timeout_ms: int = 5000,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("MainWindowShell")
        self.setWindowTitle(SYSTEM_TITLE)
        self.resize(1280, 800)
        self._session = session
        self._auth_service = auth_service
        self._registry = page_registry or build_default_page_registry()
        self._context = PageFactoryContext(
            session=session,
            services=services or {},
            devices=devices or {},
            api=api or {},
            state_store=state_store,
            event_bus=event_bus,
            config=config,
            paths=paths,
        )
        self._page_indexes: dict[str, int] = {}
        self._window_refs: list[QWidget] = []
        self._closing_allowed = False
        self._message_timeout_ms = max(0, message_timeout_ms)

        entries = self._registry.entries(include_windows=True)
        self.navigation = ShellNavigation(
            entries,
            session,
            hide_restricted=hide_restricted_navigation,
        )
        self.navigation.pageRequested.connect(self.open_entry)
        self.navigation.permissionDenied.connect(self._show_permission_denied)

        self.status_bar = ShellStatusBar(session, license_service, state_store)
        self.alert_bar = GlobalAlertBar(state_store)
        self.stack = QStackedWidget()
        self.stack.setObjectName("ShellPageStack")
        self.message_bar = SafeTextLabel("", selectable=True, max_chars=256)
        self.message_bar.setObjectName("ShellBottomMessage")
        self.message_bar.setProperty("status", "normal")
        self.message_bar.hide()
        self._message_timer = QTimer(self)
        self._message_timer.setSingleShot(True)
        self._message_timer.timeout.connect(self._hide_message_bar)

        header = self._build_header()
        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        root_layout.addWidget(header)
        root_layout.addWidget(self.alert_bar)
        root_layout.addWidget(self.stack, 1)
        root_layout.addWidget(self.status_bar)
        root_layout.addWidget(self.message_bar)
        self.setCentralWidget(root)

        self.navigation.select_first_allowed()

    @property
    def session(self) -> object:
        return self._session

    def open_entry(self, key: str) -> bool:
        try:
            entry = self._registry.get(key)
        except KeyError:
            self.show_message("页面入口不存在。", status="warning")
            return False
        if entry.kind == "window":
            return self._open_window(entry)
        return self._open_page(entry)

    def show_message(self, message: object, *, status: str = "normal") -> None:
        self._message_timer.stop()
        if status == "normal":
            self._hide_message_bar()
            return
        self.message_bar.set_safe_text(controlled_error_text(message, fallback="操作失败，请稍后重试。"))
        self.message_bar.setProperty("status", status)
        _repolish(self.message_bar)
        self.message_bar.show()
        if self._message_timeout_ms:
            self._message_timer.start(self._message_timeout_ms)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if not self._closing_allowed:
            self._logout_current_session()
        self._closing_allowed = True
        super().closeEvent(event)

    def _open_page(self, entry: PageEntry) -> bool:
        if entry.key not in self._page_indexes:
            page = self._create_widget(entry)
            self._page_indexes[entry.key] = self.stack.addWidget(page)
        self.stack.setCurrentIndex(self._page_indexes[entry.key])
        return True

    def _open_window(self, entry: PageEntry) -> bool:
        window = self._create_widget(entry)
        # Bigscreen and future window entries stay independent from the main stack
        # so opening them never replaces the operator's current monitoring page.
        self._window_refs.append(window)
        if hasattr(window, "destroyed"):
            window.destroyed.connect(lambda _obj=None, ref=window: self._drop_window_ref(ref))
        if hasattr(window, "show") and not window.isVisible():
            window.show()
        return True

    def _create_widget(self, entry: PageEntry) -> QWidget:
        try:
            widget = self._registry.create(entry.key, self._context)
        except Exception as exc:
            self.show_message(exc, status="warning")
            return placeholder_page(entry.title, "页面加载失败，请稍后重试。")
        if not isinstance(widget, QWidget):
            self.show_message("页面工厂返回无效控件。", status="warning")
            return placeholder_page(entry.title, "页面加载失败，请稍后重试。")
        return widget

    def _show_permission_denied(self, _key: str) -> None:
        self.show_message(permission_denied_text(), status="warning")

    def _request_logout(self) -> None:
        self._logout_current_session()
        self._closing_allowed = True
        self.logoutRequested.emit(self._session)
        self.close()

    def _logout_current_session(self) -> None:
        if self._auth_service is None or not hasattr(self._auth_service, "logout"):
            return
        try:
            self._auth_service.logout(self._session)
        except Exception:
            return

    def _hide_message_bar(self) -> None:
        self.message_bar.clear()
        self.message_bar.hide()

    def _drop_window_ref(self, window: QWidget) -> None:
        self._window_refs = [ref for ref in self._window_refs if ref is not window]

    def _build_header(self) -> QFrame:
        header = QFrame()
        header.setObjectName("ShellHeader")

        logo = QLabel("堃联")
        logo.setObjectName("ShellLogo")
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title = SafeTextLabel(SYSTEM_TITLE, selectable=False)
        title.setObjectName("ShellTitle")
        subtitle = SafeTextLabel("gas security management system", selectable=False)
        subtitle.setObjectName("ShellSubtitle")

        title_box = QVBoxLayout()
        title_box.setContentsMargins(0, 0, 0, 0)
        title_box.setSpacing(0)
        title_box.addWidget(title)
        title_box.addWidget(subtitle)

        layout = QHBoxLayout(header)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(12)
        layout.addWidget(logo)
        layout.addLayout(title_box)
        layout.addStretch(1)
        layout.addWidget(self.navigation, 0)
        self.logout_button = QPushButton("切换账号")
        self.logout_button.setObjectName("ShellLogoutButton")
        self.logout_button.clicked.connect(self._request_logout)
        layout.addWidget(self.logout_button)
        return header


def _repolish(widget: QWidget) -> None:
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()
