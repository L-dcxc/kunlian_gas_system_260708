from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
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
from app.ui.common.status import AlarmPulseController
from app.ui.device.detector_card import DetectorCard
from app.ui.device.detector_detail import DetectorDetail
from app.ui.monitor.view_models import ControllerGroupDisplay, DetectorDisplayItem, MonitoringSnapshot, MonitoringViewModel

LOADING_TEXT = "正在加载设备卡片..."
EMPTY_TEXT = "暂无探测器，请到设备配置新增"
ERROR_TEXT = "实时状态加载失败"
DETAIL_ERROR_TEXT = "探测器详情读取失败"
ALARM_STATUSES = {DeviceStatus.ALARM_LOW.value, DeviceStatus.ALARM_HIGH.value, DeviceStatus.OVER_RANGE.value}
FAULT_OFFLINE_STATUSES = {DeviceStatus.FAULT.value, DeviceStatus.OFFLINE.value}


class DeviceCardsPage(QWidget):
    detectorSelected = Signal(int)

    def __init__(
        self,
        view_model: MonitoringViewModel | None = None,
        parent: QWidget | None = None,
        *,
        read_service: object | None = None,
        auto_load: bool = True,
    ) -> None:
        super().__init__(parent)
        self._read_service = read_service
        self.view_model = view_model or MonitoringViewModel(read_service=read_service)
        self._owns_view_model = view_model is None
        self._snapshot: MonitoringSnapshot | None = None
        self._selected_detector_id: int | None = None
        self._detail_error_message = ""
        self._detector_cards: dict[int, DetectorCard] = {}
        self._pulse_controller = AlarmPulseController(self)

        self.error_banner = ErrorBanner(); self.error_banner.clear()
        self.metric_cards = {
            "total": MetricCard("探测器总数"),
            "online": MetricCard("在线设备"),
            "alarms": MetricCard("当前报警"),
            "fault_offline": MetricCard("故障/离线"),
        }
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
        self.detail_panel = DetectorDetail()

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

    def detector_cards(self) -> dict[int, DetectorCard]:
        return dict(self._detector_cards)

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
        self._detail_error_message = ""
        for item_id, card in self._detector_cards.items():
            card.set_selected(item_id == detector_id)
        item = self._find_detector(detector_id)
        self.detail_panel.set_loading(item)
        self.view_model.load_detail(detector_id)
        self.detectorSelected.emit(detector_id)

    def _connect_view_model(self) -> None:
        self.view_model.loading_changed.connect(self._set_loading)
        self.view_model.error_changed.connect(self._set_error)
        self.view_model.snapshot_changed.connect(self._render_snapshot)
        self.view_model.detail_changed.connect(self._render_detail)

    def _set_loading(self, loading: bool) -> None:
        if loading:
            self.error_banner.clear()
            self.set_state("loading")

    def _set_error(self, message: str) -> None:
        if not message:
            self.error_banner.clear()
            return
        text = controlled_error_text(message, fallback=ERROR_TEXT)
        if self._selected_detector_id is not None and self._snapshot is not None and not self._snapshot.is_empty:
            detail_text = DETAIL_ERROR_TEXT if text == ERROR_TEXT else controlled_error_text(message, fallback=DETAIL_ERROR_TEXT)
            self._detail_error_message = detail_text
            self.detail_panel.show_error(detail_text)
            return
        self.error_banner.set_error(text)
        self.error_message_label.set_safe_text(text)
        self.set_state("error")

    def _render_snapshot(self, snapshot: MonitoringSnapshot) -> None:
        self._snapshot = snapshot
        self._render_metrics(snapshot.detectors)
        self._render_groups(snapshot.groups)
        self.set_state("empty" if snapshot.is_empty else "ready")
        if self._selected_detector_id is not None:
            item = self._find_detector(self._selected_detector_id)
            self._render_detail(item)
            if item is None:
                self._selected_detector_id = None

    def _render_metrics(self, detectors: Iterable[DetectorDisplayItem]) -> None:
        detectors = tuple(detectors)
        online = sum(1 for item in detectors if item.status not in {DeviceStatus.OFFLINE.value, DeviceStatus.DISABLED.value})
        alarms = sum(1 for item in detectors if item.status in ALARM_STATUSES)
        fault_offline = sum(1 for item in detectors if item.status in FAULT_OFFLINE_STATUSES)
        metrics = {
            "total": (len(detectors), "台", "normal", ""),
            "online": (online, "台", "running" if online else "offline", f"总数 {len(detectors)} 台"),
            "alarms": (alarms, "条", "highAlarm" if alarms else "normal", "低报/高报/超量程"),
            "fault_offline": (fault_offline, "台", "fault" if fault_offline else "normal", "故障或离线"),
        }
        for key, (value, unit, status, subtitle) in metrics.items():
            card = self.metric_cards[key]
            card.set_metric(value, unit=unit, subtitle=subtitle)
            card.set_status(status)

    def _render_groups(self, groups: Iterable[ControllerGroupDisplay]) -> None:
        _clear_layout(self.group_body)
        self._detector_cards.clear()
        for group in groups:
            panel = QFrame()
            panel.setProperty("panel", "true")
            panel.setProperty("role", "controllerGroup")
            title = SafeTextLabel(_group_title(group), selectable=True)
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
                grid.addWidget(card, index // 3, index % 3)
            layout = QVBoxLayout(panel)
            layout.setContentsMargins(14, 14, 14, 14)
            layout.setSpacing(10)
            layout.addWidget(title)
            layout.addLayout(grid)
            self.group_body.addWidget(panel)
        self.group_body.addStretch(1)

    def _render_detail(self, item: object | None) -> None:
        if not isinstance(item, DetectorDisplayItem):
            self.detail_panel.clear_detail()
            if self._selected_detector_id is not None:
                self.detail_panel.show_error(DETAIL_ERROR_TEXT)
            return
        records, error = self._load_recent_records(item.detector_id)
        self.detail_panel.set_detail(item, recent_records=records)
        message = error or self._detail_error_message
        if message:
            self.detail_panel.show_error(message)

    def _load_recent_records(self, detector_id: int) -> tuple[tuple[object, ...], str]:
        if self._read_service is None or not hasattr(self._read_service, "list_running_records"):
            return (), ""
        try:
            result = self._read_service.list_running_records(detector_id=detector_id, page=1, per_page=5)
        except Exception:
            return (), DETAIL_ERROR_TEXT
        if not bool(getattr(result, "success", False)):
            return (), controlled_error_text(getattr(result, "message", ""), fallback=DETAIL_ERROR_TEXT)
        data = getattr(result, "data", None)
        return tuple(getattr(data, "items", ()) if data is not None else ()), ""

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
        group_content = QWidget()
        group_content.setLayout(self.group_body)
        group_scroll = QScrollArea()
        group_scroll.setWidgetResizable(True)
        group_scroll.setFrameShape(QFrame.Shape.NoFrame)
        group_scroll.setWidget(group_content)
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(group_scroll)
        panel = QWidget()
        panel.setLayout(layout)
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


def _group_title(group: ControllerGroupDisplay) -> str:
    fault_count = sum(1 for item in group.detectors if item.status == DeviceStatus.FAULT.value)
    return f"{group.title}  总数 {group.total_count} / 报警 {group.alarm_count} / 故障 {fault_count} / 离线 {group.offline_count}"


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
