from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QButtonGroup, QFrame, QHBoxLayout, QPushButton, QStackedWidget, QVBoxLayout, QWidget

from app.services.permissions import Permission, role_has_permission
from app.ui.common.permission_hint import PermissionHint
from app.ui.common.safe_text import SafeTextLabel
from app.ui.settings.controllers_page import ControllersPage
from app.ui.settings.detectors_page import DetectorsPage
from app.ui.settings.gas_types_page import GasTypesPage
from app.ui.settings.ports_page import PortsPage
from app.ui.settings.protocol_settings_page import ProtocolSettingsPage


class DeviceConfigPage(QWidget):
    configChanged = Signal()

    def __init__(
        self,
        device_config_service: object | None = None,
        map_config_service: object | None = None,
        session: object | None = None,
        parent: QWidget | None = None,
        *,
        can_configure: bool | None = None,
        page_factories: dict[str, Callable[..., QWidget]] | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = device_config_service
        self._map_config_service = map_config_service
        self._session = session
        self._can_configure = _can_configure_from_session(session) if can_configure is None else can_configure
        self._nav_buttons: dict[str, QPushButton] = {}
        self._page_keys: list[str] = []
        self._factories = page_factories or {}

        self.title_label = SafeTextLabel("设备配置", selectable=False)
        self.title_label.setProperty("role", "panelTitle")
        self.subtitle_label = SafeTextLabel("配置端口、控制器、探测器、气体类型和导入导出。", selectable=False)
        self.subtitle_label.setProperty("role", "muted")
        self.permission_hint = PermissionHint()
        self.permission_hint.setVisible(not self._can_configure)

        self.new_button = QPushButton("新增")
        self.new_button.setProperty("variant", "primary")
        self.import_button = QPushButton("导入")
        self.export_button = QPushButton("导出")
        self.template_button = QPushButton("模板下载")
        self.refresh_button = QPushButton("刷新")
        self.new_button.clicked.connect(self.trigger_new)
        self.import_button.clicked.connect(self.trigger_import)
        self.export_button.clicked.connect(self.trigger_export)
        self.template_button.clicked.connect(self.trigger_template)
        self.refresh_button.clicked.connect(self.reload_current)

        toolbar = QHBoxLayout()
        toolbar.addWidget(self.new_button)
        toolbar.addWidget(self.import_button)
        toolbar.addWidget(self.export_button)
        toolbar.addWidget(self.template_button)
        toolbar.addStretch(1)
        toolbar.addWidget(self.refresh_button)

        header = QFrame()
        header.setProperty("panel", "true")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(16, 16, 16, 16)
        header_layout.setSpacing(8)
        header_layout.addWidget(self.title_label)
        header_layout.addWidget(self.subtitle_label)
        header_layout.addWidget(self.permission_hint)
        header_layout.addLayout(toolbar)

        self.nav = QFrame()
        self.nav.setObjectName("ConfigCategoryNav")
        self.nav.setProperty("panel", "true")
        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        nav_layout = QVBoxLayout(self.nav)
        nav_layout.setContentsMargins(8, 8, 8, 8)
        nav_layout.setSpacing(6)
        self.stack = QStackedWidget()
        for key, title in _CATEGORIES:
            button = QPushButton(title)
            button.setCheckable(True)
            button.clicked.connect(lambda _checked=False, name=key: self.set_category(name))
            self.nav_group.addButton(button)
            nav_layout.addWidget(button)
            self._nav_buttons[key] = button
            self._page_keys.append(key)
            page = self._create_page(key)
            if hasattr(page, "configChanged"):
                page.configChanged.connect(self.configChanged)  # type: ignore[attr-defined]
            self.stack.addWidget(page)
        nav_layout.addStretch(1)

        body = QHBoxLayout()
        body.setSpacing(12)
        body.addWidget(self.nav)
        body.addWidget(self.stack, 1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        layout.addWidget(header)
        layout.addLayout(body, 1)
        self.set_category("ports")
        self._apply_permission_state()

    def set_category(self, key: str) -> None:
        if key not in self._page_keys:
            raise ValueError("unknown device config category")
        index = self._page_keys.index(key)
        self.stack.setCurrentIndex(index)
        self._nav_buttons[key].setChecked(True)
        self._sync_toolbar_for_category(key)
        self.reload_current()

    def current_category(self) -> str:
        return self._page_keys[self.stack.currentIndex()]

    def current_page(self) -> QWidget:
        return self.stack.currentWidget()

    def reload_current(self) -> None:
        page = self.current_page()
        reload = getattr(page, "reload", None)
        if callable(reload):
            reload()

    def trigger_new(self) -> None:
        page = self.current_page()
        action = getattr(page, "new_record", None)
        if callable(action):
            action()

    def trigger_import(self) -> None:
        self.set_category("protocol")
        page = self.current_page()
        action = getattr(page, "import_detectors", None)
        if callable(action):
            action()

    def trigger_export(self) -> None:
        self.set_category("protocol")
        page = self.current_page()
        action = getattr(page, "export_config", None)
        if callable(action):
            action()

    def trigger_template(self) -> None:
        self.set_category("protocol")
        page = self.current_page()
        action = getattr(page, "download_template", None)
        if callable(action):
            action()

    def _create_page(self, key: str) -> QWidget:
        if key in self._factories:
            return self._factories[key](self)
        kwargs = {
            "device_config_service": self._service,
            "session": self._session,
            "can_configure": self._can_configure,
        }
        if key == "ports":
            return PortsPage(parent=self, **kwargs)
        if key == "controllers":
            return ControllersPage(parent=self, **kwargs)
        if key == "detectors":
            return DetectorsPage(parent=self, **kwargs)
        if key == "gas_types":
            return GasTypesPage(parent=self, **kwargs)
        return ProtocolSettingsPage(parent=self, **kwargs)

    def _apply_permission_state(self) -> None:
        self.permission_hint.setVisible(not self._can_configure)
        for button in (self.new_button, self.import_button, self.export_button, self.template_button):
            button.setVisible(self._can_configure)
        self.refresh_button.setEnabled(True)

    def _sync_toolbar_for_category(self, key: str) -> None:
        is_protocol = key == "protocol"
        self.new_button.setEnabled(self._can_configure and not is_protocol)
        self.import_button.setEnabled(self._can_configure)
        self.export_button.setEnabled(self._can_configure)
        self.template_button.setEnabled(self._can_configure)


def _can_configure_from_session(session: object | None) -> bool:
    role = getattr(session, "role", None)
    if role is None:
        return False
    try:
        return role_has_permission(str(role), Permission.SYSTEM_SETTINGS.value)
    except ValueError:
        return False


_CATEGORIES = (
    ("ports", "端口"),
    ("controllers", "控制器"),
    ("detectors", "探测器"),
    ("gas_types", "气体类型"),
    ("protocol", "导入导出"),
)
