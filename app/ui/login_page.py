"""RND-009A login screen.

Placeholder POC login only. Explicitly NOT real authentication: no
backend call, no credential storage, no password logging. The only
"validation" is that both fields are non-empty — this exists purely to
drive the app's screen-to-screen user flow, per this stage's spec.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFormLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class LoginPage(QWidget):
    login_succeeded = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self.email_input = QLineEdit()
        self.email_input.setPlaceholderText("Email")

        self.password_input = QLineEdit()
        self.password_input.setPlaceholderText("Password")
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)

        self.validation_label = QLabel("")
        self.validation_label.setStyleSheet("color: #c0392b;")

        self.login_button = QPushButton("Login")
        self.login_button.clicked.connect(self._on_login_clicked)

        form = QFormLayout()
        form.addRow("Email", self.email_input)
        form.addRow("Password", self.password_input)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("<h2>Outlook Vision AI Automation POC</h2>"))
        layout.addWidget(QLabel("Sign in (placeholder — no real authentication in this POC)"))
        layout.addLayout(form)
        layout.addWidget(self.validation_label)
        layout.addWidget(self.login_button)
        layout.addStretch()

    def _on_login_clicked(self) -> None:
        email = self.email_input.text().strip()
        password = self.password_input.text()

        if not email or not password:
            self.validation_label.setText("Email and password are both required.")
            return

        self.validation_label.setText("")
        # Placeholder POC behavior only: no backend call, nothing written
        # to disk, nothing logged. The password is cleared from the field
        # immediately rather than left sitting in the widget.
        self.password_input.clear()
        self.login_succeeded.emit()
