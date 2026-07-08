from __future__ import annotations

# ruff: noqa: E402

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QLineEdit

from app.services.models import AcquisitionStatus, DeviceStatus
from app.ui.common.errors import ErrorBanner, ValidationHint, permission_denied_text, validation_failed_text
from app.ui.common.safe_text import SafeTextLabel, normalize_plain_text
from app.ui.common.status import (
    ALARM_PULSE_PERIOD_MS,
    AlarmPulseController,
    StatusBadge,
    alarm_property_for_status,
)
from app.ui.theme import AppTheme, ThemeMode, tokens_for_mode


class UiGlobalFoundationTests(unittest.TestCase):
    app: QApplication

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_theme_tokens_and_qss_cover_light_dark_focus_disabled_and_errors(self) -> None:
        light_theme = AppTheme()
        dark_theme = AppTheme(ThemeMode.DARK)
        self.assertEqual(light_theme.mode, ThemeMode.LIGHT)
        self.assertEqual(tokens_for_mode()["bg_window"], "#F1F5F9")
        self.assertEqual(tokens_for_mode("dark")["bg_window"], "#0B1220")

        qss = light_theme.qss()
        self.assertIn("#F1F5F9", qss)
        self.assertIn("QLineEdit:focus", qss)
        self.assertIn("QPushButton:disabled", qss)
        self.assertIn('QLineEdit[validation="error"]', qss)
        self.assertIn('QLabel[status="highAlarm"]', qss)
        self.assertNotIn("qlineargradient", qss.lower())
        self.assertNotIn("box-shadow", qss.lower())
        self.assertIn("#0B1220", dark_theme.qss())

        line_edit = QLineEdit()
        line_edit.setProperty("validation", "error")
        self.assertEqual(line_edit.property("validation"), "error")

    def test_status_badge_maps_status_to_plain_text_dynamic_properties(self) -> None:
        badge = StatusBadge(DeviceStatus.NORMAL)
        self.assertEqual(badge.text(), "正常")
        self.assertEqual(badge.property("status"), "normal")
        self.assertEqual(badge.textFormat(), Qt.TextFormat.PlainText)

        badge.set_status(DeviceStatus.ALARM_HIGH, text="<b>高报：10 %LEL</b>", active_alarm=True)
        self.assertEqual(badge.text(), "<b>高报：10 %LEL</b>")
        self.assertEqual(badge.property("status"), "highAlarm")
        self.assertEqual(badge.property("alarm"), "high")

        badge.set_status(DeviceStatus.NORMAL)
        self.assertEqual(badge.property("alarm"), None)
        self.assertEqual(badge.property("alarmPulse"), None)

        running = StatusBadge(AcquisitionStatus.RUNNING)
        self.assertEqual(running.property("status"), "running")
        self.assertEqual(alarm_property_for_status(DeviceStatus.OVER_RANGE), "overRange")
        self.assertIsNone(alarm_property_for_status(DeviceStatus.OFFLINE))
        self.assertIsNone(alarm_property_for_status(AcquisitionStatus.ERROR))

    def test_alarm_pulse_controller_starts_only_for_active_unrecovered_alarm_states(self) -> None:
        controller = AlarmPulseController()
        frame = QFrame()

        self.assertEqual(controller.pulse_period_ms, ALARM_PULSE_PERIOD_MS)
        self.assertEqual(controller.interval_ms, 400)
        self.assertFalse(controller.start_for_status(frame, DeviceStatus.NORMAL))
        self.assertFalse(controller.is_active())

        self.assertTrue(controller.start_for_status(frame, DeviceStatus.ALARM_HIGH))
        self.assertEqual(frame.property("alarm"), "high")
        self.assertEqual(frame.property("alarmPulse"), False)
        self.assertTrue(controller.is_active())

        controller._tick()
        self.assertEqual(frame.property("alarmPulse"), True)
        controller.stop(frame)
        self.assertEqual(frame.property("alarm"), None)
        self.assertEqual(frame.property("alarmPulse"), None)
        self.assertFalse(controller.is_active())

        self.assertFalse(controller.start_for_status(frame, DeviceStatus.FAULT, recovered=True))
        self.assertEqual(frame.property("alarm"), None)

    def test_safe_text_label_keeps_untrusted_text_plain_and_truncated(self) -> None:
        label = SafeTextLabel("<img src=x onerror=alert(1)>")
        self.assertEqual(label.textFormat(), Qt.TextFormat.PlainText)
        self.assertEqual(label.text(), "<img src=x onerror=alert(1)>")
        self.assertIsInstance(label, QLabel)

        label.set_safe_text("A\x00B")
        self.assertEqual(label.text(), "A\uFFFDB")
        self.assertEqual(normalize_plain_text("abcdef", max_chars=3), "abc...已截断")

    def test_error_components_hide_stack_paths_and_sensitive_details(self) -> None:
        banner = ErrorBanner(
            'Traceback File "E:\\secret\\app.sqlite3", line 10 license_code=abc machine_id=raw',
        )
        self.assertEqual(banner.label.textFormat(), Qt.TextFormat.PlainText)
        self.assertEqual(banner.label.text(), "操作失败，请稍后重试。")
        self.assertNotIn("Traceback", banner.label.text())
        self.assertNotIn("E:\\secret", banner.label.text())
        self.assertNotIn("abc", banner.label.text())

        banner.show_permission_denied()
        self.assertEqual(banner.property("severity"), "permission")
        self.assertEqual(banner.label.text(), permission_denied_text())

        hint = ValidationHint("name is required\nTraceback internal")
        self.assertEqual(hint.text(), validation_failed_text("name is required\nTraceback internal"))
        self.assertNotIn("Traceback", hint.text())

        banner.set_error("读取失败 path=E:\\data\\app.sqlite3 password=secret")
        self.assertNotIn("E:\\data", banner.label.text())
        self.assertNotIn("secret", banner.label.text())


if __name__ == "__main__":
    unittest.main()
