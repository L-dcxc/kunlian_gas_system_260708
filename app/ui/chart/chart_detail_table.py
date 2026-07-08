from __future__ import annotations

from typing import Mapping, Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from app.ui.common.data_table import DataTable, TableColumn, TableState
from app.ui.common.errors import controlled_error_text
from app.ui.common.safe_text import SafeTextLabel, normalize_plain_text


class ChartDetailTable(QWidget):
    exportRequested = Signal()
    printRequested = Signal()
    pageChanged = Signal(int, int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows: tuple[dict[str, object], ...] = ()
        self.title_label = SafeTextLabel("曲线明细", selectable=False)
        self.title_label.setProperty("role", "panelTitle")
        self.table = DataTable(_columns(), self)
        self.table.export_button.setText("导出")
        self.table.exportRequested.connect(self.exportRequested)
        self.table.pageChanged.connect(self.pageChanged)
        self.print_button = QPushButton("打印")
        self.print_button.clicked.connect(self.printRequested)

        header = QHBoxLayout()
        header.addWidget(self.title_label, 1)
        header.addWidget(self.print_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addLayout(header)
        layout.addWidget(self.table, 1)
        self.set_export_enabled(False)

    def set_points(self, points: Sequence[object], *, page: int = 1, total: int | None = None, per_page: int = 100) -> None:
        rows = tuple(_point_row(point) for point in points)
        self._rows = rows
        self.table.set_rows(rows)
        self.table.set_page(page, len(rows) if total is None else total, per_page)
        if rows:
            self.table.set_state(TableState.READY)
            self.set_export_enabled(True)
        else:
            self.table.set_state(TableState.EMPTY, "当前筛选条件下无记录")
            self.set_export_enabled(False)

    def set_loading(self, loading: bool) -> None:
        if loading:
            self.table.set_state(TableState.LOADING, "正在查询历史曲线明细...")
        self.set_export_enabled(not loading and bool(self._rows))

    def set_error(self, message: object) -> None:
        self.table.set_state(TableState.ERROR, controlled_error_text(message, fallback="历史曲线明细加载失败"))
        self.set_export_enabled(False)

    def set_export_enabled(self, enabled: bool) -> None:
        self.table.export_button.setEnabled(enabled)
        self.print_button.setEnabled(enabled)

    def export_rows(self) -> tuple[dict[str, object], ...]:
        return self._rows

    def current_state(self) -> TableState:
        return self.table.state()


def _columns() -> tuple[TableColumn, ...]:
    return (
        TableColumn("recorded_at", "记录时间", 170),
        TableColumn("detector_id", "探测器 ID", 90, Qt.AlignmentFlag.AlignRight),
        TableColumn("detector_name", "探测器", 140),
        TableColumn("position_code", "位置编号", 100),
        TableColumn("gas_type", "气体类型", 100),
        TableColumn("status", "状态", 100),
        TableColumn("concentration", "浓度", 90, Qt.AlignmentFlag.AlignRight),
        TableColumn("unit", "单位", 80),
    )


def _point_row(point: object) -> dict[str, object]:
    if isinstance(point, Mapping):
        getter = point.get
    else:
        getter = lambda key, default=None: getattr(point, key, default)  # noqa: E731
    concentration = getter("concentration")
    if concentration is None:
        concentration_text = ""
    else:
        try:
            concentration_text = f"{float(concentration):g}"
        except (TypeError, ValueError):
            concentration_text = normalize_plain_text(concentration, max_chars=80)
    return {
        "recorded_at": getter("recorded_at") or getter("timestamp") or "",
        "detector_id": getter("detector_id") or "",
        "detector_name": getter("detector_name") or "",
        "position_code": getter("position_code") or "",
        "gas_type": getter("gas_type") or "",
        "status": getter("status") or "",
        "concentration": concentration_text,
        "unit": getter("unit") or "",
    }
