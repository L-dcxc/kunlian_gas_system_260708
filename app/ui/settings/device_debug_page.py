from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.device.debug.debug_service import DebugFrameResult, DebugReadCommand
from app.services.errors import ErrorCode
from app.services.models import DeviceReading, DeviceSourceType, ProtocolMode
from app.services.permissions import Permission, role_has_permission
from app.ui.common.errors import ErrorBanner, ValidationHint, controlled_error_text
from app.ui.common.hex_viewer import HexViewer
from app.ui.common.permission_hint import PermissionHint
from app.ui.common.safe_text import SafeTextLabel
from app.ui.common.status import repolish
from app.ui.settings.frame_log_list import FrameLogList

READ_FUNCTION_CODE = 0x03
SEND_FAILED_TEXT = "设备调试发送失败，请检查端口、地址或接线。"
SERVICE_NOT_READY_TEXT = "设备调试发送服务未配置，请联系管理员。"

DebugSender = Callable[[DebugReadCommand], object]


class DeviceDebugPage(QWidget):
    requestGenerated = Signal(object)
    readRequested = Signal(object)

    def __init__(
        self,
        debug_service: object | None = None,
        device_config_service: object | None = None,
        session: object | None = None,
        parent: QWidget | None = None,
        *,
        can_debug: bool | None = None,
        send_executor: DebugSender | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = debug_service
        self._config_service = device_config_service
        self._session = session
        self._send_executor = send_executor
        self._can_debug = _can_debug_from_session(session) if can_debug is None else can_debug
        self._current_result: DebugFrameResult | None = None
        self._sending = False

        self.error_banner = ErrorBanner(); self.error_banner.clear()
        self.permission_hint = PermissionHint(); self.permission_hint.setVisible(not self._can_debug)
        self.validation_hint = ValidationHint(); self.validation_hint.clear()

        self.port_combo = QComboBox()
        self.protocol_combo = QComboBox(); self.protocol_combo.addItem("协议 1", ProtocolMode.PROTOCOL_1.value); self.protocol_combo.addItem("协议 2", ProtocolMode.PROTOCOL_2.value)
        self.source_combo = QComboBox(); self.source_combo.addItem("探头", DeviceSourceType.PROBE.value); self.source_combo.addItem("控制器", DeviceSourceType.CONTROLLER.value)
        self.address_spin = _spin(1, 255, 1)
        self.start_register_spin = _spin(0, 65535, 0)
        self.register_count_spin = _spin(1, 124, 4)
        self.timeout_spin = _spin(100, 60000, 1500)
        self.function_combo = QComboBox(); self.function_combo.addItem("03 读保持寄存器", READ_FUNCTION_CODE)
        self.function_combo.setEnabled(False)
        self.generate_button = QPushButton("生成请求"); self.generate_button.clicked.connect(self.generate_request)
        self.send_button = QPushButton("发送读取"); self.send_button.setProperty("variant", "primary"); self.send_button.clicked.connect(self.send_read)
        self.clear_button = QPushButton("清空显示"); self.clear_button.clicked.connect(self.clear_result)

        self.send_hex = HexViewer()
        self.recv_hex = HexViewer()
        self.result_badge = SafeTextLabel("未发送", selectable=False); self.result_badge.setObjectName("DebugResultBadge"); self.result_badge.setProperty("debugResult", "idle")
        self.crc_label = _value_label("未校验")
        self.length_label = _value_label("-")
        self.address_label = _value_label("-")
        self.function_label = _value_label("-")
        self.parse_status_label = _value_label("未解析")
        self.concentration_label = _value_label("-")
        self.unit_label = _value_label("-")
        self.error_reason_label = _value_label("-")
        self.frame_log = FrameLogList()

        self._build_layout()
        self._apply_permission_state()
        self.reload_ports()

    def reload_ports(self) -> None:
        self.port_combo.clear()
        ports = _safe_call_list(self._config_service, "list_ports")
        for row in ports:
            if bool(row.get("is_enabled", True)):
                label = _port_label(row)
                self.port_combo.addItem(label, int(row.get("id", 0) or 0))
        if self.port_combo.count() == 0:
            self.port_combo.addItem("未配置端口", 0)

    def generate_request(self) -> None:
        if not self._require_permission() or not self.validate_inputs():
            return
        command = self._command()
        result = self._call_build(command)
        if not self._handle_service_result(result):
            return
        self._show_result(result.data)
        self.frame_log.append_result(result.data)
        self.requestGenerated.emit(result.data)

    def send_read(self) -> None:
        if not self._require_permission() or not self.validate_inputs():
            return
        command = self._command()
        self.set_sending(True)
        self.readRequested.emit(command)
        try:
            result = self._send(command)
        except Exception:
            self.error_banner.set_error(SEND_FAILED_TEXT)
            self.set_sending(False)
            return
        self.set_sending(False)
        if not self._handle_service_result(result):
            return
        self._show_result(result.data)
        self.frame_log.append_result(result.data)

    def set_sending(self, sending: bool) -> None:
        self._sending = sending
        self.send_button.setDisabled(sending or not self._can_debug)
        self.generate_button.setDisabled(sending or not self._can_debug)
        self.send_button.setText("发送中..." if sending else "发送读取")
        if sending:
            self._set_result_state("waiting", "等待响应")

    def validate_inputs(self) -> bool:
        self.clear_validation()
        if int(self.port_combo.currentData() or 0) <= 0:
            return self._field_error(self.port_combo, "必须选择已配置端口")
        if self.address_spin.value() == 0:
            return self._field_error(self.address_spin, "调试地址不能使用广播地址 0")
        if self.start_register_spin.value() + self.register_count_spin.value() > 65536:
            return self._field_error(self.register_count_spin, "寄存器范围超出 0-65535")
        if self.protocol_combo.currentData() == ProtocolMode.PROTOCOL_2.value:
            if self.start_register_spin.value() % 4 != 0 or self.register_count_spin.value() % 4 != 0:
                return self._field_error(self.register_count_spin, "协议 2 读取需按 4 个寄存器对齐")
        if self.protocol_combo.currentData() == ProtocolMode.PROTOCOL_1.value and self.source_combo.currentData() == DeviceSourceType.CONTROLLER.value:
            if self.start_register_spin.value() % 2 != 0 or self.register_count_spin.value() % 2 != 0 or self.register_count_spin.value() > 20:
                return self._field_error(self.register_count_spin, "协议 1 控制器读取需偶数且不超过 20 个寄存器")
        return True

    def clear_validation(self) -> None:
        self.validation_hint.clear()
        for widget in (self.port_combo, self.address_spin, self.start_register_spin, self.register_count_spin):
            widget.setProperty("validation", None); repolish(widget)

    def clear_result(self) -> None:
        self.error_banner.clear()
        self.send_hex.set_hex_text("")
        self.recv_hex.set_hex_text("")
        self._set_result_state("idle", "未发送")
        for label, value in self._parse_labels().items():
            label.set_safe_text("未校验" if value == "crc" else "-")
        self._current_result = None

    def current_result(self) -> DebugFrameResult | None:
        return self._current_result

    def _build_layout(self) -> None:
        header = QFrame(); header.setProperty("panel", "true")
        title = SafeTextLabel("设备调试", selectable=False); title.setProperty("role", "panelTitle")
        subtitle = SafeTextLabel("手动生成并发送只读 Modbus 03 请求，用于现场通讯诊断。", selectable=False); subtitle.setProperty("role", "muted")
        header_layout = QVBoxLayout(header); header_layout.setContentsMargins(16, 16, 16, 16); header_layout.setSpacing(8)
        header_layout.addWidget(title); header_layout.addWidget(subtitle); header_layout.addWidget(self.permission_hint)

        form = QFrame(); form.setProperty("panel", "true")
        grid = QGridLayout(form); grid.setContentsMargins(16, 16, 16, 16); grid.setHorizontalSpacing(12); grid.setVerticalSpacing(8)
        for row, (label, widget) in enumerate((
            ("端口", self.port_combo), ("协议", self.protocol_combo), ("对象", self.source_combo),
            ("设备地址", self.address_spin), ("功能码", self.function_combo), ("起始寄存器", self.start_register_spin),
            ("寄存器数量", self.register_count_spin), ("超时(ms)", self.timeout_spin),
        )):
            grid.addWidget(_field_label(label), row // 2, (row % 2) * 2)
            grid.addWidget(widget, row // 2, (row % 2) * 2 + 1)
        grid.addWidget(self.validation_hint, 4, 1, 1, 3)
        actions = QHBoxLayout(); actions.addWidget(self.generate_button); actions.addWidget(self.send_button); actions.addWidget(self.clear_button); actions.addStretch(1)
        grid.addLayout(actions, 5, 0, 1, 4)

        hex_panel = QFrame(); hex_panel.setProperty("panel", "true")
        hex_layout = QGridLayout(hex_panel); hex_layout.setContentsMargins(16, 16, 16, 16); hex_layout.setHorizontalSpacing(12); hex_layout.setVerticalSpacing(8)
        hex_layout.addWidget(_field_label("发送 HEX"), 0, 0); hex_layout.addWidget(_field_label("返回 HEX"), 0, 1)
        hex_layout.addWidget(self.send_hex, 1, 0); hex_layout.addWidget(self.recv_hex, 1, 1)

        parse_panel = QFrame(); parse_panel.setObjectName("ParsePanel"); parse_panel.setProperty("panel", "true")
        parse_grid = QGridLayout(parse_panel); parse_grid.setContentsMargins(16, 16, 16, 16); parse_grid.setHorizontalSpacing(12); parse_grid.setVerticalSpacing(8)
        parse_grid.addWidget(_field_label("结果"), 0, 0); parse_grid.addWidget(self.result_badge, 0, 1)
        for index, (label, widget) in enumerate((
            ("CRC", self.crc_label), ("长度", self.length_label), ("地址", self.address_label), ("功能码", self.function_label),
            ("解析状态", self.parse_status_label), ("浓度", self.concentration_label), ("单位", self.unit_label), ("错误原因", self.error_reason_label),
        ), start=1):
            parse_grid.addWidget(_field_label(label), index, 0); parse_grid.addWidget(widget, index, 1)

        left = QVBoxLayout(); left.addWidget(form); left.addWidget(hex_panel, 1); left.addWidget(parse_panel)
        body = QHBoxLayout(); body.setSpacing(12); body.addLayout(left, 3); body.addWidget(self.frame_log, 2)
        layout = QVBoxLayout(self); layout.setContentsMargins(16, 16, 16, 16); layout.setSpacing(12)
        layout.addWidget(self.error_banner); layout.addWidget(header); layout.addLayout(body, 1)

    def _command(self) -> DebugReadCommand:
        return DebugReadCommand(
            source_type=str(self.source_combo.currentData()),
            port_id=int(self.port_combo.currentData() or 0),
            unit_address=self.address_spin.value(),
            start_register=self.start_register_spin.value(),
            register_count=self.register_count_spin.value(),
            mode=str(self.protocol_combo.currentData()),
            function_code=READ_FUNCTION_CODE,
            timeout_ms=self.timeout_spin.value(),
            label=self.port_combo.currentText(),
        )

    def _send(self, command: DebugReadCommand) -> object:
        if self._send_executor is not None:
            return self._send_executor(command)
        method = getattr(self._service, "send_debug_read", None)
        if method is None:
            return self._diagnostic_result(command, SERVICE_NOT_READY_TEXT)
        return method(self._session, command)

    def _call_build(self, command: DebugReadCommand) -> object:
        method = getattr(self._service, "build_read_request", None)
        if method is None:
            return self._diagnostic_result(command, "设备调试请求生成服务未配置。")
        return method(command, self._session)

    def _diagnostic_result(self, command: DebugReadCommand, message: str) -> object:
        method = getattr(self._service, "build_read_request", None)
        built = method(command, self._session) if method is not None else None
        if built is not None and bool(getattr(built, "success", False)) and getattr(built, "data", None) is not None:
            data = DebugFrameResult(request_hex=getattr(built.data, "request_hex", ""), validation_message=message, error_code="service_not_ready")
            return _UiResult(True, 0, message, data)
        return _UiResult(False, int(ErrorCode.SERVICE_UNAVAILABLE), message, None)

    def _handle_service_result(self, result: object) -> bool:
        if not bool(getattr(result, "success", False)):
            code = int(getattr(result, "code", 0) or 0)
            if code == int(ErrorCode.PERMISSION_DENIED):
                self.error_banner.show_permission_denied()
            else:
                self.error_banner.set_error(getattr(result, "message", SEND_FAILED_TEXT))
            return False
        data = getattr(result, "data", None)
        if data is None:
            self.error_banner.set_error(SEND_FAILED_TEXT)
            return False
        self.error_banner.clear()
        return True

    def _show_result(self, result: DebugFrameResult) -> None:
        self._current_result = result
        self.send_hex.set_hex_text(getattr(result, "request_hex", ""))
        self.recv_hex.set_hex_text(getattr(result, "response_hex", "") or "")
        exchange = getattr(result, "exchange", None)
        crc_ok = getattr(result, "crc_ok", None)
        self._set_result_state("ok" if _result_has_valid_reading(result) else ("error" if crc_ok is False or getattr(result, "error_code", None) else "warning"), _status_text(result))
        self.crc_label.set_safe_text(_crc_text(result))
        self.length_label.set_safe_text(_frame_length_text(exchange, result))
        self.address_label.set_safe_text(_address_text(exchange))
        self.function_label.set_safe_text(_function_text(exchange))
        self.parse_status_label.set_safe_text(_parse_status_text(exchange, result))
        reading = _first_reading(result)
        # UI 只展示服务层已解析的 DTO/readings；不在此处解析寄存器或校验 CRC。
        self.concentration_label.set_safe_text("-" if reading is None or reading.concentration is None else f"{reading.concentration:g}")
        self.unit_label.set_safe_text("-" if reading is None or reading.unit is None else reading.unit)
        self.error_reason_label.set_safe_text(_error_reason(exchange, result) or "-")

    def _set_result_state(self, state: str, text: object) -> None:
        self.result_badge.setProperty("debugResult", state)
        self.result_badge.set_safe_text(text)
        repolish(self.result_badge)

    def _parse_labels(self) -> dict[SafeTextLabel, str]:
        return {self.crc_label: "crc", self.length_label: "", self.address_label: "", self.function_label: "", self.parse_status_label: "", self.concentration_label: "", self.unit_label: "", self.error_reason_label: ""}

    def _field_error(self, widget: QWidget, message: str) -> bool:
        widget.setProperty("validation", "error"); repolish(widget); self.validation_hint.set_validation_error(message); return False

    def _require_permission(self) -> bool:
        if self._can_debug:
            return True
        self.error_banner.show_permission_denied(); return False

    def _apply_permission_state(self) -> None:
        for widget in (self.port_combo, self.protocol_combo, self.source_combo, self.address_spin, self.start_register_spin, self.register_count_spin, self.timeout_spin, self.generate_button, self.send_button):
            widget.setEnabled(self._can_debug)
        self.function_combo.setEnabled(False)


def _spin(minimum: int, maximum: int, value: int) -> QSpinBox:
    spin = QSpinBox(); spin.setRange(minimum, maximum); spin.setValue(value); return spin


def _field_label(text: str) -> QLabel:
    label = QLabel(text); label.setProperty("role", "fieldLabel"); return label


def _value_label(text: object) -> SafeTextLabel:
    label = SafeTextLabel(text); label.setProperty("role", "muted"); return label


def _safe_call_list(service: object | None, method_name: str) -> tuple[dict[str, Any], ...]:
    method = getattr(service, method_name, None)
    if method is None:
        return ()
    try:
        return tuple(dict(row) for row in method())
    except Exception:
        return ()


def _port_label(row: dict[str, Any]) -> str:
    name = str(row.get("name") or f"端口 {row.get('id')}")
    channel = "串口" if row.get("channel_type") == "serial" else "TCP"
    endpoint = row.get("serial_port_name") if row.get("channel_type") == "serial" else f"{row.get('tcp_host') or ''}:{row.get('tcp_port') or ''}"
    return f"{name}｜{channel}｜{endpoint}"


def _can_debug_from_session(session: object | None) -> bool:
    role = getattr(session, "role", None)
    if role is None:
        return False
    try:
        return role_has_permission(str(role), Permission.DEVICE_DEBUG_VIEW.value)
    except ValueError:
        return False


def _first_reading(result: object) -> DeviceReading | None:
    readings = tuple(getattr(result, "readings", ()) or ())
    return readings[0] if readings else None


def _result_has_valid_reading(result: object) -> bool:
    return _first_reading(result) is not None and getattr(result, "crc_ok", None) is True


def _status_text(result: object) -> str:
    if _result_has_valid_reading(result):
        return "解析成功"
    code = getattr(result, "error_code", None)
    if code == "timeout":
        return "通讯超时"
    if code == "empty_response":
        return "空返回"
    if getattr(result, "crc_ok", None) is False:
        return "CRC 错误"
    return "诊断结果"


def _crc_text(result: object) -> str:
    crc_ok = getattr(result, "crc_ok", None)
    exchange = getattr(result, "exchange", None)
    crc = getattr(exchange, "crc", None)
    expected = getattr(crc, "expected_hex", None)
    actual = getattr(crc, "actual_hex", None)
    if crc_ok is True:
        return f"通过（期望 {expected} / 实际 {actual}）" if expected or actual else "通过"
    if crc_ok is False:
        return f"失败（期望 {expected} / 实际 {actual}）" if expected or actual else "失败"
    return "未校验"


def _frame_length_text(exchange: object, result: object) -> str:
    response = getattr(exchange, "response_hex", None) or getattr(result, "response_hex", "") or ""
    if not response:
        return "0 字节"
    return f"{len(str(response).replace(' ', '')) // 2} 字节"


def _address_text(exchange: object) -> str:
    response = getattr(exchange, "response_hex", "") or ""
    return _hex_part(response, 0)


def _function_text(exchange: object) -> str:
    response = getattr(exchange, "response_hex", "") or ""
    return _hex_part(response, 1)


def _hex_part(raw_hex: object, index: int) -> str:
    parts = str(raw_hex or "").split()
    return parts[index] if len(parts) > index else "-"


def _parse_status_text(exchange: object, result: object) -> str:
    parse = getattr(exchange, "parse", None)
    status = getattr(parse, "status", None)
    if status:
        return str(getattr(status, "value", status))
    return "success" if _result_has_valid_reading(result) else "not_run"


def _error_reason(exchange: object, result: object) -> str:
    reason = getattr(exchange, "error_reason", "") or getattr(result, "validation_message", "") or ""
    return controlled_error_text(reason, fallback="", max_chars=256)


class _UiResult:
    def __init__(self, success: bool, code: int, message: str, data: DebugFrameResult | None) -> None:
        self.success = success; self.code = code; self.message = message; self.data = data
