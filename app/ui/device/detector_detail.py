from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from app.services.models import DeviceStatus
from app.ui.common.errors import ErrorBanner, controlled_error_text
from app.ui.common.safe_text import SafeTextLabel
from app.ui.common.status import StatusBadge
from app.ui.monitor.view_models import DetectorDisplayItem

DETAIL_ERROR_TEXT = "探测器详情读取失败"


class DetectorDetail(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("panel", "true")
        self.setProperty("role", "detectorDetail")
        self.title_label = SafeTextLabel("未选择探测器", selectable=True)
        self.title_label.setProperty("role", "panelTitle")
        self.status_badge = StatusBadge(DeviceStatus.INVALID)
        self.error_banner = ErrorBanner(); self.error_banner.clear()
        self.fields = {
            "controller": SafeTextLabel("-", selectable=True),
            "address": SafeTextLabel("-", selectable=True),
            "gas": SafeTextLabel("-", selectable=True),
            "value": SafeTextLabel("-", selectable=True),
            "quality": SafeTextLabel("-", selectable=True),
            "updated": SafeTextLabel("-", selectable=True),
            "location": SafeTextLabel("-", selectable=True),
            "recent_status": SafeTextLabel("-", selectable=True),
        }
        self.records_body = QVBoxLayout()
        self.records_body.setContentsMargins(0, 0, 0, 0)
        self.records_body.setSpacing(6)
        self._build_layout()
        self.clear_detail()

    def clear_detail(self) -> None:
        self.error_banner.clear()
        self.title_label.set_safe_text("未选择探测器")
        self.status_badge.set_status(DeviceStatus.INVALID, text="未选择")
        for label in self.fields.values():
            label.set_safe_text("-")
        self.set_recent_records(())

    def set_loading(self, item: DetectorDisplayItem | None = None) -> None:
        self.error_banner.clear()
        if item is not None:
            self.set_detail(item, recent_records=())
        self.fields["recent_status"].set_safe_text("正在读取详情...")

    def set_detail(self, item: DetectorDisplayItem, *, recent_records: Iterable[object] = ()) -> None:
        self.error_banner.clear()
        self.title_label.set_safe_text(item.name)
        self.status_badge.set_status(item.status, active_alarm=item.pulse_eligible)
        self.fields["controller"].set_safe_text(item.controller_name)
        self.fields["address"].set_safe_text(item.address)
        self.fields["gas"].set_safe_text(item.gas_type or "-")
        self.fields["value"].set_safe_text(f"{item.concentration_text} {'' if item.is_offline else item.unit}".strip())
        self.fields["quality"].set_safe_text(item.quality or "-")
        self.fields["updated"].set_safe_text(item.timestamp or "-")
        self.fields["location"].set_safe_text(item.location or "-")
        self.fields["recent_status"].set_safe_text(f"{item.status_text} / {item.timestamp or '-'}")
        self.set_recent_records(recent_records)

    def set_recent_records(self, records: Iterable[object]) -> None:
        _clear_layout(self.records_body)
        rows = tuple(records)
        if not rows:
            empty = SafeTextLabel("暂无最近记录", selectable=False)
            empty.setProperty("role", "muted")
            self.records_body.addWidget(empty)
            return
        for record in rows[:5]:
            self.records_body.addWidget(_record_item(record))

    def show_error(self, message: object) -> None:
        self.error_banner.set_error(controlled_error_text(message, fallback=DETAIL_ERROR_TEXT))

    def _build_layout(self) -> None:
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(8)
        top.addWidget(self.title_label, 1)
        top.addWidget(self.status_badge)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        for row, (key, title) in enumerate(
            (
                ("controller", "控制器"),
                ("address", "地址"),
                ("gas", "气体"),
                ("value", "当前值"),
                ("quality", "质量"),
                ("updated", "更新时间"),
                ("location", "位置"),
                ("recent_status", "最近状态"),
            )
        ):
            label = QLabel(title)
            label.setProperty("role", "fieldLabel")
            grid.addWidget(label, row, 0)
            grid.addWidget(self.fields[key], row, 1)

        records_title = SafeTextLabel("最近记录", selectable=False)
        records_title.setProperty("role", "panelTitle")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)
        layout.addLayout(top)
        layout.addWidget(self.error_banner)
        layout.addLayout(grid)
        layout.addWidget(records_title)
        layout.addLayout(self.records_body)
        layout.addStretch(1)


def _record_item(record: object) -> QFrame:
    panel = QFrame()
    panel.setProperty("role", "recentRecord")
    status = _value(record, "status", "-")
    concentration = _value(record, "concentration", _value(record, "value", "--"))
    unit = _value(record, "unit", "")
    timestamp = _value(record, "timestamp", _value(record, "created_at", _value(record, "time", "-")))
    text = SafeTextLabel(f"{timestamp}  {status}  {concentration} {unit}".strip(), selectable=True)
    text.setProperty("role", "muted")
    layout = QVBoxLayout(panel)
    layout.setContentsMargins(8, 8, 8, 8)
    layout.addWidget(text)
    return panel


def _value(source: object, name: str, default: object = None) -> object:
    if isinstance(source, dict):
        return source.get(name, default)
    return getattr(source, name, default)


def _clear_layout(layout: QVBoxLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.deleteLater()
