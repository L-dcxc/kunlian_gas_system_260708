from __future__ import annotations

from typing import Mapping

_REQUIRED_TOKENS = frozenset(
    {
        "bg_window",
        "bg_panel",
        "bg_subtle",
        "bg_error",
        "text_primary",
        "text_secondary",
        "text_inverse",
        "border_default",
        "border_strong",
        "primary",
        "primary_hover",
        "status_normal",
        "status_running",
        "status_warning",
        "status_low_alarm",
        "status_high_alarm",
        "status_high_alarm_pulse",
        "status_fault",
        "status_offline",
        "status_shielded",
        "status_warmup",
        "disabled_text",
        "disabled_bg",
        "font_ui",
        "font_mono",
        "font_size_sm",
        "font_size_md",
        "font_size_lg",
        "font_size_xl",
        "font_size_2xl",
        "font_weight_medium",
        "font_weight_bold",
        "space_1",
        "space_2",
        "space_3",
        "space_4",
        "space_6",
        "radius_sm",
        "radius_md",
        "border_width",
        "focus_width",
    }
)


def build_global_qss(tokens: Mapping[str, str]) -> str:
    missing = sorted(_REQUIRED_TOKENS.difference(tokens))
    if missing:
        raise ValueError(f"missing theme tokens: {', '.join(missing)}")

    return f"""
QWidget {{
    font-family: {tokens['font_ui']};
    font-size: {tokens['font_size_md']};
    color: {tokens['text_primary']};
    background: {tokens['bg_window']};
}}
QWidget[appTheme="dark"] {{
    background: {tokens['bg_window']};
}}
QFrame[panel="true"] {{
    background: {tokens['bg_panel']};
    border: {tokens['border_width']} solid {tokens['border_default']};
    border-radius: {tokens['radius_md']};
}}
QFrame#LoginCard, QFrame#LicenseCard, QFrame#ChangePasswordCard {{
    background: {tokens['bg_panel']};
    border: {tokens['border_width']} solid {tokens['border_default']};
    border-radius: {tokens['radius_lg']};
    min-width: 420px;
}}
QLabel#ProductTitle {{
    font-size: {tokens['font_size_2xl']};
    font-weight: {tokens['font_weight_bold']};
    color: {tokens['text_primary']};
}}
QLabel#LicenseState[status="invalid"] {{ color: {tokens['status_high_alarm']}; font-weight: {tokens['font_weight_bold']}; }}
QLabel#LicenseState[status="valid"] {{ color: {tokens['status_normal']}; font-weight: {tokens['font_weight_bold']}; }}
QLabel#LicenseState[status="warning"] {{ color: {tokens['status_warning']}; font-weight: {tokens['font_weight_bold']}; }}
QLabel {{
    background: transparent;
}}
QLabel[role="muted"] {{
    color: {tokens['text_secondary']};
}}
QLabel[role="errorText"], QLabel[role="validationError"] {{
    color: {tokens['status_high_alarm']};
    font-size: {tokens['font_size_sm']};
    font-weight: {tokens['font_weight_medium']};
}}
QLabel[role="statusBadge"] {{
    padding: {tokens['space_1']} {tokens['space_2']};
    border-radius: {tokens['radius_sm']};
    font-size: {tokens['font_size_sm']};
    font-weight: {tokens['font_weight_medium']};
}}
QLabel[status="normal"] {{ color: {tokens['status_normal']}; }}
QLabel[status="running"] {{ color: {tokens['status_running']}; }}
QLabel[status="warning"] {{ color: {tokens['status_warning']}; }}
QLabel[status="lowAlarm"] {{ color: {tokens['status_low_alarm']}; font-weight: {tokens['font_weight_bold']}; }}
QLabel[status="highAlarm"] {{ color: {tokens['status_high_alarm']}; font-weight: {tokens['font_weight_bold']}; }}
QLabel[status="fault"] {{ color: {tokens['status_fault']}; font-weight: {tokens['font_weight_bold']}; }}
QLabel[status="offline"] {{ color: {tokens['status_offline']}; }}
QLabel[status="shielded"] {{ color: {tokens['status_shielded']}; }}
QLabel[status="warmup"] {{ color: {tokens['status_warmup']}; }}
QLabel[status="overRange"] {{ color: {tokens['status_high_alarm']}; font-weight: {tokens['font_weight_bold']}; }}
QFrame[role="errorBanner"] {{
    background: {tokens['bg_error']};
    border: {tokens['border_width']} solid {tokens['status_high_alarm']};
    border-radius: {tokens['radius_md']};
}}
QFrame[role="errorBanner"][severity="warning"] {{
    border-color: {tokens['status_warning']};
}}
QFrame[role="errorBanner"][severity="permission"] {{
    border-color: {tokens['status_shielded']};
}}
QFrame[alarm="low"][alarmPulse="true"] {{
    border: {tokens['focus_width']} solid {tokens['status_low_alarm']};
}}
QFrame[alarm="low"][alarmPulse="false"] {{
    border: {tokens['focus_width']} solid {tokens['status_warning']};
}}
QFrame[alarm="high"][alarmPulse="true"], QFrame[alarm="overRange"][alarmPulse="true"] {{
    background: {tokens['bg_error']};
    border: {tokens['focus_width']} solid {tokens['status_high_alarm']};
}}
QFrame[alarm="high"][alarmPulse="false"], QFrame[alarm="overRange"][alarmPulse="false"] {{
    background: {tokens['bg_panel']};
    border: {tokens['focus_width']} solid {tokens['status_high_alarm_pulse']};
}}
QFrame[alarm="fault"][alarmPulse="true"] {{
    border: {tokens['focus_width']} solid {tokens['status_fault']};
}}
QFrame[alarm="fault"][alarmPulse="false"] {{
    border: {tokens['focus_width']} solid {tokens['status_high_alarm_pulse']};
}}
QPushButton {{
    min-height: 32px;
    padding: 0 {tokens['space_4']};
    color: {tokens['text_primary']};
    background: {tokens['bg_panel']};
    border: {tokens['border_width']} solid {tokens['border_default']};
    border-radius: {tokens['radius_md']};
    font-weight: {tokens['font_weight_medium']};
}}
QPushButton:hover {{
    border-color: {tokens['primary']};
}}
QPushButton[variant="primary"] {{
    color: {tokens['text_inverse']};
    background: {tokens['primary']};
    border-color: {tokens['primary']};
}}
QPushButton[variant="primary"]:hover {{
    background: {tokens['primary_hover']};
    border-color: {tokens['primary_hover']};
}}
QPushButton[variant="danger"] {{
    color: {tokens['text_inverse']};
    background: {tokens['status_high_alarm']};
    border-color: {tokens['status_high_alarm']};
}}
QPushButton[recordAction="delete"] {{
    color: {tokens['status_high_alarm']};
    background: {tokens['bg_panel']};
    border-color: {tokens['status_high_alarm']};
}}
QPushButton[recordAction="clearAll"] {{
    color: {tokens['text_inverse']};
    background: {tokens['status_high_alarm']};
    border-color: {tokens['status_high_alarm']};
}}
QPushButton:disabled, QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled {{
    color: {tokens['disabled_text']};
    background: {tokens['disabled_bg']};
    border-color: {tokens['border_default']};
}}
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateTimeEdit {{
    min-height: 30px;
    color: {tokens['text_primary']};
    background: {tokens['bg_panel']};
    border: {tokens['border_width']} solid {tokens['border_default']};
    border-radius: {tokens['radius_sm']};
    padding: 0 {tokens['space_2']};
}}
QTextEdit, QPlainTextEdit {{
    padding: {tokens['space_2']};
    font-family: {tokens['font_mono']};
}}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus,
QSpinBox:focus, QDoubleSpinBox:focus, QDateTimeEdit:focus {{
    border: {tokens['focus_width']} solid {tokens['primary']};
}}
QLineEdit[validation="error"], QTextEdit[validation="error"], QPlainTextEdit[validation="error"],
QComboBox[validation="error"], QSpinBox[validation="error"], QDoubleSpinBox[validation="error"],
QDateTimeEdit[validation="error"] {{
    border: {tokens['focus_width']} solid {tokens['status_high_alarm']};
}}
QTableView, QTreeView, QListView {{
    background: {tokens['bg_panel']};
    border: {tokens['border_width']} solid {tokens['border_default']};
    gridline-color: {tokens['border_default']};
    selection-background-color: {tokens['primary']};
    selection-color: {tokens['text_inverse']};
}}
QHeaderView::section {{
    color: {tokens['text_secondary']};
    background: {tokens['bg_subtle']};
    border: 0;
    border-right: {tokens['border_width']} solid {tokens['border_default']};
    padding: {tokens['space_2']};
    font-weight: {tokens['font_weight_medium']};
}}
QProgressBar {{
    min-height: 8px;
    background: {tokens['bg_subtle']};
    border: {tokens['border_width']} solid {tokens['border_default']};
    border-radius: {tokens['radius_sm']};
    text-align: center;
}}
QProgressBar::chunk {{
    background: {tokens['primary']};
    border-radius: {tokens['radius_sm']};
}}
QFrame[widget="filterPanel"] {{
    background: {tokens['bg_panel']};
    border: {tokens['border_width']} solid {tokens['border_default']};
    border-radius: {tokens['radius_md']};
}}
QFrame#ConfigCategoryNav {{
    background: {tokens['bg_panel']};
    border: {tokens['border_width']} solid {tokens['border_default']};
    border-radius: {tokens['radius_md']};
    min-width: 120px;
}}
QFrame#ConfigCategoryNav QPushButton:checked {{
    color: {tokens['text_inverse']};
    background: {tokens['primary']};
    border-color: {tokens['primary']};
}}
QLabel[role="panelTitle"], QLabel[role="dialogTitle"] {{
    font-size: {tokens['font_size_lg']};
    font-weight: {tokens['font_weight_bold']};
}}
QLabel[role="fieldLabel"] {{
    color: {tokens['text_secondary']};
    font-weight: {tokens['font_weight_medium']};
}}
QLabel[role="warningText"] {{
    color: {tokens['status_warning']};
    font-size: {tokens['font_size_sm']};
    font-weight: {tokens['font_weight_medium']};
}}
QFrame[role="permissionHint"] {{
    background: {tokens['bg_panel']};
    border: {tokens['border_width']} solid {tokens['status_warning']};
    border-radius: {tokens['radius_md']};
}}
QFrame#ApiStatus[status="running"] {{
    border-left: 4px solid {tokens['status_normal']};
    background: #F0FDF4;
}}
QFrame#ApiStatus[status="stopped"] {{
    border-left: 4px solid {tokens['status_offline']};
    background: {tokens['bg_panel']};
}}
QFrame#ApiStatus[status="starting"] {{
    border-left: 4px solid {tokens['status_running']};
    background: {tokens['bg_subtle']};
}}
QFrame#ApiStatus[status="error"] {{
    border-left: 4px solid {tokens['status_high_alarm']};
    background: {tokens['bg_error']};
}}
QLabel[role="readonlyNotice"] {{
    color: #92400E;
    background: #FFFBEB;
    border: {tokens['border_width']} solid {tokens['status_warning']};
    border-radius: {tokens['radius_sm']};
    padding: {tokens['space_2']};
}}
QListWidget#ApiEndpointList::item {{
    min-height: 34px;
    padding: {tokens['space_2']};
}}
QFrame[backupResult="success"] {{
    background: #F0FDF4;
    border: {tokens['border_width']} solid {tokens['status_normal']};
    border-radius: {tokens['radius_md']};
}}
QFrame[backupResult="error"] {{
    background: {tokens['bg_error']};
    border: {tokens['border_width']} solid {tokens['status_high_alarm']};
    border-radius: {tokens['radius_md']};
}}
QFrame[backupResult="success"] QLabel {{ color: #14532D; }}
QFrame[backupResult="error"] QLabel {{ color: {tokens['status_high_alarm']}; }}
QPushButton[linkage="manual"] {{
    color: {tokens['text_inverse']};
    background: {tokens['primary']};
    border-color: {tokens['primary']};
    border-radius: {tokens['radius_md']};
}}
QPushButton[linkage="manual"]:disabled {{
    color: {tokens['disabled_text']};
    background: {tokens['disabled_bg']};
    border-color: {tokens['border_default']};
}}
QFrame[linkageResult="success"], QFrame[linkageStatus="triggered"] {{
    background: #F0FDF4;
    border: {tokens['border_width']} solid {tokens['status_normal']};
    border-radius: {tokens['radius_md']};
}}
QFrame[linkageResult="error"], QFrame[linkageStatus="error"] {{
    background: {tokens['bg_error']};
    border: {tokens['border_width']} solid {tokens['status_high_alarm']};
    border-radius: {tokens['radius_md']};
}}
QFrame[linkageStatus="idle"] {{
    background: {tokens['bg_subtle']};
    border: {tokens['border_width']} solid {tokens['border_default']};
    border-radius: {tokens['radius_md']};
}}
QFrame[maintenance="dueSoon"] {{
    background: #FFFBEB;
    border: {tokens['border_width']} solid {tokens['status_warning']};
    border-left: 4px solid {tokens['status_warning']};
    border-radius: {tokens['radius_md']};
}}
QFrame[maintenance="overdue"] {{
    background: #FEF2F2;
    border: {tokens['border_width']} solid {tokens['status_high_alarm']};
    border-left: 4px solid {tokens['status_high_alarm']};
    border-radius: {tokens['radius_md']};
}}
QFrame[maintenance="dueSoon"] QLabel {{ color: #92400E; }}
QFrame[maintenance="overdue"] QLabel {{ color: {tokens['status_high_alarm']}; }}
QDialog[danger="true"] {{
    background: {tokens['bg_panel']};
}}
QDialog[danger="true"] QPushButton[role="confirm"] {{
    color: {tokens['text_inverse']};
    background: {tokens['status_high_alarm']};
    border-color: {tokens['status_high_alarm']};
}}
QLabel[role="riskSummary"] {{
    color: {tokens['text_secondary']};
    background: {tokens['bg_subtle']};
    border: {tokens['border_width']} solid {tokens['border_default']};
    border-radius: {tokens['radius_sm']};
    padding: {tokens['space_2']};
}}
QFrame[role="metricCard"] {{
    background: {tokens['bg_panel']};
    border: {tokens['border_width']} solid {tokens['border_default']};
    border-radius: {tokens['radius_md']};
}}
QFrame[role="metricCard"][status="normal"] {{ border-left: 4px solid {tokens['status_normal']}; }}
QFrame[role="metricCard"][status="running"] {{ border-left: 4px solid {tokens['status_running']}; }}
QFrame[role="metricCard"][status="warning"] {{ border-left: 4px solid {tokens['status_warning']}; }}
QFrame[role="metricCard"][status="lowAlarm"] {{ border-left: 4px solid {tokens['status_low_alarm']}; }}
QFrame[role="metricCard"][status="highAlarm"], QFrame[role="metricCard"][status="overRange"] {{ border-left: 4px solid {tokens['status_high_alarm']}; }}
QFrame[role="metricCard"][status="fault"] {{ border-left: 4px solid {tokens['status_fault']}; }}
QFrame[role="metricCard"][status="offline"] {{ border-left: 4px solid {tokens['status_offline']}; }}
QFrame[role="metricCard"][status="shielded"] {{ border-left: 4px solid {tokens['status_shielded']}; }}
QFrame[role="metricCard"][status="warmup"] {{ border-left: 4px solid {tokens['status_warmup']}; }}
QLabel[role="metricValue"] {{
    font-size: {tokens['font_size_2xl']};
    font-weight: {tokens['font_weight_bold']};
}}
QLabel[role="metricValue"][status="normal"] {{ color: {tokens['status_normal']}; }}
QLabel[role="metricValue"][status="running"] {{ color: {tokens['status_running']}; }}
QLabel[role="metricValue"][status="warning"] {{ color: {tokens['status_warning']}; }}
QLabel[role="metricValue"][status="lowAlarm"] {{ color: {tokens['status_low_alarm']}; }}
QLabel[role="metricValue"][status="highAlarm"], QLabel[role="metricValue"][status="overRange"] {{ color: {tokens['status_high_alarm']}; }}
QLabel[role="metricValue"][status="fault"] {{ color: {tokens['status_fault']}; }}
QLabel[role="metricValue"][status="offline"] {{ color: {tokens['status_offline']}; }}
QLabel[role="metricValue"][status="shielded"] {{ color: {tokens['status_shielded']}; }}
QLabel[role="metricValue"][status="warmup"] {{ color: {tokens['status_warmup']}; }}
QFrame[card="detector"] {{
    background: {tokens['bg_panel']};
    border: {tokens['border_width']} solid {tokens['border_default']};
    border-radius: {tokens['radius_md']};
}}
QFrame[card="detector"]:hover {{
    border-color: {tokens['primary']};
}}
QFrame[card="detector"][selected="true"] {{
    border: {tokens['focus_width']} solid {tokens['primary']};
}}
QFrame[card="detector"][deviceStatus="normal"] {{ border-left: 4px solid {tokens['status_normal']}; }}
QFrame[card="detector"][deviceStatus="lowAlarm"] {{ border-left: 4px solid {tokens['status_low_alarm']}; }}
QFrame[card="detector"][deviceStatus="highAlarm"], QFrame[card="detector"][deviceStatus="overRange"] {{ border-left: 4px solid {tokens['status_high_alarm']}; }}
QFrame[card="detector"][deviceStatus="fault"] {{ border-left: 4px solid {tokens['status_fault']}; }}
QFrame[card="detector"][deviceStatus="offline"] {{ color: {tokens['status_offline']}; background: {tokens['disabled_bg']}; }}
QFrame[card="detector"][deviceStatus="shielded"] {{ border-left: 4px solid {tokens['status_shielded']}; }}
QFrame[card="detector"][deviceStatus="warmup"] {{ border-left: 4px solid {tokens['status_warmup']}; }}
QLabel[role="concentration"] {{
    font-size: {tokens['font_size_2xl']};
    font-weight: {tokens['font_weight_bold']};
}}
QFrame#MapToolbar {{
    background: {tokens['bg_panel']};
    border: {tokens['border_width']} solid {tokens['border_default']};
    border-radius: {tokens['radius_md']};
}}
QListWidget#MapList {{
    background: {tokens['bg_panel']};
    border: {tokens['border_width']} solid {tokens['border_default']};
    border-radius: {tokens['radius_md']};
}}
QListWidget#MapList::item {{
    min-height: 44px;
    padding: {tokens['space_2']};
}}
QFrame[role="mapPoint"] {{
    background: {tokens['status_normal']};
    border: {tokens['border_width']} solid {tokens['bg_panel']};
    border-radius: 22px;
}}
QFrame[role="mapPoint"][pointStatus="lowAlarm"] {{ background: {tokens['status_low_alarm']}; }}
QFrame[role="mapPoint"][pointStatus="highAlarm"], QFrame[role="mapPoint"][pointStatus="overRange"] {{ background: {tokens['status_high_alarm']}; }}
QFrame[role="mapPoint"][pointStatus="fault"] {{ background: {tokens['status_fault']}; }}
QFrame[role="mapPoint"][pointStatus="offline"] {{ background: {tokens['status_offline']}; }}
QFrame[role="mapPoint"][pointStatus="shielded"] {{ background: {tokens['status_shielded']}; }}
QFrame[role="mapPoint"][pointStatus="warmup"] {{ background: {tokens['status_warmup']}; }}
QFrame[role="mapPoint"][readonly="true"] {{ border-style: dashed; }}
QLabel[role="mapPointText"] {{
    color: {tokens['text_inverse']};
    font-size: {tokens['font_size_xs']};
    font-weight: {tokens['font_weight_bold']};
}}
QFrame[role="alarmListItem"] {{
    background: {tokens['bg_panel']};
    border: {tokens['border_width']} solid {tokens['border_default']};
    border-radius: {tokens['radius_md']};
}}
QFrame[role="alarmListItem"]:hover {{ border-color: {tokens['primary']}; }}
QFrame[role="controllerGroup"] {{
    background: {tokens['bg_panel']};
    border: {tokens['border_width']} solid {tokens['border_default']};
    border-radius: {tokens['radius_md']};
}}
QFrame[role="detectorDetail"] {{
    min-width: 280px;
}}
QFrame[role="recentRecord"] {{
    background: {tokens['bg_subtle']};
    border: {tokens['border_width']} solid {tokens['border_default']};
    border-radius: {tokens['radius_sm']};
}}
QDialog[role="alarmPopup"] {{
    background: {tokens['bg_panel']};
}}
QPlainTextEdit[viewer="hex"] {{
    font-family: {tokens['font_mono']};
    font-size: {tokens['font_size_sm']};
    background: #0B1220;
    color: #E2E8F0;
}}
QLabel#DebugResultBadge {{
    padding: {tokens['space_1']} {tokens['space_2']};
    border-radius: {tokens['radius_sm']};
    font-weight: {tokens['font_weight_bold']};
}}
QLabel#DebugResultBadge[debugResult="ok"] {{ color: {tokens['status_normal']}; }}
QLabel#DebugResultBadge[debugResult="warning"], QLabel#DebugResultBadge[debugResult="waiting"] {{ color: {tokens['status_warning']}; }}
QLabel#DebugResultBadge[debugResult="error"] {{ color: {tokens['status_high_alarm']}; }}
QFrame#ParsePanel {{
    background: {tokens['bg_panel']};
    border: {tokens['border_width']} solid {tokens['border_default']};
    border-radius: {tokens['radius_md']};
}}
QFrame#ChartPanel {{
    background: {tokens['bg_panel']};
    border: {tokens['border_width']} solid {tokens['border_default']};
    border-radius: {tokens['radius_md']};
}}
QLabel[role="chartEmpty"] {{
    color: {tokens['status_offline']};
    font-size: {tokens['font_size_md']};
}}
QFrame#ChartPanel QPushButton {{
    min-width: 48px;
}}
QCheckBox {{
    spacing: {tokens['space_2']};
    background: transparent;
}}
QMainWindow#MainWindowShell {{
    background: {tokens['bg_window']};
}}
QListWidget#ShellNav {{
    background: #111827;
    color: #CBD5E1;
    border: none;
    font-size: {tokens['font_size_md']};
    outline: none;
}}
QListWidget#ShellNav::item {{
    min-height: 40px;
    padding-left: {tokens['space_4']};
}}
QListWidget#ShellNav::item:selected {{
    background: {tokens['primary']};
    color: {tokens['text_inverse']};
}}
QListWidget#ShellNav::item:disabled {{
    color: #64748B;
}}
QFrame#ShellContent {{
    background: {tokens['bg_window']};
}}
QFrame#ShellTopBar {{
    background: {tokens['bg_panel']};
    border-bottom: {tokens['border_width']} solid {tokens['border_default']};
}}
QFrame#GlobalAlertBar {{
    background: {tokens['bg_subtle']};
    border-bottom: {tokens['border_width']} solid {tokens['border_default']};
}}
QFrame#GlobalAlertBar[active="true"] {{
    background: {tokens['status_high_alarm']};
    border-bottom-color: {tokens['status_high_alarm']};
}}
QFrame#GlobalAlertBar[active="true"] QLabel {{
    color: {tokens['text_inverse']};
    font-weight: {tokens['font_weight_bold']};
}}
QLabel#ShellBottomMessage {{
    min-height: 28px;
    padding: {tokens['space_1']} {tokens['space_4']};
    color: {tokens['text_secondary']};
    background: {tokens['bg_panel']};
    border-top: {tokens['border_width']} solid {tokens['border_default']};
}}
QLabel#ShellBottomMessage[status="warning"] {{
    color: #92400E;
    background: #FFFBEB;
}}
QStackedWidget#ShellPageStack {{
    background: {tokens['bg_window']};
    border: none;
}}
""".strip()
