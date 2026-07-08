from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget

from app.services.device_config_service import ControllerCommand
from app.services.errors import ErrorCode
from app.ui.common.data_table import DataTable, TableColumn, TableState
from app.ui.common.dialogs import RiskConfirmDialog
from app.ui.common.errors import ErrorBanner, ValidationHint, controlled_error_text
from app.ui.common.safe_text import SafeTextLabel
from app.ui.common.status import repolish

LOAD_FAILED_TEXT = "控制器列表加载失败，请稍后重试。"
SAVE_FAILED_TEXT = "控制器保存失败，请稍后重试。"


class ControllersPage(QWidget):
    configChanged = Signal()

    def __init__(
        self,
        device_config_service: object | None = None,
        session: object | None = None,
        parent: QWidget | None = None,
        *,
        can_configure: bool = True,
        confirm_delete: object | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = device_config_service
        self._session = session
        self._can_configure = can_configure
        self._confirm_delete = confirm_delete or _confirm_delete_controller
        self._rows: tuple[dict[str, Any], ...] = ()
        self._ports: tuple[dict[str, Any], ...] = ()
        self._selected_id: int | None = None
        self._editing_id: int | None = None

        self.error_banner = ErrorBanner(); self.error_banner.clear()
        self.table = DataTable([
            TableColumn("id", "ID", 64, Qt.AlignmentFlag.AlignRight),
            TableColumn("name", "控制器名称", 180),
            TableColumn("port_label", "端口", 150),
            TableColumn("address", "地址", 80, Qt.AlignmentFlag.AlignRight),
            TableColumn("model", "型号", 140),
            TableColumn("detector_count", "探测器数", 100, Qt.AlignmentFlag.AlignRight),
            TableColumn("status_label", "状态", 80),
        ])
        self.table.retryRequested.connect(self.reload)
        self.table.emptyActionRequested.connect(self.new_record)
        self.table.table.selectionModel().selectionChanged.connect(self._selection_changed)
        self.table.export_button.setVisible(False)

        self.form = QFrame(); self.form.setProperty("panel", "true")
        self.port_combo = QComboBox()
        self.name_edit = QLineEdit(); self.name_edit.setMaxLength(80)
        self.address_spin = _spin(1, 247, 1)
        self.model_edit = QLineEdit(); self.model_edit.setMaxLength(80)
        self.detector_count_spin = _spin(0, 4096, 0)
        self.enabled_check = QCheckBox("启用控制器"); self.enabled_check.setChecked(True)
        self.validation_hint = ValidationHint(); self.validation_hint.clear()
        self.new_button = QPushButton("新增")
        self.save_button = QPushButton("保存控制器"); self.save_button.setProperty("variant", "primary")
        self.delete_button = QPushButton("删除"); self.delete_button.setProperty("variant", "danger")
        self.new_button.clicked.connect(self.new_record)
        self.save_button.clicked.connect(self.save_current)
        self.delete_button.clicked.connect(self.delete_selected)
        self._build_form()

        actions = QHBoxLayout(); actions.addWidget(self.new_button); actions.addWidget(self.save_button); actions.addWidget(self.delete_button); actions.addStretch(1)
        body = QHBoxLayout(); body.addWidget(self.table, 3); body.addWidget(self.form, 2)
        layout = QVBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(12)
        layout.addWidget(self.error_banner); layout.addLayout(actions); layout.addLayout(body, 1)
        self._apply_permission_state(); self._apply_selection_state()

    def reload(self) -> None:
        self.error_banner.clear(); self.table.set_state(TableState.LOADING, "正在加载控制器")
        try:
            self._ports = tuple(dict(row) for row in (self._service.list_ports() if self._service else ()))
            self._rows = tuple(dict(row) for row in (self._service.list_controllers() if self._service else ()))
        except Exception:
            self._show_table_error(LOAD_FAILED_TEXT); return
        self._reload_ports()
        self.table.set_rows([_controller_to_table(row, self._ports) for row in self._rows])
        self.table.set_page(1, len(self._rows), max(1, len(self._rows) or 1))
        self.table.set_state(TableState.READY if self._rows else TableState.EMPTY, "暂无控制器，请先新增控制器")
        self._apply_selection_state()

    def new_record(self) -> None:
        if not self._require_permission():
            return
        self._editing_id = None; self._selected_id = None
        self.name_edit.clear(); self.model_edit.clear(); self.address_spin.setValue(1); self.detector_count_spin.setValue(0); self.enabled_check.setChecked(True)
        self.clear_validation(); self._apply_selection_state()

    def save_current(self) -> None:
        if not self._require_permission() or not self.validate_form():
            return
        try:
            result = self._service.save_controller(self._session, self._command())
        except Exception:
            self.error_banner.set_error(SAVE_FAILED_TEXT); return
        if bool(getattr(result, "success", False)):
            self.reload(); self.configChanged.emit(); return
        if int(getattr(result, "code", 0) or 0) == int(ErrorCode.PERMISSION_DENIED):
            self.error_banner.show_permission_denied()
        else:
            self.error_banner.set_error(getattr(result, "message", SAVE_FAILED_TEXT))

    def delete_selected(self) -> None:
        if not self._require_permission() or self._selected_id is None:
            return
        row = self.selected_row()
        if row is None or not self._confirm_delete(self, row):
            return
        try:
            result = self._service.delete_controller(self._session, self._selected_id)
        except Exception:
            self.error_banner.set_error(SAVE_FAILED_TEXT); return
        if bool(getattr(result, "success", False)):
            self.reload(); self.configChanged.emit(); return
        self.error_banner.set_error(getattr(result, "message", SAVE_FAILED_TEXT))

    def selected_row(self) -> dict[str, Any] | None:
        return next((row for row in self._rows if int(row.get("id", 0)) == self._selected_id), None)

    def validate_form(self) -> bool:
        self.clear_validation()
        if self.port_combo.currentData() is None:
            return self._field_error(self.port_combo, "必须选择端口")
        if not self.name_edit.text().strip():
            return self._field_error(self.name_edit, "控制器名称不能为空")
        return True

    def clear_validation(self) -> None:
        self.validation_hint.clear()
        for widget in (self.port_combo, self.name_edit):
            widget.setProperty("validation", None); repolish(widget)

    def _command(self) -> ControllerCommand:
        return ControllerCommand(
            id=self._editing_id,
            port_id=int(self.port_combo.currentData()),
            name=self.name_edit.text(),
            address=self.address_spin.value(),
            model=self.model_edit.text() or None,
            detector_count=self.detector_count_spin.value(),
            is_enabled=self.enabled_check.isChecked(),
        )

    def _reload_ports(self) -> None:
        current = self.port_combo.currentData()
        self.port_combo.clear()
        self.port_combo.addItem("请选择端口", None)
        for row in self._ports:
            self.port_combo.addItem(str(row.get("name", f"端口 {row.get('id')}")), int(row.get("id", 0)))
        index = self.port_combo.findData(current)
        if index >= 0:
            self.port_combo.setCurrentIndex(index)

    def _build_form(self) -> None:
        grid = QGridLayout(self.form); grid.setContentsMargins(16, 16, 16, 16); grid.setHorizontalSpacing(10); grid.setVerticalSpacing(8)
        for row, (label, widget) in enumerate((("所属端口", self.port_combo), ("名称", self.name_edit), ("Modbus 地址", self.address_spin), ("型号", self.model_edit), ("探测器数量", self.detector_count_spin), ("", self.enabled_check), ("", self.validation_hint))):
            if label:
                label_widget = QLabel(label); label_widget.setProperty("role", "fieldLabel"); grid.addWidget(label_widget, row, 0)
            grid.addWidget(widget, row, 1)

    def _selection_changed(self) -> None:
        indexes = self.table.table.selectionModel().selectedRows()
        row = self._rows[indexes[0].row()] if indexes and 0 <= indexes[0].row() < len(self._rows) else None
        self._selected_id = int(row["id"]) if row else None; self._editing_id = self._selected_id
        if row:
            self.name_edit.setText(str(row.get("name", ""))); self.model_edit.setText(str(row.get("model") or ""))
            _set_combo(self.port_combo, row.get("port_id")); self.address_spin.setValue(int(row.get("address", 1)))
            self.detector_count_spin.setValue(int(row.get("detector_count", 0))); self.enabled_check.setChecked(bool(row.get("is_enabled", True)))
        self._apply_selection_state()

    def _field_error(self, widget: QWidget, message: str) -> bool:
        widget.setProperty("validation", "error"); repolish(widget); self.validation_hint.set_validation_error(message); return False

    def _require_permission(self) -> bool:
        if self._can_configure:
            return True
        self.error_banner.show_permission_denied(); return False

    def _apply_permission_state(self) -> None:
        for widget in (self.port_combo, self.name_edit, self.address_spin, self.model_edit, self.detector_count_spin, self.enabled_check, self.new_button, self.save_button, self.delete_button):
            widget.setEnabled(self._can_configure)

    def _apply_selection_state(self) -> None:
        self.delete_button.setEnabled(self._can_configure and self._selected_id is not None)

    def _show_table_error(self, message: object) -> None:
        self.table.set_rows([]); self.table.set_page(1, 0, 1); self.table.set_state(TableState.ERROR, controlled_error_text(message, fallback=LOAD_FAILED_TEXT))


def _controller_to_table(row: dict[str, Any], ports: tuple[dict[str, Any], ...]) -> dict[str, Any]:
    port = next((item for item in ports if int(item.get("id", 0)) == int(row.get("port_id", 0))), None)
    return {**row, "port_label": port.get("name", "") if port else row.get("port_id", ""), "status_label": "启用" if row.get("is_enabled", True) else "停用"}


def _spin(minimum: int, maximum: int, value: int) -> QSpinBox:
    spin = QSpinBox(); spin.setRange(minimum, maximum); spin.setValue(value); return spin


def _set_combo(combo: QComboBox, value: object) -> None:
    index = combo.findData(value)
    if index < 0:
        try:
            index = combo.findData(int(value))
        except (TypeError, ValueError):
            index = -1
    if index >= 0:
        combo.setCurrentIndex(index)


def _confirm_delete_controller(parent: QWidget, row: dict[str, Any]) -> bool:
    return RiskConfirmDialog.confirm(
        "确认删除控制器",
        f"将删除控制器：{row.get('name', '')}。绑定探测器和地图点位可能受影响；服务层会按引用关系拒绝。",
        parent,
        confirm_text="确认删除",
    )
