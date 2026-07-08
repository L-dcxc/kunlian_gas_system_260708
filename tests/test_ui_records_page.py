from __future__ import annotations

# ruff: noqa: E402

import os
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QDateTime, Qt
from PySide6.QtWidgets import QApplication

from app.services.errors import ErrorCode
from app.services.models import Page, Pagination, ServiceResult
from app.services.record_service import ClearRecordsCommand, ExportRecordsCommand, RecordQuery
from app.ui.records import RecordsPage
from app.ui.records.record_filters import FILTER_WHITELISTS, MAX_RECORDS_PER_PAGE, RecordFilterValues
from app.ui.records.view_models import RecordQueryViewModel
from app.ui.theme import AppTheme


class FakeRecordService:
    def __init__(self) -> None:
        self.query_calls: list[RecordQuery] = []
        self.delete_calls: list[dict[str, object]] = []
        self.clear_calls: list[ClearRecordsCommand] = []
        self.export_calls: list[ExportRecordsCommand] = []
        self.query_result: ServiceResult[Page[dict[str, object]]] | None = None
        self.export_result: ServiceResult[object] | None = None
        self.on_query = None
        self.on_export = None

    def query_records(self, session, query: RecordQuery):  # noqa: ANN001 ANN201
        self.query_calls.append(query)
        if self.on_query is not None:
            self.on_query(query)
        if self.query_result is not None:
            return self.query_result
        return ServiceResult.ok(Page((_row(query.record_type, 1),), Pagination(query.page, query.per_page), 1))

    def delete_record(self, session, *, record_type, record_id, confirmed=False):  # noqa: ANN001 ANN201
        self.delete_calls.append({"record_type": record_type, "record_id": record_id, "confirmed": confirmed})
        return ServiceResult.ok(None)

    def clear_records(self, session, command: ClearRecordsCommand):  # noqa: ANN001 ANN201
        self.clear_calls.append(command)
        return ServiceResult.ok(SimpleNamespace(deleted_count=3))

    def export_records(self, session, command: ExportRecordsCommand):  # noqa: ANN001 ANN201
        self.export_calls.append(command)
        if self.on_export is not None:
            self.on_export(command)
        if self.export_result is not None:
            return self.export_result
        return ServiceResult.ok(SimpleNamespace(filename=f"{command.record_type}.{command.export_format}", format=command.export_format))


class UiRecordsPageTests(unittest.TestCase):
    app: QApplication

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        AppTheme().apply_to(cls.app)

    def tearDown(self) -> None:
        self.app.processEvents()

    def page(self, service: FakeRecordService | None = None, *, can_delete: bool = True, confirm=True) -> RecordsPage:  # noqa: ANN001
        callback = confirm if callable(confirm) else (lambda parent, title, message, confirm_text: bool(confirm))
        page = RecordsPage(service or FakeRecordService(), _session("admin" if can_delete else "operator"), can_delete=can_delete, confirm_danger=callback)
        page.resize(1200, 760)
        page.show()
        self.app.processEvents()
        return page

    def test_three_record_tabs_switch_independently(self) -> None:
        page = self.page()

        self.assertEqual(page.tabs.count(), 3)
        self.assertEqual([page.tabs.tabText(i) for i in range(page.tabs.count())], ["报警记录", "运行记录", "操作记录"])
        self.assertIs(page.panes["alarm"], page.tabs.widget(0))
        page.tabs.setCurrentWidget(page.panes["operation"])
        self.assertEqual(page.current_record_type(), "operation")
        self.assertIsNot(page.panes["operation"].filter_widget, page.panes["alarm"].filter_widget)

    def test_filter_fields_are_whitelisted_per_record_type(self) -> None:
        service = FakeRecordService()
        page = self.page(service)
        self.assertTrue(page.panes["alarm"].filter_widget.has_field("detector_id"))
        self.assertFalse(page.panes["operation"].filter_widget.has_field("detector_id"))
        self.assertIn("keyword", FILTER_WHITELISTS["operation"])
        self.assertNotIn("keyword", FILTER_WHITELISTS["alarm"])

        vm = RecordQueryViewModel(service, _session("admin"), "operation")
        query = vm.build_query(RecordFilterValues("operation", {"keyword": "<b>查找</b>", "detector_id": 9}, page=1, per_page=20))
        self.assertEqual(query.filters, {"keyword": "<b>查找</b>"})

    def test_time_range_validation_and_per_page_limit(self) -> None:
        page = self.page()
        pane = page.panes["running"]
        now = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
        pane.filter_widget.start_time_edit.setDateTime(QDateTime(now))
        pane.filter_widget.end_time_edit.setDateTime(QDateTime(now - timedelta(hours=1)))
        pane.filter_widget.per_page_spin.setValue(999)

        self.assertFalse(pane.query_records())

        self.assertEqual(pane.filter_widget.end_time_edit.property("validation"), "error")
        self.assertIn("结束时间不能早于开始时间", pane.filter_widget.panel._fields["end_time"].hint.text())
        self.assertEqual(pane.filter_widget.per_page(), MAX_RECORDS_PER_PAGE)

    def test_pagination_query_uses_current_record_type_filters_and_per_page(self) -> None:
        service = FakeRecordService()
        service.query_result = ServiceResult.ok(Page((_row("running", 1), _row("running", 2)), Pagination(2, 5), 12))
        page = self.page(service)
        pane = page.panes["running"]
        _set_valid_range(pane)
        pane.filter_widget.per_page_spin.setValue(5)
        pane.filter_widget._editors["position_code"].setText("A-001")

        self.assertTrue(pane.query_records(page=2))

        self.assertEqual(service.query_calls[-1].record_type, "running")
        self.assertEqual(service.query_calls[-1].filters["position_code"], "A-001")
        self.assertEqual(service.query_calls[-1].page, 2)
        self.assertEqual(service.query_calls[-1].per_page, 5)
        self.assertEqual(pane.table.model().rowCount(), 2)
        self.assertEqual(pane.table.page_label.text(), "第 2 页 / 共 3 页，共 12 条")

    def test_query_failure_is_controlled_and_redacted(self) -> None:
        service = FakeRecordService()
        service.query_result = ServiceResult.fail(
            int(ErrorCode.INTERNAL_ERROR),
            'Traceback File "C:\\secret\\app.sqlite", line 8 password=abc SELECT * FROM operation_logs',
        )
        page = self.page(service)
        pane = page.panes["operation"]
        _set_valid_range(pane)

        self.assertFalse(pane.query_records())

        text = pane.table._error_message.text()
        self.assertIn("记录查询失败", text)
        self.assertNotIn("C:\\secret", text)
        self.assertNotIn("password", text.lower())
        self.assertNotIn("SELECT", text)

    def test_record_text_is_rendered_as_plain_text(self) -> None:
        service = FakeRecordService()
        service.query_result = ServiceResult.ok(Page((_row("alarm", 1, detector_name="<b>探测器</b>", position_code="=A1"),), Pagination(1, 20), 1))
        page = self.page(service)
        pane = page.panes["alarm"]
        _set_valid_range(pane)
        self.assertTrue(pane.query_records())

        model = pane.table.model()
        detector_col = _column_index(model, "探测器")
        position_col = _column_index(model, "位置编号")
        self.assertEqual(model.data(model.index(0, detector_col), Qt.ItemDataRole.DisplayRole), "<b>探测器</b>")
        self.assertEqual(model.data(model.index(0, position_col), Qt.ItemDataRole.DisplayRole), "=A1")

    def test_operator_delete_and_clear_are_restricted(self) -> None:
        service = FakeRecordService()
        page = self.page(service, can_delete=False)
        pane = page.panes["alarm"]
        _set_valid_range(pane)
        self.assertTrue(pane.query_records())
        pane.table.table.selectRow(0)
        self.app.processEvents()

        self.assertFalse(pane.actions.delete_button.isEnabled())
        self.assertFalse(pane.actions.clear_button.isEnabled())
        self.assertFalse(pane.actions.delete_selected())
        self.assertIn("当前账号无权限", pane.actions.last_message)
        self.assertEqual(service.delete_calls, [])
        self.assertEqual(service.clear_calls, [])

    def test_admin_delete_and_clear_require_confirmation_and_call_service_confirmed(self) -> None:
        service = FakeRecordService()
        confirms: list[tuple[str, str]] = []

        def confirm(parent, title, message, confirm_text):  # noqa: ANN001 ANN202
            confirms.append((title, confirm_text))
            return True

        page = self.page(service, can_delete=True, confirm=confirm)
        pane = page.panes["running"]
        _set_valid_range(pane)
        pane.filter_widget._editors["position_code"].setText("A-001")
        self.assertTrue(pane.query_records())
        pane.table.table.selectRow(0)
        self.app.processEvents()

        self.assertTrue(pane.actions.delete_selected())
        self.assertTrue(pane.actions.clear_current())

        self.assertEqual(service.delete_calls[-1]["confirmed"], True)
        self.assertEqual(service.delete_calls[-1]["record_id"], 1)
        self.assertTrue(service.clear_calls[-1].confirmed)
        self.assertEqual(service.clear_calls[-1].filters["position_code"], "A-001")
        self.assertEqual([item[1] for item in confirms], ["确认删除", "确认清空"])

    def test_export_pdf_and_print_reuse_current_filters_and_disable_repeat(self) -> None:
        service = FakeRecordService()
        page = self.page(service)
        pane = page.panes["operation"]
        _set_valid_range(pane)
        pane.filter_widget._editors["username"].setText("<admin>")
        self.assertTrue(pane.query_records())
        states: dict[str, bool] = {}

        def inspect_during_export(command):  # noqa: ANN001 ANN202
            states["export_disabled"] = not pane.actions.export_button.isEnabled()
            states["print_disabled"] = not pane.actions.print_button.isEnabled()
            states["repeat_blocked"] = not pane.actions.export_current("xlsx")

        service.on_export = inspect_during_export

        self.assertTrue(pane.actions.export_current("xlsx"))
        service.on_export = None
        self.assertTrue(pane.actions.export_current("pdf"))
        self.assertTrue(pane.actions.export_current("print"))

        self.assertTrue(states["export_disabled"])
        self.assertTrue(states["print_disabled"])
        self.assertTrue(states["repeat_blocked"])
        self.assertEqual([call.export_format for call in service.export_calls], ["xlsx", "pdf", "print"])
        self.assertEqual(service.export_calls[0].record_type, "operation")
        self.assertEqual(service.export_calls[0].filters["username"], "<admin>")
        self.assertIn("operation.pdf", pane.actions.last_export_payload.filename)
        self.assertIn("operation.print", pane.actions.last_print_payload.filename)


def _session(role: str) -> SimpleNamespace:
    return SimpleNamespace(role=role, username=role)


def _set_valid_range(pane) -> None:  # noqa: ANN001 ANN202
    now = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
    pane.filter_widget.start_time_edit.setDateTime(QDateTime(now - timedelta(hours=1)))
    pane.filter_widget.end_time_edit.setDateTime(QDateTime(now))


def _row(record_type: str, row_id: int, **overrides: object) -> dict[str, object]:
    base = {
        "id": row_id,
        "start_time": "2026-01-01T09:00:00+00:00",
        "end_time": "",
        "recorded_at": "2026-01-01T09:30:00+00:00",
        "created_at": "2026-01-01T09:40:00+00:00",
        "position_code": "A-001",
        "detector_name": "探测器 1",
        "controller_name": "控制器 1",
        "alarm_type": "alarm_low",
        "alarm_level": 1,
        "trigger_value": 25.0,
        "status": "active" if record_type == "alarm" else "normal",
        "concentration": 10.0,
        "gas_type": "甲烷",
        "unit": "%LEL",
        "actor_name": "admin",
        "action_type": "records.test",
        "result": "success",
        "target_type": "record",
        "target_id": str(row_id),
        "summary": "<unsafe log content>",
    }
    base.update(overrides)
    return base


def _column_index(model, title: str) -> int:  # noqa: ANN001
    for index in range(model.columnCount()):
        if model.headerData(index, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole) == title:
            return index
    raise AssertionError(f"missing column {title}")


if __name__ == "__main__":
    unittest.main()
