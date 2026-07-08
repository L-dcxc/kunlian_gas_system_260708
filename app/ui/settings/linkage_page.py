from __future__ import annotations

from typing import Any, Final

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.services.errors import ErrorCode
from app.services.linkage_service import LINKAGE_ALARM_TYPES, LinkageObjectCommand, LinkageRuleCommand
from app.services.permissions import Permission, role_has_permission
from app.ui.common.data_table import DataTable, TableColumn, TableState
from app.ui.common.dialogs import RiskConfirmDialog
from app.ui.common.errors import ErrorBanner, ValidationHint, controlled_error_text
from app.ui.common.permission_hint import PermissionHint
from app.ui.common.safe_text import SafeTextLabel
from app.ui.common.status import repolish
from app.ui.settings.linkage_control_panel import LinkageControlPanel
from app.ui.settings.linkage_records_panel import LinkageRecordsPanel

LOAD_FAILED_TEXT: Final[str] = "联动配置读取失败，请稍后重试。"
SAVE_OBJECT_FAILED_TEXT: Final[str] = "联动对象保存失败，请稍后重试。"
SAVE_RULE_FAILED_TEXT: Final[str] = "联动规则保存失败，请稍后重试。"
ALARM_TYPE_LABELS: Final[dict[str, str]] = {
    "*": "全部报警类型",
    "alarm_low": "低报",
    "alarm_high": "高报",
    "over_range": "超量程",
    "fault": "故障",
    "offline": "离线",
    "disabled": "屏蔽",
    "warming": "预热",
}
ACTION_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_:-.")


class LinkagePanel(QWidget):
    configChanged = Signal()

    def __init__(
        self,
        linkage_service: object | None = None,
        session: object | None = None,
        parent: QWidget | None = None,
        *,
        can_manage: bool | None = None,
        can_control: bool | None = None,
        confirm_delete: object | None = None,
        confirm_manual: object | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = linkage_service
        self._session = session
        self._can_manage = _can_manage_from_session(session) if can_manage is None else can_manage
        self._can_control = _can_control_from_session(session) if can_control is None else can_control
        self._confirm_delete = confirm_delete or _confirm_delete_linkage
        self._busy = False
        self._objects: tuple[dict[str, Any], ...] = ()
        self._rules: tuple[dict[str, Any], ...] = ()
        self._selected_object_id: int | None = None
        self._editing_object_id: int | None = None
        self._selected_rule_id: int | None = None
        self._editing_rule_id: int | None = None

        self.title_label = SafeTextLabel("联动管理", selectable=False)
        self.title_label.setProperty("role", "panelTitle")
        self.subtitle_label = SafeTextLabel("配置联动对象、报警规则、手动模拟控制和联动记录。", selectable=False)
        self.subtitle_label.setProperty("role", "muted")
        self.simulation_notice = SafeTextLabel("真实 IO 协议和点表未确认：当前仅允许 simulated 模拟联动，真实下发不可配置。", selectable=True)
        self.simulation_notice.setProperty("role", "warningText")
        self.permission_hint = PermissionHint(); self.permission_hint.setVisible(not self._can_manage)
        self.error_banner = ErrorBanner(); self.error_banner.clear()
        self.reload_button = QPushButton("刷新")
        self.reload_button.clicked.connect(self.reload)

        header_actions = QHBoxLayout()
        header_actions.addStretch(1)
        header_actions.addWidget(self.reload_button)
        header = QFrame(self)
        header.setProperty("panel", "true")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(16, 16, 16, 16)
        header_layout.setSpacing(8)
        header_layout.addWidget(self.title_label)
        header_layout.addWidget(self.subtitle_label)
        header_layout.addWidget(self.simulation_notice)
        header_layout.addWidget(self.permission_hint)
        header_layout.addLayout(header_actions)

        self.object_table = self._build_object_table()
        self.object_form = self._build_object_form()
        self.rule_table = self._build_rule_table()
        self.rule_form = self._build_rule_form()
        self.control_panel = LinkageControlPanel(linkage_service, session, can_control=self._can_control, confirm_manual=confirm_manual)
        self.records_panel = LinkageRecordsPanel(linkage_service, session)

        object_tab = QWidget()
        object_body = QSplitter(Qt.Orientation.Horizontal, object_tab)
        object_body.addWidget(self.object_table)
        object_body.addWidget(self.object_form)
        object_body.setStretchFactor(0, 3)
        object_body.setStretchFactor(1, 2)
        object_layout = QVBoxLayout(object_tab)
        object_layout.setContentsMargins(0, 0, 0, 0)
        object_layout.addWidget(object_body)

        rule_tab = QWidget()
        rule_body = QSplitter(Qt.Orientation.Horizontal, rule_tab)
        rule_body.addWidget(self.rule_table)
        rule_body.addWidget(self.rule_form)
        rule_body.setStretchFactor(0, 3)
        rule_body.setStretchFactor(1, 2)
        rule_layout = QVBoxLayout(rule_tab)
        rule_layout.setContentsMargins(0, 0, 0, 0)
        rule_layout.addWidget(rule_body)

        self.tabs = QTabWidget(self)
        self.tabs.addTab(object_tab, "联动对象")
        self.tabs.addTab(rule_tab, "联动规则")
        self.tabs.addTab(self.control_panel, "手动控制")
        self.tabs.addTab(self.records_panel, "联动记录")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        layout.addWidget(header)
        layout.addWidget(self.error_banner)
        layout.addWidget(self.tabs, 1)
        self._apply_permission_state()
        self._apply_selection_state()

    def reload(self) -> None:
        if self._busy:
            return
        self.error_banner.clear()
        self._set_busy(True)
        self.object_table.set_state(TableState.LOADING, "正在加载联动对象")
        self.rule_table.set_state(TableState.LOADING, "正在加载联动规则")
        try:
            self._objects = tuple(dict(row) for row in (self._service.list_objects() if self._service else ()))
            self._rules = tuple(dict(row) for row in (self._service.list_rules() if self._service else ()))
        except Exception:
            self._show_load_error(LOAD_FAILED_TEXT)
            self._set_busy(False)
            return
        self._set_busy(False)
        self._reload_object_table()
        self._reload_rule_table()
        self._reload_rule_object_combo()
        self.control_panel.reload_objects()
        self.records_panel.reload()
        self._apply_selection_state()

    def new_object(self) -> None:
        if not self._require_manage_permission():
            return
        self._editing_object_id = None
        self._selected_object_id = None
        self.object_type_edit.setText("relay")
        self.object_name_edit.clear()
        self.object_location_edit.clear()
        self.object_adapter_combo.setCurrentIndex(self.object_adapter_combo.findData("simulated"))
        self.object_enabled_check.setChecked(True)
        self.clear_object_validation()
        self._apply_selection_state()

    def save_object(self) -> None:
        if self._busy or not self._require_manage_permission() or not self.validate_object_form():
            return
        if self._service is None or not hasattr(self._service, "save_object"):
            self.error_banner.set_error("联动服务未配置")
            return
        self._set_busy(True)
        try:
            result = self._service.save_object(self._session, self._object_command())
        except Exception:
            self._set_busy(False)
            self.error_banner.set_error(SAVE_OBJECT_FAILED_TEXT)
            return
        self._set_busy(False)
        if bool(getattr(result, "success", False)):
            self.reload(); self.configChanged.emit(); return
        self._handle_service_failure(result, SAVE_OBJECT_FAILED_TEXT)

    def delete_object(self) -> None:
        if self._busy or not self._require_manage_permission() or self._selected_object_id is None:
            return
        row = self.selected_object()
        if row is None or not self._confirm_delete(self, "联动对象", row):
            return
        self._set_busy(True)
        try:
            result = self._service.delete_object(self._session, self._selected_object_id)
        except Exception:
            self._set_busy(False)
            self.error_banner.set_error(SAVE_OBJECT_FAILED_TEXT)
            return
        self._set_busy(False)
        if bool(getattr(result, "success", False)):
            self.reload(); self.configChanged.emit(); return
        self._handle_service_failure(result, SAVE_OBJECT_FAILED_TEXT)

    def new_rule(self) -> None:
        if not self._require_manage_permission():
            return
        self._editing_rule_id = None
        self._selected_rule_id = None
        self.rule_name_edit.clear()
        self.rule_detector_id_edit.clear()
        self.rule_alarm_combo.setCurrentIndex(self.rule_alarm_combo.findData("*"))
        self.rule_alarm_level_spin.setValue(0)
        self.rule_alarm_level_enabled.setChecked(False)
        self.rule_action_edit.setText("open")
        self.rule_recovery_action_edit.clear()
        self.rule_delay_spin.setValue(0)
        self.rule_enabled_check.setChecked(True)
        self.clear_rule_validation()
        self._apply_selection_state()

    def save_rule(self) -> None:
        if self._busy or not self._require_manage_permission() or not self.validate_rule_form():
            return
        if self._service is None or not hasattr(self._service, "save_rule"):
            self.error_banner.set_error("联动服务未配置")
            return
        self._set_busy(True)
        try:
            result = self._service.save_rule(self._session, self._rule_command())
        except Exception:
            self._set_busy(False)
            self.error_banner.set_error(SAVE_RULE_FAILED_TEXT)
            return
        self._set_busy(False)
        if bool(getattr(result, "success", False)):
            self.reload(); self.configChanged.emit(); return
        self._handle_service_failure(result, SAVE_RULE_FAILED_TEXT)

    def delete_rule(self) -> None:
        if self._busy or not self._require_manage_permission() or self._selected_rule_id is None:
            return
        row = self.selected_rule()
        if row is None or not self._confirm_delete(self, "联动规则", row):
            return
        self._set_busy(True)
        try:
            result = self._service.delete_rule(self._session, self._selected_rule_id)
        except Exception:
            self._set_busy(False)
            self.error_banner.set_error(SAVE_RULE_FAILED_TEXT)
            return
        self._set_busy(False)
        if bool(getattr(result, "success", False)):
            self.reload(); self.configChanged.emit(); return
        self._handle_service_failure(result, SAVE_RULE_FAILED_TEXT)

    def selected_object(self) -> dict[str, Any] | None:
        return next((row for row in self._objects if _optional_int(row.get("id")) == self._selected_object_id), None)

    def selected_rule(self) -> dict[str, Any] | None:
        return next((row for row in self._rules if _optional_int(row.get("id")) == self._selected_rule_id), None)

    def validate_object_form(self) -> bool:
        self.clear_object_validation()
        if not _valid_code(self.object_type_edit.text(), 40):
            return self._object_field_error(self.object_type_edit, "对象类型不能为空，且仅允许字母、数字、下划线、冒号、点和短横线")
        if not self.object_name_edit.text().strip():
            return self._object_field_error(self.object_name_edit, "对象名称不能为空")
        if len(self.object_name_edit.text().strip()) > 120:
            return self._object_field_error(self.object_name_edit, "对象名称长度不能超过 120")
        if len(self.object_location_edit.text().strip()) > 200:
            return self._object_field_error(self.object_location_edit, "位置长度不能超过 200")
        if self.object_adapter_combo.currentData() != "simulated":
            return self._object_field_error(self.object_adapter_combo, "真实 IO 协议未确认，当前只能选择 simulated")
        return True

    def validate_rule_form(self) -> bool:
        self.clear_rule_validation()
        if not self.rule_name_edit.text().strip():
            return self._rule_field_error(self.rule_name_edit, "规则名称不能为空")
        if len(self.rule_name_edit.text().strip()) > 120:
            return self._rule_field_error(self.rule_name_edit, "规则名称长度不能超过 120")
        if self.rule_object_combo.currentData() is None:
            return self._rule_field_error(self.rule_object_combo, "必须选择联动对象")
        detector_text = self.rule_detector_id_edit.text().strip()
        if detector_text and _optional_int(detector_text) is None:
            return self._rule_field_error(self.rule_detector_id_edit, "探测器 ID 必须为空或正整数")
        if self.rule_alarm_combo.currentData() not in LINKAGE_ALARM_TYPES:
            return self._rule_field_error(self.rule_alarm_combo, "报警类型不受支持")
        if self.rule_alarm_level_enabled.isChecked() and self.rule_alarm_level_spin.value() < 0:
            return self._rule_field_error(self.rule_alarm_level_spin, "报警级别必须大于等于 0")
        if not _valid_code(self.rule_action_edit.text(), 80):
            return self._rule_field_error(self.rule_action_edit, "动作码不能为空，且仅允许字母、数字、下划线、冒号、点和短横线")
        recovery = self.rule_recovery_action_edit.text().strip()
        if recovery and not _valid_code(recovery, 80):
            return self._rule_field_error(self.rule_recovery_action_edit, "恢复动作码字符或长度不合法")
        return True

    def clear_object_validation(self) -> None:
        self.object_validation_hint.clear()
        for widget in (self.object_type_edit, self.object_name_edit, self.object_location_edit, self.object_adapter_combo):
            widget.setProperty("validation", None); repolish(widget)

    def clear_rule_validation(self) -> None:
        self.rule_validation_hint.clear()
        for widget in (
            self.rule_name_edit,
            self.rule_object_combo,
            self.rule_detector_id_edit,
            self.rule_alarm_combo,
            self.rule_alarm_level_spin,
            self.rule_action_edit,
            self.rule_recovery_action_edit,
            self.rule_delay_spin,
        ):
            widget.setProperty("validation", None); repolish(widget)

    def _object_command(self) -> LinkageObjectCommand:
        # Real IO relay protocol is still [待确认], so UI writes always stay in
        # simulated adapter mode even if old backend rows contain adapter_type=real.
        return LinkageObjectCommand(
            id=self._editing_object_id,
            object_type=self.object_type_edit.text().strip(),
            name=self.object_name_edit.text().strip(),
            location=self.object_location_edit.text().strip() or None,
            adapter_type="simulated",
            is_enabled=self.object_enabled_check.isChecked(),
        )

    def _rule_command(self) -> LinkageRuleCommand:
        return LinkageRuleCommand(
            id=self._editing_rule_id,
            name=self.rule_name_edit.text().strip(),
            object_id=int(self.rule_object_combo.currentData()),
            action=self.rule_action_edit.text().strip(),
            detector_id=_optional_int(self.rule_detector_id_edit.text().strip()),
            alarm_type=str(self.rule_alarm_combo.currentData()),
            alarm_level=self.rule_alarm_level_spin.value() if self.rule_alarm_level_enabled.isChecked() else None,
            trigger_delay_sec=self.rule_delay_spin.value(),
            recovery_action=self.rule_recovery_action_edit.text().strip() or None,
            is_enabled=self.rule_enabled_check.isChecked(),
        )

    def _build_object_table(self) -> DataTable:
        table = DataTable(
            [
                TableColumn("id", "ID", 58, Qt.AlignmentFlag.AlignRight),
                TableColumn("name", "对象名称", 150),
                TableColumn("object_type", "类型", 100),
                TableColumn("location", "位置", 150),
                TableColumn("adapter_label", "模式", 130),
                TableColumn("status_label", "状态", 80),
            ]
        )
        table.export_button.setVisible(False)
        table.retryRequested.connect(self.reload)
        table.emptyActionRequested.connect(self.new_object)
        table.table.selectionModel().selectionChanged.connect(self._object_selection_changed)
        return table

    def _build_object_form(self) -> QFrame:
        form = QFrame(); form.setProperty("panel", "true")
        self.object_type_edit = QLineEdit("relay"); self.object_type_edit.setMaxLength(40)
        self.object_name_edit = QLineEdit(); self.object_name_edit.setMaxLength(120)
        self.object_location_edit = QLineEdit(); self.object_location_edit.setMaxLength(200)
        self.object_adapter_combo = QComboBox()
        self.object_adapter_combo.addItem("simulated 模拟联动", "simulated")
        self.object_adapter_combo.addItem("real 真实 IO（待确认不可用）", "real")
        self.object_adapter_combo.model().item(1).setEnabled(False)
        self.object_enabled_check = QCheckBox("启用联动对象"); self.object_enabled_check.setChecked(True)
        self.object_validation_hint = ValidationHint(); self.object_validation_hint.clear()
        self.new_object_button = QPushButton("新增对象")
        self.save_object_button = QPushButton("保存对象"); self.save_object_button.setProperty("variant", "primary")
        self.delete_object_button = QPushButton("删除对象"); self.delete_object_button.setProperty("variant", "danger")
        self.new_object_button.clicked.connect(self.new_object)
        self.save_object_button.clicked.connect(self.save_object)
        self.delete_object_button.clicked.connect(self.delete_object)

        grid = QGridLayout(); grid.setHorizontalSpacing(12); grid.setVerticalSpacing(10)
        self._add_field(grid, 0, "对象类型", self.object_type_edit)
        self._add_field(grid, 1, "对象名称", self.object_name_edit)
        self._add_field(grid, 2, "位置", self.object_location_edit)
        self._add_field(grid, 3, "联动模式", self.object_adapter_combo)
        grid.addWidget(self.object_enabled_check, 4, 1)
        actions = QHBoxLayout(); actions.addWidget(self.new_object_button); actions.addWidget(self.save_object_button); actions.addWidget(self.delete_object_button); actions.addStretch(1)
        layout = QVBoxLayout(form); layout.setContentsMargins(16, 16, 16, 16); layout.setSpacing(10)
        layout.addWidget(SafeTextLabel("对象表单", selectable=False)); layout.addLayout(grid); layout.addWidget(self.object_validation_hint); layout.addLayout(actions); layout.addStretch(1)
        return form

    def _build_rule_table(self) -> DataTable:
        table = DataTable(
            [
                TableColumn("id", "ID", 58, Qt.AlignmentFlag.AlignRight),
                TableColumn("name", "规则名称", 150),
                TableColumn("object_label", "联动对象", 140),
                TableColumn("detector_label", "探测器", 100),
                TableColumn("alarm_type_label", "报警类型", 110),
                TableColumn("alarm_level_label", "级别", 70),
                TableColumn("action", "动作", 100),
                TableColumn("delay_label", "延迟", 80),
                TableColumn("status_label", "状态", 80),
            ]
        )
        table.export_button.setVisible(False)
        table.retryRequested.connect(self.reload)
        table.emptyActionRequested.connect(self.new_rule)
        table.table.selectionModel().selectionChanged.connect(self._rule_selection_changed)
        return table

    def _build_rule_form(self) -> QFrame:
        form = QFrame(); form.setProperty("panel", "true")
        self.rule_name_edit = QLineEdit(); self.rule_name_edit.setMaxLength(120)
        self.rule_object_combo = QComboBox()
        self.rule_detector_id_edit = QLineEdit(); self.rule_detector_id_edit.setMaxLength(12)
        self.rule_alarm_combo = QComboBox()
        for code, label in ALARM_TYPE_LABELS.items():
            self.rule_alarm_combo.addItem(label, code)
        self.rule_alarm_level_enabled = QCheckBox("限定报警级别")
        self.rule_alarm_level_spin = _spin(0, 9999, 0)
        self.rule_action_edit = QLineEdit("open"); self.rule_action_edit.setMaxLength(80)
        self.rule_recovery_action_edit = QLineEdit(); self.rule_recovery_action_edit.setMaxLength(80)
        self.rule_delay_spin = _spin(0, 86400, 0)
        self.rule_enabled_check = QCheckBox("启用联动规则"); self.rule_enabled_check.setChecked(True)
        self.rule_validation_hint = ValidationHint(); self.rule_validation_hint.clear()
        self.new_rule_button = QPushButton("新增规则")
        self.save_rule_button = QPushButton("保存规则"); self.save_rule_button.setProperty("variant", "primary")
        self.delete_rule_button = QPushButton("删除规则"); self.delete_rule_button.setProperty("variant", "danger")
        self.new_rule_button.clicked.connect(self.new_rule)
        self.save_rule_button.clicked.connect(self.save_rule)
        self.delete_rule_button.clicked.connect(self.delete_rule)

        grid = QGridLayout(); grid.setHorizontalSpacing(12); grid.setVerticalSpacing(10)
        self._add_field(grid, 0, "规则名称", self.rule_name_edit)
        self._add_field(grid, 1, "联动对象", self.rule_object_combo)
        self._add_field(grid, 2, "探测器 ID", self.rule_detector_id_edit)
        self._add_field(grid, 3, "报警类型", self.rule_alarm_combo)
        grid.addWidget(self.rule_alarm_level_enabled, 4, 0)
        grid.addWidget(self.rule_alarm_level_spin, 4, 1)
        self._add_field(grid, 5, "动作码", self.rule_action_edit)
        self._add_field(grid, 6, "恢复动作", self.rule_recovery_action_edit)
        self._add_field(grid, 7, "延迟秒", self.rule_delay_spin)
        grid.addWidget(self.rule_enabled_check, 8, 1)
        actions = QHBoxLayout(); actions.addWidget(self.new_rule_button); actions.addWidget(self.save_rule_button); actions.addWidget(self.delete_rule_button); actions.addStretch(1)
        layout = QVBoxLayout(form); layout.setContentsMargins(16, 16, 16, 16); layout.setSpacing(10)
        layout.addWidget(SafeTextLabel("规则表单", selectable=False)); layout.addLayout(grid); layout.addWidget(self.rule_validation_hint); layout.addLayout(actions); layout.addStretch(1)
        return form

    def _reload_object_table(self) -> None:
        rows = [_object_to_table(row) for row in self._objects]
        self.object_table.set_rows(rows)
        self.object_table.set_page(1, len(rows), max(1, len(rows) or 1))
        self.object_table.set_state(TableState.READY if rows else TableState.EMPTY, "暂无联动对象，请先新增模拟联动对象")

    def _reload_rule_table(self) -> None:
        rows = [_rule_to_table(row, self._objects) for row in self._rules]
        self.rule_table.set_rows(rows)
        self.rule_table.set_page(1, len(rows), max(1, len(rows) or 1))
        self.rule_table.set_state(TableState.READY if rows else TableState.EMPTY, "暂无联动规则，请先新增规则")

    def _reload_rule_object_combo(self) -> None:
        current = self.rule_object_combo.currentData()
        self.rule_object_combo.clear()
        self.rule_object_combo.addItem("请选择联动对象", None)
        for row in self._objects:
            if str(row.get("adapter_type", "simulated")) != "simulated":
                continue
            self.rule_object_combo.addItem(str(row.get("name", f"对象 {row.get('id')}")), int(row.get("id", 0)))
        index = self.rule_object_combo.findData(current)
        self.rule_object_combo.setCurrentIndex(index if index >= 0 else 0)

    def _object_selection_changed(self) -> None:
        indexes = self.object_table.table.selectionModel().selectedRows()
        row = self._objects[indexes[0].row()] if indexes and 0 <= indexes[0].row() < len(self._objects) else None
        self._selected_object_id = _optional_int(row.get("id")) if row else None
        self._editing_object_id = self._selected_object_id
        if row:
            self.object_type_edit.setText(str(row.get("object_type", "")))
            self.object_name_edit.setText(str(row.get("name", "")))
            self.object_location_edit.setText(str(row.get("location") or ""))
            self.object_adapter_combo.setCurrentIndex(self.object_adapter_combo.findData("simulated"))
            self.object_enabled_check.setChecked(bool(row.get("is_enabled", True)))
        self._apply_selection_state()

    def _rule_selection_changed(self) -> None:
        indexes = self.rule_table.table.selectionModel().selectedRows()
        row = self._rules[indexes[0].row()] if indexes and 0 <= indexes[0].row() < len(self._rules) else None
        self._selected_rule_id = _optional_int(row.get("id")) if row else None
        self._editing_rule_id = self._selected_rule_id
        if row:
            self.rule_name_edit.setText(str(row.get("name", "")))
            _set_combo(self.rule_object_combo, row.get("object_id"))
            self.rule_detector_id_edit.setText("" if row.get("detector_id") is None else str(row.get("detector_id")))
            _set_combo(self.rule_alarm_combo, row.get("alarm_type", "*"))
            self.rule_alarm_level_enabled.setChecked(row.get("alarm_level") is not None)
            self.rule_alarm_level_spin.setValue(int(row.get("alarm_level") or 0))
            self.rule_action_edit.setText(str(row.get("action", "")))
            self.rule_recovery_action_edit.setText(str(row.get("recovery_action") or ""))
            self.rule_delay_spin.setValue(int(row.get("trigger_delay_sec", 0) or 0))
            self.rule_enabled_check.setChecked(bool(row.get("is_enabled", True)))
        self._apply_selection_state()

    def _require_manage_permission(self) -> bool:
        if self._can_manage:
            return True
        self.permission_hint.show_denied()
        self.error_banner.show_permission_denied()
        return False

    def _handle_service_failure(self, result: object, fallback: str) -> None:
        if int(getattr(result, "code", 0) or 0) == int(ErrorCode.PERMISSION_DENIED):
            self.permission_hint.show_denied()
            self.error_banner.show_permission_denied()
            return
        self.error_banner.set_error(controlled_error_text(getattr(result, "message", fallback), fallback=fallback))

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._apply_selection_state()

    def _apply_permission_state(self) -> None:
        self.permission_hint.setVisible(not self._can_manage)
        for widget in (
            self.object_type_edit,
            self.object_name_edit,
            self.object_location_edit,
            self.object_adapter_combo,
            self.object_enabled_check,
            self.rule_name_edit,
            self.rule_object_combo,
            self.rule_detector_id_edit,
            self.rule_alarm_combo,
            self.rule_alarm_level_enabled,
            self.rule_alarm_level_spin,
            self.rule_action_edit,
            self.rule_recovery_action_edit,
            self.rule_delay_spin,
            self.rule_enabled_check,
        ):
            if hasattr(widget, "setReadOnly"):
                widget.setReadOnly(not self._can_manage)  # type: ignore[attr-defined]
            else:
                widget.setEnabled(self._can_manage)
        self._apply_selection_state()

    def _apply_selection_state(self) -> None:
        can_write = self._can_manage and not self._busy
        for button in (self.new_object_button, self.save_object_button, self.new_rule_button, self.save_rule_button):
            button.setEnabled(can_write)
        self.delete_object_button.setEnabled(can_write and self._selected_object_id is not None)
        self.delete_rule_button.setEnabled(can_write and self._selected_rule_id is not None)
        self.reload_button.setEnabled(not self._busy)

    def _show_load_error(self, message: object) -> None:
        self._objects = (); self._rules = ()
        self.object_table.set_rows([]); self.object_table.set_page(1, 0, 1); self.object_table.set_state(TableState.ERROR, controlled_error_text(message, fallback=LOAD_FAILED_TEXT))
        self.rule_table.set_rows([]); self.rule_table.set_page(1, 0, 1); self.rule_table.set_state(TableState.ERROR, controlled_error_text(message, fallback=LOAD_FAILED_TEXT))

    def _object_field_error(self, widget: QWidget, message: str) -> bool:
        widget.setProperty("validation", "error"); repolish(widget); self.object_validation_hint.set_validation_error(message); widget.setFocus(); return False

    def _rule_field_error(self, widget: QWidget, message: str) -> bool:
        widget.setProperty("validation", "error"); repolish(widget); self.rule_validation_hint.set_validation_error(message); widget.setFocus(); return False

    @staticmethod
    def _add_field(grid: QGridLayout, row: int, label: str, widget: QWidget) -> None:
        label_widget = QLabel(label)
        label_widget.setProperty("role", "fieldLabel")
        grid.addWidget(label_widget, row, 0)
        grid.addWidget(widget, row, 1)


def _object_to_table(row: dict[str, Any]) -> dict[str, object]:
    adapter = str(row.get("adapter_type", "simulated"))
    return {
        **row,
        "adapter_label": "模拟联动" if adapter == "simulated" else "真实 IO 待确认",
        "status_label": "启用" if row.get("is_enabled", True) else "停用",
    }


def _rule_to_table(row: dict[str, Any], objects: tuple[dict[str, Any], ...]) -> dict[str, object]:
    object_id = _optional_int(row.get("object_id"))
    linked = next((item for item in objects if _optional_int(item.get("id")) == object_id), None)
    alarm_type = str(row.get("alarm_type", "*"))
    return {
        **row,
        "object_label": linked.get("name", object_id) if linked else object_id or "-",
        "detector_label": row.get("detector_id") or "全部",
        "alarm_type_label": ALARM_TYPE_LABELS.get(alarm_type, alarm_type),
        "alarm_level_label": row.get("alarm_level") if row.get("alarm_level") is not None else "不限",
        "delay_label": f"{row.get('trigger_delay_sec', 0)} 秒",
        "status_label": "启用" if row.get("is_enabled", True) else "停用",
    }


def _valid_code(value: object, max_length: int) -> bool:
    if not isinstance(value, str):
        return False
    text = value.strip()
    return bool(text) and len(text) <= max_length and all(char in ACTION_CHARS for char in text)


def _optional_int(value: object) -> int | None:
    if value in {None, ""}:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


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
    spin = QSpinBox()
    spin.setRange(minimum, maximum)
    spin.setValue(value)
    return spin


def _can_manage_from_session(session: object | None) -> bool:
    permissions = tuple(getattr(session, "permissions", ()) or ())
    if "*" in permissions or Permission.SYSTEM_SETTINGS.value in permissions:
        return True
    role = getattr(session, "role", None)
    if role is None:
        return False
    try:
        return role_has_permission(str(role), Permission.SYSTEM_SETTINGS.value)
    except ValueError:
        return False


def _can_control_from_session(session: object | None) -> bool:
    permissions = tuple(getattr(session, "permissions", ()) or ())
    if "*" in permissions or Permission.LINKAGE_MANUAL_CONTROL.value in permissions:
        return True
    role = getattr(session, "role", None)
    if role is None:
        return False
    try:
        return role_has_permission(str(role), Permission.LINKAGE_MANUAL_CONTROL.value)
    except ValueError:
        return False


def _confirm_delete_linkage(parent: QWidget, title: str, row: dict[str, Any]) -> bool:
    return RiskConfirmDialog.confirm(
        f"确认删除{title}",
        f"将删除{title}：{row.get('name', '')}。服务层会记录操作日志并按引用关系校验。",
        parent,
        confirm_text="确认删除",
    )


LinkagePage = LinkagePanel

__all__ = ["LinkagePanel", "LinkagePage"]
