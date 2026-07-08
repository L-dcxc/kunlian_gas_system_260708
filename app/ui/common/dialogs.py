from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from app.ui.common.safe_text import SafeTextLabel


class ConfirmDangerDialog(QDialog):
    def __init__(
        self,
        title: str = "确认危险操作",
        message: object = "此操作执行后可能无法撤销。",
        parent: QWidget | None = None,
        *,
        confirm_text: str = "确认执行",
        cancel_text: str = "取消",
    ) -> None:
        super().__init__(parent)
        self.setProperty("danger", "true")
        self.setWindowTitle(title)
        self.setModal(True)

        self.title_label = SafeTextLabel(title, selectable=False)
        self.title_label.setProperty("role", "dialogTitle")
        self.message_label = SafeTextLabel(message, selectable=True)
        self.message_label.setProperty("role", "muted")

        self.confirm_button = QPushButton(confirm_text)
        self.confirm_button.setProperty("variant", "danger")
        self.confirm_button.setProperty("role", "confirm")
        self.cancel_button = QPushButton(cancel_text)
        self.cancel_button.setProperty("role", "cancel")

        self.confirm_button.clicked.connect(self.accept)
        self.cancel_button.clicked.connect(self.reject)

        actions = QHBoxLayout()
        actions.addStretch(1)
        actions.addWidget(self.cancel_button)
        actions.addWidget(self.confirm_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)
        layout.addWidget(self.title_label)
        layout.addWidget(self.message_label)
        layout.addLayout(actions)

        # Destructive actions must require an explicit red-button click; Enter
        # should activate Cancel unless the user deliberately moves focus.
        self.cancel_button.setDefault(True)
        self.cancel_button.setAutoDefault(True)
        self.confirm_button.setDefault(False)
        self.confirm_button.setAutoDefault(False)
        self.cancel_button.setFocus(Qt.FocusReason.OtherFocusReason)

    @classmethod
    def confirm(
        cls,
        title: str,
        message: object,
        parent: QWidget | None = None,
        *,
        confirm_text: str = "确认执行",
    ) -> bool:
        dialog = cls(title, message, parent, confirm_text=confirm_text)
        return dialog.exec() == QDialog.DialogCode.Accepted


class RiskConfirmDialog(ConfirmDangerDialog):
    def __init__(
        self,
        title: str = "确认高风险操作",
        message: object = "请确认已了解覆盖、清空或联动控制风险。",
        parent: QWidget | None = None,
        *,
        risk_summary: object = "",
        confirm_text: str = "确认风险并执行",
        cancel_text: str = "取消",
    ) -> None:
        super().__init__(title, message, parent, confirm_text=confirm_text, cancel_text=cancel_text)
        self.risk_label = SafeTextLabel(risk_summary, selectable=True)
        self.risk_label.setProperty("role", "riskSummary")
        insert_index = self.layout().count() - 1
        self.layout().insertWidget(insert_index, self.risk_label)
        self.risk_label.setVisible(bool(self.risk_label.text()))

    def set_risk_summary(self, summary: object) -> None:
        self.risk_label.set_safe_text(summary)
        self.risk_label.setVisible(bool(self.risk_label.text()))
