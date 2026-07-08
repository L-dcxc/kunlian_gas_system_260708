from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget


def build_config_editor(
    panel: QFrame,
    title: str,
    actions: Sequence[QPushButton],
    *,
    fields_width: int,
) -> tuple[QWidget, QGridLayout]:
    panel.setObjectName("ConfigEditorPanel")
    panel.setProperty("panel", "true")

    root = QVBoxLayout(panel)
    root.setContentsMargins(0, 0, 0, 0)
    root.setSpacing(0)

    header = QFrame(panel)
    header.setObjectName("ConfigEditorHeader")
    header_layout = QHBoxLayout(header)
    header_layout.setContentsMargins(14, 8, 14, 8)
    header_layout.setSpacing(8)

    title_label = QLabel(title, header)
    title_label.setObjectName("ConfigEditorTitle")
    title_label.setProperty("role", "editorTitle")
    header_layout.addWidget(title_label)
    header_layout.addStretch(1)
    for button in actions:
        header_layout.addWidget(button)
    root.addWidget(header)

    body = QWidget(panel)
    body.setObjectName("ConfigEditorBody")
    body_layout = QHBoxLayout(body)
    body_layout.setContentsMargins(16, 12, 16, 14)
    body_layout.setSpacing(0)

    fields_panel = QWidget(body)
    fields_panel.setObjectName("ConfigFormFields")
    fields_panel.setMaximumWidth(fields_width)
    body_layout.addWidget(fields_panel)
    body_layout.addStretch(1)
    root.addWidget(body)

    grid = QGridLayout(fields_panel)
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setHorizontalSpacing(12)
    grid.setVerticalSpacing(8)
    grid.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
    return fields_panel, grid
