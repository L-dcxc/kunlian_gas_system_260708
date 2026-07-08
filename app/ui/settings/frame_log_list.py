from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPushButton, QVBoxLayout, QWidget

from app.ui.common.data_table import DataTable, TableColumn, TableState
from app.ui.common.safe_text import SafeTextLabel, normalize_plain_text

MAX_FRAME_LOG_ROWS = 100
FRAME_LOG_SUMMARY_CHARS = 180


class FrameLogList(QWidget):
    def __init__(self, parent: QWidget | None = None, *, max_rows: int = MAX_FRAME_LOG_ROWS) -> None:
        super().__init__(parent)
        if max_rows < 1:
            raise ValueError("max_rows must be positive")
        self._max_rows = max_rows
        self._rows: list[dict[str, object]] = []

        self.title_label = SafeTextLabel("调试记录", selectable=False)
        self.title_label.setProperty("role", "panelTitle")
        self.table = DataTable(
            [
                TableColumn("time", "时间", 150),
                TableColumn("direction", "方向/结果", 100),
                TableColumn("status", "状态", 90),
                TableColumn("summary", "摘要", 360),
            ]
        )
        self.table.export_button.setVisible(False)
        self.table.empty_action_button.setVisible(False)
        self.table.set_page(1, 0, 1)
        self.table.set_state(TableState.EMPTY, "暂无调试记录")
        self.clear_button = QPushButton("清空记录")
        self.clear_button.clicked.connect(self.clear)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self.title_label)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.clear_button, alignment=Qt.AlignmentFlag.AlignRight)

    def append_result(self, result: object) -> None:
        now = _display_time(getattr(getattr(result, "exchange", None), "created_at", None))
        request_hex = getattr(result, "request_hex", "") or ""
        response_hex = getattr(result, "response_hex", "") or ""
        crc_ok = getattr(result, "crc_ok", None)
        validation_message = getattr(result, "validation_message", "") or ""
        error_code = getattr(result, "error_code", "") or ""

        self._append_row(now, "发送", "已发送", request_hex or "已生成读请求")
        if response_hex:
            self._append_row(now, "接收", _result_text(crc_ok), response_hex)
        else:
            self._append_row(now, "响应", _result_text(crc_ok), validation_message or error_code or "未收到响应")
        if validation_message or error_code:
            self._append_row(now, "诊断", _result_text(crc_ok), validation_message or error_code)
        self._refresh()

    def clear(self) -> None:
        self._rows.clear()
        self._refresh()

    def rows(self) -> tuple[Mapping[str, object], ...]:
        return tuple(self._rows)

    def _append_row(self, time_text: str, direction: str, status: str, summary: object) -> None:
        # 调试日志仅保存在 UI 内存中，避免把原始帧写入长期操作日志主体。
        self._rows.insert(
            0,
            {
                "time": time_text,
                "direction": direction,
                "status": status,
                "summary": normalize_plain_text(summary, max_chars=FRAME_LOG_SUMMARY_CHARS),
            },
        )
        del self._rows[self._max_rows :]

    def _refresh(self) -> None:
        self.table.set_rows(self._rows)
        total = len(self._rows)
        self.table.set_page(1, total, max(1, total or 1))
        self.table.set_state(TableState.READY if total else TableState.EMPTY, "暂无调试记录")
        self.clear_button.setEnabled(total > 0)


def _result_text(crc_ok: object) -> str:
    if crc_ok is True:
        return "成功"
    if crc_ok is False:
        return "错误"
    return "诊断"


def _display_time(value: object) -> str:
    if isinstance(value, datetime):
        return value.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
