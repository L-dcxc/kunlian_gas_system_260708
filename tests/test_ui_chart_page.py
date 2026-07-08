from __future__ import annotations

# ruff: noqa: E402

import os
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QDateTime, Qt
from PySide6.QtWidgets import QApplication, QCheckBox

from app.services.chart_service import RealtimeSeriesPoint, RealtimeSeriesView
from app.services.errors import ErrorCode
from app.services.models import Page, Pagination, ServiceResult
from app.ui.chart import ChartPage
from app.ui.theme import AppTheme


class FakeChartService:
    def __init__(self) -> None:
        self.realtime_calls: list[tuple[int, ...]] = []
        self.history_commands: list[object] = []
        self.realtime_result: ServiceResult[tuple[RealtimeSeriesView, ...]] = ServiceResult.ok(())
        self.history_result: ServiceResult[Page[object]] = ServiceResult.ok(Page((), Pagination(1, 100), 0))
        self.on_history_query = None

    def get_realtime_series(self, detector_ids):  # noqa: ANN001 ANN201
        self.realtime_calls.append(tuple(detector_ids))
        return self.realtime_result

    def query_history(self, command):  # noqa: ANN001 ANN201
        self.history_commands.append(command)
        if self.on_history_query is not None:
            self.on_history_query()
        return self.history_result


class FakeExportService:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[dict[str, object], ...], str]] = []
        self.fail_message: str | None = None

    def build_chart_export(self, *, rows, export_format):  # noqa: ANN001 ANN201
        self.calls.append((tuple(rows), export_format))
        if self.fail_message is not None:
            return ServiceResult.fail(int(ErrorCode.INTERNAL_ERROR), self.fail_message)
        return ServiceResult.ok(SimpleNamespace(rows=tuple(rows), format=export_format, filename=f"chart.{export_format}"))


class UiChartPageTests(unittest.TestCase):
    app: QApplication

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        AppTheme().apply_to(cls.app)

    def tearDown(self) -> None:
        self.app.processEvents()

    def page(self, service: FakeChartService | None = None, export_service: FakeExportService | None = None) -> ChartPage:
        page = ChartPage(service or FakeChartService(), export_service or FakeExportService(), auto_start_realtime=False)
        page.resize(1100, 720)
        page.show()
        self.app.processEvents()
        return page

    def test_page_initializes_realtime_history_filters_chart_and_detail(self) -> None:
        page = self.page()

        self.assertEqual(page.tabs.count(), 2)
        self.assertEqual(page.tabs.tabText(0), "实时曲线")
        self.assertEqual(page.tabs.tabText(1), "历史曲线")
        self.assertEqual(page.detail_table.export_rows(), ())
        self.assertFalse(page.detail_table.table.export_button.isEnabled())
        self.assertFalse(page.detail_table.print_button.isEnabled())
        self.assertFalse(page.realtime_timer.isActive())

    def test_realtime_refresh_uses_timer_entry_without_long_loop(self) -> None:
        service = FakeChartService()
        service.realtime_result = ServiceResult.ok(
            (
                RealtimeSeriesView(
                    detector_id=1,
                    points=(RealtimeSeriesPoint(1, "normal", 12.5, "%LEL", "2026-01-01T00:00:00+00:00"),),
                ),
            )
        )
        page = self.page(service)
        page.realtime_detector_edit.setText("1")

        page.start_realtime()

        self.assertTrue(page.realtime_timer.isActive())
        self.assertEqual(service.realtime_calls, [(1,)])
        self.assertEqual(len(page.realtime_chart.series()), 1)
        self.assertIn("12.5", page.realtime_chart.current_label.text())

    def test_history_time_range_validation_marks_end_time_error(self) -> None:
        page = self.page()
        now = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
        page.start_time_edit.setDateTime(QDateTime(now))
        page.end_time_edit.setDateTime(QDateTime(now - timedelta(hours=1)))
        page.history_detector_edit.setText("1")

        self.assertFalse(page.query_history())

        self.assertIn("结束时间不能早于开始时间", page.error_banner.label.text())
        self.assertEqual(page.end_time_edit.property("validation"), "error")

    def test_querying_disables_repeat_submit_and_export_until_service_returns(self) -> None:
        service = FakeChartService()
        service.history_result = ServiceResult.ok(Page((_point(1, 10.0),), Pagination(1, 100), 1))
        page = self.page(service)
        _set_valid_history_filter(page)
        states: dict[str, bool] = {}

        def inspect_during_query() -> None:
            states["search_disabled"] = not page.history_filter.search_button.isEnabled()
            states["export_disabled"] = not page.detail_table.table.export_button.isEnabled()
            states["print_disabled"] = not page.detail_table.print_button.isEnabled()
            states["repeat_blocked"] = not page.query_history()

        service.on_history_query = inspect_during_query

        self.assertTrue(page.query_history())

        self.assertTrue(states["search_disabled"])
        self.assertTrue(states["export_disabled"])
        self.assertTrue(states["print_disabled"])
        self.assertTrue(states["repeat_blocked"])
        self.assertTrue(page.history_filter.search_button.isEnabled())
        self.assertTrue(page.detail_table.table.export_button.isEnabled())

    def test_history_curve_and_detail_table_use_same_service_result(self) -> None:
        service = FakeChartService()
        service.history_result = ServiceResult.ok(Page((_point(1, 10.0), _point(1, 11.5), _point(2, 8.0)), Pagination(1, 100), 3))
        page = self.page(service)
        _set_valid_history_filter(page, "1,2")

        self.assertTrue(page.query_history())

        self.assertEqual(sum(len(series.points) for series in page.history_chart.series()), 3)
        self.assertEqual(len(page.detail_table.export_rows()), 3)
        self.assertEqual(page.detail_table.table.model().rowCount(), 3)
        self.assertEqual(page.detail_table.export_rows()[0]["concentration"], "10")
        table_text = page.detail_table.table.model().data(page.detail_table.table.model().index(0, 2), Qt.ItemDataRole.DisplayRole)
        self.assertEqual(table_text, "<b>探测器 1</b>")

    def test_legend_can_hide_and_show_series(self) -> None:
        service = FakeChartService()
        service.history_result = ServiceResult.ok(Page((_point(1, 10.0), _point(2, 20.0)), Pagination(1, 100), 2))
        page = self.page(service)
        _set_valid_history_filter(page, "1,2")
        self.assertTrue(page.query_history())

        checks = {check.text(): check for check in page.history_chart.findChildren(QCheckBox) if "探测器" in check.text()}
        check = checks["<b>探测器 1</b>"]

        check.setChecked(False)
        self.app.processEvents()
        self.assertNotIn(1, page.history_chart.visible_series_ids())

        check.setChecked(True)
        self.app.processEvents()
        self.assertIn(1, page.history_chart.visible_series_ids())

    def test_service_error_is_controlled_and_redacted(self) -> None:
        service = FakeChartService()
        service.history_result = ServiceResult.fail(
            int(ErrorCode.INTERNAL_ERROR),
            'Traceback File "C:\\secret\\app.sqlite", line 8 password=abc SELECT * FROM running_records',
        )
        page = self.page(service)
        _set_valid_history_filter(page)

        self.assertFalse(page.query_history())

        text = page.error_banner.label.text()
        self.assertIn("历史曲线查询失败", text)
        self.assertNotIn("C:\\secret", text)
        self.assertNotIn("abc", text)
        self.assertNotIn("SELECT", text)

    def test_export_and_print_reuse_current_filters_and_rows(self) -> None:
        service = FakeChartService()
        export = FakeExportService()
        service.history_result = ServiceResult.ok(Page((_point(1, 10.0), _point(1, 12.0)), Pagination(1, 100), 2))
        page = self.page(service, export)
        _set_valid_history_filter(page, "1")
        page.port_edit.setText("COM1")
        page.controller_edit.setText("1")
        self.assertTrue(page.query_history())

        self.assertTrue(page.export_current())
        self.assertTrue(page.print_current())

        self.assertEqual([item[1] for item in export.calls], ["xlsx", "print"])
        self.assertEqual(export.calls[0][0], page.detail_table.export_rows())
        self.assertEqual(page.last_export_filters["detector_ids"], (1,))
        self.assertEqual(page.last_export_filters["port_id"], "COM1")
        self.assertEqual(page.last_print_filters["controller_id"], "1")


def _set_valid_history_filter(page: ChartPage, detector_text: str = "1") -> None:
    now = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
    page.start_time_edit.setDateTime(QDateTime(now - timedelta(hours=1)))
    page.end_time_edit.setDateTime(QDateTime(now))
    page.history_detector_edit.setText(detector_text)


def _point(detector_id: int, concentration: float) -> SimpleNamespace:
    return SimpleNamespace(
        id=detector_id,
        detector_id=detector_id,
        recorded_at=f"2026-01-01T0{detector_id}:00:00+00:00",
        status="normal",
        concentration=concentration,
        gas_type="甲烷",
        unit="%LEL",
        position_code=f"A-{detector_id:03d}",
        detector_name=f"<b>探测器 {detector_id}</b>",
    )


if __name__ == "__main__":
    unittest.main()
