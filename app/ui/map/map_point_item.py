from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtWidgets import QFrame, QVBoxLayout, QWidget

from app.services.models import DeviceStatus
from app.ui.common.safe_text import SafeTextLabel
from app.ui.common.status import AlarmPulseController, device_status_visual, repolish
from app.ui.map.view_models import MapPointDisplay

POINT_SIZE = 44
DRAG_THRESHOLD_PX = 3


class MapPointItem(QFrame):
    clicked = Signal(int)
    dragStarted = Signal(int)
    dragMoved = Signal(int, object)
    dragFinished = Signal(int, object)

    def __init__(
        self,
        point: MapPointDisplay,
        pulse_controller: AlarmPulseController,
        parent: QWidget | None = None,
        *,
        draggable: bool = True,
    ) -> None:
        super().__init__(parent)
        self._point = point
        self._pulse_controller = pulse_controller
        self._draggable = draggable
        self._dragging = False
        self._press_pos: QPoint | None = None
        self._press_top_left: QPoint | None = None
        self.setFixedSize(POINT_SIZE, POINT_SIZE)
        self.setProperty("role", "mapPoint")
        self.setCursor(self.cursor())
        self.name_label = SafeTextLabel(selectable=False, max_chars=40)
        self.name_label.setProperty("role", "mapPointText")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addStretch(1)
        layout.addWidget(self.name_label)
        layout.addStretch(1)
        self.update_point(point)

    @property
    def point(self) -> MapPointDisplay:
        return self._point

    def set_draggable(self, draggable: bool) -> None:
        self._draggable = bool(draggable)
        self.setProperty("readonly", "false" if self._draggable else "true")
        repolish(self)

    def update_point(self, point: MapPointDisplay) -> None:
        self._point = point
        label = point.label or point.detector_position_code or str(point.detector_id)
        self.name_label.set_safe_text(label)
        visual = device_status_visual(point.status)
        self.setToolTip(point.display_name)
        self.setProperty("pointStatus", visual.property_value)
        self.setProperty("offline", "true" if point.status == DeviceStatus.OFFLINE.value else "false")
        if point.pulse_eligible:
            self._pulse_controller.start_for_status(self, point.status, recovered=False)
        else:
            self._pulse_controller.stop(self)
        repolish(self)

    def set_center(self, center: QPoint) -> None:
        self.move(int(center.x() - self.width() / 2), int(center.y() - self.height() / 2))

    def center(self) -> QPoint:
        return QPoint(self.x() + self.width() // 2, self.y() + self.height() // 2)

    def mousePressEvent(self, event) -> None:  # noqa: N802 ANN001
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.pos()
            self._press_top_left = self.pos()
            self._dragging = False
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 ANN001
        if self._press_pos is None or self._press_top_left is None or not self._draggable:
            super().mouseMoveEvent(event)
            return
        delta = event.pos() - self._press_pos
        if not self._dragging and (abs(delta.x()) > DRAG_THRESHOLD_PX or abs(delta.y()) > DRAG_THRESHOLD_PX):
            self._dragging = True
            self.dragStarted.emit(self._point.point_id)
        if self._dragging:
            new_top_left = self._press_top_left + delta
            new_center = QPoint(new_top_left.x() + self.width() // 2, new_top_left.y() + self.height() // 2)
            self.dragMoved.emit(self._point.point_id, new_center)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 ANN001
        if event.button() == Qt.MouseButton.LeftButton and self._press_pos is not None:
            if self._dragging:
                self.dragFinished.emit(self._point.point_id, self.center())
            else:
                self.clicked.emit(self._point.point_id)
            self._press_pos = None
            self._press_top_left = None
            self._dragging = False
            event.accept()
            return
        super().mouseReleaseEvent(event)
