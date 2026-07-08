from __future__ import annotations

from PySide6.QtCore import QDateTime, Qt, QTimer
from PySide6.QtGui import QKeyEvent, QShortcut
from PySide6.QtWidgets import QFrame, QHBoxLayout, QMainWindow, QPushButton, QStackedWidget, QVBoxLayout, QWidget

from app.services.bigscreen_service import BigscreenAlarmFocus, BigscreenSnapshot
from app.ui.bigscreen.pages import BigscreenAlarmPage, BigscreenDataPage, BigscreenDevicesPage, BigscreenMapPage
from app.ui.bigscreen.view_models import BigscreenViewModel
from app.ui.common.errors import ErrorBanner
from app.ui.common.safe_text import SafeTextLabel
from app.ui.common.status import StatusBadge, repolish
from app.ui.theme import AppTheme, ThemeMode

SYSTEM_TITLE = "气体安全报警监控大屏"
TIME_FORMAT = "yyyy-MM-dd HH:mm:ss"


class BigscreenWindow(QMainWindow):
    def __init__(self, service: object, parent: QWidget | None = None, *, auto_start: bool = True) -> None:
        super().__init__(parent)
        self.setObjectName("BigscreenWindow")
        self.setWindowTitle(SYSTEM_TITLE)
        self.setMinimumSize(1024, 720)
        self.view_model = BigscreenViewModel(service, self, auto_start=False)
        self._snapshot: BigscreenSnapshot | None = None
        self._alert_pulse = False

        AppTheme(ThemeMode.DARK).apply_to(self)
        self.setStyleSheet(f"{self.styleSheet()}\n{_bigscreen_qss()}")

        self.header = BigscreenHeader()
        self.alert_bar = BigscreenAlertBar()
        self.error_banner = ErrorBanner("", severity="warning")
        self.error_banner.hide()
        self.stack = QStackedWidget()
        self.data_page = BigscreenDataPage()
        self.map_page = BigscreenMapPage()
        self.devices_page = BigscreenDevicesPage()
        self.alarm_page = BigscreenAlarmPage()
        self._page_widgets = {
            "data": self.data_page,
            "map": self.map_page,
            "devices": self.devices_page,
            "alarm": self.alarm_page,
        }
        for widget in self._page_widgets.values():
            self.stack.addWidget(widget)

        root = QWidget()
        root.setObjectName("BigscreenRoot")
        layout = QVBoxLayout(root)
        layout.setContentsMargins(32, 32, 32, 32)
        layout.setSpacing(24)
        layout.addWidget(self.header)
        layout.addWidget(self.alert_bar)
        layout.addWidget(self.error_banner)
        layout.addWidget(self.stack, 1)
        self.setCentralWidget(root)

        self._clock_timer = QTimer(self)
        self._clock_timer.setInterval(1000)
        self._clock_timer.timeout.connect(self.header.update_time)
        self._clock_timer.start()

        self._alert_timer = QTimer(self)
        self._alert_timer.setInterval(400)
        self._alert_timer.timeout.connect(self._toggle_alert_pulse)

        QShortcut(Qt.Key.Key_F11, self, activated=self.toggle_fullscreen)
        self.header.exit_fullscreen_button.clicked.connect(self.exit_fullscreen)
        self.header.close_button.clicked.connect(self.close)
        self._connect_view_model()
        self.header.update_time()
        self.showFullScreen()
        if auto_start:
            self.view_model.load()

    def toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def exit_fullscreen(self) -> None:
        self.showNormal()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape and self.isFullScreen():
            self.exit_fullscreen()
            event.accept()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event) -> None:  # noqa: N802 ANN001
        self.view_model.dispose()
        self._clock_timer.stop()
        self._alert_timer.stop()
        super().closeEvent(event)

    def _connect_view_model(self) -> None:
        self.view_model.snapshotChanged.connect(self._render_snapshot)
        self.view_model.pageChanged.connect(self._set_page)
        self.view_model.alarmFocusChanged.connect(self._render_alarm_focus)
        self.view_model.errorChanged.connect(self._show_error)
        self.view_model.loadingChanged.connect(self._set_loading)

    def _render_snapshot(self, snapshot: BigscreenSnapshot) -> None:
        self._snapshot = snapshot
        self.error_banner.clear()
        self.header.render(snapshot)
        self.data_page.render(snapshot)
        self.map_page.render(snapshot)
        self.devices_page.render(snapshot)
        self.alarm_page.render(snapshot)

    def _render_alarm_focus(self, focus: BigscreenAlarmFocus | None) -> None:
        self.alert_bar.render(focus)
        if focus is None:
            self._alert_timer.stop()
            self.alert_bar.setProperty("alarmPulse", None)
            repolish(self.alert_bar)
        elif not self._alert_timer.isActive():
            # Only the narrow alert bar pulses; fullscreen backgrounds remain stable for readability.
            self._alert_timer.start()

    def _set_page(self, page_key: str) -> None:
        widget = self._page_widgets.get(page_key)
        if widget is not None:
            self.stack.setCurrentWidget(widget)

    def _show_error(self, message: str) -> None:
        self.error_banner.set_error(message, severity="warning")
        self.error_banner.show()

    def _set_loading(self, loading: bool) -> None:
        self.header.set_loading(loading)

    def _toggle_alert_pulse(self) -> None:
        self._alert_pulse = not self._alert_pulse
        self.alert_bar.setProperty("alarmPulse", self._alert_pulse)
        repolish(self.alert_bar)


class BigscreenHeader(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("BigscreenHeader")
        self.setProperty("screenPanel", "true")
        self.title_label = SafeTextLabel(SYSTEM_TITLE, selectable=False)
        self.title_label.setObjectName("BigscreenTitle")
        self.time_label = SafeTextLabel("--", selectable=False)
        self.time_label.setProperty("role", "bigscreenTime")
        self.acquisition_badge = StatusBadge("not_started")
        self.alarm_count_label = SafeTextLabel("报警 0", selectable=False)
        self.alarm_count_label.setProperty("role", "alarmCount")
        self.loading_label = SafeTextLabel("", selectable=False)
        self.loading_label.setProperty("role", "bigscreenMuted")
        self.exit_fullscreen_button = QPushButton("退出全屏")
        self.exit_fullscreen_button.setObjectName("BigscreenExitFullscreen")
        self.close_button = QPushButton("关闭大屏")
        self.close_button.setObjectName("BigscreenClose")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(24, 18, 24, 18)
        layout.setSpacing(18)
        layout.addWidget(self.title_label, 1)
        layout.addWidget(self.loading_label)
        layout.addWidget(self.acquisition_badge)
        layout.addWidget(self.alarm_count_label)
        layout.addWidget(self.time_label)
        layout.addWidget(self.exit_fullscreen_button)
        layout.addWidget(self.close_button)

    def render(self, snapshot: BigscreenSnapshot) -> None:
        self.acquisition_badge.set_status(snapshot.summary.acquisition_status)
        self.alarm_count_label.set_safe_text(f"报警 {snapshot.summary.active_alarm_count}")
        self.alarm_count_label.setProperty("active", "true" if snapshot.summary.active_alarm_count else "false")
        repolish(self.alarm_count_label)

    def update_time(self) -> None:
        self.time_label.set_safe_text(QDateTime.currentDateTime().toString(TIME_FORMAT))

    def set_loading(self, loading: bool) -> None:
        self.loading_label.set_safe_text("刷新中" if loading else "")


class BigscreenAlertBar(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("BigscreenAlertBar")
        self.setProperty("active", "false")
        self.title_label = SafeTextLabel("当前无未恢复警情", selectable=False)
        self.title_label.setProperty("role", "alarmText")
        self.detail_label = SafeTextLabel("", selectable=True)
        self.detail_label.setProperty("role", "bigscreenAlertDetail")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(24, 0, 24, 0)
        layout.setSpacing(20)
        layout.addWidget(self.title_label, 2)
        layout.addWidget(self.detail_label, 3)

    def render(self, focus: BigscreenAlarmFocus | None) -> None:
        if focus is None:
            self.setProperty("active", "false")
            self.title_label.set_safe_text("当前无未恢复警情")
            self.detail_label.set_safe_text("大屏按配置轮播数据、地图和设备页面")
        else:
            self.setProperty("active", "true")
            self.title_label.set_safe_text(f"警情：{focus.device_card.detector_name} {focus.alarm_type}")
            value = "--" if focus.trigger_value is None else f"{focus.trigger_value:g}"
            self.detail_label.set_safe_text(
                f"位置 {focus.device_card.position_code} / 值 {value} {focus.device_card.unit or ''} / 开始 {focus.start_time}"
            )
        repolish(self)


def _bigscreen_qss() -> str:
    return """
QMainWindow#BigscreenWindow, QWidget#BigscreenRoot {
    background: #0B1220;
    color: #F8FAFC;
}
QFrame[screenPanel="true"] {
    background: #111827;
    border: 1px solid #334155;
    border-radius: 10px;
}
QLabel#BigscreenTitle {
    font-size: 36px;
    font-weight: 700;
    color: #F8FAFC;
}
QLabel[role="bigscreenTime"] {
    font-size: 20px;
    font-weight: 500;
    color: #CBD5E1;
}
QLabel[role="alarmCount"] {
    font-size: 20px;
    font-weight: 700;
    color: #22C55E;
}
QLabel[role="alarmCount"][active="true"] { color: #F87171; }
QFrame#BigscreenAlertBar {
    min-height: 72px;
    background: #111827;
    border: 1px solid #334155;
    border-radius: 10px;
}
QFrame#BigscreenAlertBar[active="true"] {
    background: #DC2626;
    border-color: #F87171;
}
QFrame#BigscreenAlertBar[active="true"][alarmPulse="true"] { background: #B91C1C; }
QLabel[role="alarmText"] {
    font-size: 24px;
    font-weight: 700;
    color: #FFFFFF;
}
QLabel[role="bigscreenAlertDetail"], QLabel[role="bigscreenMuted"] {
    font-size: 18px;
    font-weight: 500;
    color: #CBD5E1;
}
QLabel[role="screenMetric"] {
    font-size: 56px;
    font-weight: 700;
    color: #22D3EE;
}
QLabel[role="screenMetric"][status="highAlarm"], QLabel[role="screenMetric"][status="fault"] { color: #F87171; }
QLabel[role="screenMetric"][status="offline"] { color: #94A3B8; }
QLabel[role="screenMetric"][status="normal"] { color: #22C55E; }
QLabel[role="bigscreenMetricTitle"], QLabel[role="bigscreenPanelTitle"] {
    font-size: 20px;
    font-weight: 700;
    color: #F8FAFC;
}
QLabel[role="bigscreenMetricUnit"] {
    font-size: 24px;
    font-weight: 700;
    color: #CBD5E1;
}
QLabel#BigscreenEmptyLabel {
    font-size: 24px;
    color: #94A3B8;
}
QFrame#BigscreenMapCanvas {
    background: #0F172A;
}
QFrame[role="bigscreenMapPoint"] {
    background: #22C55E;
    border: 2px solid #E2E8F0;
    border-radius: 28px;
}
QFrame[role="bigscreenMapPoint"][pointStatus="lowAlarm"] { background: #FB923C; }
QFrame[role="bigscreenMapPoint"][pointStatus="highAlarm"], QFrame[role="bigscreenMapPoint"][pointStatus="overRange"] { background: #F87171; }
QFrame[role="bigscreenMapPoint"][pointStatus="fault"] { background: #FCA5A5; }
QFrame[role="bigscreenMapPoint"][pointStatus="offline"] { background: #94A3B8; }
QFrame[role="bigscreenMapPoint"][pointStatus="shielded"] { background: #A78BFA; }
QFrame[role="bigscreenMapPoint"][pointStatus="warmup"] { background: #22D3EE; }
QFrame[role="bigscreenMapPoint"][alarmPulse="true"] { border: 4px solid #FFFFFF; }
QFrame[role="bigscreenAlarmItem"] {
    background: #3F1216;
    border: 1px solid #F87171;
    border-radius: 8px;
}
QFrame[card="bigscreenDetector"] {
    min-height: 132px;
    background: #0F172A;
    border: 1px solid #334155;
    border-radius: 8px;
}
QFrame[card="bigscreenDetector"][deviceStatus="normal"] { border-left: 5px solid #22C55E; }
QFrame[card="bigscreenDetector"][deviceStatus="lowAlarm"] { border-left: 5px solid #FB923C; }
QFrame[card="bigscreenDetector"][deviceStatus="highAlarm"], QFrame[card="bigscreenDetector"][deviceStatus="overRange"] { border-left: 5px solid #F87171; }
QFrame[card="bigscreenDetector"][deviceStatus="fault"] { border-left: 5px solid #FCA5A5; }
QFrame[card="bigscreenDetector"][deviceStatus="offline"] { background: #1F2937; color: #94A3B8; }
QLabel[role="bigscreenDeviceName"] {
    font-size: 18px;
    font-weight: 700;
    color: #F8FAFC;
}
QLabel[role="bigscreenDeviceValue"] {
    font-size: 36px;
    font-weight: 700;
    color: #22D3EE;
}
QPushButton#BigscreenExitFullscreen, QPushButton#BigscreenClose {
    min-height: 36px;
    font-size: 16px;
}
""".strip()
