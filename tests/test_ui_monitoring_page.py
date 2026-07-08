from __future__ import annotations

# ruff: noqa: E402

import os
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QThread
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog

from app.core.state_store import StateStore
from app.services.errors import ErrorCode
from app.services.models import (
    AcquisitionState,
    AcquisitionStatus,
    DeviceReading,
    DeviceSourceType,
    DeviceStatus,
    Page,
    Pagination,
    ProtocolMode,
    ServiceResult,
)
from app.ui.monitor.alarm_popup import AlarmPopupManager
from app.ui.monitor.monitor_page import DetectorCard, MonitoringPage
from app.ui.monitor.view_models import MonitoringViewModel
from app.ui.theme import AppTheme


class FakeMonitoringService:
    def __init__(self, rows: tuple[object, ...] = (), alarms: tuple[object, ...] = ()) -> None:
        self.rows = rows
        self.alarms = alarms
        self.calls = 0
        self.fail_message: str | None = None
        self.status = AcquisitionState(AcquisitionStatus.RUNNING, "采集正常")

    def list_realtime(self, **kwargs: object) -> ServiceResult[Page[object]]:
        self.calls += 1
        if self.fail_message is not None:
            return ServiceResult.fail(int(ErrorCode.INTERNAL_ERROR), self.fail_message)
        return ServiceResult.ok(Page(self.rows, Pagination(1, 100), len(self.rows)))

    def list_active_alarms(self) -> ServiceResult[tuple[object, ...]]:
        if self.fail_message is not None:
            return ServiceResult.fail(int(ErrorCode.INTERNAL_ERROR), self.fail_message)
        return ServiceResult.ok(self.alarms)

    def get_realtime(self, detector_id: int) -> ServiceResult[object]:
        for row in self.rows:
            if int(getattr(row, "detector_id", 0)) == detector_id:
                return ServiceResult.ok(row)
        return ServiceResult.fail(int(ErrorCode.NOT_FOUND), "不存在")

    def get_acquisition_status(self) -> AcquisitionState:
        return self.status


class CapturingPopup(QDialog):
    created: list[str] = []

    def __init__(self, alarm: object, parent=None) -> None:  # noqa: ANN001
        super().__init__(parent)
        key = getattr(alarm, "key", f"{getattr(alarm, 'detector_id', '')}:{getattr(alarm, 'status', '')}")
        self.alarm_key = str(key)
        CapturingPopup.created.append(self.alarm_key)

    def show(self) -> None:
        self.setVisible(True)


class UiMonitoringPageTests(unittest.TestCase):
    app: QApplication

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        AppTheme().apply_to(cls.app)

    def tearDown(self) -> None:
        self.app.processEvents()

    def test_view_model_loads_snapshot_and_throttles_state_events_to_qt_thread(self) -> None:
        service = FakeMonitoringService((_row(1, DeviceStatus.NORMAL),))
        state = StateStore(publish_interval_ms=0)
        view_model = MonitoringViewModel(service, state_store=state, throttle_ms=80)
        emitted_threads: list[QThread] = []
        view_model.snapshot_changed.connect(lambda _snapshot: emitted_threads.append(QThread.currentThread()))

        view_model.load()
        initial_calls = service.calls
        for _ in range(5):
            state.update_readings([_reading(1, DeviceStatus.ALARM_HIGH, 40.0)])
            self.app.processEvents()
        self.assertEqual(service.calls, initial_calls)

        QTest.qWait(view_model.throttle_interval_ms + 30)

        self.assertEqual(service.calls, initial_calls + 1)
        self.assertIs(emitted_threads[-1], self.app.thread())
        view_model.dispose()

    def test_monitoring_page_ready_select_detail_offline_and_status_styles(self) -> None:
        rows = (
            _row(1, DeviceStatus.NORMAL, concentration=12.5),
            _row(2, DeviceStatus.OFFLINE, concentration=None),
            _row(3, DeviceStatus.WARMING, concentration=0),
            _row(4, DeviceStatus.ALARM_HIGH, concentration=60),
        )
        alarms = (_alarm(10, 4, DeviceStatus.ALARM_HIGH, "2026-01-01T00:00:00+00:00"),)
        page = MonitoringPage(MonitoringViewModel(FakeMonitoringService(rows, alarms)), auto_load=True)

        self.assertEqual(page.current_state(), "ready")
        self.assertEqual(page.metric_cards["online"].value_label.text(), "3")
        offline = page._detector_cards[2]
        warming = page._detector_cards[3]
        high_alarm = page._detector_cards[4]
        self.assertEqual(offline.value_label.text(), "--")
        self.assertEqual(offline.property("deviceStatus"), "offline")
        self.assertEqual(warming.property("deviceStatus"), "warmup")
        self.assertIsNone(warming.property("alarm"))
        self.assertEqual(high_alarm.property("alarm"), "high")

        page.select_detector(2)

        self.assertEqual(page.selected_detector_id(), 2)
        self.assertEqual(page.detail_fields["value"].text(), "--")
        self.assertEqual(offline.property("selected"), True)

    def test_monitoring_page_empty_and_error_states_are_controlled(self) -> None:
        empty_page = MonitoringPage(MonitoringViewModel(FakeMonitoringService(())), auto_load=True)
        self.assertEqual(empty_page.current_state(), "empty")

        service = FakeMonitoringService(())
        service.fail_message = 'Traceback File "E:\\secret\\app.db", line 1 password=abc'
        error_page = MonitoringPage(MonitoringViewModel(service), auto_load=True)

        self.assertEqual(error_page.current_state(), "error")
        self.assertIn("实时状态加载失败", error_page.error_message_label.text())
        self.assertNotIn("E:\\secret", error_page.error_message_label.text())
        self.assertNotIn("abc", error_page.error_message_label.text())

    def test_alarm_popup_manager_dedupes_recovers_and_skips_warming_offline(self) -> None:
        CapturingPopup.created.clear()
        manager = AlarmPopupManager(popup_factory=CapturingPopup)
        alarm = _alarm(21, 1, DeviceStatus.ALARM_HIGH, "2026-01-01T00:00:00+00:00")
        warming = _alarm(None, 2, DeviceStatus.WARMING, "2026-01-01T00:00:00+00:00")
        offline = _alarm(None, 3, DeviceStatus.OFFLINE, "2026-01-01T00:00:00+00:00")

        self.assertEqual(manager.notify((alarm, warming, offline)), 1)
        self.assertEqual(manager.notify((alarm,)), 0)
        self.assertEqual(CapturingPopup.created, ["alarm:21"])
        manager.notify(())
        self.assertEqual(manager.notify((alarm,)), 1)

    def test_popup_text_is_controlled(self) -> None:
        CapturingPopup.created.clear()
        popup = CapturingPopup
        manager = AlarmPopupManager(popup_factory=popup)
        alarm = SimpleNamespace(
            key="detector:5:fault:t1",
            detector_id=5,
            detector_name='Traceback File "C:\\secret\\driver.py", line 2',
            status=DeviceStatus.FAULT.value,
            status_text="故障",
            message='故障 File "C:\\secret\\driver.py" password=abc',
            started_at="2026-01-01T00:00:00+00:00",
        )

        manager.notify((alarm,))
        real_popup = __import__("app.ui.monitor.alarm_popup", fromlist=["AlarmPopup"]).AlarmPopup(alarm)

        self.assertNotIn("C:\\secret", real_popup.title_label.text())
        self.assertNotIn("abc", real_popup.message_label.text())
        self.assertIn("报警信息", real_popup.message_label.text())


def _row(
    detector_id: int,
    status: DeviceStatus,
    *,
    concentration: float | None = 10.0,
    timestamp: str = "2026-01-01T00:00:00+00:00",
) -> SimpleNamespace:
    return SimpleNamespace(
        detector_id=detector_id,
        name=f"探测器 {detector_id}",
        controller_id=1,
        controller_name="控制器 A",
        port_id=1,
        address=str(detector_id),
        detector_address=detector_id,
        gas_type="甲烷",
        status=status.value,
        concentration=concentration,
        unit="%LEL",
        timestamp=timestamp,
        quality="valid",
        location="一层",
    )


def _alarm(active_id: int | None, detector_id: int, status: DeviceStatus, started_at: str) -> SimpleNamespace:
    return SimpleNamespace(
        key=f"alarm:{active_id}" if active_id is not None else f"detector:{detector_id}:{status.value}:{started_at}",
        active_alarm_id=active_id,
        detector_id=detector_id,
        detector_name=f"探测器 {detector_id}",
        status=status.value,
        status_text=status.value,
        message=f"{status.value}：30 %LEL",
        started_at=started_at,
        value_text="30",
        unit="%LEL",
    )


def _reading(detector_id: int, status: DeviceStatus, concentration: float | None) -> DeviceReading:
    return DeviceReading(
        protocol=ProtocolMode.PROTOCOL_2,
        source_type=DeviceSourceType.PROBE,
        port_id=1,
        controller_id=1,
        detector_id=detector_id,
        controller_address=1,
        detector_address=detector_id,
        status=status,
        concentration=concentration,
        gas_type="甲烷",
        unit="%LEL",
        alarm_level=1,
        raw_status=status.value,
        raw_value="fixture",
        timestamp=datetime.now(timezone.utc),
    )


if __name__ == "__main__":
    unittest.main()
