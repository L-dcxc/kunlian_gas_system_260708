from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout, QWidget

from app.ui.common.safe_text import SafeTextLabel
from app.ui.common.status import AlarmPulseController, StatusBadge, repolish
from app.ui.monitor.view_models import DetectorDisplayItem


class DetectorCard(QFrame):
    clicked = Signal(int)

    def __init__(
        self,
        item: DetectorDisplayItem,
        pulse_controller: AlarmPulseController,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setProperty("card", "detector")
        self.setProperty("selected", False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._pulse_controller = pulse_controller
        self._detector_id = item.detector_id

        self.name_label = SafeTextLabel(selectable=True)
        self.name_label.setProperty("role", "panelTitle")
        self.address_label = SafeTextLabel(selectable=True)
        self.address_label.setProperty("role", "muted")
        self.gas_label = SafeTextLabel(selectable=True)
        self.gas_label.setProperty("role", "muted")
        self.value_label = SafeTextLabel(selectable=True, max_chars=80)
        self.value_label.setProperty("role", "concentration")
        self.unit_label = SafeTextLabel(selectable=False, max_chars=32)
        self.unit_label.setProperty("role", "muted")
        self.status_badge = StatusBadge(item.status)
        self.time_label = SafeTextLabel(selectable=True)
        self.time_label.setProperty("role", "muted")
        self.location_label = SafeTextLabel(selectable=True)
        self.location_label.setProperty("role", "muted")

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)
        header.addWidget(self.name_label, 1)
        header.addWidget(self.status_badge)

        value_row = QHBoxLayout()
        value_row.setContentsMargins(0, 0, 0, 0)
        value_row.setSpacing(6)
        value_row.addWidget(self.value_label)
        value_row.addWidget(self.unit_label)
        value_row.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        layout.addLayout(header)
        layout.addWidget(self.address_label)
        layout.addWidget(self.gas_label)
        layout.addLayout(value_row)
        layout.addWidget(self.time_label)
        layout.addWidget(self.location_label)
        self.update_item(item)

    @property
    def detector_id(self) -> int:
        return self._detector_id

    def update_item(self, item: DetectorDisplayItem) -> None:
        # The card consumes only the monitoring display DTO; protocol registers and
        # frame validation stay behind acquisition/protocol adapters.
        self._detector_id = item.detector_id
        self.name_label.set_safe_text(item.name)
        self.address_label.set_safe_text(f"{item.controller_name} / 地址 {item.address}")
        self.gas_label.set_safe_text(f"气体：{item.gas_type or '-'}")
        self.value_label.set_safe_text("--" if item.is_offline else item.concentration_text)
        self.unit_label.set_safe_text("" if item.is_offline else item.unit)
        self.time_label.set_safe_text(f"更新：{item.timestamp or '-'}")
        self.location_label.set_safe_text(f"位置：{item.location or '-'}")
        self.status_badge.set_status(item.status, active_alarm=item.pulse_eligible)
        self.setProperty("deviceStatus", item.status_property)
        if item.pulse_eligible:
            self._pulse_controller.start_for_status(self, item.status)
        else:
            # Recovery/offline/warmup must clear stale alarm properties on reused cards.
            self._pulse_controller.stop(self)
        repolish(self)

    def set_selected(self, selected: bool) -> None:
        self.setProperty("selected", selected)
        repolish(self)

    def mousePressEvent(self, event) -> None:  # noqa: N802 ANN001
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._detector_id)
        super().mousePressEvent(event)
