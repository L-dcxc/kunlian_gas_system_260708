from __future__ import annotations

# ruff: noqa: E402

import os
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QApplication

from app.services.errors import ErrorCode
from app.services.models import DeviceStatus, ServiceResult
from app.ui.map import MapMonitoringPage, MapMonitoringViewModel
from app.ui.theme import AppTheme


class FakeMapFacade:
    def __init__(self) -> None:
        self.maps = [_map(1, "一层平面图"), _map(2, "二层平面图")]
        self.runtime_by_map = {
            1: [_point(11, 1, 101, 0.2, 0.3, DeviceStatus.NORMAL)],
            2: [_point(22, 2, 202, 0.5, 0.6, DeviceStatus.ALARM_HIGH, active_alarm=True)],
        }
        self.upload_calls: list[object] = []
        self.delete_calls: list[int] = []
        self.saved_positions: list[tuple[int, float, float]] = []
        self.runtime_fail_message: str | None = None
        self.upload_fail_message: str | None = None
        self.save_fail_message: str | None = None
        self.delete_fail_message: str | None = None

    def list_maps(self) -> tuple[object, ...]:
        return tuple(self.maps)

    def get_map_runtime_view(self, map_id: int) -> ServiceResult[object]:
        if self.runtime_fail_message is not None:
            return ServiceResult.fail(int(ErrorCode.INTERNAL_ERROR), self.runtime_fail_message)
        selected = next((item for item in self.maps if item.id == map_id), None)
        if selected is None:
            return ServiceResult.fail(int(ErrorCode.NOT_FOUND), "地图不存在")
        return ServiceResult.ok(SimpleNamespace(map=selected, points=tuple(self.runtime_by_map.get(map_id, ()))))

    def upload_map(self, session: object, command: object) -> ServiceResult[object]:
        self.upload_calls.append(command)
        if self.upload_fail_message is not None:
            return ServiceResult.fail(int(ErrorCode.VALIDATION_ERROR), self.upload_fail_message)
        new_id = 30 + len(self.upload_calls)
        created = _map(new_id, "新地图")
        self.maps.append(created)
        self.runtime_by_map[new_id] = []
        return ServiceResult.ok(created)

    def delete_map(self, session: object, map_id: int) -> ServiceResult[None]:
        self.delete_calls.append(map_id)
        if self.delete_fail_message is not None:
            return ServiceResult.fail(int(ErrorCode.INTERNAL_ERROR), self.delete_fail_message)
        self.maps = [item for item in self.maps if item.id != map_id]
        self.runtime_by_map.pop(map_id, None)
        return ServiceResult.ok(None)

    def save_point(self, session: object, command: object) -> ServiceResult[object]:
        if self.save_fail_message is not None:
            return ServiceResult.fail(int(ErrorCode.INTERNAL_ERROR), self.save_fail_message)
        map_id = int(getattr(command, "map_id"))
        detector_id = int(getattr(command, "detector_id"))
        x_ratio = float(getattr(command, "x_ratio"))
        y_ratio = float(getattr(command, "y_ratio"))
        for index, point in enumerate(self.runtime_by_map.get(map_id, [])):
            if int(point.detector_id) == detector_id:
                updated = _copy_point(point, x_ratio=x_ratio, y_ratio=y_ratio)
                self.runtime_by_map[map_id][index] = updated
                self.saved_positions.append((point.id, x_ratio, y_ratio))
                return ServiceResult.ok(updated)
        return ServiceResult.fail(int(ErrorCode.NOT_FOUND), "点位不存在")


class UiMapMonitoringTests(unittest.TestCase):
    app: QApplication

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        AppTheme().apply_to(cls.app)

    def tearDown(self) -> None:
        self.app.processEvents()

    def page(self, service: FakeMapFacade | None = None, **kwargs: object) -> MapMonitoringPage:
        facade = service or FakeMapFacade()
        view_model = MapMonitoringViewModel(facade, auto_subscribe=False)
        page = MapMonitoringPage(view_model, can_configure=True, auto_load=True, **kwargs)
        page.resize(980, 620)
        page.show()
        self.app.processEvents()
        return page

    def test_page_initializes_map_list_canvas_and_detail(self) -> None:
        page = self.page()

        self.assertEqual(page.current_state(), "ready")
        self.assertEqual(page.map_list.count(), 2)
        self.assertEqual(page.selected_map_id(), 1)
        self.assertIsNotNone(page.canvas.item_for_point(11))
        self.assertEqual(page.detail_title.text(), "未选择点位")
        self.assertEqual(page.delete_button.isEnabled(), True)

    def test_without_permission_disables_upload_delete_and_save(self) -> None:
        service = FakeMapFacade()
        page = MapMonitoringPage(
            MapMonitoringViewModel(service, auto_subscribe=False),
            can_configure=False,
            upload_path_provider=lambda: Path("C:/unsafe/map.png"),
            auto_load=True,
        )

        self.assertFalse(page.upload_button.isEnabled())
        self.assertFalse(page.delete_button.isEnabled())
        self.assertFalse(page.save_button.isEnabled())
        self.assertFalse(page.cancel_button.isEnabled())
        self.assertFalse(page.permission_hint.isHidden())

        self.assertFalse(page.upload_map())

        self.assertEqual(service.upload_calls, [])
        self.assertIn("无权限", page.error_banner.label.text())

    def test_map_switch_updates_runtime_and_alarm_detail(self) -> None:
        page = self.page()

        page.map_list.setCurrentRow(1)
        self.app.processEvents()
        page.select_point(22)

        self.assertEqual(page.selected_map_id(), 2)
        self.assertEqual(page.selected_point_id(), 22)
        self.assertEqual(page.detail_title.text(), "点位 22")
        self.assertIn("60", page.detail_fields["value"].text())
        self.assertEqual(page.canvas.item_for_point(22).property("alarm"), "high")

    def test_ratio_drag_save_and_cancel_keep_coordinates_as_ratios(self) -> None:
        service = FakeMapFacade()
        page = self.page(service)
        point_item = page.canvas.item_for_point(11)
        self.assertIsNotNone(point_item)
        rect = page.canvas.image_rect()
        new_center = QPoint(rect.left() + round(rect.width() * 0.7), rect.top() + round(rect.height() * 0.8))

        point_item.dragMoved.emit(11, new_center)
        self.app.processEvents()

        pending = page.canvas.pending_ratios()[11]
        self.assertAlmostEqual(pending[0], 0.7, delta=0.02)
        self.assertAlmostEqual(pending[1], 0.8, delta=0.02)
        self.assertTrue(page.save_button.isEnabled())
        self.assertIn("未保存", page.detail_fields["coords"].text())

        self.assertTrue(page.save_pending_positions())

        self.assertEqual(service.saved_positions[-1][0], 11)
        self.assertAlmostEqual(service.saved_positions[-1][1], 0.7, delta=0.02)
        self.assertAlmostEqual(service.saved_positions[-1][2], 0.8, delta=0.02)
        self.assertFalse(page.save_button.isEnabled())

        moved_again = QPoint(rect.left() + round(rect.width() * 0.1), rect.top() + round(rect.height() * 0.2))
        page.canvas.item_for_point(11).dragMoved.emit(11, moved_again)
        self.assertIn(11, page.canvas.pending_ratios())
        page.cancel_pending_positions()

        self.assertEqual(page.canvas.pending_ratios(), {})
        self.assertEqual(len(service.saved_positions), 1)

    def test_alarm_recovery_clears_point_pulse_and_alarm_list(self) -> None:
        service = FakeMapFacade()
        page = self.page(service)
        page.map_list.setCurrentRow(1)
        self.app.processEvents()
        alarm_item = page.canvas.item_for_point(22)
        self.assertEqual(alarm_item.property("alarm"), "high")

        service.runtime_by_map[2][0] = _point(22, 2, 202, 0.5, 0.6, DeviceStatus.NORMAL, active_alarm=False)
        page.view_model.refresh_current()
        self.app.processEvents()

        recovered = page.canvas.item_for_point(22)
        self.assertEqual(recovered.property("pointStatus"), "normal")
        self.assertIsNone(recovered.property("alarm"))
        self.assertIsNone(recovered.property("alarmPulse"))
        self.assertIn("当前无未恢复警情", page.alarm_body.itemAt(0).widget().text())

    def test_service_errors_are_controlled_and_do_not_leak_sensitive_details(self) -> None:
        service = FakeMapFacade()
        service.runtime_fail_message = 'Traceback File "C:\\secret\\app.db", line 1 password=abc SELECT * FROM maps'
        page = self.page(service)

        self.assertEqual(page.current_state(), "error")
        text = page.error_banner.label.text()
        self.assertIn("地图加载失败", text)
        self.assertNotIn("C:\\secret", text)
        self.assertNotIn("abc", text)
        self.assertNotIn("SELECT", text)

        service.runtime_fail_message = None
        service.delete_fail_message = 'sqlite failure File "D:\\db\\gas.sqlite", line 8 token=abc'
        page.view_model.load()
        self.assertFalse(page.delete_selected_map(confirm=False))
        text = page.error_banner.label.text()
        self.assertIn("地图删除失败", text)
        self.assertNotIn("D:\\db", text)
        self.assertNotIn("abc", text)

    def test_upload_uses_view_model_and_validation_error_is_generic(self) -> None:
        service = FakeMapFacade()
        service.upload_fail_message = 'File "C:\\secret\\bad.png", line 1 token=abc'
        page = self.page(service, upload_path_provider=lambda: Path("C:/secret/bad.png"))

        self.assertFalse(page.upload_map())

        self.assertEqual(len(service.upload_calls), 1)
        self.assertIn("图片格式或大小不符合要求", page.upload_result_label.text())
        self.assertNotIn("C:\\secret", page.upload_result_label.text())
        self.assertNotIn("abc", page.upload_result_label.text())


def _map(map_id: int, name: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=map_id,
        name=name,
        original_file_name=f"{name}.png",
        safe_file_name=f"map_{map_id}.png",
        relative_path=f"maps/map_{map_id}.png",
        is_enabled=True,
    )


def _point(
    point_id: int,
    map_id: int,
    detector_id: int,
    x_ratio: float,
    y_ratio: float,
    status: DeviceStatus,
    *,
    active_alarm: bool = False,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=point_id,
        map_id=map_id,
        detector_id=detector_id,
        x_ratio=x_ratio,
        y_ratio=y_ratio,
        label=f"点位 {point_id}",
        detector_position_code=f"M-{detector_id}",
        detector_name=f"探测器 {detector_id}",
        controller_name="控制器 A",
        status=status.value,
        concentration=None if status is DeviceStatus.OFFLINE else 60.0 if status is DeviceStatus.ALARM_HIGH else 12.5,
        gas_type="甲烷",
        unit="%LEL",
        alarm_level=2 if status is DeviceStatus.ALARM_HIGH else None,
        timestamp="2026-01-01T00:00:00+00:00",
        active_alarm=active_alarm,
        active_alarm_type=status.value if active_alarm else "",
    )


def _copy_point(point: SimpleNamespace, **changes: object) -> SimpleNamespace:
    values = dict(vars(point))
    values.update(changes)
    return SimpleNamespace(**values)


if __name__ == "__main__":
    unittest.main()
