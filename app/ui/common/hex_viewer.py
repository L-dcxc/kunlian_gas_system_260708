from __future__ import annotations

from PySide6.QtWidgets import QPlainTextEdit, QVBoxLayout, QWidget

from app.ui.common.safe_text import SafeTextLabel, normalize_plain_text

HEX_VIEWER_MAX_CHARS = 2048


class HexViewer(QWidget):
    def __init__(self, text: object = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._truncated = False
        self.viewer = QPlainTextEdit(self)
        self.viewer.setProperty("viewer", "hex")
        self.viewer.setReadOnly(True)
        self.viewer.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.truncation_label = SafeTextLabel("", selectable=False)
        self.truncation_label.setProperty("role", "warningText")
        self.truncation_label.setVisible(False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self.viewer)
        layout.addWidget(self.truncation_label)
        self.set_hex_text(text)

    def set_hex_text(self, text: object) -> None:
        raw = "" if text is None else str(text).replace("\x00", "�")
        self._truncated = len(raw) > HEX_VIEWER_MAX_CHARS
        # Device debug frames can be large; a hard UI cap prevents accidental
        # unbounded rendering while still making truncation visible to operators.
        shown = normalize_plain_text(raw, max_chars=HEX_VIEWER_MAX_CHARS)
        self.viewer.setPlainText(shown)
        if self._truncated:
            self.truncation_label.set_safe_text(f"HEX 内容超过 {HEX_VIEWER_MAX_CHARS} 字符，已截断。")
            self.truncation_label.setVisible(True)
        else:
            self.truncation_label.set_safe_text("")
            self.truncation_label.setVisible(False)

    def text(self) -> str:
        return self.viewer.toPlainText()

    def is_truncated(self) -> bool:
        return self._truncated
