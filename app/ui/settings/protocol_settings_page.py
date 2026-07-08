from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtWidgets import QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QComboBox, QVBoxLayout, QWidget

from app.services.errors import ErrorCode
from app.ui.common.errors import ErrorBanner
from app.ui.common.safe_text import SafeTextLabel
from app.ui.settings.import_result_dialog import ImportResultDialog

SAVE_FAILED_TEXT = "协议或导入导出操作失败，请稍后重试。"
PROTOCOL_RESTART_TEXT = "切换协议模式需停止采集，并在重启采集或重启软件后生效。"


class ProtocolSettingsPage(QWidget):
    def __init__(
        self,
        device_config_service: object | None = None,
        session: object | None = None,
        parent: QWidget | None = None,
        *,
        can_configure: bool = True,
        import_path_provider: object | None = None,
        export_path_provider: object | None = None,
        template_path_provider: object | None = None,
        import_result_dialog_factory: object | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = device_config_service
        self._session = session
        self._can_configure = can_configure
        self._import_path_provider = import_path_provider or self._choose_import_path
        self._export_path_provider = export_path_provider or self._choose_export_path
        self._template_path_provider = template_path_provider or self._choose_template_path
        self._dialog_factory = import_result_dialog_factory or self._default_dialog_factory
        self.last_import_dialog: ImportResultDialog | None = None

        self.error_banner = ErrorBanner(); self.error_banner.clear()
        self.title_label = SafeTextLabel("协议设置与导入导出", selectable=False)
        self.title_label.setProperty("role", "panelTitle")
        self.warning_label = SafeTextLabel(PROTOCOL_RESTART_TEXT, selectable=False)
        self.warning_label.setProperty("role", "warningText")
        self.protocol_combo = QComboBox()
        self.protocol_combo.addItem("协议 1", "protocol_1")
        self.protocol_combo.addItem("协议 2", "protocol_2")
        self.mode_hint = SafeTextLabel("当前项目一次只启用一种协议模式。", selectable=False)
        self.mode_hint.setProperty("role", "muted")
        self.save_protocol_button = QPushButton("保存协议模式")
        self.save_protocol_button.setProperty("variant", "primary")
        self.save_protocol_button.clicked.connect(self.save_protocol_mode)

        self.import_file_label = SafeTextLabel("未选择导入文件", selectable=True)
        self.export_file_label = SafeTextLabel("未选择导出文件", selectable=True)
        self.import_button = QPushButton("导入探测器")
        self.export_button = QPushButton("导出配置")
        self.template_button = QPushButton("下载模板")
        self.import_button.clicked.connect(self.import_detectors)
        self.export_button.clicked.connect(self.export_config)
        self.template_button.clicked.connect(self.download_template)

        card = QFrame(); card.setProperty("panel", "true")
        grid = QGridLayout(card); grid.setContentsMargins(16, 16, 16, 16); grid.setHorizontalSpacing(12); grid.setVerticalSpacing(10)
        self._add_field(grid, 0, "协议模式", self.protocol_combo)
        grid.addWidget(self.mode_hint, 1, 1)
        grid.addWidget(self.save_protocol_button, 2, 1)
        self._add_field(grid, 3, "导入文件", self.import_file_label)
        grid.addWidget(self.import_button, 4, 1)
        self._add_field(grid, 5, "导出文件", self.export_file_label)
        export_actions = QHBoxLayout(); export_actions.addWidget(self.export_button); export_actions.addWidget(self.template_button); export_actions.addStretch(1)
        grid.addLayout(export_actions, 6, 1)

        layout = QVBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(12)
        layout.addWidget(self.error_banner)
        layout.addWidget(self.title_label)
        layout.addWidget(self.warning_label)
        layout.addWidget(card)
        layout.addStretch(1)
        self._apply_permission_state()

    def reload(self) -> None:
        if self._service is None or not hasattr(self._service, "get_protocol_mode"):
            return
        try:
            mode = self._service.get_protocol_mode()
        except Exception:
            self.error_banner.set_error(SAVE_FAILED_TEXT); return
        index = self.protocol_combo.findData(mode)
        if index >= 0:
            self.protocol_combo.setCurrentIndex(index)

    def save_protocol_mode(self) -> None:
        if not self._require_permission():
            return
        try:
            result = self._service.set_protocol_mode(self._session, str(self.protocol_combo.currentData()))
        except Exception:
            self.error_banner.set_error(SAVE_FAILED_TEXT); return
        if bool(getattr(result, "success", False)):
            # Protocol adapters are deployment-wide. The UI makes the lifecycle
            # boundary explicit instead of implying that live polling changed instantly.
            message = getattr(result, "message", "协议模式已切换，请重启采集或软件后生效。")
            self.error_banner.set_error(message, severity="warning")
            return
        if int(getattr(result, "code", 0) or 0) == int(ErrorCode.PERMISSION_DENIED):
            self.error_banner.show_permission_denied()
        else:
            self.error_banner.set_error(getattr(result, "message", SAVE_FAILED_TEXT))

    def import_detectors(self) -> None:
        if not self._require_permission():
            return
        path = _provider_path(self._import_path_provider)
        if path is None:
            return
        self.import_file_label.set_safe_text(f"文件：{path.name}")
        self.import_button.setEnabled(False)
        try:
            result = self._service.import_detectors(self._session, path)
        except Exception:
            self.error_banner.set_error(SAVE_FAILED_TEXT); self.import_button.setEnabled(True); return
        self.import_button.setEnabled(self._can_configure)
        if not bool(getattr(result, "success", False)):
            self.error_banner.set_error(getattr(result, "message", SAVE_FAILED_TEXT)); return
        data = getattr(result, "data", None)
        dialog = self._dialog_factory(
            imported_count=int(getattr(data, "imported_count", 0) or 0),
            errors=tuple(getattr(data, "errors", ()) or ()),
            source_name=path.name,
            parent=self,
        )
        self.last_import_dialog = dialog
        dialog.exec()

    def export_config(self) -> None:
        if not self._require_permission():
            return
        path = _provider_path(self._export_path_provider)
        if path is None:
            return
        self.export_file_label.set_safe_text(f"文件：{path.name}")
        self._run_export("export_detectors", path, self.export_button, fallback="导出服务未配置")

    def download_template(self) -> None:
        if not self._require_permission():
            return
        path = _provider_path(self._template_path_provider)
        if path is None:
            return
        self.export_file_label.set_safe_text(f"文件：{path.name}")
        self._run_export("export_detector_template", path, self.template_button, fallback="模板下载服务未配置")

    def _run_export(self, method_name: str, path: Path, button: QPushButton, *, fallback: str) -> None:
        method = getattr(self._service, method_name, None) if self._service is not None else None
        if method is None:
            self.error_banner.set_error(fallback); return
        button.setEnabled(False)
        try:
            result = method(path)
        except TypeError:
            result = method(self._session, path)
        except Exception:
            self.error_banner.set_error(SAVE_FAILED_TEXT); button.setEnabled(True); return
        button.setEnabled(self._can_configure)
        if bool(getattr(result, "success", False)):
            self.error_banner.set_error("文件已生成。", severity="warning")
        else:
            self.error_banner.set_error(getattr(result, "message", SAVE_FAILED_TEXT))

    def _require_permission(self) -> bool:
        if self._can_configure:
            return True
        self.error_banner.show_permission_denied(); return False

    def _apply_permission_state(self) -> None:
        for widget in (self.protocol_combo, self.save_protocol_button, self.import_button, self.export_button, self.template_button):
            widget.setEnabled(self._can_configure)

    def _choose_import_path(self) -> Path | None:
        path, _ = QFileDialog.getOpenFileName(self, "选择导入文件", "", "CSV 文件 (*.csv);;Excel 文件 (*.xlsx);;所有文件 (*)")
        return Path(path) if path else None

    def _choose_export_path(self) -> Path | None:
        path, _ = QFileDialog.getSaveFileName(self, "选择导出文件", "detectors.csv", "CSV 文件 (*.csv);;所有文件 (*)")
        return Path(path) if path else None

    def _choose_template_path(self) -> Path | None:
        path, _ = QFileDialog.getSaveFileName(self, "选择模板保存位置", "detector_template.csv", "CSV 文件 (*.csv);;所有文件 (*)")
        return Path(path) if path else None

    def _default_dialog_factory(self, **kwargs: Any) -> ImportResultDialog:
        return ImportResultDialog(**kwargs)

    @staticmethod
    def _add_field(grid: QGridLayout, row: int, label: str, widget: QWidget) -> None:
        label_widget = QLabel(label); label_widget.setProperty("role", "fieldLabel")
        grid.addWidget(label_widget, row, 0); grid.addWidget(widget, row, 1)


def _provider_path(provider: object) -> Path | None:
    value = provider() if callable(provider) else provider
    if value in {None, ""}:
        return None
    return Path(value)
