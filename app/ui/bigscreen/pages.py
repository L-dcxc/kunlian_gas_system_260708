from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.services.bigscreen_service import (
    BigscreenAlarmFocus,
    BigscreenDeviceCard,
    BigscreenMapPoint,
    BigscreenMetricSummary,
    BigscreenSnapshot,
)
from app.services.models import DeviceStatus
from app.ui.common.errors import controlled_error_text
from app.ui.common.safe_text import SafeTextLabel
from app.ui.common.status import AlarmPulseController, StatusBadge, device_status_visual, repolish

EMPTY_TEXT = "暂无实时数据"
ALARM_STATUSES = {DeviceStatus.ALARM_LOW.value, DeviceStatus.ALARM_HIGH.value, DeviceStatus.OVER_RANGE.value}


class BigscreenDataPage(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("bigscreenPage", "true")
        self.metric_cards: dict[str, BigscreenMetricCard] = {
            "online_rate": BigscreenMetricCard("在线率", "--", unit="%"),
            "active_alarms": BigscreenMetricCard("当前报警", "0", unit="条"),
            "faults": BigscreenMetricCard("故障设备", "0", unit="台"),
            "running": BigscreenMetricCard("运行设备", "0", unit="台"),
        }
        self.empty_label = SafeTextLabel(EMPTY_TEXT, selectable=False)
        self.empty_label.setObjectName("BigscreenEmptyLabel")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.alarm_panel = BigscreenAlarmFocusPanel()

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(24)
        for index, card in enumerate(self.metric_cards.values()):
            grid.addWidget(card, index // 2, index % 2)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(24)
        layout.addLayout(grid, 3)
        layout.addWidget(self.alarm_panel, 2)
        layout.addWidget(self.empty_label)

    def render(self, snapshot: BigscreenSnapshot | None) -> None:
        summary = snapshot.summary if snapshot is not None else None
        cards = snapshot.device_cards if snapshot is not None else ()
        self._render_summary(summary)
        self.alarm_panel.render(snapshot.alarm_focus if snapshot is not None else None)
        self.empty_label.setVisible(not cards)

    def _render_summary(self, summary: BigscreenMetricSummary | None) -> None:
        if summary is None or summary.total_detectors <= 0:
            values = {"online_rate": "--", "active_alarms": "0", "faults": "0", "running": "0"}
            subtitles = {key: EMPTY_TEXT for key in values}
        else:
            running = max(0, summary.total_detectors - summary.offline_count - summary.disabled_count)
            online_rate = round(running * 100 / summary.total_detectors)
            values = {
                "online_rate": str(online_rate),
                "active_alarms": str(summary.active_alarm_count),
                "faults": str(summary.fault_count),
                "running": str(running),
            }
            subtitles = {
                "online_rate": f"总数 {summary.total_detectors} 台 / 离线 {summary.offline_count} 台",
                "active_alarms": f"低/高/超量程 {summary.alarm_count} 台",
                "faults": f"异常 {summary.invalid_count} 台 / 预热 {summary.warming_count} 台",
                "running": f"正常 {summary.normal_count} 台 / 屏蔽 {summary.disabled_count} 台",
            }
        statuses = {
            "online_rate": "running" if values["online_rate"] != "--" else "offline",
            "active_alarms": "highAlarm" if values["active_alarms"] != "0" else "normal",
            "faults": "fault" if values["faults"] != "0" else "normal",
            "running": "running" if values["running"] != "0" else "offline",
        }
        for key, card in self.metric_cards.items():
            card.set_metric(values[key], subtitle=subtitles[key], status=statuses[key])


class BigscreenMapPage(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("bigscreenPage", "true")
        self.map_canvas = BigscreenMapCanvas()
        self.side_panel = QFrame()
        self.side_panel.setProperty("screenPanel", "true")
        self.map_title = SafeTextLabel("地图监控", selectable=False)
        self.map_title.setProperty("role", "bigscreenPanelTitle")
        self.point_count_label = SafeTextLabel("点位 0", selectable=False)
        self.point_count_label.setProperty("role", "bigscreenMuted")
        self.alarm_list = QVBoxLayout()
        self.alarm_list.setSpacing(12)

        side_layout = QVBoxLayout(self.side_panel)
        side_layout.setContentsMargins(24, 24, 24, 24)
        side_layout.setSpacing(16)
        side_layout.addWidget(self.map_title)
        side_layout.addWidget(self.point_count_label)
        side_layout.addLayout(self.alarm_list)
        side_layout.addStretch(1)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(24)
        layout.addWidget(self.map_canvas, 5)
        layout.addWidget(self.side_panel, 2)

    def render(self, snapshot: BigscreenSnapshot | None) -> None:
        points = snapshot.map_points if snapshot is not None else ()
        self.map_canvas.set_points(points)
        map_name = points[0].map_name if points else "地图监控"
        self.map_title.set_safe_text(map_name or "地图监控")
        self.point_count_label.set_safe_text(f"点位 {len(points)} / 报警 {sum(1 for item in points if item.active_alarm)}")
        _clear_layout(self.alarm_list)
        alarm_points = [point for point in points if point.active_alarm]
        if not alarm_points:
            self.alarm_list.addWidget(_message_label("当前无未恢复警情"))
            return
        for point in alarm_points[:6]:
            self.alarm_list.addWidget(BigscreenMapAlarmItem(point))


class BigscreenDevicesPage(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("bigscreenPage", "true")
        self.content = QWidget()
        self.body = QVBoxLayout(self.content)
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(18)
        scroll = QScrollArea()
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.content)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(scroll)

    def render(self, snapshot: BigscreenSnapshot | None) -> None:
        cards = snapshot.device_cards if snapshot is not None else ()
        _clear_layout(self.body)
        if not cards:
            self.body.addWidget(_message_panel(EMPTY_TEXT))
            self.body.addStretch(1)
            return
        for title, items in _group_device_cards(cards):
            self.body.addWidget(BigscreenDeviceGroup(title, items))
        self.body.addStretch(1)


class BigscreenAlarmFocusPanel(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("screenPanel", "true")
        self.title_label = SafeTextLabel("报警焦点", selectable=False)
        self.title_label.setProperty("role", "bigscreenPanelTitle")
        self.name_label = SafeTextLabel("当前无未恢复警情", selectable=True)
        self.name_label.setProperty("role", "alarmText")
        self.detail_label = SafeTextLabel("", selectable=True)
        self.detail_label.setProperty("role", "bigscreenMuted")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)
        layout.addWidget(self.title_label)
        layout.addWidget(self.name_label)
        layout.addWidget(self.detail_label)
        layout.addStretch(1)

    def render(self, focus: BigscreenAlarmFocus | None) -> None:
        if focus is None:
            self.setProperty("alarm", None)
            self.name_label.set_safe_text("当前无未恢复警情")
            self.detail_label.set_safe_text("报警恢复后大屏将继续轮播")
        else:
            visual = device_status_visual(focus.alarm_type)
            self.setProperty("alarm", visual.alarm_value or "high")
            value = _value_text(focus.trigger_value, focus.device_card.unit)
            self.name_label.set_safe_text(_safe_display(f"{focus.device_card.detector_name}  {visual.text}"))
            self.detail_label.set_safe_text(_safe_display(f"位置 {focus.device_card.position_code} / 当前值 {value} / 开始 {focus.start_time}"))
        repolish(self)


class BigscreenMetricCard(QFrame):
    def __init__(self, title: str, value: str, *, unit: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("screenPanel", "true")
        self.title_label = SafeTextLabel(title, selectable=False)
        self.title_label.setProperty("role", "bigscreenMetricTitle")
        self.value_label = SafeTextLabel(value, selectable=False, max_chars=80)
        self.value_label.setProperty("role", "screenMetric")
        self.unit_label = SafeTextLabel(unit, selectable=False, max_chars=32)
        self.unit_label.setProperty("role", "bigscreenMetricUnit")
        self.subtitle_label = SafeTextLabel("", selectable=False)
        self.subtitle_label.setProperty("role", "bigscreenMuted")

        value_row = QHBoxLayout()
        value_row.setContentsMargins(0, 0, 0, 0)
        value_row.setSpacing(8)
        value_row.addWidget(self.value_label)
        value_row.addWidget(self.unit_label, 0, Qt.AlignmentFlag.AlignBottom)
        value_row.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)
        layout.addWidget(self.title_label)
        layout.addLayout(value_row)
        layout.addWidget(self.subtitle_label)

    def set_metric(self, value: object, *, subtitle: object = "", status: str = "normal") -> None:
        self.value_label.set_safe_text(value)
        self.subtitle_label.set_safe_text(subtitle)
        self.setProperty("status", status)
        self.value_label.setProperty("status", status)
        repolish(self)
        repolish(self.value_label)


class BigscreenMapCanvas(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("BigscreenMapCanvas")
        self.setProperty("screenPanel", "true")
        self.setMinimumSize(520, 380)
        self._points: tuple[BigscreenMapPoint, ...] = ()
        self._items: dict[int, BigscreenMapPointItem] = {}
        self._pulse_controller = AlarmPulseController(self)
        self.message_label = SafeTextLabel(EMPTY_TEXT, self, selectable=False)
        self.message_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.message_label.setProperty("role", "bigscreenMuted")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.message_label, 1)

    def set_points(self, points: Iterable[BigscreenMapPoint]) -> None:
        self._points = tuple(points)
        self.message_label.setVisible(not self._points)
        active_ids = {point.point_id for point in self._points}
        for point_id in tuple(self._items):
            if point_id not in active_ids:
                self._items[point_id].deleteLater()
                del self._items[point_id]
        for point in self._points:
            item = self._items.get(point.point_id)
            if item is None:
                item = BigscreenMapPointItem(point, self._pulse_controller, self)
                self._items[point.point_id] = item
            else:
                item.update_point(point)
            item.show()
            item.raise_()
        self._layout_points()

    def item_for_point(self, point_id: int) -> BigscreenMapPointItem | None:
        return self._items.get(point_id)

    def point_center(self, point_id: int) -> QPoint | None:
        item = self._items.get(point_id)
        return None if item is None else item.center()

    def plot_rect(self) -> QRect:
        return self.contentsRect().adjusted(28, 28, -28, -28)

    def resizeEvent(self, event) -> None:  # noqa: N802 ANN001
        super().resizeEvent(event)
        self._layout_points()

    def _layout_points(self) -> None:
        rect = self.plot_rect()
        for point in self._points:
            item = self._items.get(point.point_id)
            if item is None:
                continue
            # Bigscreen displays service-provided ratios only; it never persists or emits pixel coordinates.
            x = rect.left() + _clamp_ratio(point.x_ratio) * rect.width()
            y = rect.top() + _clamp_ratio(point.y_ratio) * rect.height()
            item.set_center(QPoint(round(x), round(y)))
            item.raise_()


class BigscreenMapPointItem(QFrame):
    def __init__(
        self,
        point: BigscreenMapPoint,
        pulse_controller: AlarmPulseController,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._point = point
        self._pulse_controller = pulse_controller
        self.setFixedSize(56, 56)
        self.setProperty("role", "bigscreenMapPoint")
        self.label = SafeTextLabel(selectable=False, max_chars=40)
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setProperty("role", "mapPointText")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self.label)
        self.update_point(point)

    def update_point(self, point: BigscreenMapPoint) -> None:
        self._point = point
        visual = device_status_visual(point.status)
        self.label.set_safe_text(_safe_display(point.label or point.detector_name or point.detector_id, max_chars=40))
        self.setProperty("pointStatus", visual.property_value)
        self.setProperty("offline", "true" if point.status == DeviceStatus.OFFLINE.value else "false")
        self.setToolTip(_safe_display(point.detector_name or point.label or point.detector_id))
        if point.active_alarm and visual.pulse_eligible:
            self._pulse_controller.start_for_status(self, point.status)
        else:
            self._pulse_controller.stop(self)
        repolish(self)

    def set_center(self, center: QPoint) -> None:
        self.move(int(center.x() - self.width() / 2), int(center.y() - self.height() / 2))

    def center(self) -> QPoint:
        return QPoint(self.x() + self.width() // 2, self.y() + self.height() // 2)


class BigscreenMapAlarmItem(QFrame):
    def __init__(self, point: BigscreenMapPoint, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("role", "bigscreenAlarmItem")
        visual = device_status_visual(point.status)
        title = SafeTextLabel(_safe_display(point.detector_name or point.label or point.detector_id), selectable=True)
        title.setProperty("role", "alarmText")
        detail = SafeTextLabel(_safe_display(f"{visual.text} / {point.map_name} / {_value_text(point.concentration, point.unit)}"), selectable=True)
        detail.setProperty("role", "bigscreenMuted")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)
        layout.addWidget(title)
        layout.addWidget(detail)


class BigscreenDeviceGroup(QFrame):
    def __init__(self, title: str, cards: tuple[BigscreenDeviceCard, ...], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("screenPanel", "true")
        title_label = SafeTextLabel(_safe_display(title), selectable=True)
        title_label.setProperty("role", "bigscreenPanelTitle")
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(16)
        for index, card in enumerate(cards):
            grid.addWidget(BigscreenDeviceItem(card), index // 4, index % 4)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(18)
        layout.addWidget(title_label)
        layout.addLayout(grid)


class BigscreenDeviceItem(QFrame):
    def __init__(self, card: BigscreenDeviceCard, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("card", "bigscreenDetector")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._pulse_controller = AlarmPulseController(self)
        self.name_label = SafeTextLabel(selectable=True)
        self.name_label.setProperty("role", "bigscreenDeviceName")
        self.value_label = SafeTextLabel(selectable=False, max_chars=80)
        self.value_label.setProperty("role", "bigscreenDeviceValue")
        self.unit_label = SafeTextLabel(selectable=False, max_chars=32)
        self.unit_label.setProperty("role", "bigscreenMuted")
        self.status_badge = StatusBadge()
        self.detail_label = SafeTextLabel(selectable=True)
        self.detail_label.setProperty("role", "bigscreenMuted")

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.addWidget(self.name_label, 1)
        header.addWidget(self.status_badge)
        value = QHBoxLayout()
        value.setContentsMargins(0, 0, 0, 0)
        value.setSpacing(8)
        value.addWidget(self.value_label)
        value.addWidget(self.unit_label, 0, Qt.AlignmentFlag.AlignBottom)
        value.addStretch(1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)
        layout.addLayout(header)
        layout.addLayout(value)
        layout.addWidget(self.detail_label)
        self.update_card(card)

    def update_card(self, card: BigscreenDeviceCard) -> None:
        visual = device_status_visual(card.status)
        is_offline = card.status == DeviceStatus.OFFLINE.value
        self.name_label.set_safe_text(_safe_display(card.detector_name))
        self.value_label.set_safe_text("--" if is_offline else _format_number(card.concentration))
        self.unit_label.set_safe_text("" if is_offline else _safe_display(card.unit or "", max_chars=32))
        self.detail_label.set_safe_text(_safe_display(f"{card.position_code} / {card.controller_name or '直连探头'} / {card.gas_type or '-'}"))
        self.status_badge.set_status(card.status, active_alarm=card.active_alarm)
        self.setProperty("deviceStatus", visual.property_value)
        if card.active_alarm and visual.pulse_eligible:
            self._pulse_controller.start_for_status(self, card.status)
        else:
            self._pulse_controller.stop(self)
        repolish(self)


class BigscreenAlarmPage(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("bigscreenPage", "true")
        self.focus_panel = BigscreenAlarmFocusPanel()
        self.map_canvas = BigscreenMapCanvas()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(24)
        layout.addWidget(self.focus_panel, 2)
        layout.addWidget(self.map_canvas, 3)

    def render(self, snapshot: BigscreenSnapshot | None) -> None:
        focus = snapshot.alarm_focus if snapshot is not None else None
        self.focus_panel.render(focus)
        if focus is not None and focus.map_point is not None:
            self.map_canvas.set_points((focus.map_point,))
        elif snapshot is not None:
            self.map_canvas.set_points(tuple(point for point in snapshot.map_points if point.active_alarm))
        else:
            self.map_canvas.set_points(())


def _group_device_cards(cards: Iterable[BigscreenDeviceCard]) -> tuple[tuple[str, tuple[BigscreenDeviceCard, ...]], ...]:
    buckets: dict[tuple[int | None, str], list[BigscreenDeviceCard]] = defaultdict(list)
    for card in cards:
        title = _safe_display(card.controller_name or "直连探头")
        buckets[(card.controller_id, title)].append(card)
    groups: list[tuple[str, tuple[BigscreenDeviceCard, ...]]] = []
    for (_controller_id, title), items in sorted(buckets.items(), key=lambda pair: (pair[0][0] is None, pair[0][0] or 0)):
        ordered = tuple(sorted(items, key=lambda item: item.detector_id))
        alarm_count = sum(1 for item in ordered if item.status in ALARM_STATUSES)
        offline_count = sum(1 for item in ordered if item.status == DeviceStatus.OFFLINE.value)
        groups.append((f"{title}  总数 {len(ordered)} / 报警 {alarm_count} / 离线 {offline_count}", ordered))
    return tuple(groups)


def _message_panel(message: str) -> QFrame:
    panel = QFrame()
    panel.setProperty("screenPanel", "true")
    label = _message_label(message)
    layout = QVBoxLayout(panel)
    layout.setContentsMargins(24, 24, 24, 24)
    layout.addWidget(label)
    return panel


def _message_label(message: str) -> SafeTextLabel:
    label = SafeTextLabel(message, selectable=False)
    label.setProperty("role", "bigscreenMuted")
    return label


def _value_text(value: object, unit: object | None) -> str:
    return f"{_format_number(value)} {_safe_display(unit or '', max_chars=32)}".strip()


def _safe_display(value: object, *, max_chars: int = 160) -> str:
    return controlled_error_text(value, fallback="-", max_chars=max_chars) or "-"


def _format_number(value: object) -> str:
    if value is None or value == "":
        return "--"
    try:
        return f"{float(value):g}"
    except (TypeError, ValueError):
        return "--"


def _clamp_ratio(value: object) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return min(1.0, max(0.0, parsed))


def _clear_layout(layout: QVBoxLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.deleteLater()
        child_layout = item.layout()
        if child_layout is not None:
            _clear_layout(child_layout)  # type: ignore[arg-type]
