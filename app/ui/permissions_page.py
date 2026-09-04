"""RND-009A terms & permissions screen.

Continue is disabled until all three checkboxes are checked — enforced
by the widget itself (tested), not just by convention.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QCheckBox, QLabel, QPushButton, QVBoxLayout, QWidget

PERMISSIONS_EXPLANATION = (
    "This POC may:\n"
    "- observe the visible Windows desktop\n"
    "- use mouse input\n"
    "- use keyboard input\n"
    "- launch Outlook through Windows UI\n"
    "- inspect visible email content\n"
    "- generate a draft reply\n"
    "- perform controlled actions during the POC"
)


class PermissionsPage(QWidget):
    permissions_accepted = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self.checkbox_observe = QCheckBox("I understand the application can observe my screen")
        self.checkbox_control = QCheckBox("I allow controlled mouse and keyboard interaction")
        self.checkbox_vision_ai = QCheckBox("I understand this is a Vision AI POC")

        self.continue_button = QPushButton("Continue")
        self.continue_button.setEnabled(False)
        self.continue_button.clicked.connect(self.permissions_accepted.emit)

        for checkbox in (self.checkbox_observe, self.checkbox_control, self.checkbox_vision_ai):
            checkbox.stateChanged.connect(self._update_continue_enabled)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("<h2>Terms &amp; Permissions</h2>"))
        layout.addWidget(QLabel(PERMISSIONS_EXPLANATION))
        layout.addWidget(self.checkbox_observe)
        layout.addWidget(self.checkbox_control)
        layout.addWidget(self.checkbox_vision_ai)
        layout.addWidget(self.continue_button)
        layout.addStretch()

    def _update_continue_enabled(self) -> None:
        all_checked = (
            self.checkbox_observe.isChecked()
            and self.checkbox_control.isChecked()
            and self.checkbox_vision_ai.isChecked()
        )
        self.continue_button.setEnabled(all_checked)
