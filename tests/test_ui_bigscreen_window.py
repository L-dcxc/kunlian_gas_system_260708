from __future__ import annotations

# ruff: noqa: E402

import os
import unittest
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton

from app.services.bigscreen_service import (
    BigscreenAlarmFocus,
    BigscreenCarouselConfig,
    BigscreenDeviceCard,
    BigscreenMapPoint,
    BigscreenMetricSummary,
    BigscreenSnapshot,
)
from app.services.errors import ErrorCode
from app.services.models import DeviceStatus, ServiceResult
from app.ui.bigscreen import BigscreenDataPage, BigscreenMapPage, BigscreenViewModel, BigscreenWindow


class FakeBigscreenService:
    def __init__(self, snapshot: BigscreenSnapshot | None = None) -> None:
        self.snapshot = snapshot or _snapshot()
        self.calls = 0
        self.fail_message: str | None = None

    def get_snapshot(self) -> ServiceResult[BigscreenSnapshot]:
        self.calls += 1
        if self.fail_message is not None:
            return ServiceResult.fail(int(ErrorCode.INTERNAL_ERROR), self.fail_message)
        return ServiceResult.ok(self.snapshot)


class UiBigscreenWindowTests(unittest.TestCase):
    app: QApplication

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self) -> None:
        self.app.processEvents()

    def test_window_defaults_fullscreen_header_and_no_dangerous_entries(self) -> None:
        window = BigscreenWindow(FakeBigscreenService(_snapshot()), auto_start=True)
        self.app.processEvents()

        self.assertTrue(window.isFullScreen())
        self.assertIn("气体安全报警监控大屏", window.header.title_label.text())
        self.assertEqual(window.header.alarm_count_label.text(), "报警 0")
        self.assertEqual(window.stack.currentWidget(), window.data_page)
        button_texts = {button.text() for button in window.findChildren(QPushButton)}
        self.assertEqual(button_texts, {"退出全屏", "关闭大屏"})
        self.assertFalse(button_texts.intersection({"系统配置", "数据恢复", "删除", "批量清空", "手动联动"}))

        QTest.keyClick(window, Qt.Key.Key_F11)
        self.app.processEvents()

        self.assertFalse(window.isFullScreen())
        window.close()

    def test_data_page_keeps_metric_shell_for_empty_snapshot(self) -> None:
        page = BigscreenDataPage()
        page.render(_snapshot(cards=(), points=()))

        self.assertEqual(page.metric_cards["online_rate"].value_label.text(), "--")
        self.assertEqual(page.metric_cards["running"].subtitle_label.text(), "暂无实时数据")
        self.assertFalse(page.empty_label.isHidden())

    def test_devices_page_renders_offline_as_gray_dash(self) -> None:
        window = BigscreenWindow(FakeBigscreenService(_snapshot()), auto_start=True)
        self.app.processEvents()

        offline_items = [item for item in window.devices_page.findChildren(object) if getattr(item, "property", lambda _name: None)("deviceStatus") == "offline"]
        self.assertTrue(offline_items)
        offline = offline_items[0]
        self.assertEqual(offline.value_label.text(), "--")
        self.assertEqual(offline.unit_label.text(), "")
        window.close()

    def test_map_page_positions_points_from_ratios_after_resize(self) -> None:
        page = BigscreenMapPage()
        page.resize(1000, 700)
        snapshot = _snapshot()
        page.render(snapshot)
        page.show()
        self.app.processEvents()

        point = snapshot.map_points[0]
        rect = page.map_canvas.plot_rect()
        center = page.map_canvas.point_center(point.point_id)
        self.assertIsNotNone(center)
        self.assertAlmostEqual(center.x(), rect.left() + rect.width() * point.x_ratio, delta=2)
        self.assertAlmostEqual(center.y(), rect.top() + rect.height() * point.y_ratio, delta=2)

        page.resize(1200, 800)
        self.app.processEvents()
        rect = page.map_canvas.plot_rect()
        moved = page.map_canvas.point_center(point.point_id)
        self.assertAlmostEqual(moved.x(), rect.left() + rect.width() * point.x_ratio, delta=2)
        self.assertAlmostEqual(moved.y(), rect.top() + rect.height() * point.y_ratio, delta=2)
        self.assertFalse(hasattr(point, "x_pixel"))
        self.assertFalse(hasattr(point, "pixel_x"))
        page.close()

    def test_view_model_rotates_without_alarm_and_prioritizes_active_alarm(self) -> None:
        service = FakeBigscreenService(_snapshot(config=_config(pages=("data", "map", "devices"))))
        vm = BigscreenViewModel(service, auto_start=False)
        seen_pages: list[str] = []
        vm.pageChanged.connect(seen_pages.append)

        vm.load()
        vm.next_page()
        vm.next_page()

        self.assertEqual(seen_pages[-2:], ["map", "devices"])

        service.snapshot = _snapshot(alarm=True)
        vm.refresh()

        self.assertEqual(vm.current_page, "alarm")
        self.assertEqual(seen_pages[-1], "alarm")
        self.assertGreaterEqual(vm.refresh_interval_ms, 250)
        vm.dispose()

    def test_alarm_focus_switches_window_and_alert_bar_pulses_only_bar(self) -> None:
        window = BigscreenWindow(FakeBigscreenService(_snapshot(alarm=True)), auto_start=True)
        self.app.processEvents()

        self.assertEqual(window.stack.currentWidget(), window.alarm_page)
        self.assertEqual(window.alert_bar.property("active"), "true")
        self.assertTrue(window._alert_timer.isActive())
        window._toggle_alert_pulse()
        self.assertEqual(window.alert_bar.property("alarmPulse"), True)
        self.assertIsNone(window.centralWidget().property("alarmPulse"))
        window.close()

    def test_errors_and_user_text_are_plain_and_redacted(self) -> None:
        service = FakeBigscreenService(_snapshot())
        service.fail_message = 'Traceback File "C:\\secret\\app.sqlite3", line 1 password=abc SELECT * FROM devices'
        window = BigscreenWindow(service, auto_start=True)
        self.app.processEvents()

        text = window.error_banner.label.text()
        self.assertIn("大屏数据读取失败", text)
        self.assertNotIn("C:\\secret", text)
        self.assertNotIn("abc", text)
        self.assertNotIn("SELECT", text)

        service.fail_message = None
        service.snapshot = _snapshot(name='<b>探测器</b> token=abc C:\\secret\\db.sqlite3')
        window.view_model.refresh()
        self.app.processEvents()

        self.assertEqual(window.devices_page.findChildren(QPushButton), [])
        all_text = " ".join(label.text() for label in window.findChildren(type(window.header.title_label)))
        self.assertIn("探测器", all_text)
        self.assertNotIn("token=abc", all_text)
        self.assertNotIn("C:\\secret", all_text)
        window.close()


def _config(*, pages: tuple[str, ...] = ("data", "map", "devices"), interval: int = 5) -> BigscreenCarouselConfig:
    return BigscreenCarouselConfig(pages=pages, interval_seconds=interval, alarm_priority_enabled=True, refresh_after_ms=250)


def _summary(active_alarm_count: int = 0) -> BigscreenMetricSummary:
    return BigscreenMetricSummary(
        total_detectors=3,
        normal_count=1,
        alarm_count=1 if active_alarm_count else 0,
        offline_count=1,
        fault_count=0,
        disabled_count=0,
        warming_count=0,
        invalid_count=0,
        active_alarm_count=active_alarm_count,
        acquisition_status="running",
        generated_at="2026-01-01T00:00:00+00:00",
        refresh_after_ms=250,
    )


def _card(detector_id: int, status: DeviceStatus, *, name: str | None = None, alarm: bool = False) -> BigscreenDeviceCard:
    return BigscreenDeviceCard(
        detector_id=detector_id,
        position_code=f"D-{detector_id:03d}",
        detector_name=name or f"探测器 {detector_id}",
        controller_id=1,
        controller_name="控制器 A",
        status=status.value,
        concentration=None if status is DeviceStatus.OFFLINE else 35.5,
        gas_type="甲烷",
        unit="%LEL",
        alarm_level=2 if alarm else None,
        timestamp="2026-01-01T00:00:00+00:00",
        active_alarm=alarm,
        active_alarm_type=status.value if alarm else None,
    )


def _point(point_id: int, card: BigscreenDeviceCard, *, alarm: bool = False) -> BigscreenMapPoint:
    return BigscreenMapPoint(
        point_id=point_id,
        map_id=1,
        map_name="一层平面图",
        detector_id=card.detector_id,
        x_ratio=0.25,
        y_ratio=0.75,
        label="A1",
        detector_name=card.detector_name,
        status=card.status,
        concentration=card.concentration,
        unit=card.unit,
        active_alarm=alarm,
        active_alarm_type=card.status if alarm else None,
    )


def _snapshot(
    *,
    config: BigscreenCarouselConfig | None = None,
    cards: tuple[BigscreenDeviceCard, ...] | None = None,
    points: tuple[BigscreenMapPoint, ...] | None = None,
    alarm: bool = False,
    name: str | None = None,
) -> BigscreenSnapshot:
    normal = _card(1, DeviceStatus.NORMAL, name=name)
    offline = _card(2, DeviceStatus.OFFLINE)
    alarm_card = _card(3, DeviceStatus.ALARM_HIGH, alarm=alarm)
    device_cards = cards if cards is not None else (normal, offline, alarm_card)
    map_points = points if points is not None else (_point(10, alarm_card, alarm=alarm),)
    focus = None
    if alarm:
        focus = BigscreenAlarmFocus(
            alarm_id=99,
            detector_id=alarm_card.detector_id,
            alarm_type=DeviceStatus.ALARM_HIGH.value,
            alarm_level=2,
            trigger_value=70.0,
            start_time="2026-01-01T00:00:00+00:00",
            device_card=alarm_card,
            map_point=map_points[0] if map_points else None,
            refresh_after_ms=250,
        )
    return BigscreenSnapshot(
        config=config or _config(),
        summary=_summary(1 if alarm else 0) if cards is None else replace(_summary(), total_detectors=len(cards)),
        alarm_focus=focus,
        device_cards=device_cards,
        map_points=map_points,
    )


if __name__ == "__main__":
    unittest.main()
