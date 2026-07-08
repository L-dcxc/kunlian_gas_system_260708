from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.services.device_config_service import PortCommand
from app.services.errors import ErrorCode
from app.ui.common.data_table import DataTable, TableColumn, TableState
from app.ui.common.dialogs import RiskConfirmDialog
from app.ui.common.errors import ErrorBanner, ValidationHint, controlled_error_text
from app.ui.common.status import repolish
from app.ui.settings.config_editor import build_config_editor

LOAD_FAILED_TEXT = "端口列表加载失败，请稍后重试。"
SAVE_FAILED_TEXT = "端口保存失败，请稍后重试。"


class PortsPage(QWidget):
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
        self._confirm_delete = confirm_delete or _confirm_delete_port
        self._rows: tuple[dict[str, Any], ...] = ()
        self._selected_id: int | None = None
        self._editing_id: int | None = None

        self.error_banner = ErrorBanner()
        self.error_banner.clear()
        self.table = DataTable(
            [
                TableColumn("id", "ID", 64, Qt.AlignmentFlag.AlignRight),
                TableColumn("name", "端口名称", 160),
                TableColumn("channel_label", "类型", 90),
                TableColumn("endpoint", "连接参数", 220),
                TableColumn("poll_interval_ms", "采集周期(ms)", 120, Qt.AlignmentFlag.AlignRight),
                TableColumn("timeout_ms", "超时(ms)", 100, Qt.AlignmentFlag.AlignRight),
                TableColumn("status_label", "状态", 80),
            ]
        )
        self.table.retryRequested.connect(self.reload)
        self.table.emptyActionRequested.connect(self.new_record)
        self.table.table.selectionModel().selectionChanged.connect(self._selection_changed)
        self.table.export_button.setVisible(False)

        self.form = QFrame()
        self.form.setProperty("panel", "true")
        self.name_edit = QLineEdit()
        self.name_edit.setMaxLength(80)
        self.channel_combo = QComboBox()
        self.channel_combo.addItem("串口 RS485", "serial")
        self.channel_combo.addItem("TCP RTU-over-TCP", "tcp")
        self.serial_port_edit = QLineEdit()
        self.serial_port_edit.setMaxLength(40)
        self.baud_spin = _spin(1200, 115200, 9600)
        self.data_bits_spin = _spin(5, 8, 8)
        self.parity_combo = QComboBox()
        for label, value in (("无校验", "N"), ("偶校验", "E"), ("奇校验", "O")):
            self.parity_combo.addItem(label, value)
        self.stop_bits_combo = QComboBox()
        for value in (1, 1.5, 2):
            self.stop_bits_combo.addItem(str(value), value)
        self.tcp_host_edit = QLineEdit()
        self.tcp_host_edit.setMaxLength(253)
        self.tcp_port_spin = _spin(1, 65535, 502)
        self.poll_spin = _spin(100, 600000, 1000)
        self.timeout_spin = _spin(100, 60000, 1500)
        self.failure_spin = _spin(1, 20, 3)
        self.reconnect_spin = _spin(500, 600000, 3000)
        self.enabled_check = QCheckBox("启用端口")
        self.enabled_check.setChecked(True)
        self.validation_hint = ValidationHint()
        self.validation_hint.clear()
        self.save_button = QPushButton("保存端口")
        self.save_button.setProperty("variant", "primary")
        self.delete_button = QPushButton("删除")
        self.delete_button.setProperty("variant", "danger")
        self.new_button = QPushButton("新增")
        self.save_button.clicked.connect(self.save_current)
        self.delete_button.clicked.connect(self.delete_selected)
        self.new_button.clicked.connect(self.new_record)
        self.channel_combo.currentIndexChanged.connect(self._sync_channel_fields)

        self._apply_field_widths()
        self._build_form()
        body = QVBoxLayout()
        body.setSpacing(12)
        body.addWidget(self.table, 3)
        body.addWidget(self.form, 2)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        layout.addWidget(self.error_banner)
        layout.addLayout(body, 1)
        self._apply_permission_state()
        self._sync_channel_fields()
        self._apply_selection_state()

    def reload(self) -> None:
        self.error_banner.clear()
        self.table.set_state(TableState.LOADING, "正在加载端口")
        try:
            rows = self._service.list_ports() if self._service is not None else ()
        except Exception:
            self._show_table_error(LOAD_FAILED_TEXT)
            return
        self._rows = tuple(dict(row) for row in rows)
        self.table.set_rows([_port_to_table(row) for row in self._rows])
        self.table.set_page(1, len(self._rows), max(1, len(self._rows) or 1))
        self.table.set_state(TableState.READY if self._rows else TableState.EMPTY, "暂无端口，请新增串口或 TCP 通道")
        self._apply_selection_state()

    def new_record(self) -> None:
        if not self._require_permission():
            return
        self._editing_id = None
        self._selected_id = None
        self.name_edit.clear()
        self.serial_port_edit.setText("COM1")
        self.tcp_host_edit.setText("127.0.0.1")
        self.enabled_check.setChecked(True)
        self.clear_validation()
        self._apply_selection_state()

    def save_current(self) -> None:
        if not self._require_permission() or not self.validate_form():
            return
        self.save_button.setEnabled(False)
        try:
            result = self._service.save_port(self._session, self._command())
        except Exception:
            self.error_banner.set_error(SAVE_FAILED_TEXT)
            self.save_button.setEnabled(True)
            return
        self.save_button.setEnabled(True)
        if bool(getattr(result, "success", False)):
            self.reload()
            self.configChanged.emit()
            return
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
            result = self._service.delete_port(self._session, self._selected_id)
        except Exception:
            self.error_banner.set_error(SAVE_FAILED_TEXT)
            return
        if bool(getattr(result, "success", False)):
            self.reload()
            self.configChanged.emit()
            return
        self.error_banner.set_error(getattr(result, "message", SAVE_FAILED_TEXT))

    def selected_row(self) -> dict[str, Any] | None:
        for row in self._rows:
            if int(row.get("id", 0)) == self._selected_id:
                return row
        return None

    def validate_form(self) -> bool:
        self.clear_validation()
        # Client checks make the form usable; service validation remains authoritative.
        if not self.name_edit.text().strip():
            return self._field_error(self.name_edit, "端口名称不能为空")
        if self.channel_combo.currentData() == "serial" and not self.serial_port_edit.text().strip():
            return self._field_error(self.serial_port_edit, "串口号不能为空")
        if self.channel_combo.currentData() == "tcp" and not self.tcp_host_edit.text().strip():
            return self._field_error(self.tcp_host_edit, "TCP 主机不能为空")
        if self.timeout_spin.value() > self.poll_spin.value() * 10:
            return self._field_error(self.timeout_spin, "超时不应远大于采集周期")
        return True

    def clear_validation(self) -> None:
        self.validation_hint.clear()
        for widget in (self.name_edit, self.serial_port_edit, self.tcp_host_edit, self.timeout_spin):
            widget.setProperty("validation", None)
            repolish(widget)

    def _command(self) -> PortCommand:
        return PortCommand(
            id=self._editing_id,
            name=self.name_edit.text(),
            channel_type=str(self.channel_combo.currentData()),
            serial_port_name=self.serial_port_edit.text() or None,
            baud_rate=self.baud_spin.value(),
            data_bits=self.data_bits_spin.value(),
            parity=str(self.parity_combo.currentData()),
            stop_bits=float(self.stop_bits_combo.currentData()),
            tcp_host=self.tcp_host_edit.text() or None,
            tcp_port=self.tcp_port_spin.value(),
            poll_interval_ms=self.poll_spin.value(),
            timeout_ms=self.timeout_spin.value(),
            failure_threshold=self.failure_spin.value(),
            reconnect_interval_ms=self.reconnect_spin.value(),
            is_enabled=self.enabled_check.isChecked(),
        )

    def _build_form(self) -> None:
        _fields_panel, grid = build_config_editor(
            self.form,
            "编辑端口配置",
            (self.new_button, self.save_button, self.delete_button),
            fields_width=760,
        )
        self.form_grid = grid
        fields = [
            ("端口名称", self.name_edit), ("类型", self.channel_combo), ("串口号", self.serial_port_edit),
            ("波特率", self.baud_spin), ("数据位", self.data_bits_spin), ("校验", self.parity_combo),
            ("停止位", self.stop_bits_combo), ("TCP 主机", self.tcp_host_edit), ("TCP 端口", self.tcp_port_spin),
            ("采集周期", self.poll_spin), ("超时", self.timeout_spin), ("失败阈值", self.failure_spin),
            ("重连间隔", self.reconnect_spin), ("", self.enabled_check),
        ]
        for index, (label, widget) in enumerate(fields):
            row = index // 2
            column = 0 if index % 2 == 0 else 2
            if label:
                label_widget = QLabel(label)
                label_widget.setProperty("role", "fieldLabel")
                grid.addWidget(label_widget, row, column)
            grid.addWidget(widget, row, column + 1, alignment=Qt.AlignmentFlag.AlignLeft)
        hint_row = (len(fields) + 1) // 2
        grid.addWidget(self.validation_hint, hint_row, 0, 1, 4)
        grid.setColumnMinimumWidth(0, 82)
        grid.setColumnMinimumWidth(2, 82)

    def _apply_field_widths(self) -> None:
        for widget in (self.name_edit, self.serial_port_edit, self.tcp_host_edit):
            widget.setMaximumWidth(260)
        for widget in (self.channel_combo, self.parity_combo, self.stop_bits_combo):
            widget.setMaximumWidth(160)
        for widget in (
            self.baud_spin,
            self.data_bits_spin,
            self.tcp_port_spin,
            self.poll_spin,
            self.timeout_spin,
            self.failure_spin,
            self.reconnect_spin,
        ):
            widget.setMaximumWidth(140)

    def _selection_changed(self) -> None:
        indexes = self.table.table.selectionModel().selectedRows()
        if not indexes:
            self._selected_id = None
            self._editing_id = None
        else:
            row_index = indexes[0].row()
            row = self._rows[row_index] if 0 <= row_index < len(self._rows) else None
            self._selected_id = int(row["id"]) if row else None
            self._editing_id = self._selected_id
            if row:
                self._apply_row(row)
        self._apply_selection_state()

    def _apply_row(self, row: dict[str, Any]) -> None:
        self.name_edit.setText(str(row.get("name", "")))
        self.channel_combo.setCurrentIndex(self.channel_combo.findData(row.get("channel_type", "serial")))
        self.serial_port_edit.setText(str(row.get("serial_port_name") or ""))
        self.tcp_host_edit.setText(str(row.get("tcp_host") or ""))
        _set_spin(self.baud_spin, row.get("baud_rate"), 9600)
        _set_spin(self.data_bits_spin, row.get("data_bits"), 8)
        _set_combo(self.parity_combo, row.get("parity", "N"))
        _set_combo(self.stop_bits_combo, row.get("stop_bits", 1))
        _set_spin(self.tcp_port_spin, row.get("tcp_port"), 502)
        _set_spin(self.poll_spin, row.get("poll_interval_ms"), 1000)
        _set_spin(self.timeout_spin, row.get("timeout_ms"), 1500)
        _set_spin(self.failure_spin, row.get("failure_threshold"), 3)
        _set_spin(self.reconnect_spin, row.get("reconnect_interval_ms"), 3000)
        self.enabled_check.setChecked(bool(row.get("is_enabled", True)))

    def _sync_channel_fields(self) -> None:
        serial = self.channel_combo.currentData() == "serial"
        for widget in (self.serial_port_edit, self.baud_spin, self.data_bits_spin, self.parity_combo, self.stop_bits_combo):
            widget.setEnabled(serial and self._can_configure)
        for widget in (self.tcp_host_edit, self.tcp_port_spin):
            widget.setEnabled((not serial) and self._can_configure)

    def _field_error(self, widget: QWidget, message: str) -> bool:
        widget.setProperty("validation", "error")
        repolish(widget)
        self.validation_hint.set_validation_error(message)
        return False

    def _require_permission(self) -> bool:
        if self._can_configure:
            return True
        self.error_banner.show_permission_denied()
        return False

    def _apply_permission_state(self) -> None:
        for widget in (self.name_edit, self.channel_combo, self.enabled_check, self.save_button, self.new_button, self.delete_button):
            widget.setEnabled(self._can_configure)
        self._sync_channel_fields()

    def _apply_selection_state(self) -> None:
        self.delete_button.setEnabled(self._can_configure and self._selected_id is not None)

    def _show_table_error(self, message: object) -> None:
        self.table.set_rows([])
        self.table.set_page(1, 0, 1)
        self.table.set_state(TableState.ERROR, controlled_error_text(message, fallback=LOAD_FAILED_TEXT))


def _port_to_table(row: dict[str, Any]) -> dict[str, Any]:
    serial = row.get("channel_type") == "serial"
    endpoint = row.get("serial_port_name") if serial else f"{row.get('tcp_host') or ''}:{row.get('tcp_port') or ''}"
    return {**row, "channel_label": "串口" if serial else "TCP", "endpoint": endpoint, "status_label": "启用" if row.get("is_enabled", True) else "停用"}


def _spin(minimum: int, maximum: int, value: int) -> QSpinBox:
    spin = QSpinBox()
    spin.setRange(minimum, maximum)
    spin.setValue(value)
    return spin


def _set_spin(spin: QSpinBox, value: object, default: int) -> None:
    spin.setValue(default if value in {None, ""} else int(value))


def _set_combo(combo: QComboBox, value: object) -> None:
    index = combo.findData(value)
    if index >= 0:
        combo.setCurrentIndex(index)


def _confirm_delete_port(parent: QWidget, row: dict[str, Any]) -> bool:
    return RiskConfirmDialog.confirm(
        "确认删除端口",
        f"将删除端口：{row.get('name', '')}。已配置探测器可能受影响；服务层会按引用关系拒绝。",
        parent,
        confirm_text="确认删除",
    )
