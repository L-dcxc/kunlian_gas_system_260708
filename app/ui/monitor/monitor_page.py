from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from app.services.models import DeviceStatus
from app.ui.common.errors import ErrorBanner, controlled_error_text
from app.ui.common.metric_card import MetricCard
from app.ui.common.safe_text import SafeTextLabel
from app.ui.common.status import AlarmPulseController, StatusBadge, repolish
from app.ui.monitor.alarm_popup import AlarmPopupManager
from app.ui.monitor.view_models import (
    AlarmItemDisplay,
    ControllerGroupDisplay,
    DetectorDisplayItem,
    MonitoringSnapshot,
    MonitoringViewModel,
)

LOADING_TEXT = "正在加载实时状态..."
EMPTY_TEXT = "暂无探测器，请到设备配置新增"
ERROR_TEXT = "实时状态加载失败"


class DetectorCard(QFrame):
    clicked = Signal(int)

    def __init__(self, item: DetectorDisplayItem, pulse_controller: AlarmPulseController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("card", "detector")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._pulse_controller = pulse_controller
        self._detector_id = item.detector_id
        self.name_label = SafeTextLabel(selectable=True)
        self.name_label.setProperty("role", "panelTitle")
        self.address_label = SafeTextLabel(selectable=True)
        self.address_label.setProperty("role", "muted")
        self.value_label = SafeTextLabel(selectable=True, max_chars=80)
        self.value_label.setProperty("role", "concentration")
        self.unit_label = SafeTextLabel(selectable=False, max_chars=32)
        self.unit_label.setProperty("role", "muted")
        self.status_badge = StatusBadge(item.status)
        self.time_label = SafeTextLabel(selectable=True)
        self.time_label.setProperty("role", "muted")

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)
        header.addWidget(self.name_label, 1)
        header.addWidget(self.status_badge)

        value_row = QHBoxLayout()
        value_row.setContentsMargins(0, 0, 0, 0)
        value_row.setSpacing(6)
        value_row.addWidget(self.value_label)
        value_row.addWidget(self.unit_label)
        value_row.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        layout.addLayout(header)
        layout.addWidget(self.address_label)
        layout.addLayout(value_row)
        layout.addWidget(self.time_label)
        self.update_item(item)

    @property
    def detector_id(self) -> int:
        return self._detector_id

    def update_item(self, item: DetectorDisplayItem) -> None:
        self._detector_id = item.detector_id
        self.name_label.set_safe_text(item.name)
        self.address_label.set_safe_text(f"{item.controller_name} / 地址 {item.address} / {item.gas_type or '-'}")
        self.value_label.set_safe_text(item.concentration_text)
        self.unit_label.set_safe_text("" if item.is_offline else item.unit)
        self.time_label.set_safe_text(f"更新：{item.timestamp or '-'}")
        self.status_badge.set_status(item.status, active_alarm=item.pulse_eligible)
        self.setProperty("deviceStatus", item.status_property)
        self.setProperty("selected", False)
        if item.pulse_eligible:
            self._pulse_controller.start_for_status(self, item.status)
        else:
            self._pulse_controller.stop(self)
        repolish(self)

    def set_selected(self, selected: bool) -> None:
        self.setProperty("selected", selected)
        repolish(self)

    def mousePressEvent(self, event) -> None:  # noqa: N802 ANN001
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._detector_id)
        super().mousePressEvent(event)


class AlarmListItem(QFrame):
    clicked = Signal(int)

    def __init__(self, alarm: AlarmItemDisplay, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("role", "alarmListItem")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._detector_id = alarm.detector_id
        self.status_badge = StatusBadge(alarm.status)
        self.status_badge.set_status(alarm.status, active_alarm=alarm.popup_eligible)
        self.title_label = SafeTextLabel(alarm.detector_name, selectable=True)
        self.title_label.setProperty("role", "panelTitle")
        self.message_label = SafeTextLabel(alarm.message, selectable=True)
        self.message_label.setProperty("role", "warningText" if alarm.popup_eligible else "muted")
        self.time_label = SafeTextLabel(alarm.started_at, selectable=True)
        self.time_label.setProperty("role", "muted")

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        row.addWidget(self.status_badge)
        row.addWidget(self.title_label, 1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)
        layout.addLayout(row)
        layout.addWidget(self.message_label)
        layout.addWidget(self.time_label)

    def mousePressEvent(self, event) -> None:  # noqa: N802 ANN001
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._detector_id)
        super().mousePressEvent(event)


class MonitoringPage(QWidget):
    detectorSelected = Signal(int)

    def __init__(
        self,
        view_model: MonitoringViewModel | None = None,
        parent: QWidget | None = None,
        *,
        popup_manager: AlarmPopupManager | None = None,
        auto_load: bool = True,
    ) -> None:
        super().__init__(parent)
        self.view_model = view_model or MonitoringViewModel()
        self._owns_view_model = view_model is None
        self._snapshot: MonitoringSnapshot | None = None
        self._detector_cards: dict[int, DetectorCard] = {}
        self._selected_detector_id: int | None = None
        self._pulse_controller = AlarmPulseController(self)
        self.popup_manager = popup_manager or AlarmPopupManager(self)

        self.error_banner = ErrorBanner(); self.error_banner.clear()
        self.metric_cards = {
            "online": MetricCard("在线设备"),
            "alarms": MetricCard("当前报警"),
            "faults": MetricCard("故障设备"),
            "acquisition": MetricCard("采集状态"),
        }
        self.alarm_list_body = QVBoxLayout()
        self.alarm_list_body.setContentsMargins(0, 0, 0, 0)
        self.alarm_list_body.setSpacing(8)
        self.group_body = QVBoxLayout()
        self.group_body.setContentsMargins(0, 0, 0, 0)
        self.group_body.setSpacing(12)

        self.loading_panel = _message_panel(LOADING_TEXT, role="loading")
        self.empty_panel = _message_panel(EMPTY_TEXT, role="empty")
        self.error_panel = self._build_error_panel()
        self.content_panel = self._build_content_panel()
        self.stack = QStackedWidget()
        for panel in (self.loading_panel, self.empty_panel, self.error_panel, self.content_panel):
            self.stack.addWidget(panel)

        self.detail_panel = self._build_detail_panel()
        self._build_layout()
        self._connect_view_model()
        self.set_state("loading" if auto_load else "empty")
        if auto_load:
            self.view_model.load()

    def current_state(self) -> str:
        widget = self.stack.currentWidget()
        if widget is self.loading_panel:
            return "loading"
        if widget is self.empty_panel:
            return "empty"
        if widget is self.error_panel:
            return "error"
        return "ready"

    def selected_detector_id(self) -> int | None:
        return self._selected_detector_id

    def closeEvent(self, event) -> None:  # noqa: N802 ANN001
        self._pulse_controller.stop_all()
        if self._owns_view_model:
            self.view_model.dispose()
        super().closeEvent(event)

    def set_state(self, state: str) -> None:
        mapping = {
            "loading": self.loading_panel,
            "empty": self.empty_panel,
            "error": self.error_panel,
            "ready": self.content_panel,
        }
        self.stack.setCurrentWidget(mapping.get(state, self.content_panel))

    def select_detector(self, detector_id: int) -> None:
        self._selected_detector_id = detector_id
        for item_id, card in self._detector_cards.items():
            card.set_selected(item_id == detector_id)
        item = self._find_detector(detector_id)
        self._render_detail(item)
        self.view_model.load_detail(detector_id)
        self.detectorSelected.emit(detector_id)

    def _connect_view_model(self) -> None:
        self.view_model.loading_changed.connect(self._set_loading)
        self.view_model.error_changed.connect(self._set_error)
        self.view_model.snapshot_changed.connect(self._render_snapshot)
        self.view_model.detail_changed.connect(self._render_detail)
        self.view_model.alarm_popup_requested.connect(self.popup_manager.notify)

    def _set_loading(self, loading: bool) -> None:
        if loading:
            self.error_banner.clear()
            self.set_state("loading")

    def _set_error(self, message: str) -> None:
        if not message:
            self.error_banner.clear()
            return
        text = controlled_error_text(message, fallback=ERROR_TEXT)
        self.error_banner.set_error(text)
        if self._snapshot is None or self._snapshot.is_empty:
            self.error_message_label.set_safe_text(text)
            self.set_state("error")

    def _render_snapshot(self, snapshot: MonitoringSnapshot) -> None:
        self._snapshot = snapshot
        self._render_metrics(snapshot)
        self._render_alarm_list(snapshot.alarms)
        self._render_groups(snapshot.groups)
        self.set_state("empty" if snapshot.is_empty else "ready")
        if self._selected_detector_id is not None:
            self._render_detail(self._find_detector(self._selected_detector_id))

    def _render_metrics(self, snapshot: MonitoringSnapshot) -> None:
        for key, metric in zip(self.metric_cards, snapshot.metrics, strict=False):
            card = self.metric_cards[key]
            card.set_metric(metric.value, unit=metric.unit, subtitle=metric.subtitle)
            card.set_status(metric.status)

    def _render_alarm_list(self, alarms: Iterable[AlarmItemDisplay]) -> None:
        _clear_layout(self.alarm_list_body)
        alarms = tuple(alarms)
        if not alarms:
            label = SafeTextLabel("当前无未恢复警情", selectable=False)
            label.setProperty("role", "muted")
            self.alarm_list_body.addWidget(label)
        for alarm in alarms:
            item = AlarmListItem(alarm)
            item.clicked.connect(self.select_detector)
            self.alarm_list_body.addWidget(item)
        self.alarm_list_body.addStretch(1)

    def _render_groups(self, groups: Iterable[ControllerGroupDisplay]) -> None:
        _clear_layout(self.group_body)
        self._detector_cards.clear()
        for group in groups:
            panel = QFrame()
            panel.setProperty("panel", "true")
            title = SafeTextLabel(
                f"{group.title}  总数 {group.total_count} / 报警 {group.alarm_count} / 离线 {group.offline_count}",
                selectable=True,
            )
            title.setProperty("role", "panelTitle")
            grid = QGridLayout()
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setHorizontalSpacing(10)
            grid.setVerticalSpacing(10)
            for index, detector in enumerate(group.detectors):
                card = DetectorCard(detector, self._pulse_controller)
                card.clicked.connect(self.select_detector)
                card.set_selected(detector.detector_id == self._selected_detector_id)
                self._detector_cards[detector.detector_id] = card
                grid.addWidget(card, index // 2, index % 2)
            layout = QVBoxLayout(panel)
            layout.setContentsMargins(14, 14, 14, 14)
            layout.setSpacing(10)
            layout.addWidget(title)
            layout.addLayout(grid)
            self.group_body.addWidget(panel)
        self.group_body.addStretch(1)

    def _render_detail(self, item: object | None) -> None:
        if not isinstance(item, DetectorDisplayItem):
            self.detail_title.set_safe_text("未选择探测器")
            self.detail_status.set_status(DeviceStatus.INVALID, text="未选择")
            for label in self.detail_fields.values():
                label.set_safe_text("-")
            return
        self.detail_title.set_safe_text(item.name)
        self.detail_status.set_status(item.status, active_alarm=item.pulse_eligible)
        self.detail_fields["controller"].set_safe_text(item.controller_name)
        self.detail_fields["address"].set_safe_text(item.address)
        self.detail_fields["gas"].set_safe_text(item.gas_type)
        self.detail_fields["value"].set_safe_text(f"{item.concentration_text} {'' if item.is_offline else item.unit}".strip())
        self.detail_fields["quality"].set_safe_text(item.quality)
        self.detail_fields["updated"].set_safe_text(item.timestamp)
        self.detail_fields["location"].set_safe_text(item.location or "-")

    def _find_detector(self, detector_id: int) -> DetectorDisplayItem | None:
        if self._snapshot is None:
            return None
        return next((item for item in self._snapshot.detectors if item.detector_id == detector_id), None)

    def _build_layout(self) -> None:
        metrics = QHBoxLayout()
        metrics.setSpacing(12)
        for card in self.metric_cards.values():
            metrics.addWidget(card)

        body = QHBoxLayout()
        body.setSpacing(12)
        body.addWidget(self.stack, 4)
        body.addWidget(self.detail_panel, 2)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        layout.addWidget(self.error_banner)
        layout.addLayout(metrics)
        layout.addLayout(body, 1)

    def _build_content_panel(self) -> QWidget:
        panel = QWidget()
        alarm_panel = QFrame()
        alarm_panel.setProperty("panel", "true")
        alarm_title = SafeTextLabel("警情列表", selectable=False)
        alarm_title.setProperty("role", "panelTitle")
        alarm_layout = QVBoxLayout(alarm_panel)
        alarm_layout.setContentsMargins(12, 12, 12, 12)
        alarm_layout.setSpacing(8)
        alarm_layout.addWidget(alarm_title)
        alarm_layout.addLayout(self.alarm_list_body, 1)

        group_content = QWidget()
        group_content.setLayout(self.group_body)
        group_scroll = QScrollArea()
        group_scroll.setWidgetResizable(True)
        group_scroll.setFrameShape(QFrame.Shape.NoFrame)
        group_scroll.setWidget(group_content)

        layout = QHBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        layout.addWidget(alarm_panel, 1)
        layout.addWidget(group_scroll, 3)
        return panel

    def _build_error_panel(self) -> QWidget:
        panel = QFrame()
        panel.setProperty("panel", "true")
        self.error_message_label = SafeTextLabel(ERROR_TEXT, selectable=True)
        self.error_message_label.setProperty("role", "errorText")
        retry_button = QPushButton("重试")
        retry_button.setProperty("variant", "primary")
        retry_button.clicked.connect(self.view_model.retry)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        layout.addWidget(self.error_message_label)
        layout.addWidget(retry_button, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addStretch(1)
        return panel

    def _build_detail_panel(self) -> QFrame:
        panel = QFrame()
        panel.setProperty("panel", "true")
        self.detail_title = SafeTextLabel("未选择探测器", selectable=True)
        self.detail_title.setProperty("role", "panelTitle")
        self.detail_status = StatusBadge(DeviceStatus.INVALID)
        self.detail_fields = {
            "controller": SafeTextLabel("-", selectable=True),
            "address": SafeTextLabel("-", selectable=True),
            "gas": SafeTextLabel("-", selectable=True),
            "value": SafeTextLabel("-", selectable=True),
            "quality": SafeTextLabel("-", selectable=True),
            "updated": SafeTextLabel("-", selectable=True),
            "location": SafeTextLabel("-", selectable=True),
        }
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        for row, (key, title) in enumerate(
            (
                ("controller", "控制器"),
                ("address", "地址"),
                ("gas", "气体"),
                ("value", "当前值"),
                ("quality", "质量"),
                ("updated", "更新时间"),
                ("location", "位置"),
            )
        ):
            label = QLabel(title)
            label.setProperty("role", "fieldLabel")
            grid.addWidget(label, row, 0)
            grid.addWidget(self.detail_fields[key], row, 1)
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.addWidget(self.detail_title, 1)
        top.addWidget(self.detail_status)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)
        layout.addLayout(top)
        layout.addLayout(grid)
        layout.addStretch(1)
        return panel


def _message_panel(message: str, *, role: str) -> QFrame:
    panel = QFrame()
    panel.setProperty("panel", "true")
    panel.setProperty("state", role)
    label = SafeTextLabel(message, selectable=False)
    label.setProperty("role", "muted")
    layout = QVBoxLayout(panel)
    layout.setContentsMargins(16, 16, 16, 16)
    layout.addWidget(label, 0, Qt.AlignmentFlag.AlignTop)
    layout.addStretch(1)
    return panel


def _clear_layout(layout: QVBoxLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.deleteLater()
