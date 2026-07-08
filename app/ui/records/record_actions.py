from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QPushButton, QWidget

from app.services.errors import ErrorCode
from app.services.permissions import Permission, role_has_permission
from app.services.record_service import ClearRecordsCommand, ExportRecordsCommand, RecordType
from app.ui.common.dialogs import RiskConfirmDialog
from app.ui.common.errors import controlled_error_text, permission_denied_text
from app.ui.common.safe_text import SafeTextLabel
from app.ui.records.record_filters import RECORD_TYPE_LABELS

ConfirmCallback = Callable[[QWidget | None, str, object, str], bool]


class RecordActionBar(QFrame):
    recordChanged = Signal()
    exportBuilt = Signal(object)
    printBuilt = Signal(object)

    def __init__(
        self,
        record_service: object,
        session: object | None,
        parent: QWidget | None = None,
        *,
        can_delete: bool | None = None,
        confirm_danger: ConfirmCallback | None = None,
    ) -> None:
        super().__init__(parent)
        self.setProperty("panel", "true")
        self.record_service = record_service
        self.session = session
        self._can_delete = _can_delete_from_session(session) if can_delete is None else can_delete
        self._confirm_danger = confirm_danger or _confirm_danger
        self._record_type: RecordType = "alarm"
        self._record_id: int | None = None
        self._filters: dict[str, object] = {}
        self._busy = False
        self.last_message = ""
        self.last_export_payload: object | None = None
        self.last_print_payload: object | None = None

        self.delete_button = QPushButton("删除选中")
        self.delete_button.setProperty("recordAction", "delete")
        self.delete_button.clicked.connect(self.delete_selected)
        self.clear_button = QPushButton("批量清空")
        self.clear_button.setProperty("recordAction", "clearAll")
        self.clear_button.clicked.connect(self.clear_current)
        self.export_button = QPushButton("导出 Excel")
        self.export_button.clicked.connect(lambda: self.export_current("xlsx"))
        self.pdf_button = QPushButton("导出 PDF")
        self.pdf_button.clicked.connect(lambda: self.export_current("pdf"))
        self.print_button = QPushButton("打印")
        self.print_button.clicked.connect(lambda: self.export_current("print"))
        self.message_label = SafeTextLabel("", selectable=True)
        self.message_label.setProperty("role", "muted")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(8)
        layout.addWidget(self.delete_button)
        layout.addWidget(self.clear_button)
        layout.addWidget(self.export_button)
        layout.addWidget(self.pdf_button)
        layout.addWidget(self.print_button)
        layout.addWidget(self.message_label, 1)
        self.set_context("alarm", None, {})
        self._apply_state()

    def set_context(self, record_type: RecordType, selected_record_id: int | None, filters: dict[str, object]) -> None:
        self._record_type = _record_type(record_type)
        self._record_id = selected_record_id if selected_record_id and selected_record_id > 0 else None
        self._filters = dict(filters)
        self._apply_state()

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._apply_state()

    def show_permission_denied(self) -> None:
        self._set_message(permission_denied_text())

    def delete_selected(self) -> bool:
        if not self._require_delete_permission():
            return False
        if self._record_id is None:
            self._set_message("请先选择一条记录")
            return False
        label = RECORD_TYPE_LABELS[self._record_type]
        # UI confirmation is required before calling the service with confirmed=True;
        # the service remains authoritative if another caller bypasses this page.
        if not self._confirm_danger(self, f"确认删除{label}", f"将删除记录 ID：{self._record_id}。此操作不可撤销。", "确认删除"):
            return False
        self.set_busy(True)
        try:
            result = self.record_service.delete_record(
                self.session,
                record_type=self._record_type,
                record_id=self._record_id,
                confirmed=True,
            )
        except Exception:
            result = _failure("删除记录失败")
        finally:
            self.set_busy(False)
        if not _result_success(result):
            self._set_result_error(result, "删除记录失败")
            return False
        self._set_message("记录已删除")
        self.recordChanged.emit()
        return True

    def clear_current(self) -> bool:
        if not self._require_delete_permission():
            return False
        label = RECORD_TYPE_LABELS[self._record_type]
        risk = "将按当前筛选条件批量清空记录。请确认筛选范围正确，此操作不可撤销。"
        if not self._confirm_danger(self, f"确认清空{label}", risk, "确认清空"):
            return False
        self.set_busy(True)
        try:
            result = self.record_service.clear_records(
                self.session,
                ClearRecordsCommand(self._record_type, filters=dict(self._filters), confirmed=True),
            )
        except Exception:
            result = _failure("清空记录失败")
        finally:
            self.set_busy(False)
        if not _result_success(result):
            self._set_result_error(result, "清空记录失败")
            return False
        deleted_count = getattr(_result_data(result), "deleted_count", "")
        self._set_message(f"记录已清空，删除 {deleted_count} 条")
        self.recordChanged.emit()
        return True

    def export_current(self, export_format: str = "xlsx") -> bool:
        if self._busy:
            return False
        self.set_busy(True)
        try:
            result = self.record_service.export_records(
                self.session,
                ExportRecordsCommand(self._record_type, filters=dict(self._filters), export_format=_export_format(export_format)),
            )
        except Exception:
            result = _failure("导出数据准备失败")
        finally:
            self.set_busy(False)
        if not _result_success(result):
            self._set_result_error(result, "导出数据准备失败")
            return False
        payload = _result_data(result)
        filename = getattr(payload, "filename", "导出结果")
        # Only the export filename is shown; internal output paths are deliberately
        # not constructed or displayed by the UI layer.
        self._set_message(f"导出数据已准备：{filename}")
        if export_format == "print":
            self.last_print_payload = payload
            self.printBuilt.emit(payload)
        else:
            self.last_export_payload = payload
            self.exportBuilt.emit(payload)
        return True

    def _require_delete_permission(self) -> bool:
        if self._can_delete:
            return True
        self.show_permission_denied()
        return False

    def _set_result_error(self, result: object, fallback: str) -> None:
        if int(getattr(result, "code", 0) or 0) == int(ErrorCode.PERMISSION_DENIED):
            self.show_permission_denied()
            return
        self._set_message(controlled_error_text(getattr(result, "message", fallback), fallback=fallback))

    def _set_message(self, message: object) -> None:
        self.message_label.set_safe_text(message)
        self.last_message = self.message_label.text()

    def _apply_state(self) -> None:
        self.delete_button.setEnabled(self._can_delete and not self._busy and self._record_id is not None)
        self.clear_button.setEnabled(self._can_delete and not self._busy)
        for button in (self.export_button, self.pdf_button, self.print_button):
            button.setEnabled(not self._busy)
        permission_tip = "🔒 " if not self._can_delete else ""
        self.delete_button.setText(f"{permission_tip}删除选中")
        self.clear_button.setText(f"{permission_tip}批量清空")


def _confirm_danger(parent: QWidget | None, title: str, message: object, confirm_text: str) -> bool:
    return RiskConfirmDialog.confirm(title, message, parent, confirm_text=confirm_text)


def _can_delete_from_session(session: object | None) -> bool:
    role = getattr(session, "role", None)
    if role is None:
        return False
    try:
        return role_has_permission(str(role), Permission.RECORD_DELETE.value) and role_has_permission(str(role), Permission.RECORD_CLEAR.value)
    except ValueError:
        return False


def _record_type(value: str) -> RecordType:
    if value not in RECORD_TYPE_LABELS:
        raise ValueError("unsupported record type")
    return value  # type: ignore[return-value]


def _export_format(value: str):  # noqa: ANN201
    if value not in {"xlsx", "pdf", "print"}:
        raise ValueError("unsupported export format")
    return value


def _result_success(result: object) -> bool:
    return bool(getattr(result, "success", False))


def _result_data(result: object) -> object:
    return getattr(result, "data", None)


def _failure(message: str) -> object:
    from app.services.models import ServiceResult

    return ServiceResult.fail(500, message)
