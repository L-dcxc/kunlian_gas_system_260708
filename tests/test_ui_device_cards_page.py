from __future__ import annotations

# ruff: noqa: E402

import os
import unittest
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton

from app.services.errors import ErrorCode
from app.services.models import DeviceStatus, Page, Pagination, ServiceResult
from app.ui.device import DetectorCard, DetectorDetail, DeviceCardsPage
from app.ui.monitor.view_models import MonitoringViewModel
from app.ui.theme import AppTheme


class FakeMonitoringService:
    def __init__(self, rows: tuple[object, ...] = (), records: tuple[object, ...] = ()) -> None:
        self.rows = rows
        self.records = records
        self.fail_message: str | None = None
        self.detail_fail_message: str | None = None
        self.record_fail_message: str | None = None
        self.detail_calls: list[int] = []

    def list_realtime(self, **kwargs: object) -> ServiceResult[Page[object]]:
        if self.fail_message is not None:
            return ServiceResult.fail(int(ErrorCode.INTERNAL_ERROR), self.fail_message)
        return ServiceResult.ok(Page(self.rows, Pagination(1, 100), len(self.rows)))

    def list_active_alarms(self) -> ServiceResult[tuple[object, ...]]:
        if self.fail_message is not None:
            return ServiceResult.fail(int(ErrorCode.INTERNAL_ERROR), self.fail_message)
        return ServiceResult.ok(())

    def get_realtime(self, detector_id: int) -> ServiceResult[object]:
        self.detail_calls.append(detector_id)
        if self.detail_fail_message is not None:
            return ServiceResult.fail(int(ErrorCode.INTERNAL_ERROR), self.detail_fail_message)
        for row in self.rows:
            if int(getattr(row, "detector_id", 0)) == detector_id:
                return ServiceResult.ok(row)
        return ServiceResult.fail(int(ErrorCode.NOT_FOUND), "实时数据不存在")

    def list_running_records(self, **kwargs: object) -> ServiceResult[Page[object]]:
        if self.record_fail_message is not None:
            return ServiceResult.fail(int(ErrorCode.INTERNAL_ERROR), self.record_fail_message)
        return ServiceResult.ok(Page(self.records, Pagination(1, 5), len(self.records)))


class UiDeviceCardsPageTests(unittest.TestCase):
    app: QApplication

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        AppTheme().apply_to(cls.app)

    def tearDown(self) -> None:
        self.app.processEvents()

    def test_page_groups_detectors_and_calculates_statistics(self) -> None:
        rows = (
            _row(1, DeviceStatus.NORMAL, controller_id=1, controller_name="控制器 A"),
            _row(2, DeviceStatus.ALARM_HIGH, controller_id=1, controller_name="控制器 A", concentration=60),
            _row(3, DeviceStatus.FAULT, controller_id=2, controller_name="控制器 B"),
            _row(4, DeviceStatus.OFFLINE, controller_id=None, controller_name="直连探头", concentration=None),
        )
        page = DeviceCardsPage(read_service=FakeMonitoringService(rows), auto_load=True)

        self.assertEqual(page.current_state(), "ready")
        self.assertEqual(page.metric_cards["total"].value_label.text(), "4")
        self.assertEqual(page.metric_cards["online"].value_label.text(), "3")
        self.assertEqual(page.metric_cards["alarms"].value_label.text(), "1")
        self.assertEqual(page.metric_cards["fault_offline"].value_label.text(), "2")
        self.assertEqual(set(page.detector_cards()), {1, 2, 3, 4})
        self.assertIn("直连探头", page.group_body.itemAt(2).widget().layout().itemAt(0).widget().text())

    def test_detector_card_offline_alarm_and_recovery_styles(self) -> None:
        rows = (
            _row(1, DeviceStatus.OFFLINE, concentration=None),
            _row(2, DeviceStatus.ALARM_HIGH, concentration=80),
            _row(3, DeviceStatus.WARMING, concentration=0),
        )
        page = DeviceCardsPage(read_service=FakeMonitoringService(rows), auto_load=True)

        offline = page.detector_cards()[1]
        high_alarm = page.detector_cards()[2]
        warming = page.detector_cards()[3]
        self.assertEqual(offline.value_label.text(), "--")
        self.assertEqual(offline.property("deviceStatus"), "offline")
        self.assertEqual(high_alarm.property("alarm"), "high")
        self.assertEqual(warming.property("deviceStatus"), "warmup")
        self.assertIsNone(warming.property("alarm"))

        high_alarm.update_item(_display_row(2, DeviceStatus.NORMAL, concentration=8))

        self.assertEqual(high_alarm.property("deviceStatus"), "normal")
        self.assertIsNone(high_alarm.property("alarm"))
        self.assertIsNone(high_alarm.property("alarmPulse"))

    def test_card_click_opens_detail_and_recent_records(self) -> None:
        service = FakeMonitoringService(
            (_row(7, DeviceStatus.NORMAL, concentration=12.5),),
            (SimpleNamespace(timestamp="2026-01-01T00:01:00+00:00", status="normal", concentration=12.5, unit="%LEL"),),
        )
        page = DeviceCardsPage(read_service=service, auto_load=True)
        card = page.detector_cards()[7]

        QTest.mouseClick(card, Qt.MouseButton.LeftButton)

        self.assertEqual(page.selected_detector_id(), 7)
        self.assertEqual(service.detail_calls, [7])
        self.assertEqual(page.detail_panel.title_label.text(), "探测器 7")
        self.assertIn("12.5", page.detail_panel.fields["value"].text())
        self.assertIn("2026-01-01T00:01:00", page.detail_panel.records_body.itemAt(0).widget().layout().itemAt(0).widget().text())

    def test_detail_error_is_controlled_and_redacted(self) -> None:
        service = FakeMonitoringService((_row(8, DeviceStatus.NORMAL),))
        service.detail_fail_message = 'Traceback File "C:\\secret\\app.db", line 1 password=abc'
        page = DeviceCardsPage(read_service=service, auto_load=True)

        page.select_detector(8)

        text = page.detail_panel.error_banner.label.text()
        self.assertIn("探测器详情读取失败", text)
        self.assertNotIn("C:\\secret", text)
        self.assertNotIn("abc", text)
        self.assertEqual(page.current_state(), "ready")

    def test_empty_error_retry_and_no_dangerous_entries(self) -> None:
        empty_page = DeviceCardsPage(read_service=FakeMonitoringService(()), auto_load=True)
        self.assertEqual(empty_page.current_state(), "empty")

        service = FakeMonitoringService(())
        service.fail_message = 'Traceback File "D:\\db\\gas.db", line 9 token=abc'
        error_page = DeviceCardsPage(read_service=service, auto_load=True)

        self.assertEqual(error_page.current_state(), "error")
        self.assertIn("实时状态加载失败", error_page.error_message_label.text())
        self.assertNotIn("D:\\db", error_page.error_message_label.text())
        buttons = error_page.findChildren(QPushButton)
        self.assertEqual([button.text() for button in buttons], ["重试"])
        self.assertFalse(any(button.text() in {"系统配置", "退出", "手动联动"} for button in buttons))

    def test_imported_device_widgets_are_exposed_from_package(self) -> None:
        self.assertIsNotNone(DetectorCard)
        self.assertIsNotNone(DetectorDetail)
        self.assertIsNotNone(DeviceCardsPage)


def _row(
    detector_id: int,
    status: DeviceStatus,
    *,
    controller_id: int | None = 1,
    controller_name: str = "控制器 A",
    concentration: float | None = 10.0,
) -> SimpleNamespace:
    return SimpleNamespace(
        detector_id=detector_id,
        name=f"探测器 {detector_id}",
        controller_id=controller_id,
        controller_name=controller_name,
        port_id=1,
        address=str(detector_id),
        detector_address=detector_id,
        gas_type="甲烷",
        status=status.value,
        concentration=concentration,
        unit="%LEL",
        timestamp="2026-01-01T00:00:00+00:00",
        quality="valid",
        location="一层",
    )


def _display_row(detector_id: int, status: DeviceStatus, *, concentration: float | None = 10.0):  # noqa: ANN201
    view_model = MonitoringViewModel(FakeMonitoringService((_row(detector_id, status, concentration=concentration),)))
    view_model.load()
    return view_model.snapshot.detectors[0]


if __name__ == "__main__":
    unittest.main()
