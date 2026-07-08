from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget

from app.ui.common.safe_text import SafeTextLabel
from app.ui.common.status import AlarmPulseController
from app.ui.map.map_point_item import MapPointItem
from app.ui.map.view_models import MapPointDisplay, MapRuntimeDisplay


class MapCanvas(QFrame):
    pointClicked = Signal(int)
    pointMoved = Signal(int, float, float)
    dirtyChanged = Signal(bool)

    def __init__(self, parent: QWidget | None = None, *, editable: bool = True) -> None:
        super().__init__(parent)
        self.setObjectName("MapCanvas")
        self.setProperty("panel", "true")
        self.setMinimumSize(420, 320)
        self._runtime: MapRuntimeDisplay | None = None
        self._pixmap = QPixmap()
        self._image_rect = QRect()
        self._items: dict[int, MapPointItem] = {}
        self._original_ratios: dict[int, tuple[float, float]] = {}
        self._pending_ratios: dict[int, tuple[float, float]] = {}
        self._editable = editable
        self._pulse_controller = AlarmPulseController(self)

        self.image_label = QLabel(self)
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setScaledContents(True)
        self.message_label = SafeTextLabel("暂无地图，请上传厂区平面图", self, selectable=False)
        self.message_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.message_label.setProperty("role", "muted")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.message_label, 1)

    def set_editable(self, editable: bool) -> None:
        self._editable = bool(editable)
        for item in self._items.values():
            item.set_draggable(self._editable)

    def set_loading(self) -> None:
        self.clear_runtime("地图加载中")

    def clear_runtime(self, message: str = "暂无地图，请上传厂区平面图") -> None:
        self._runtime = None
        self._pixmap = QPixmap()
        self._pending_ratios.clear()
        self._original_ratios.clear()
        self._clear_items()
        self.image_label.clear()
        self.image_label.hide()
        self.message_label.set_safe_text(message)
        self.message_label.show()
        self.dirtyChanged.emit(False)

    def set_runtime(self, runtime: MapRuntimeDisplay, image_path: Path | None = None) -> None:
        self._runtime = runtime
        self._pending_ratios.clear()
        self._original_ratios = {point.point_id: (point.x_ratio, point.y_ratio) for point in runtime.points}
        self._pixmap = QPixmap(str(image_path)) if image_path is not None else QPixmap()
        if self._pixmap.isNull():
            self.image_label.clear()
            self.message_label.set_safe_text("地图图片不可用，仅显示点位比例位置")
            self.message_label.show()
        else:
            self.message_label.hide()
            self.image_label.setPixmap(self._pixmap)
            self.image_label.show()
        self._sync_items(runtime.points)
        self._layout_scene()
        self.dirtyChanged.emit(False)

    def image_rect(self) -> QRect:
        return QRect(self._image_rect)

    def item_for_point(self, point_id: int) -> MapPointItem | None:
        return self._items.get(point_id)

    def pending_ratios(self) -> dict[int, tuple[float, float]]:
        return dict(self._pending_ratios)

    def ratio_from_position(self, center: QPoint) -> tuple[float, float]:
        rect = self._image_rect
        if rect.width() <= 0 or rect.height() <= 0:
            return 0.0, 0.0
        # Ratio coordinates are derived from the displayed image area, not the
        # viewport, so resizing/fullscreen keeps points anchored to the same map spot.
        x_ratio = (center.x() - rect.left()) / rect.width()
        y_ratio = (center.y() - rect.top()) / rect.height()
        return _clamp_ratio(x_ratio), _clamp_ratio(y_ratio)

    def cancel_pending(self) -> None:
        # Cancel restores the service-provided ratios and drops unsaved drag output.
        self._pending_ratios.clear()
        self._layout_scene()
        self.dirtyChanged.emit(False)

    def resizeEvent(self, event) -> None:  # noqa: N802 ANN001
        super().resizeEvent(event)
        self._layout_scene()

    def _sync_items(self, points: tuple[MapPointDisplay, ...]) -> None:
        active_ids = {point.point_id for point in points}
        for point_id in tuple(self._items):
            if point_id not in active_ids:
                self._items[point_id].deleteLater()
                del self._items[point_id]
        for point in points:
            item = self._items.get(point.point_id)
            if item is None:
                item = MapPointItem(point, self._pulse_controller, self, draggable=self._editable)
                item.clicked.connect(self.pointClicked)
                item.dragMoved.connect(self._handle_drag_move)
                item.dragFinished.connect(self._handle_drag_finish)
                self._items[point.point_id] = item
            else:
                item.update_point(point)
                item.set_draggable(self._editable)
            item.show()
            item.raise_()

    def _handle_drag_move(self, point_id: int, center: object) -> None:
        if not isinstance(center, QPoint):
            return
        x_ratio, y_ratio = self.ratio_from_position(center)
        self._pending_ratios[int(point_id)] = (x_ratio, y_ratio)
        self._place_item(point_id, x_ratio, y_ratio)
        self.pointMoved.emit(int(point_id), x_ratio, y_ratio)
        self.dirtyChanged.emit(True)

    def _handle_drag_finish(self, point_id: int, center: object) -> None:
        self._handle_drag_move(point_id, center)

    def _layout_scene(self) -> None:
        self._image_rect = self._calculate_image_rect()
        self.image_label.setGeometry(self._image_rect)
        for point in self._runtime.points if self._runtime is not None else ():
            x_ratio, y_ratio = self._pending_ratios.get(point.point_id, (point.x_ratio, point.y_ratio))
            self._place_item(point.point_id, x_ratio, y_ratio)

    def _place_item(self, point_id: int, x_ratio: float, y_ratio: float) -> None:
        item = self._items.get(point_id)
        if item is None:
            return
        rect = self._image_rect
        x = rect.left() + _clamp_ratio(x_ratio) * rect.width()
        y = rect.top() + _clamp_ratio(y_ratio) * rect.height()
        item.set_center(QPoint(round(x), round(y)))
        item.raise_()

    def _calculate_image_rect(self) -> QRect:
        area = self.contentsRect().adjusted(12, 12, -12, -12)
        if area.width() <= 0 or area.height() <= 0:
            return QRect()
        if self._pixmap.isNull():
            return area
        image_size = self._pixmap.size()
        if image_size.width() <= 0 or image_size.height() <= 0:
            return area
        scale = min(area.width() / image_size.width(), area.height() / image_size.height())
        width = max(1, int(image_size.width() * scale))
        height = max(1, int(image_size.height() * scale))
        left = area.left() + (area.width() - width) // 2
        top = area.top() + (area.height() - height) // 2
        return QRect(left, top, width, height)

    def _clear_items(self) -> None:
        self._pulse_controller.stop_all()
        for item in self._items.values():
            item.deleteLater()
        self._items.clear()


def _clamp_ratio(value: float) -> float:
    return min(1.0, max(0.0, float(value)))
