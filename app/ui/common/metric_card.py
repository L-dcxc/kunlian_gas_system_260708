from __future__ import annotations

from PySide6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout, QWidget

from app.ui.common.safe_text import SafeTextLabel
from app.ui.common.status import repolish

_ALLOWED_STATUSES = {
    "normal",
    "running",
    "warning",
    "lowAlarm",
    "highAlarm",
    "fault",
    "offline",
    "shielded",
    "warmup",
    "overRange",
}


class MetricCard(QFrame):
    def __init__(
        self,
        title: object,
        value: object = "--",
        parent: QWidget | None = None,
        *,
        unit: object = "",
        status: str = "normal",
        subtitle: object = "",
    ) -> None:
        super().__init__(parent)
        self.setProperty("role", "metricCard")
        self.setProperty("panel", "true")
        self.title_label = SafeTextLabel(title, selectable=False)
        self.title_label.setProperty("role", "muted")
        self.value_label = SafeTextLabel(value, selectable=True, max_chars=128)
        self.value_label.setProperty("role", "metricValue")
        self.unit_label = SafeTextLabel(unit, selectable=False, max_chars=32)
        self.unit_label.setProperty("role", "muted")
        self.subtitle_label = SafeTextLabel(subtitle, selectable=True)
        self.subtitle_label.setProperty("role", "muted")

        value_row = QHBoxLayout()
        value_row.setContentsMargins(0, 0, 0, 0)
        value_row.setSpacing(6)
        value_row.addWidget(self.value_label)
        value_row.addWidget(self.unit_label)
        value_row.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)
        layout.addWidget(self.title_label)
        layout.addLayout(value_row)
        layout.addWidget(self.subtitle_label)
        self.set_status(status)

    def set_metric(self, value: object, *, unit: object | None = None, subtitle: object | None = None) -> None:
        self.value_label.set_safe_text(value)
        if unit is not None:
            self.unit_label.set_safe_text(unit)
        if subtitle is not None:
            self.subtitle_label.set_safe_text(subtitle)
            self.subtitle_label.setVisible(bool(self.subtitle_label.text()))

    def set_status(self, status: str) -> None:
        safe_status = status if status in _ALLOWED_STATUSES else "normal"
        self.setProperty("status", safe_status)
        self.value_label.setProperty("status", safe_status)
        repolish(self)
        repolish(self.value_label)

    def status(self) -> str:
        return str(self.property("status"))
