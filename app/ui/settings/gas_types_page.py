from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QCheckBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from app.services.device_config_service import GasTypeCommand
from app.services.errors import ErrorCode
from app.ui.common.data_table import DataTable, TableColumn, TableState
from app.ui.common.dialogs import RiskConfirmDialog
from app.ui.common.errors import ErrorBanner, ValidationHint, controlled_error_text
from app.ui.common.status import repolish

LOAD_FAILED_TEXT = "气体类型列表加载失败，请稍后重试。"
SAVE_FAILED_TEXT = "气体类型保存失败，请稍后重试。"


class GasTypesPage(QWidget):
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
        self._confirm_delete = confirm_delete or _confirm_delete_gas_type
        self._rows: tuple[dict[str, Any], ...] = ()
        self._selected_id: int | None = None
        self._editing_id: int | None = None

        self.error_banner = ErrorBanner(); self.error_banner.clear()
        self.table = DataTable([
            TableColumn("id", "ID", 64, Qt.AlignmentFlag.AlignRight),
            TableColumn("name", "气体类型", 160),
            TableColumn("unit", "单位", 90),
            TableColumn("range_label", "默认量程", 160),
            TableColumn("alarm_label", "默认阈值", 180),
            TableColumn("status_label", "状态", 80),
        ])
        self.table.retryRequested.connect(self.reload)
        self.table.emptyActionRequested.connect(self.new_record)
        self.table.table.selectionModel().selectionChanged.connect(self._selection_changed)
        self.table.export_button.setVisible(False)

        self.form = QFrame(); self.form.setProperty("panel", "true")
        self.name_edit = QLineEdit(); self.name_edit.setMaxLength(80)
        self.unit_edit = QLineEdit(); self.unit_edit.setMaxLength(32)
        self.range_min_edit = QLineEdit("0")
        self.range_max_edit = QLineEdit("100")
        self.alarm_low_edit = QLineEdit("20")
        self.alarm_high_edit = QLineEdit("50")
        self.enabled_check = QCheckBox("启用气体类型"); self.enabled_check.setChecked(True)
        self.validation_hint = ValidationHint(); self.validation_hint.clear()
        self.new_button = QPushButton("新增")
        self.save_button = QPushButton("保存气体类型"); self.save_button.setProperty("variant", "primary")
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
        self.error_banner.clear(); self.table.set_state(TableState.LOADING, "正在加载气体类型")
        try:
            self._rows = tuple(dict(row) for row in (self._service.list_gas_types() if self._service else ()))
        except Exception:
            self._show_table_error(LOAD_FAILED_TEXT); return
        self.table.set_rows([_gas_to_table(row) for row in self._rows])
        self.table.set_page(1, len(self._rows), max(1, len(self._rows) or 1))
        self.table.set_state(TableState.READY if self._rows else TableState.EMPTY, "暂无气体类型，请先新增气体类型")
        self._apply_selection_state()

    def new_record(self) -> None:
        if not self._require_permission():
            return
        self._editing_id = None; self._selected_id = None
        self.name_edit.clear(); self.unit_edit.setText("%LEL"); self.range_min_edit.setText("0"); self.range_max_edit.setText("100")
        self.alarm_low_edit.setText("20"); self.alarm_high_edit.setText("50"); self.enabled_check.setChecked(True)
        self.clear_validation(); self._apply_selection_state()

    def save_current(self) -> None:
        if not self._require_permission() or not self.validate_form():
            return
        try:
            result = self._service.save_gas_type(self._session, self._command())
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
            result = self._service.delete_gas_type(self._session, self._selected_id)
        except Exception:
            self.error_banner.set_error(SAVE_FAILED_TEXT); return
        if bool(getattr(result, "success", False)):
            self.reload(); self.configChanged.emit(); return
        self.error_banner.set_error(getattr(result, "message", SAVE_FAILED_TEXT))

    def selected_row(self) -> dict[str, Any] | None:
        return next((row for row in self._rows if int(row.get("id", 0)) == self._selected_id), None)

    def validate_form(self) -> bool:
        self.clear_validation()
        if not self.name_edit.text().strip():
            return self._field_error(self.name_edit, "气体类型名称不能为空")
        if not self.unit_edit.text().strip():
            return self._field_error(self.unit_edit, "单位不能为空")
        try:
            low = float(self.range_min_edit.text()); high = float(self.range_max_edit.text())
            alarm_low = _optional_float(self.alarm_low_edit.text()); alarm_high = _optional_float(self.alarm_high_edit.text())
        except ValueError:
            return self._field_error(self.range_min_edit, "量程和阈值必须为数字")
        if low >= high:
            return self._field_error(self.range_min_edit, "量程下限必须小于上限")
        if alarm_low is not None and not low <= alarm_low <= high:
            return self._field_error(self.alarm_low_edit, "低报阈值必须在量程内")
        if alarm_high is not None and not low <= alarm_high <= high:
            return self._field_error(self.alarm_high_edit, "高报阈值必须在量程内")
        if alarm_low is not None and alarm_high is not None and alarm_low > alarm_high:
            return self._field_error(self.alarm_low_edit, "低报阈值不能高于高报阈值")
        return True

    def clear_validation(self) -> None:
        self.validation_hint.clear()
        for widget in (self.name_edit, self.unit_edit, self.range_min_edit, self.range_max_edit, self.alarm_low_edit, self.alarm_high_edit):
            widget.setProperty("validation", None); repolish(widget)

    def _command(self) -> GasTypeCommand:
        return GasTypeCommand(
            id=self._editing_id,
            name=self.name_edit.text(),
            unit=self.unit_edit.text(),
            range_min=float(self.range_min_edit.text()),
            range_max=float(self.range_max_edit.text()),
            default_alarm_low=_optional_float(self.alarm_low_edit.text()),
            default_alarm_high=_optional_float(self.alarm_high_edit.text()),
            is_enabled=self.enabled_check.isChecked(),
        )

    def _build_form(self) -> None:
        grid = QGridLayout(self.form); grid.setContentsMargins(16, 16, 16, 16); grid.setHorizontalSpacing(10); grid.setVerticalSpacing(8)
        for row, (label, widget) in enumerate((("名称", self.name_edit), ("单位", self.unit_edit), ("量程下限", self.range_min_edit), ("量程上限", self.range_max_edit), ("默认低报", self.alarm_low_edit), ("默认高报", self.alarm_high_edit), ("", self.enabled_check), ("", self.validation_hint))):
            if label:
                label_widget = QLabel(label); label_widget.setProperty("role", "fieldLabel"); grid.addWidget(label_widget, row, 0)
            grid.addWidget(widget, row, 1)

    def _selection_changed(self) -> None:
        indexes = self.table.table.selectionModel().selectedRows()
        row = self._rows[indexes[0].row()] if indexes and 0 <= indexes[0].row() < len(self._rows) else None
        self._selected_id = int(row["id"]) if row else None; self._editing_id = self._selected_id
        if row:
            self.name_edit.setText(str(row.get("name", ""))); self.unit_edit.setText(str(row.get("unit", "")))
            self.range_min_edit.setText(str(row.get("range_min", ""))); self.range_max_edit.setText(str(row.get("range_max", "")))
            self.alarm_low_edit.setText("" if row.get("default_alarm_low") is None else str(row.get("default_alarm_low")))
            self.alarm_high_edit.setText("" if row.get("default_alarm_high") is None else str(row.get("default_alarm_high")))
            self.enabled_check.setChecked(bool(row.get("is_enabled", True)))
        self._apply_selection_state()

    def _field_error(self, widget: QWidget, message: str) -> bool:
        widget.setProperty("validation", "error"); repolish(widget); self.validation_hint.set_validation_error(message); return False

    def _require_permission(self) -> bool:
        if self._can_configure:
            return True
        self.error_banner.show_permission_denied(); return False

    def _apply_permission_state(self) -> None:
        for widget in (self.name_edit, self.unit_edit, self.range_min_edit, self.range_max_edit, self.alarm_low_edit, self.alarm_high_edit, self.enabled_check, self.new_button, self.save_button, self.delete_button):
            widget.setEnabled(self._can_configure)

    def _apply_selection_state(self) -> None:
        self.delete_button.setEnabled(self._can_configure and self._selected_id is not None)

    def _show_table_error(self, message: object) -> None:
        self.table.set_rows([]); self.table.set_page(1, 0, 1); self.table.set_state(TableState.ERROR, controlled_error_text(message, fallback=LOAD_FAILED_TEXT))


def _gas_to_table(row: dict[str, Any]) -> dict[str, Any]:
    return {**row, "range_label": f"{row.get('range_min', '')} - {row.get('range_max', '')} {row.get('unit', '')}", "alarm_label": f"低 {row.get('default_alarm_low', '')} / 高 {row.get('default_alarm_high', '')}", "status_label": "启用" if row.get("is_enabled", True) else "停用"}


def _optional_float(text: str) -> float | None:
    value = text.strip()
    return None if value == "" else float(value)


def _confirm_delete_gas_type(parent: QWidget, row: dict[str, Any]) -> bool:
    return RiskConfirmDialog.confirm(
        "确认删除气体类型",
        f"将删除气体类型：{row.get('name', '')}。已绑定探测器可能受影响；服务层会按引用关系拒绝。",
        parent,
        confirm_text="确认删除",
    )
