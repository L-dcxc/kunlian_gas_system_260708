from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Sequence

from PySide6.QtCore import QDateTime, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDateTimeEdit,
    QHBoxLayout,
    QLineEdit,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.services.chart_service import HistoryCurveQuery
from app.services.export_service import ExportService
from app.services.models import ServiceResult
from app.ui.chart.chart_detail_table import ChartDetailTable
from app.ui.chart.chart_widgets import ChartCurveWidget, ChartPoint, ChartSeries
from app.ui.common.errors import ErrorBanner, controlled_error_text
from app.ui.common.filter_panel import FilterPanel
from app.ui.common.safe_text import SafeTextLabel

HISTORY_RANGE_ERROR = "结束时间不能早于开始时间"
HISTORY_QUERY_ERROR = "历史曲线查询失败"
REALTIME_QUERY_ERROR = "实时曲线查询失败"
EXPORT_ERROR = "导出数据准备失败"


class ChartPage(QWidget):
    historyQueryRequested = Signal(object)
    exportBuilt = Signal(object)
    printBuilt = Signal(object)

    def __init__(
        self,
        chart_service: object,
        export_service: object | None = None,
        parent: QWidget | None = None,
        *,
        realtime_interval_ms: int = 1000,
        auto_start_realtime: bool = False,
    ) -> None:
        super().__init__(parent)
        self.chart_service = chart_service
        self.export_service = export_service or ExportService()
        self._current_history_points: tuple[object, ...] = ()
        self._current_filters: dict[str, object] = {}
        self.last_export_payload: object | None = None
        self.last_export_filters: dict[str, object] = {}
        self.last_print_payload: object | None = None
        self.last_print_filters: dict[str, object] = {}
        self._querying = False

        self.error_banner = ErrorBanner(); self.error_banner.clear()
        self.tabs = QTabWidget(self)
        self.realtime_filter = self._build_realtime_filter()
        self.history_filter = self._build_history_filter()
        self.realtime_chart = ChartCurveWidget("实时曲线")
        self.history_chart = ChartCurveWidget("历史曲线")
        self.detail_table = ChartDetailTable()
        self.detail_table.exportRequested.connect(self.export_current)
        self.detail_table.printRequested.connect(self.print_current)

        self.realtime_timer = QTimer(self)
        self.realtime_timer.setInterval(realtime_interval_ms)
        self.realtime_timer.timeout.connect(self.refresh_realtime)

        self._build_layout()
        self._set_querying(False)
        if auto_start_realtime:
            self.start_realtime()

    def start_realtime(self) -> None:
        if not self.realtime_timer.isActive():
            self.realtime_timer.start()
        self.refresh_realtime()

    def stop_realtime(self) -> None:
        self.realtime_timer.stop()

    def refresh_realtime(self) -> bool:
        self.realtime_filter.clear_validation_errors()
        detector_ids = _parse_ids(self.realtime_detector_edit.text())
        if not detector_ids:
            self.realtime_filter.set_validation_error("detectors", "请选择探测器")
            return False
        try:
            result = self.chart_service.get_realtime_series(detector_ids)
        except Exception:
            self.realtime_chart.set_error(REALTIME_QUERY_ERROR)
            return False
        if not _result_success(result):
            self.realtime_chart.set_error(controlled_error_text(_result_message(result), fallback=REALTIME_QUERY_ERROR))
            return False
        self.realtime_chart.clear_error()
        self.realtime_chart.set_series(_series_from_realtime(_result_data(result)))
        return True

    def query_history(self, *, page: int = 1, per_page: int = 100) -> bool:
        if self._querying:
            return False
        self.history_filter.clear_validation_errors()
        self.error_banner.clear()
        if not self._validate_history_range():
            return False
        detector_ids = _parse_ids(self.history_detector_edit.text())
        if not detector_ids:
            self.history_filter.set_validation_error("detectors", "请选择探测器")
            return False
        filters = self.collect_history_filters(page=page, per_page=per_page)
        try:
            command = HistoryCurveQuery(
                detector_ids=tuple(detector_ids),
                start_time=str(filters["start_time"]),
                end_time=str(filters["end_time"]),
                page=page,
                per_page=per_page,
            )
        except ValueError as exc:
            self.error_banner.set_error(controlled_error_text(str(exc), fallback="输入内容校验失败，请检查后重试。"), severity="warning")
            return False
        self._set_querying(True)
        self.historyQueryRequested.emit(command)
        try:
            result = self.chart_service.query_history(command)
        except Exception:
            result = ServiceResult.fail(500, HISTORY_QUERY_ERROR)
        finally:
            self._set_querying(False)
        if not _result_success(result):
            message = controlled_error_text(_result_message(result), fallback=HISTORY_QUERY_ERROR)
            self.error_banner.set_error(message)
            self.history_chart.set_error(message)
            self.detail_table.set_error(message)
            return False
        page_data = _result_data(result)
        points = tuple(getattr(page_data, "items", ()) if page_data is not None else ())
        total = int(getattr(page_data, "total", len(points))) if page_data is not None else len(points)
        pagination = getattr(page_data, "pagination", None)
        current_page = int(getattr(pagination, "page", page)) if pagination is not None else page
        current_per_page = int(getattr(pagination, "per_page", per_page)) if pagination is not None else per_page
        self._current_history_points = points
        self._current_filters = filters
        self.history_chart.clear_error()
        self.history_chart.set_series(_series_from_history(points))
        self.detail_table.set_points(points, page=current_page, total=total, per_page=current_per_page)
        return True

    def collect_history_filters(self, *, page: int = 1, per_page: int = 100) -> dict[str, object]:
        return {
            "start_time": _editor_iso(self.start_time_edit),
            "end_time": _editor_iso(self.end_time_edit),
            "port_id": self.port_edit.text().strip(),
            "controller_id": self.controller_edit.text().strip(),
            "detector_ids": tuple(_parse_ids(self.history_detector_edit.text())),
            "status": self.status_combo.currentData() or "",
            "page": page,
            "per_page": per_page,
        }

    def export_current(self) -> bool:
        return self._build_export("xlsx")

    def print_current(self) -> bool:
        return self._build_export("print")

    def is_querying(self) -> bool:
        return self._querying

    def _build_export(self, export_format: str) -> bool:
        rows = self.detail_table.export_rows()
        if not rows:
            self.error_banner.set_error("当前筛选条件下无记录", severity="warning")
            return False
        self.detail_table.set_export_enabled(False)
        try:
            result = self.export_service.build_chart_export(rows=rows, export_format=export_format)
        except Exception:
            result = ServiceResult.fail(500, EXPORT_ERROR)
        finally:
            self.detail_table.set_export_enabled(True)
        if not _result_success(result):
            self.error_banner.set_error(controlled_error_text(_result_message(result), fallback=EXPORT_ERROR))
            return False
        payload = _result_data(result)
        if export_format == "print":
            self.last_print_payload = payload
            self.last_print_filters = dict(self._current_filters)
            self.printBuilt.emit(payload)
        else:
            self.last_export_payload = payload
            self.last_export_filters = dict(self._current_filters)
            self.exportBuilt.emit(payload)
        return True

    def _set_querying(self, querying: bool) -> None:
        self._querying = querying
        self.history_filter.set_querying(querying)
        self.detail_table.set_loading(querying)
        if querying:
            self.history_chart.set_loading(True)
        else:
            self.history_chart.set_loading(False)
            self.detail_table.set_export_enabled(bool(self.detail_table.export_rows()))

    def _validate_history_range(self) -> bool:
        if self.end_time_edit.dateTime() < self.start_time_edit.dateTime():
            self.history_filter.set_validation_error("end_time", HISTORY_RANGE_ERROR)
            self.error_banner.set_error(HISTORY_RANGE_ERROR, severity="warning")
            return False
        return True

    def _build_realtime_filter(self) -> FilterPanel:
        panel = FilterPanel("实时曲线筛选")
        self.realtime_detector_edit = QLineEdit()
        self.realtime_detector_edit.setPlaceholderText("示例：1,2,3")
        panel.add_field("detectors", "探测器 ID", self.realtime_detector_edit)
        panel.search_button.setText("刷新")
        panel.searchRequested.connect(self.refresh_realtime)
        panel.resetRequested.connect(lambda: self.realtime_detector_edit.clear())
        return panel

    def _build_history_filter(self) -> FilterPanel:
        panel = FilterPanel("历史曲线筛选")
        now = datetime.now(timezone.utc).replace(microsecond=0)
        self.start_time_edit = QDateTimeEdit(QDateTime(now - timedelta(hours=1)))
        self.end_time_edit = QDateTimeEdit(QDateTime(now))
        for editor in (self.start_time_edit, self.end_time_edit):
            editor.setCalendarPopup(True)
            editor.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        self.port_edit = QLineEdit()
        self.controller_edit = QLineEdit()
        self.history_detector_edit = QLineEdit()
        self.history_detector_edit.setPlaceholderText("示例：1,2,3")
        self.status_combo = QComboBox()
        self.status_combo.addItem("全部", "")
        self.status_combo.addItem("正常", "normal")
        self.status_combo.addItem("低报", "alarm_low")
        self.status_combo.addItem("高报", "alarm_high")
        self.status_combo.addItem("故障", "fault")
        self.status_combo.addItem("离线", "offline")
        panel.add_field("start_time", "开始时间", self.start_time_edit)
        panel.add_field("end_time", "结束时间", self.end_time_edit)
        panel.add_field("port", "端口", self.port_edit)
        panel.add_field("controller", "控制器", self.controller_edit)
        panel.add_field("detectors", "探测器 ID", self.history_detector_edit)
        panel.add_field("status", "状态", self.status_combo)
        panel.searchRequested.connect(self.query_history)
        panel.resetRequested.connect(self._reset_history_filter)
        return panel

    def _reset_history_filter(self) -> None:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        self.start_time_edit.setDateTime(QDateTime(now - timedelta(hours=1)))
        self.end_time_edit.setDateTime(QDateTime(now))
        self.port_edit.clear()
        self.controller_edit.clear()
        self.history_detector_edit.clear()
        self.status_combo.setCurrentIndex(0)
        self.history_filter.clear_validation_errors()
        self.error_banner.clear()

    def _build_layout(self) -> None:
        realtime_page = QWidget()
        realtime_layout = QVBoxLayout(realtime_page)
        realtime_layout.setContentsMargins(0, 0, 0, 0)
        realtime_layout.setSpacing(12)
        realtime_layout.addWidget(self.realtime_filter)
        realtime_layout.addWidget(self.realtime_chart, 1)

        history_page = QWidget()
        history_layout = QVBoxLayout(history_page)
        history_layout.setContentsMargins(0, 0, 0, 0)
        history_layout.setSpacing(12)
        history_layout.addWidget(self.history_filter)
        split = QHBoxLayout()
        split.addWidget(self.history_chart, 3)
        split.addWidget(self.detail_table, 2)
        history_layout.addLayout(split, 1)

        self.tabs.addTab(realtime_page, "实时曲线")
        self.tabs.addTab(history_page, "历史曲线")
        title = SafeTextLabel("曲线监控", selectable=False)
        title.setProperty("role", "panelTitle")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        layout.addWidget(title)
        layout.addWidget(self.error_banner)
        layout.addWidget(self.tabs, 1)


def _parse_ids(text: str) -> tuple[int, ...]:
    values: list[int] = []
    for chunk in text.replace("，", ",").split(","):
        item = chunk.strip()
        if not item:
            continue
        try:
            value = int(item)
        except ValueError:
            continue
        if value > 0 and value not in values:
            values.append(value)
    return tuple(values)


def _editor_iso(editor: QDateTimeEdit) -> str:
    value = editor.dateTime().toPython()
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _series_from_realtime(data: object) -> tuple[ChartSeries, ...]:
    series: list[ChartSeries] = []
    for view in tuple(data or ()):  # type: ignore[arg-type]
        detector_id = int(getattr(view, "detector_id", 0))
        points = []
        for point in tuple(getattr(view, "points", ())):
            points.append(
                ChartPoint(
                    detector_id=detector_id,
                    timestamp=str(getattr(point, "timestamp", "") or ""),
                    value=getattr(point, "concentration", None),
                    unit=str(getattr(point, "unit", "") or ""),
                    status=str(getattr(point, "status", "") or ""),
                    detector_name=f"探测器 {detector_id}",
                )
            )
        series.append(ChartSeries(series_id=detector_id, name=f"探测器 {detector_id}", points=tuple(points)))
    return tuple(series)


def _series_from_history(points: Sequence[object]) -> tuple[ChartSeries, ...]:
    grouped: dict[int, list[ChartPoint]] = {}
    names: dict[int, str] = {}
    units: dict[int, str] = {}
    gases: dict[int, str] = {}
    for point in points:
        detector_id = int(getattr(point, "detector_id", 0))
        if detector_id <= 0:
            continue
        name = str(getattr(point, "detector_name", "") or f"探测器 {detector_id}")
        unit = str(getattr(point, "unit", "") or "")
        gas = str(getattr(point, "gas_type", "") or "")
        names[detector_id] = name
        units[detector_id] = unit
        gases[detector_id] = gas
        grouped.setdefault(detector_id, []).append(
            ChartPoint(
                detector_id=detector_id,
                timestamp=str(getattr(point, "recorded_at", "") or ""),
                value=getattr(point, "concentration", None),
                unit=unit,
                status=str(getattr(point, "status", "") or ""),
                detector_name=name,
                gas_type=gas,
            )
        )
    return tuple(
        ChartSeries(series_id=detector_id, name=names[detector_id], points=tuple(grouped[detector_id]), unit=units[detector_id], gas_type=gases[detector_id])
        for detector_id in sorted(grouped)
    )


def _result_success(result: object) -> bool:
    return bool(getattr(result, "success", False))


def _result_data(result: object) -> object:
    return getattr(result, "data", None)


def _result_message(result: object) -> object:
    return getattr(result, "message", "")
