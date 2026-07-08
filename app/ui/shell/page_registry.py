from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget

from app.services.permissions import Permission
from app.ui.common.safe_text import SafeTextLabel

PageKind = Literal["page", "window"]
PageFactory = Callable[["PageFactoryContext"], QWidget]


@dataclass(frozen=True, slots=True)
class PageFactoryContext:
    session: object | None = None
    services: Mapping[str, object] = field(default_factory=dict)
    devices: Mapping[str, object] = field(default_factory=dict)
    api: Mapping[str, object] = field(default_factory=dict)
    state_store: object | None = None
    event_bus: object | None = None
    config: object | None = None
    paths: object | None = None


@dataclass(frozen=True, slots=True)
class PageEntry:
    key: str
    title: str
    permission: str | None
    factory: PageFactory
    kind: PageKind = "page"


class PageRegistry:
    def __init__(self, entries: list[PageEntry] | tuple[PageEntry, ...] = ()) -> None:
        self._entries: dict[str, PageEntry] = {}
        for entry in entries:
            self.register(entry)

    def register(self, entry: PageEntry) -> None:
        if not entry.key or entry.key in self._entries:
            raise ValueError("page key must be unique")
        self._entries[entry.key] = entry

    def entries(self, *, include_windows: bool = True) -> tuple[PageEntry, ...]:
        values = tuple(self._entries.values())
        if include_windows:
            return values
        return tuple(entry for entry in values if entry.kind == "page")

    def get(self, key: str) -> PageEntry:
        try:
            return self._entries[key]
        except KeyError as exc:
            raise KeyError(f"unknown page: {key}") from exc

    def create(self, key: str, context: PageFactoryContext) -> QWidget:
        return self.get(key).factory(context)


def build_default_page_registry() -> PageRegistry:
    return PageRegistry(
        (
            PageEntry("monitor", "实时监控", Permission.MONITOR_VIEW.value, _monitor_page),
            PageEntry("map", "地图监控", Permission.MAP_VIEW.value, _map_page),
            PageEntry("device", "设备监控", Permission.MONITOR_VIEW.value, _device_page),
            PageEntry("chart", "曲线分析", Permission.CHART_VIEW.value, _chart_page),
            PageEntry("records", "记录查询", Permission.RECORD_VIEW.value, _records_page),
            PageEntry("settings", "系统配置", Permission.SYSTEM_SETTINGS.value, _settings_page),
            PageEntry("debug", "设备调试", Permission.DEVICE_DEBUG_VIEW.value, _debug_page),
            PageEntry("backup", "备份恢复", Permission.BACKUP_RESTORE.value, _backup_page),
            PageEntry("api", "本地 API", Permission.SYSTEM_SETTINGS.value, _api_page),
            PageEntry("maintenance", "维护提醒", Permission.MAINTENANCE_VIEW.value, _maintenance_page),
            PageEntry("linkage", "报警联动", Permission.LINKAGE_MANUAL_CONTROL.value, _linkage_page),
            PageEntry("bigscreen", "大屏展示", Permission.MONITOR_VIEW.value, _bigscreen_window, "window"),
        )
    )


def placeholder_page(title: str, detail: str = "页面服务未装配，当前仅显示容器占位。") -> QWidget:
    page = QFrame()
    page.setProperty("panel", "true")
    title_label = SafeTextLabel(title, selectable=False)
    title_label.setProperty("role", "panelTitle")
    detail_label = SafeTextLabel(detail, selectable=True)
    detail_label.setProperty("role", "muted")
    layout = QVBoxLayout(page)
    layout.setContentsMargins(24, 24, 24, 24)
    layout.setSpacing(12)
    layout.addWidget(title_label)
    layout.addWidget(detail_label)
    layout.addStretch(1)
    return page


def _service(ctx: PageFactoryContext, key: str) -> object | None:
    return ctx.services.get(key)


def _monitor_page(ctx: PageFactoryContext) -> QWidget:
    from app.ui.monitor.monitor_page import MonitoringPage
    from app.ui.monitor.view_models import MonitoringViewModel

    view_model = _service(ctx, "monitoring_view_model")
    if view_model is None:
        view_model = MonitoringViewModel(
            read_service=_service(ctx, "monitoring_read"),
            state_store=ctx.state_store,
            event_bus=ctx.event_bus,
        )
    return MonitoringPage(view_model=view_model, auto_load=False)


def _map_page(ctx: PageFactoryContext) -> QWidget:
    from app.ui.map.map_page import MapMonitoringPage
    from app.ui.map.view_models import MapMonitoringViewModel

    view_model = _service(ctx, "map_view_model")
    if view_model is None:
        view_model = MapMonitoringViewModel(
            map_service=_service(ctx, "map_runtime") or _service(ctx, "map_config"),
            state_store=ctx.state_store,
            event_bus=ctx.event_bus,
        )
    return MapMonitoringPage(view_model=view_model, session=ctx.session, auto_load=False)


def _device_page(ctx: PageFactoryContext) -> QWidget:
    from app.ui.device.device_cards_page import DeviceCardsPage

    return DeviceCardsPage(read_service=_service(ctx, "monitoring_read"), auto_load=False)


def _chart_page(ctx: PageFactoryContext) -> QWidget:
    chart_service = _service(ctx, "chart")
    if chart_service is None:
        return placeholder_page("曲线分析", "曲线服务未装配，无法查询实时或历史曲线。")
    from app.ui.chart.chart_page import ChartPage

    return ChartPage(chart_service=chart_service, export_service=_service(ctx, "export"), auto_start_realtime=False)


def _records_page(ctx: PageFactoryContext) -> QWidget:
    record_service = _service(ctx, "records")
    if record_service is None:
        return placeholder_page("记录查询", "记录服务未装配，无法查询报警、运行或操作记录。")
    from app.ui.records.records_page import RecordsPage

    return RecordsPage(record_service=record_service, session=ctx.session)


def _settings_page(ctx: PageFactoryContext) -> QWidget:
    from app.ui.settings.device_config_page import DeviceConfigPage

    return DeviceConfigPage(
        device_config_service=_service(ctx, "device_config"),
        map_config_service=_service(ctx, "map_config"),
        session=ctx.session,
    )


def _debug_page(ctx: PageFactoryContext) -> QWidget:
    from app.ui.settings.device_debug_page import DeviceDebugPage

    executor = _service(ctx, "device_debug_executor")
    send_executor = None
    if executor is not None and hasattr(executor, "send_debug_read"):
        send_executor = lambda command, _executor=executor: _executor.send_debug_read(ctx.session, command)
    elif callable(executor):
        send_executor = executor
    return DeviceDebugPage(
        debug_service=_service(ctx, "device_debug"),
        device_config_service=_service(ctx, "device_config"),
        session=ctx.session,
        send_executor=send_executor,
    )


def _backup_page(ctx: PageFactoryContext) -> QWidget:
    from app.ui.settings.backup_page import BackupRestorePage

    return BackupRestorePage(backup_service=_service(ctx, "backup"), session=ctx.session)


def _api_page(ctx: PageFactoryContext) -> QWidget:
    from app.ui.settings.local_api_page import LocalApiSettingsPage

    return LocalApiSettingsPage(
        api_host=ctx.api.get("host"),
        api_config_facade=ctx.api.get("config_facade"),
        session=ctx.session,
    )


def _maintenance_page(ctx: PageFactoryContext) -> QWidget:
    from app.ui.settings.maintenance_page import MaintenancePanel

    return MaintenancePanel(maintenance_service=_service(ctx, "maintenance"), session=ctx.session)


def _linkage_page(ctx: PageFactoryContext) -> QWidget:
    from app.ui.settings.linkage_page import LinkagePanel

    return LinkagePanel(linkage_service=_service(ctx, "linkage"), session=ctx.session)


def _bigscreen_window(ctx: PageFactoryContext) -> QWidget:
    service = _service(ctx, "bigscreen")
    if service is None:
        return placeholder_page("大屏展示", "大屏服务未装配，无法打开全屏展示。")
    from app.ui.bigscreen.bigscreen_window import BigscreenWindow

    return BigscreenWindow(service, auto_start=False)
