"""RND-009A automation dashboard.

Displays playbook/vision/safety status, a Start/Abort control pair, and
a read-only activity log. This widget only emits signals when the user
clicks a button — it has no knowledge of the playbook engine itself;
AutomationController (controllers/automation_controller.py) owns that
wiring, keeping the UI layer free of playbook/safety logic.
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class AutomationPage(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self.title_label = QLabel("<h2>Outlook Vision AI Automation</h2>")

        self.application_status_label = QLabel("Application Status: Ready")
        self.playbook_status_label = QLabel("Playbook Status: Not Started")
        self.current_step_label = QLabel("Current Step: READY")
        self.last_action_label = QLabel("Last Action: -")
        self.vision_status_label = QLabel("Vision Status: Idle")
        self.safety_status_label = QLabel("Safety Status: Armed")

        # Per-run target — read by AutomationController at Start time and
        # passed through to SendWorker/SendFlowSteps/find_target_email().
        # Sender is required (validated by the controller before Start);
        # subject is optional ("subject optional" — sender-only matching).
        # No default text is pre-filled here — an empty field must mean
        # empty, never a silently-assumed value.
        self.sender_input = QLineEdit()
        self.sender_input.setPlaceholderText("Target Sender (required)")
        self.subject_input = QLineEdit()
        self.subject_input.setPlaceholderText("Target Subject (optional)")

        # Single-Send approval — SEPARATE from PermissionsPage's general
        # screen/mouse/keyboard automation approval. Must be explicitly
        # checked, per run, before Start will proceed to SendWorker with
        # send_approval_granted=True. Unchecked by default.
        self.send_approval_checkbox = QCheckBox(
            "I explicitly approve ONE real Send click for this automation run"
        )

        self.start_button = QPushButton("Start Automation")
        self.abort_button = QPushButton("Stop / Abort")
        self.abort_button.setEnabled(False)

        self.activity_log = QListWidget()
        # QListWidget items are not editable by default (NoEditTriggers is
        # the default edit trigger set), which is what makes this "read
        # only" from the user's perspective — nothing in this class ever
        # calls setFlags() to make an item user-editable.

        status_layout = QVBoxLayout()
        for label in (
            self.application_status_label,
            self.playbook_status_label,
            self.current_step_label,
            self.last_action_label,
            self.vision_status_label,
            self.safety_status_label,
        ):
            status_layout.addWidget(label)

        target_layout = QVBoxLayout()
        target_layout.addWidget(QLabel("Target Sender"))
        target_layout.addWidget(self.sender_input)
        target_layout.addWidget(QLabel("Target Subject (optional)"))
        target_layout.addWidget(self.subject_input)
        target_layout.addWidget(self.send_approval_checkbox)

        buttons_layout = QHBoxLayout()
        buttons_layout.addWidget(self.start_button)
        buttons_layout.addWidget(self.abort_button)

        layout = QVBoxLayout(self)
        layout.addWidget(self.title_label)
        layout.addLayout(status_layout)
        layout.addLayout(target_layout)
        layout.addLayout(buttons_layout)
        layout.addWidget(QLabel("Activity Log"))
        layout.addWidget(self.activity_log)

    def append_log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.activity_log.addItem(f"[{timestamp}] {message}")

    def set_application_status(self, status: str) -> None:
        self.application_status_label.setText(f"Application Status: {status}")

    def set_playbook_status(self, status: str) -> None:
        self.playbook_status_label.setText(f"Playbook Status: {status}")

    def set_current_step(self, step_name: str) -> None:
        self.current_step_label.setText(f"Current Step: {step_name}")

    def set_last_action(self, action: str) -> None:
        self.last_action_label.setText(f"Last Action: {action}")

    def set_vision_status(self, status: str) -> None:
        self.vision_status_label.setText(f"Vision Status: {status}")

    def set_safety_status(self, status: str) -> None:
        self.safety_status_label.setText(f"Safety Status: {status}")

    def set_running(self) -> None:
        self.start_button.setEnabled(False)
        self.abort_button.setEnabled(True)

    def set_stopped(self) -> None:
        self.start_button.setEnabled(True)
        self.abort_button.setEnabled(False)
