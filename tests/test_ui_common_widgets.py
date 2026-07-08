from __future__ import annotations

# ruff: noqa: E402

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLineEdit

from app.ui.common.data_table import DataTable, TableColumn, TableState
from app.ui.common.dialogs import ConfirmDangerDialog, RiskConfirmDialog
from app.ui.common.filter_panel import FilterPanel
from app.ui.common.hex_viewer import HEX_VIEWER_MAX_CHARS, HexViewer
from app.ui.common.metric_card import MetricCard
from app.ui.common.permission_hint import PermissionHint
from app.ui.theme import AppTheme


class UiCommonWidgetTests(unittest.TestCase):
    app: QApplication

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        AppTheme().apply_to(cls.app)

    def test_filter_panel_validation_error_and_collapse(self) -> None:
        panel = FilterPanel("记录筛选")
        editor = panel.add_field("keyword", "关键字", QLineEdit())

        panel.set_validation_error("keyword", "字段不能为空")
        self.assertEqual(editor.property("validation"), "error")
        self.assertIn("字段不能为空", panel._fields["keyword"].hint.text())
        self.assertFalse(panel._fields["keyword"].hint.isHidden())

        panel.clear_validation_errors()
        self.assertIsNone(editor.property("validation"))
        self.assertFalse(panel._fields["keyword"].hint.isVisible())

        panel.set_collapsed(True)
        self.assertTrue(panel.is_collapsed())
        self.assertFalse(panel.field("keyword").isVisible())

    def test_data_table_states_pagination_and_plain_text_model(self) -> None:
        table = DataTable([TableColumn("name", "名称"), TableColumn("value", "值")])
        table.set_rows([{"name": "<b>探测器</b>", "value": "10 %LEL"}])
        table.set_page(1, total=30, per_page=20)
        table.set_state(TableState.READY)

        index = table.model().index(0, 0)
        self.assertEqual(table.model().data(index, Qt.ItemDataRole.DisplayRole), "<b>探测器</b>")
        self.assertEqual(table.table.editTriggers(), table.table.EditTrigger.NoEditTriggers)
        self.assertFalse(table.table.wordWrap())
        self.assertEqual(table.table.textElideMode(), Qt.TextElideMode.ElideRight)
        self.assertTrue(table.export_button.isEnabled())
        self.assertFalse(table.prev_button.isEnabled())
        self.assertTrue(table.next_button.isEnabled())

        table.set_state(TableState.LOADING, "查询中")
        self.assertEqual(table.state(), TableState.LOADING)
        self.assertFalse(table.export_button.isEnabled())
        self.assertFalse(table.prev_button.isEnabled())
        self.assertFalse(table.next_button.isEnabled())

        table.set_state(TableState.EMPTY, "暂无匹配记录")
        self.assertFalse(table.empty_action_button.isHidden())
        table.set_state(TableState.ERROR, "Traceback File \"C:\\data\\app.db\", line 1")
        self.assertEqual(table._error_message.text(), "操作失败，请稍后重试。")

    def test_danger_dialogs_default_to_cancel(self) -> None:
        dialog = ConfirmDangerDialog("清空记录", "清空后无法恢复", confirm_text="确认清空")
        dialog.show()
        self.app.processEvents()
        self.assertTrue(dialog.cancel_button.isDefault())
        self.assertFalse(dialog.confirm_button.isDefault())
        self.assertIs(dialog.focusWidget(), dialog.cancel_button)

        risk_dialog = RiskConfirmDialog("恢复备份", "将覆盖当前数据", risk_summary="已停止采集")
        self.assertTrue(risk_dialog.cancel_button.isDefault())
        self.assertEqual(risk_dialog.risk_label.text(), "已停止采集")

    def test_permission_hint_redacts_permission_code_and_mentions_logged_event(self) -> None:
        hint = PermissionHint("当前账号无权限 permission_code=SYS_ADMIN_DELETE")
        text = hint.message_label.text()
        self.assertIn("已记录", text)
        self.assertNotIn("SYS_ADMIN_DELETE", text)
        self.assertNotIn("permission_code", text.lower())

        hint.show_denied()
        self.assertIn("已记录", hint.message_label.text())

    def test_metric_card_status_dynamic_properties(self) -> None:
        card = MetricCard("在线设备", 12, unit="台", status="running", subtitle="最近 1 分钟")
        self.assertEqual(card.property("status"), "running")
        self.assertEqual(card.value_label.property("status"), "running")
        self.assertEqual(card.value_label.text(), "12")

        card.set_status("highAlarm")
        self.assertEqual(card.status(), "highAlarm")
        self.assertEqual(card.value_label.property("status"), "highAlarm")
        card.set_metric("<b>15</b>", unit="%LEL", subtitle="A\x00B")
        self.assertEqual(card.value_label.text(), "<b>15</b>")
        self.assertEqual(card.subtitle_label.text(), "A�B")

    def test_hex_viewer_uses_mono_qss_and_truncates_long_text(self) -> None:
        viewer = HexViewer("01 03 AA")
        self.assertEqual(viewer.viewer.property("viewer"), "hex")
        self.assertEqual(viewer.text(), "01 03 AA")
        self.assertFalse(viewer.is_truncated())

        viewer.set_hex_text("A" * (HEX_VIEWER_MAX_CHARS + 10))
        self.assertTrue(viewer.is_truncated())
        self.assertIn("已截断", viewer.text())
        self.assertIn(str(HEX_VIEWER_MAX_CHARS), viewer.truncation_label.text())

    def test_global_qss_contains_common_widget_selectors(self) -> None:
        qss = AppTheme().qss()
        self.assertIn('QFrame[widget="filterPanel"]', qss)
        self.assertIn('QDialog[danger="true"] QPushButton[role="confirm"]', qss)
        self.assertIn('QPlainTextEdit[viewer="hex"]', qss)
        self.assertIn('QFrame[role="metricCard"][status="highAlarm"]', qss)
        self.assertIn("QPushButton:focus { outline: none; }", qss)
        self.assertIn("QTableView::item:focus", qss)
        self.assertIn("QCheckBox:focus { outline: none; }", qss)


if __name__ == "__main__":
    unittest.main()
