from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QCheckBox, QComboBox, QFrame, QLabel, QLineEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget

from app.services.device_config_service import DetectorCommand
from app.services.errors import ErrorCode
from app.ui.common.data_table import DataTable, TableColumn, TableState
from app.ui.common.dialogs import RiskConfirmDialog
from app.ui.common.errors import ErrorBanner, ValidationHint, controlled_error_text
from app.ui.common.status import repolish
from app.ui.settings.config_editor import build_config_editor

LOAD_FAILED_TEXT = "探测器列表加载失败，请稍后重试。"
SAVE_FAILED_TEXT = "探测器保存失败，请稍后重试。"


class DetectorsPage(QWidget):
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
        self._confirm_delete = confirm_delete or _confirm_delete_detector
        self._rows: tuple[dict[str, Any], ...] = ()
        self._ports: tuple[dict[str, Any], ...] = ()
        self._controllers: tuple[dict[str, Any], ...] = ()
        self._gas_types: tuple[dict[str, Any], ...] = ()
        self._selected_id: int | None = None
        self._editing_id: int | None = None

        self.error_banner = ErrorBanner(); self.error_banner.clear()
        self.table = DataTable([
            TableColumn("id", "ID", 58, Qt.AlignmentFlag.AlignRight),
            TableColumn("position_code", "位号", 110),
            TableColumn("name", "探测器", 150),
            TableColumn("port_label", "端口", 120),
            TableColumn("controller_label", "控制器", 120),
            TableColumn("protocol_address", "地址", 70, Qt.AlignmentFlag.AlignRight),
            TableColumn("gas_label", "气体", 120),
            TableColumn("range_label", "量程", 130),
            TableColumn("alarm_label", "阈值", 130),
            TableColumn("status_label", "状态", 70),
        ])
        self.table.retryRequested.connect(self.reload)
        self.table.emptyActionRequested.connect(self.new_record)
        self.table.table.selectionModel().selectionChanged.connect(self._selection_changed)
        self.table.export_button.setVisible(False)

        self.form = QFrame(); self.form.setProperty("panel", "true")
        self.port_combo = QComboBox(); self.controller_combo = QComboBox(); self.gas_combo = QComboBox()
        self.position_edit = QLineEdit(); self.position_edit.setMaxLength(80)
        self.name_edit = QLineEdit(); self.name_edit.setMaxLength(80)
        self.address_spin = _spin(1, 247, 1); self.register_spin = _spin(0, 65535, 0)
        self.unit_edit = QLineEdit(); self.unit_edit.setMaxLength(32)
        self.range_min_edit = QLineEdit("0"); self.range_max_edit = QLineEdit("100")
        self.alarm_low_edit = QLineEdit("20"); self.alarm_high_edit = QLineEdit("50")
        self.model_edit = QLineEdit(); self.model_edit.setMaxLength(80)
        self.alarm_type_combo = QComboBox()
        for label, value in (("无报警", "none"), ("低报", "low"), ("高报", "high"), ("低报+高报", "low_high")):
            self.alarm_type_combo.addItem(label, value)
        self.sound_check = QCheckBox("声音报警"); self.sound_check.setChecked(True)
        self.store_interval_spin = _spin(1, 86400, 60)
        self.sensor_life_edit = QLineEdit(); self.sensor_life_edit.setPlaceholderText("YYYY-MM-DD，可空")
        self.calibration_cycle_spin = _spin(0, 3650, 365)
        self.enabled_check = QCheckBox("启用探测器"); self.enabled_check.setChecked(True)
        self.validation_hint = ValidationHint(); self.validation_hint.clear()
        self.new_button = QPushButton("新增")
        self.save_button = QPushButton("保存探测器"); self.save_button.setProperty("variant", "primary")
        self.delete_button = QPushButton("删除"); self.delete_button.setProperty("variant", "danger")
        self.new_button.clicked.connect(self.new_record); self.save_button.clicked.connect(self.save_current); self.delete_button.clicked.connect(self.delete_selected)
        self.port_combo.currentIndexChanged.connect(self._filter_controller_combo)
        self.gas_combo.currentIndexChanged.connect(self._apply_selected_gas_defaults)
        self._apply_field_widths()
        self._build_form()

        body = QVBoxLayout(); body.setSpacing(12); body.addWidget(self.table, 3); body.addWidget(self.form, 2)
        layout = QVBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(12)
        layout.addWidget(self.error_banner); layout.addLayout(body, 1)
        self._apply_permission_state(); self._apply_selection_state()

    def reload(self) -> None:
        self.error_banner.clear(); self.table.set_state(TableState.LOADING, "正在加载探测器")
        try:
            self._ports = tuple(dict(row) for row in (self._service.list_ports() if self._service else ()))
            self._controllers = tuple(dict(row) for row in (self._service.list_controllers() if self._service else ()))
            self._gas_types = tuple(dict(row) for row in (self._service.list_gas_types() if self._service else ()))
            self._rows = tuple(dict(row) for row in (self._service.list_detectors() if self._service else ()))
        except Exception:
            self._show_table_error(LOAD_FAILED_TEXT); return
        self._reload_refs()
        self.table.set_rows([_detector_to_table(row, self._ports, self._controllers, self._gas_types) for row in self._rows])
        self.table.set_page(1, len(self._rows), max(1, len(self._rows) or 1))
        self.table.set_state(TableState.READY if self._rows else TableState.EMPTY, "暂无探测器，请先新增探测器")
        self._apply_selection_state()

    def new_record(self) -> None:
        if not self._require_permission():
            return
        self._editing_id = None; self._selected_id = None
        for editor in (self.position_edit, self.name_edit, self.model_edit, self.sensor_life_edit):
            editor.clear()
        self.address_spin.setValue(1); self.register_spin.setValue(0); self.unit_edit.setText("%LEL")
        self.range_min_edit.setText("0"); self.range_max_edit.setText("100"); self.alarm_low_edit.setText("20"); self.alarm_high_edit.setText("50")
        self.sound_check.setChecked(True); self.store_interval_spin.setValue(60); self.calibration_cycle_spin.setValue(365); self.enabled_check.setChecked(True)
        self.clear_validation(); self._apply_selection_state()

    def save_current(self) -> None:
        if not self._require_permission() or not self.validate_form():
            return
        try:
            result = self._service.save_detector(self._session, self._command())
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
            result = self._service.delete_detector(self._session, self._selected_id)
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
        if self.gas_combo.currentData() is None:
            return self._field_error(self.gas_combo, "必须选择气体类型")
        if not self.position_edit.text().strip():
            return self._field_error(self.position_edit, "位号不能为空")
        if not self.name_edit.text().strip():
            return self._field_error(self.name_edit, "探测器名称不能为空")
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
        life = self.sensor_life_edit.text().strip()
        if life and (len(life) < 10 or life[4:5] != "-" or life[7:8] != "-"):
            return self._field_error(self.sensor_life_edit, "传感器寿命日期格式应为 YYYY-MM-DD")
        return True

    def clear_validation(self) -> None:
        self.validation_hint.clear()
        for widget in (self.port_combo, self.gas_combo, self.position_edit, self.name_edit, self.unit_edit, self.range_min_edit, self.range_max_edit, self.alarm_low_edit, self.alarm_high_edit, self.sensor_life_edit):
            widget.setProperty("validation", None); repolish(widget)

    def _command(self) -> DetectorCommand:
        calibration = self.calibration_cycle_spin.value() or None
        return DetectorCommand(
            id=self._editing_id,
            port_id=int(self.port_combo.currentData()),
            controller_id=self.controller_combo.currentData(),
            position_code=self.position_edit.text(),
            name=self.name_edit.text(),
            protocol_address=self.address_spin.value(),
            register_index=self.register_spin.value(),
            gas_type_id=int(self.gas_combo.currentData()),
            unit=self.unit_edit.text(),
            range_min=float(self.range_min_edit.text()),
            range_max=float(self.range_max_edit.text()),
            model=self.model_edit.text() or None,
            alarm_low=_optional_float(self.alarm_low_edit.text()),
            alarm_high=_optional_float(self.alarm_high_edit.text()),
            alarm_type=str(self.alarm_type_combo.currentData()),
            sound_enabled=self.sound_check.isChecked(),
            store_interval_sec=self.store_interval_spin.value(),
            sensor_life_until=self.sensor_life_edit.text().strip() or None,
            calibration_cycle_days=calibration,
            is_enabled=self.enabled_check.isChecked(),
        )

    def _reload_refs(self) -> None:
        _fill_combo(self.port_combo, self._ports, "请选择端口")
        _fill_combo(self.gas_combo, self._gas_types, "请选择气体类型")
        self._filter_controller_combo()

    def _filter_controller_combo(self) -> None:
        current = self.controller_combo.currentData(); port_id = self.port_combo.currentData()
        self.controller_combo.clear(); self.controller_combo.addItem("无控制器", None)
        for row in self._controllers:
            if port_id is None or int(row.get("port_id", 0)) == int(port_id):
                self.controller_combo.addItem(str(row.get("name", f"控制器 {row.get('id')}")), int(row.get("id", 0)))
        _set_combo(self.controller_combo, current)

    def _apply_selected_gas_defaults(self) -> None:
        gas = _find(self._gas_types, self.gas_combo.currentData())
        if gas is None:
            return
        self.unit_edit.setText(str(gas.get("unit") or ""))
        self.range_min_edit.setText(_compact_number(gas.get("range_min"), "0"))
        self.range_max_edit.setText(_compact_number(gas.get("range_max"), "100"))
        self.alarm_low_edit.setText(_compact_number(gas.get("default_alarm_low"), ""))
        self.alarm_high_edit.setText(_compact_number(gas.get("default_alarm_high"), ""))

    def _apply_field_widths(self) -> None:
        for widget in (self.port_combo, self.controller_combo, self.gas_combo, self.name_edit, self.model_edit):
            widget.setMaximumWidth(260)
        for widget in (self.position_edit, self.sensor_life_edit):
            widget.setMaximumWidth(180)
        for widget in (
            self.address_spin,
            self.register_spin,
            self.unit_edit,
            self.range_min_edit,
            self.range_max_edit,
            self.alarm_low_edit,
            self.alarm_high_edit,
            self.alarm_type_combo,
            self.store_interval_spin,
            self.calibration_cycle_spin,
        ):
            widget.setMaximumWidth(140)

    def _build_form(self) -> None:
        _fields_panel, grid = build_config_editor(
            self.form,
            "编辑探测器配置",
            (self.new_button, self.save_button, self.delete_button),
            fields_width=980,
        )
        self.form_grid = grid
        fields = (
            ("端口", self.port_combo), ("控制器", self.controller_combo), ("位号", self.position_edit),
            ("名称", self.name_edit), ("设备地址", self.address_spin), ("寄存器索引", self.register_spin),
            ("气体类型", self.gas_combo), ("单位", self.unit_edit), ("报警类型", self.alarm_type_combo),
            ("量程下限", self.range_min_edit), ("量程上限", self.range_max_edit), ("低报阈值", self.alarm_low_edit),
            ("高报阈值", self.alarm_high_edit), ("存储周期(s)", self.store_interval_spin), ("校验周期(天)", self.calibration_cycle_spin),
            ("型号", self.model_edit), ("传感器寿命", self.sensor_life_edit), ("", self.sound_check),
            ("", self.enabled_check),
        )
        for index, (label, widget) in enumerate(fields):
            row = index // 3
            column = (index % 3) * 2
            if label:
                label_widget = QLabel(label); label_widget.setProperty("role", "fieldLabel"); grid.addWidget(label_widget, row, column)
            grid.addWidget(widget, row, column + 1, alignment=Qt.AlignmentFlag.AlignLeft)
        hint_row = (len(fields) + 2) // 3
        grid.addWidget(self.validation_hint, hint_row, 0, 1, 6)
        grid.setColumnMinimumWidth(0, 82); grid.setColumnMinimumWidth(2, 82); grid.setColumnMinimumWidth(4, 82)

    def _selection_changed(self) -> None:
        indexes = self.table.table.selectionModel().selectedRows()
        row = self._rows[indexes[0].row()] if indexes and 0 <= indexes[0].row() < len(self._rows) else None
        self._selected_id = int(row["id"]) if row else None; self._editing_id = self._selected_id
        if row:
            _set_combo(self.port_combo, row.get("port_id")); self._filter_controller_combo(); _set_combo(self.controller_combo, row.get("controller_id")); _set_combo(self.gas_combo, row.get("gas_type_id"))
            self.position_edit.setText(str(row.get("position_code", ""))); self.name_edit.setText(str(row.get("name", "")))
            self.address_spin.setValue(int(row.get("protocol_address", 1))); self.register_spin.setValue(int(row.get("register_index", 0)))
            self.unit_edit.setText(str(row.get("unit", ""))); self.range_min_edit.setText(str(row.get("range_min", ""))); self.range_max_edit.setText(str(row.get("range_max", "")))
            self.alarm_low_edit.setText("" if row.get("alarm_low") is None else str(row.get("alarm_low"))); self.alarm_high_edit.setText("" if row.get("alarm_high") is None else str(row.get("alarm_high")))
            self.model_edit.setText(str(row.get("model") or "")); _set_combo(self.alarm_type_combo, row.get("alarm_type", "low_high")); self.sound_check.setChecked(bool(row.get("sound_enabled", True)))
            self.store_interval_spin.setValue(int(row.get("store_interval_sec", 60))); self.sensor_life_edit.setText(str(row.get("sensor_life_until") or "")); self.calibration_cycle_spin.setValue(int(row.get("calibration_cycle_days") or 0)); self.enabled_check.setChecked(bool(row.get("is_enabled", True)))
        self._apply_selection_state()

    def _field_error(self, widget: QWidget, message: str) -> bool:
        widget.setProperty("validation", "error"); repolish(widget); self.validation_hint.set_validation_error(message); return False

    def _require_permission(self) -> bool:
        if self._can_configure:
            return True
        self.error_banner.show_permission_denied(); return False

    def _apply_permission_state(self) -> None:
        for widget in (self.port_combo, self.controller_combo, self.gas_combo, self.position_edit, self.name_edit, self.address_spin, self.register_spin, self.unit_edit, self.range_min_edit, self.range_max_edit, self.alarm_low_edit, self.alarm_high_edit, self.model_edit, self.alarm_type_combo, self.sound_check, self.store_interval_spin, self.sensor_life_edit, self.calibration_cycle_spin, self.enabled_check, self.new_button, self.save_button, self.delete_button):
            widget.setEnabled(self._can_configure)

    def _apply_selection_state(self) -> None:
        self.delete_button.setEnabled(self._can_configure and self._selected_id is not None)

    def _show_table_error(self, message: object) -> None:
        self.table.set_rows([]); self.table.set_page(1, 0, 1); self.table.set_state(TableState.ERROR, controlled_error_text(message, fallback=LOAD_FAILED_TEXT))


def _detector_to_table(row: dict[str, Any], ports: tuple[dict[str, Any], ...], controllers: tuple[dict[str, Any], ...], gas_types: tuple[dict[str, Any], ...]) -> dict[str, Any]:
    port = _find(ports, row.get("port_id")); controller = _find(controllers, row.get("controller_id")); gas = _find(gas_types, row.get("gas_type_id"))
    return {**row, "port_label": port.get("name", "") if port else row.get("port_id", ""), "controller_label": controller.get("name", "") if controller else "-", "gas_label": gas.get("name", "") if gas else row.get("gas_type_id", ""), "range_label": f"{row.get('range_min', '')}-{row.get('range_max', '')} {row.get('unit', '')}", "alarm_label": f"低 {row.get('alarm_low', '')} / 高 {row.get('alarm_high', '')}", "status_label": "启用" if row.get("is_enabled", True) else "停用"}


def _find(rows: tuple[dict[str, Any], ...], row_id: object) -> dict[str, Any] | None:
    return next((row for row in rows if row_id is not None and int(row.get("id", 0)) == int(row_id)), None)


def _fill_combo(combo: QComboBox, rows: tuple[dict[str, Any], ...], empty: str) -> None:
    current = combo.currentData(); combo.clear(); combo.addItem(empty, None)
    for row in rows:
        combo.addItem(str(row.get("name", f"ID {row.get('id')}")), int(row.get("id", 0)))
    _set_combo(combo, current)


def _set_combo(combo: QComboBox, value: object) -> None:
    index = combo.findData(value)
    if index < 0:
        try:
            index = combo.findData(int(value))
        except (TypeError, ValueError):
            index = -1
    if index >= 0:
        combo.setCurrentIndex(index)


def _spin(minimum: int, maximum: int, value: int) -> QSpinBox:
    spin = QSpinBox(); spin.setRange(minimum, maximum); spin.setValue(value); return spin


def _optional_float(text: str) -> float | None:
    value = text.strip()
    return None if value == "" else float(value)


def _compact_number(value: object, default: str) -> str:
    if value in {None, ""}:
        return default
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return str(value)
    if numeric.is_integer():
        return str(int(numeric))
    return str(numeric)


def _confirm_delete_detector(parent: QWidget, row: dict[str, Any]) -> bool:
    return RiskConfirmDialog.confirm(
        "确认删除探测器",
        f"将删除探测器：{row.get('position_code', '')} / {row.get('name', '')}。地图点位、报警规则和历史查询可能受影响。",
        parent,
        confirm_text="确认删除",
    )
