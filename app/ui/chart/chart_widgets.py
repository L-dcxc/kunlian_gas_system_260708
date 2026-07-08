from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Sequence

from PySide6.QtCore import QPoint, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPen, QWheelEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.ui.common.errors import controlled_error_text
from app.ui.common.safe_text import SafeTextLabel, normalize_plain_text

EMPTY_CHART_TEXT = "当前筛选条件下无记录"
_SERIES_COLORS = ("#1D4ED8", "#16A34A", "#EA580C", "#DC2626", "#7C3AED", "#0891B2", "#F59E0B")


@dataclass(frozen=True, slots=True)
class ChartPoint:
    detector_id: int
    timestamp: str
    value: float | None
    unit: str = ""
    status: str = ""
    detector_name: str = ""
    gas_type: str = ""


@dataclass(frozen=True, slots=True)
class ChartSeries:
    series_id: int
    name: str
    points: tuple[ChartPoint, ...]
    unit: str = ""
    gas_type: str = ""


class ChartPlotCanvas(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(260)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._series: tuple[ChartSeries, ...] = ()
        self._visible_ids: set[int] = set()
        self._zoom = 1.0
        self._pan = 0.0
        self._drag_start: QPoint | None = None

    def set_series(self, series: Sequence[ChartSeries], visible_ids: set[int] | None = None) -> None:
        self._series = tuple(series)
        self._visible_ids = set(visible_ids if visible_ids is not None else {item.series_id for item in self._series})
        self.update()

    def visible_ids(self) -> set[int]:
        return set(self._visible_ids)

    def set_visible(self, series_id: int, visible: bool) -> None:
        if visible:
            self._visible_ids.add(series_id)
        else:
            self._visible_ids.discard(series_id)
        self.update()

    def zoom_in(self) -> None:
        self._zoom = min(8.0, self._zoom * 1.25)
        self.update()

    def zoom_out(self) -> None:
        self._zoom = max(1.0, self._zoom / 1.25)
        self.update()

    def pan_left(self) -> None:
        self._pan = max(0.0, self._pan - 0.08)
        self.update()

    def pan_right(self) -> None:
        self._pan = min(1.0, self._pan + 0.08)
        self.update()

    def reset_view(self) -> None:
        self._zoom = 1.0
        self._pan = 0.0
        self.update()

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        if event.angleDelta().y() > 0:
            self.zoom_in()
        else:
            self.zoom_out()
        event.accept()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = event.position().toPoint()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._drag_start is not None and self.width() > 0:
            delta = event.position().toPoint().x() - self._drag_start.x()
            self._pan = min(1.0, max(0.0, self._pan - delta / max(self.width(), 1)))
            self._drag_start = event.position().toPoint()
            self.update()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self._drag_start = None
        super().mouseReleaseEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802 ANN001
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(44, 18, -18, -34)
        if rect.width() <= 4 or rect.height() <= 4:
            return
        painter.fillRect(self.rect(), QColor("#FFFFFF"))
        painter.setPen(QPen(QColor("#CBD5E1"), 1))
        painter.drawRect(rect)
        values = self._visible_points()
        if not values:
            painter.setPen(QColor("#6B7280"))
            painter.drawText(QRectF(rect), Qt.AlignmentFlag.AlignCenter, EMPTY_CHART_TEXT)
            return
        y_values = [point.value for _, point in values if point.value is not None and isfinite(point.value)]
        if not y_values:
            painter.setPen(QColor("#6B7280"))
            painter.drawText(QRectF(rect), Qt.AlignmentFlag.AlignCenter, EMPTY_CHART_TEXT)
            return
        min_y = min(y_values)
        max_y = max(y_values)
        if min_y == max_y:
            min_y -= 1.0
            max_y += 1.0
        painter.setPen(QColor("#475569"))
        painter.drawText(4, rect.top() + 8, f"{max_y:.2f}")
        painter.drawText(4, rect.bottom(), f"{min_y:.2f}")
        for index, series in enumerate(self._series):
            if series.series_id not in self._visible_ids:
                continue
            usable = [point for point in series.points if point.value is not None and isfinite(point.value)]
            if not usable:
                continue
            color = QColor(_SERIES_COLORS[index % len(_SERIES_COLORS)])
            painter.setPen(QPen(color, 2))
            visible_points = self._window_points(usable)
            last = None
            for point_index, point in enumerate(visible_points):
                x_ratio = 0.0 if len(visible_points) == 1 else point_index / (len(visible_points) - 1)
                y_ratio = (float(point.value) - min_y) / (max_y - min_y)
                pos = QPoint(rect.left() + round(rect.width() * x_ratio), rect.bottom() - round(rect.height() * y_ratio))
                if last is not None:
                    painter.drawLine(last, pos)
                painter.drawEllipse(pos, 3, 3)
                last = pos

    def _visible_points(self) -> list[tuple[ChartSeries, ChartPoint]]:
        points: list[tuple[ChartSeries, ChartPoint]] = []
        for series in self._series:
            if series.series_id in self._visible_ids:
                points.extend((series, point) for point in series.points)
        return points

    def _window_points(self, points: Sequence[ChartPoint]) -> tuple[ChartPoint, ...]:
        if self._zoom <= 1.0 or len(points) <= 2:
            return tuple(points)
        window = max(2, round(len(points) / self._zoom))
        max_start = max(0, len(points) - window)
        start = round(max_start * self._pan)
        return tuple(points[start : start + window])


class ChartCurveWidget(QFrame):
    visibilityChanged = Signal(int, bool)

    def __init__(self, title: str = "曲线", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ChartPanel")
        self.setProperty("panel", "true")
        self._series: tuple[ChartSeries, ...] = ()
        self._legend_checks: dict[int, QCheckBox] = {}

        self.title_label = SafeTextLabel(title, selectable=False)
        self.title_label.setProperty("role", "panelTitle")
        self.current_label = SafeTextLabel("当前值：--", selectable=False)
        self.current_label.setProperty("role", "muted")
        self.error_label = SafeTextLabel("", selectable=True)
        self.error_label.setProperty("role", "errorText")
        self.error_label.hide()
        self.empty_label = SafeTextLabel(EMPTY_CHART_TEXT, selectable=False)
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setProperty("role", "chartEmpty")
        self.canvas = ChartPlotCanvas(self)

        self.zoom_in_button = QPushButton("放大")
        self.zoom_out_button = QPushButton("缩小")
        self.pan_left_button = QPushButton("左移")
        self.pan_right_button = QPushButton("右移")
        self.reset_button = QPushButton("复位")
        self.zoom_in_button.clicked.connect(self.canvas.zoom_in)
        self.zoom_out_button.clicked.connect(self.canvas.zoom_out)
        self.pan_left_button.clicked.connect(self.canvas.pan_left)
        self.pan_right_button.clicked.connect(self.canvas.pan_right)
        self.reset_button.clicked.connect(self.canvas.reset_view)

        self.legend_layout = QHBoxLayout()
        self.legend_layout.setContentsMargins(0, 0, 0, 0)
        self.legend_layout.setSpacing(8)

        toolbar = QHBoxLayout()
        toolbar.addWidget(self.title_label, 1)
        toolbar.addWidget(self.current_label, 1)
        for button in (self.zoom_in_button, self.zoom_out_button, self.pan_left_button, self.pan_right_button, self.reset_button):
            toolbar.addWidget(button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        layout.addLayout(toolbar)
        layout.addWidget(self.error_label)
        layout.addWidget(self.canvas, 1)
        layout.addWidget(self.empty_label)
        layout.addLayout(self.legend_layout)
        self.set_series(())

    def set_series(self, series: Sequence[ChartSeries]) -> None:
        previous_visible = self.canvas.visible_ids()
        self._series = tuple(series)
        visible = previous_visible.intersection({item.series_id for item in self._series}) or {item.series_id for item in self._series}
        self.canvas.set_series(self._series, visible)
        self._rebuild_legend(visible)
        self._update_current_value()
        self.empty_label.setVisible(not self._has_visible_points())

    def set_loading(self, loading: bool) -> None:
        self.setProperty("loading", loading)
        self.canvas.setDisabled(loading)
        self.empty_label.set_safe_text("正在加载曲线数据..." if loading else EMPTY_CHART_TEXT)
        self.empty_label.setVisible(loading or not self._has_visible_points())

    def set_error(self, message: object) -> None:
        self.error_label.set_safe_text(controlled_error_text(message, fallback="曲线数据加载失败"))
        self.error_label.show()

    def clear_error(self) -> None:
        self.error_label.set_safe_text("")
        self.error_label.hide()

    def visible_series_ids(self) -> set[int]:
        return self.canvas.visible_ids()

    def series(self) -> tuple[ChartSeries, ...]:
        return self._series

    def _rebuild_legend(self, visible: set[int]) -> None:
        while self.legend_layout.count():
            item = self.legend_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._legend_checks.clear()
        for index, series in enumerate(self._series):
            text = normalize_plain_text(series.name or f"探测器 {series.series_id}", max_chars=80)
            check = QCheckBox(text)
            check.setChecked(series.series_id in visible)
            check.setProperty("seriesColor", _SERIES_COLORS[index % len(_SERIES_COLORS)])
            check.toggled.connect(lambda checked, sid=series.series_id: self._toggle_series(sid, checked))
            self._legend_checks[series.series_id] = check
            self.legend_layout.addWidget(check)
        self.legend_layout.addStretch(1)

    def _toggle_series(self, series_id: int, checked: bool) -> None:
        self.canvas.set_visible(series_id, checked)
        self._update_current_value()
        self.empty_label.setVisible(not self._has_visible_points())
        self.visibilityChanged.emit(series_id, checked)

    def _update_current_value(self) -> None:
        latest: ChartPoint | None = None
        for series in self._series:
            if series.series_id not in self.canvas.visible_ids():
                continue
            for point in series.points:
                if point.value is not None:
                    latest = point
        if latest is None:
            self.current_label.set_safe_text("当前值：--")
            return
        unit = f" {latest.unit}" if latest.unit else ""
        name = latest.detector_name or f"探测器 {latest.detector_id}"
        self.current_label.set_safe_text(f"当前值：{name} {latest.value:g}{unit}")

    def _has_visible_points(self) -> bool:
        visible = self.canvas.visible_ids()
        return any(series.series_id in visible and any(point.value is not None for point in series.points) for series in self._series)
