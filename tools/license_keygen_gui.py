from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.services.license_codes import DEFAULT_LICENSE_SIGNING_KEY, build_authorization_code
from app.ui.theme import AppTheme
from tools.license_keygen import build_license_payload, mask_machine_code
from tools.license_registry import CustomerLicenseRecord, CustomerLicenseStore


class LicenseKeygenWindow(QWidget):
    def __init__(self, store: CustomerLicenseStore | None = None) -> None:
        super().__init__()
        self.setObjectName("LicenseKeygenWindow")
        self.setStyleSheet(KEYGEN_QSS)
        self._store = store or CustomerLicenseStore()
        self._records: tuple[CustomerLicenseRecord, ...] = ()
        self._last_record: CustomerLicenseRecord | None = None

        self.setWindowTitle("堃联气体报警系统授权注册机")
        self.resize(860, 620)

        self.tabs = QTabWidget(self)
        self.tabs.setObjectName("KeygenTabs")
        self.tabs.tabBar().setObjectName("KeygenTabBar")
        self.generate_tab = QWidget()
        self.records_tab = QWidget()
        self.tabs.addTab(self.generate_tab, "生成授权")
        self.tabs.addTab(self.records_tab, "客户列表")

        self._build_generate_tab()
        self._build_records_tab()

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.addWidget(self.tabs)
        self.reload_records()

    def generate(self) -> bool:
        try:
            expires_at = None if self.permanent_checkbox.isChecked() else self.expires_at_edit.text().strip()
            payload = build_license_payload(
                machine_code=self.machine_code_edit.text(),
                expires_at=expires_at,
                customer_name=self.customer_name_edit.text().strip() or None,
                note=self.note_edit.text().strip() or None,
            )
            code = build_authorization_code(payload, DEFAULT_LICENSE_SIGNING_KEY)
            record = self._store.save_record(
                customer_name=payload.get("customer_name", "未命名客户"),
                machine_code=payload["machine_fingerprint_hash"],
                license_type=payload["license_type"],
                issued_at=payload["issued_at"],
                expires_at=payload.get("expires_at"),
                note=payload.get("note", ""),
                authorization_code=code,
            )
        except (ValueError, OSError) as exc:
            self.show_status(f"生成失败：{exc}", error=True)
            return False
        self._last_record = record
        self.result_edit.setPlainText(code)
        self.show_status(
            f"授权码已生成并保存客户记录：{record.customer_name} / {mask_machine_code(record.machine_code)}。",
            error=False,
        )
        self.reload_records()
        return True

    def copy_code(self) -> bool:
        code = self.result_edit.toPlainText().strip()
        if not code:
            self.show_status("请先生成授权码。", error=True)
            return False
        QApplication.clipboard().setText(code)
        self.show_status("授权码已复制。", error=False)
        return True

    def save_license_file(self) -> bool:
        code = self.result_edit.toPlainText().strip()
        if not code and not self.generate():
            return False
        code = self.result_edit.toPlainText().strip()
        default_name = _safe_filename(self.customer_name_edit.text().strip() or "customer") + ".lic"
        path, _ = QFileDialog.getSaveFileName(self, "保存授权文件", default_name, "授权文件 (*.lic);;所有文件 (*)")
        if not path:
            return False
        try:
            Path(path).write_text(code + "\n", encoding="utf-8")
        except OSError:
            self.show_status("保存失败，请检查输出目录权限。", error=True)
            return False
        self.show_status(f"授权文件已保存：{Path(path).name}", error=False)
        return True

    def clear_output(self) -> None:
        self.result_edit.clear()
        self.status_label.setText("")

    def reload_records(self) -> None:
        self._records = self._store.list_records()
        self.records_table.setRowCount(len(self._records))
        for row_index, record in enumerate(self._records):
            values = (
                record.customer_name,
                mask_machine_code(record.machine_code),
                "永久" if record.license_type == "permanent" else "期限",
                _display_time(record.expires_at) if record.expires_at else "永久",
                record.note,
                _display_time(record.issued_at),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.records_table.setItem(row_index, column, item)
        self.records_status_label.setText(f"共 {len(self._records)} 条客户记录")

    def copy_selected_record_code(self) -> bool:
        record = self._selected_record()
        if record is None:
            self.records_status_label.setText("请先选择一条客户记录。")
            return False
        QApplication.clipboard().setText(record.authorization_code)
        self.records_status_label.setText(f"已复制 {record.customer_name} 的授权码。")
        return True

    def load_selected_record_to_form(self) -> bool:
        record = self._selected_record()
        if record is None:
            self.records_status_label.setText("请先选择一条客户记录。")
            return False
        self.customer_name_edit.setText(record.customer_name)
        self.machine_code_edit.setText(record.machine_code)
        self.note_edit.setText(record.note)
        self.permanent_checkbox.setChecked(record.license_type == "permanent")
        self.expires_at_edit.setText(record.expires_at[:10] if record.expires_at else "")
        self.result_edit.setPlainText(record.authorization_code)
        self.tabs.setCurrentWidget(self.generate_tab)
        self.show_status(f"已载入客户记录：{record.customer_name}", error=False)
        return True

    def show_status(self, message: str, *, error: bool) -> None:
        self.status_label.setText(message)
        self.status_label.setProperty("status", "highAlarm" if error else "normal")
        style = self.status_label.style()
        style.unpolish(self.status_label)
        style.polish(self.status_label)

    def _apply_expiration_state(self, permanent: bool) -> None:
        self.expires_at_edit.setEnabled(not permanent)
        if permanent:
            self.expires_at_edit.clear()

    def _selected_record(self) -> CustomerLicenseRecord | None:
        selected = self.records_table.selectionModel().selectedRows()
        if not selected:
            return None
        row = selected[0].row()
        return self._records[row] if 0 <= row < len(self._records) else None

    def _build_generate_tab(self) -> None:
        card = QFrame(self.generate_tab)
        card.setProperty("panel", "true")

        title_label = QLabel("授权注册机")
        title_label.setProperty("role", "panelTitle")
        subtitle_label = QLabel("客户只需要复制机器码、粘贴授权码；本工具会自动保存客户授权记录。")
        subtitle_label.setProperty("role", "muted")

        self.machine_code_edit = QLineEdit()
        self.machine_code_edit.setPlaceholderText("粘贴客户授权窗口复制的完整机器码")
        self.machine_code_edit.setMaxLength(160)
        self.permanent_checkbox = QCheckBox("永久授权")
        self.permanent_checkbox.setChecked(True)
        self.permanent_checkbox.toggled.connect(self._apply_expiration_state)
        self.expires_at_edit = QLineEdit()
        self.expires_at_edit.setPlaceholderText("YYYY-MM-DD，例如 2027-12-31")
        self.expires_at_edit.setEnabled(False)
        self.customer_name_edit = QLineEdit()
        self.customer_name_edit.setPlaceholderText("客户名称")
        self.note_edit = QLineEdit()
        self.note_edit.setPlaceholderText("备注")

        form = QGridLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)
        form.addWidget(_field_label("客户名称"), 0, 0)
        form.addWidget(self.customer_name_edit, 0, 1)
        form.addWidget(_field_label("机器码"), 1, 0)
        form.addWidget(self.machine_code_edit, 1, 1)
        form.addWidget(_field_label("授权期限"), 2, 0)
        form.addWidget(self.permanent_checkbox, 2, 1)
        form.addWidget(_field_label("到期日期"), 3, 0)
        form.addWidget(self.expires_at_edit, 3, 1)
        form.addWidget(_field_label("备注"), 4, 0)
        form.addWidget(self.note_edit, 4, 1)

        self.generate_button = QPushButton("生成授权码")
        self.generate_button.setProperty("variant", "primary")
        self.copy_button = QPushButton("复制授权码")
        self.save_button = QPushButton("保存 .lic")
        self.clear_button = QPushButton("清空")
        self.generate_button.clicked.connect(self.generate)
        self.copy_button.clicked.connect(self.copy_code)
        self.save_button.clicked.connect(self.save_license_file)
        self.clear_button.clicked.connect(self.clear_output)

        actions = QHBoxLayout()
        actions.addWidget(self.generate_button)
        actions.addWidget(self.copy_button)
        actions.addWidget(self.save_button)
        actions.addStretch(1)
        actions.addWidget(self.clear_button)

        self.result_edit = QPlainTextEdit()
        self.result_edit.setReadOnly(True)
        self.result_edit.setPlaceholderText("授权码会显示在这里")
        self.result_edit.setMaximumBlockCount(20)
        self.status_label = QLabel("")
        self.status_label.setProperty("role", "muted")

        layout = QVBoxLayout(card)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)
        layout.addWidget(title_label)
        layout.addWidget(subtitle_label)
        layout.addLayout(form)
        layout.addLayout(actions)
        layout.addWidget(self.result_edit)
        layout.addWidget(self.status_label)

        root = QVBoxLayout(self.generate_tab)
        root.setContentsMargins(12, 12, 12, 12)
        root.addWidget(card)

    def _build_records_tab(self) -> None:
        card = QFrame(self.records_tab)
        card.setProperty("panel", "true")

        title_label = QLabel("客户授权列表")
        title_label.setProperty("role", "panelTitle")
        subtitle_label = QLabel("记录客户名称、机器码、期限和备注；同一机器码再次生成会更新原记录。")
        subtitle_label.setProperty("role", "muted")

        self.records_table = QTableWidget(0, 6)
        self.records_table.setObjectName("CustomerRecordsTable")
        self.records_table.setHorizontalHeaderLabels(("客户名称", "机器码", "授权", "到期时间", "备注", "生成时间"))
        self.records_table.setAlternatingRowColors(True)
        self.records_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.records_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.records_table.verticalHeader().setVisible(False)
        self.records_table.horizontalHeader().setObjectName("CustomerRecordsHeader")
        self.records_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)

        self.copy_record_button = QPushButton("复制选中授权码")
        self.load_record_button = QPushButton("载入到生成页")
        self.refresh_records_button = QPushButton("刷新")
        self.copy_record_button.clicked.connect(self.copy_selected_record_code)
        self.load_record_button.clicked.connect(self.load_selected_record_to_form)
        self.refresh_records_button.clicked.connect(self.reload_records)

        actions = QHBoxLayout()
        actions.addWidget(self.copy_record_button)
        actions.addWidget(self.load_record_button)
        actions.addStretch(1)
        actions.addWidget(self.refresh_records_button)

        self.records_status_label = QLabel("")
        self.records_status_label.setProperty("role", "muted")

        layout = QVBoxLayout(card)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)
        layout.addWidget(title_label)
        layout.addWidget(subtitle_label)
        layout.addLayout(actions)
        layout.addWidget(self.records_table, 1)
        layout.addWidget(self.records_status_label)

        root = QVBoxLayout(self.records_tab)
        root.setContentsMargins(12, 12, 12, 12)
        root.addWidget(card)


def _field_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setProperty("role", "fieldLabel")
    label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    return label


def _safe_filename(value: str) -> str:
    text = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value.strip())
    return text[:80] or "customer"


def _display_time(value: str | None) -> str:
    if not value:
        return ""
    text = value.strip()
    if re_match := _parse_time(text):
        return re_match.strftime("%Y-%m-%d %H:%M:%S")
    if "T" in text:
        return text.replace("T", " ").split("+", 1)[0].split(".", 1)[0]
    return text


def _parse_time(value: str) -> datetime | None:
    for candidate in (value, value.replace("Z", "+00:00")):
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            continue
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone()
        return parsed.replace(tzinfo=None)
    return None


KEYGEN_QSS = """
QWidget#LicenseKeygenWindow {
    background: #F1F5F9;
    color: #0F172A;
}
QWidget#LicenseKeygenWindow QLabel {
    color: #0F172A;
    background: transparent;
}
QWidget#LicenseKeygenWindow QLabel[role="muted"] {
    color: #475569;
}
QWidget#LicenseKeygenWindow QLabel[role="panelTitle"] {
    color: #0F172A;
    font-size: 18px;
    font-weight: 700;
}
QWidget#LicenseKeygenWindow QLabel[role="fieldLabel"] {
    color: #334155;
    font-weight: 600;
}
QTabWidget#KeygenTabs::pane {
    background: #FFFFFF;
    border: 1px solid #CBD5E1;
    border-radius: 6px;
    top: -1px;
}
QTabBar#KeygenTabBar::tab {
    min-width: 112px;
    min-height: 34px;
    padding: 6px 18px;
    margin-right: 4px;
    color: #0F172A;
    background: #E2E8F0;
    border: 1px solid #CBD5E1;
    border-bottom: none;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    font-weight: 700;
}
QTabBar#KeygenTabBar::tab:selected {
    color: #FFFFFF;
    background: #1D4ED8;
    border-color: #1D4ED8;
}
QTabBar#KeygenTabBar::tab:!selected:hover {
    color: #0F172A;
    background: #CBD5E1;
}
QFrame[panel="true"] {
    background: #FFFFFF;
    border: 1px solid #CBD5E1;
    border-radius: 6px;
}
QLineEdit, QPlainTextEdit {
    color: #0F172A;
    background: #FFFFFF;
    border: 1px solid #94A3B8;
    border-radius: 4px;
    padding: 6px 8px;
    selection-background-color: #1D4ED8;
    selection-color: #FFFFFF;
}
QLineEdit:focus, QPlainTextEdit:focus {
    border: 2px solid #1D4ED8;
}
QCheckBox {
    color: #0F172A;
    background: transparent;
    spacing: 8px;
}
QPushButton {
    min-height: 32px;
    color: #0F172A;
    background: #FFFFFF;
    border: 1px solid #94A3B8;
    border-radius: 6px;
    padding: 0 14px;
    font-weight: 600;
}
QPushButton:hover {
    border-color: #1D4ED8;
    background: #EFF6FF;
}
QPushButton[variant="primary"] {
    color: #FFFFFF;
    background: #1D4ED8;
    border-color: #1D4ED8;
}
QPushButton[variant="primary"]:hover {
    background: #2563EB;
    border-color: #2563EB;
}
QTableWidget#CustomerRecordsTable {
    color: #0F172A;
    background: #FFFFFF;
    alternate-background-color: #F8FAFC;
    border: 1px solid #94A3B8;
    border-radius: 4px;
    gridline-color: #CBD5E1;
    selection-background-color: #1D4ED8;
    selection-color: #FFFFFF;
    outline: 0;
}
QTableWidget#CustomerRecordsTable::item {
    min-height: 30px;
    padding: 6px;
}
QTableWidget#CustomerRecordsTable::item:selected {
    color: #FFFFFF;
    background: #1D4ED8;
}
QTableWidget#CustomerRecordsTable::item:selected:!active {
    color: #FFFFFF;
    background: #2563EB;
}
QHeaderView#CustomerRecordsHeader::section {
    color: #0F172A;
    background: #CBD5E1;
    border: none;
    border-right: 1px solid #94A3B8;
    border-bottom: 1px solid #94A3B8;
    padding: 8px;
    font-weight: 700;
}
QWidget#LicenseKeygenWindow QLabel[status="normal"] {
    color: #15803D;
    font-weight: 700;
}
QWidget#LicenseKeygenWindow QLabel[status="highAlarm"] {
    color: #B91C1C;
    font-weight: 700;
}
""".strip()


def main() -> int:
    app = QApplication.instance() or QApplication(["license-keygen"])
    AppTheme().apply_to(app)
    window = LicenseKeygenWindow()
    window.show()
    return int(app.exec())


if __name__ == "__main__":
    raise SystemExit(main())
