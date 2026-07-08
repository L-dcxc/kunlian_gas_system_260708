from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QWidget

DEFAULT_MAX_TEXT_CHARS = 2048


def normalize_plain_text(value: object, *, max_chars: int = DEFAULT_MAX_TEXT_CHARS) -> str:
    if max_chars < 1:
        raise ValueError("max_chars must be positive")
    text = "" if value is None else str(value)
    text = text.replace("\x00", "\uFFFD")
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}...已截断"


class SafeTextLabel(QLabel):
    def __init__(
        self,
        text: object = "",
        parent: QWidget | None = None,
        *,
        max_chars: int = DEFAULT_MAX_TEXT_CHARS,
        selectable: bool = True,
    ) -> None:
        super().__init__(parent)
        self._max_chars = max_chars
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setWordWrap(True)
        if selectable:
            self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.set_safe_text(text)

    def set_safe_text(self, text: object) -> None:
        # QLabel can auto-detect rich text; forcing PlainText prevents user,
        # device, import and tool-output strings from becoming markup.
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setText(normalize_plain_text(text, max_chars=self._max_chars))

    def max_chars(self) -> int:
        return self._max_chars
