"""RND-009C QThread worker — wraps FindOpenEmailSteps for the real GUI.

Same pattern as OutlookLaunchWorker (RND-009B): runs on a background
QThread, never touches a Qt widget directly, only emits signals. Bounded
session approval (one upfront yes, obtained before this worker is even
constructed) covers the whole chained sequence through EMAIL_OPENED —
no mid-run pause, for the same reason RND-009B's mid-run pause was
removed: any interruption mid-flight risks stealing foreground away
from the exact desktop state being validated.
"""

from __future__ import annotations

from app.playbook.failure_reasons import LaunchFailureReason
from app.outlook.find_email import FindOpenEmailSteps
from app.outlook.launch import _provider
from app.safety.abort_controller import AbortController
from PySide6.QtCore import QObject, Signal


class FindOpenEmailWorker(QObject):
    status = Signal(str)
    current_step = Signal(str)
    vision_status = Signal(str)
    safety_status = Signal(str)
    log_message = Signal(str)
    success = Signal(dict)
    failure = Signal(str, str)  # (failure_reason, message)
    aborted = Signal(str)
    metrics_update = Signal(dict)

    def __init__(self, abort_controller: AbortController, bounded_approval_granted: bool) -> None:
        super().__init__()
        self.abort_controller = abort_controller
        self.bounded_approval_granted = bounded_approval_granted

    def run(self) -> None:
        self.status.emit("Running")
        self.safety_status.emit("Armed")

        try:
            provider, model = _provider()
        except SystemExit as exc:
            self.failure.emit(LaunchFailureReason.TECHNICAL_PROVIDER_ERROR, str(exc))
            return

        steps = FindOpenEmailSteps(self.abort_controller, provider, model)

        if not self.bounded_approval_granted:
            self.log_message.emit("Bounded approval was not granted for this session.")
            steps.result.result = "ABORTED"
            steps.result.failure_reason = LaunchFailureReason.HUMAN_REJECTED_TARGET
            self._finish_from_result(steps)
            return

        # Pre-RND-009D hardening: session timer starts here, exactly once,
        # the moment the bounded run actually begins — not in __init__, so
        # rejection above never fabricates an elapsed-time reading.
        steps.start_session()

        # --- Phases 1-2: launch + readiness (reused RND-009B logic) ---
        self.log_message.emit("Pressing Windows key")
        self.vision_status.emit("Calling Claude (Windows Search grounding)")
        launch_ok = steps.run_launch_and_readiness()
        self.metrics_update.emit(steps.result.model_dump(mode="json"))
        if not launch_ok:
            self.vision_status.emit("Idle")
            self._finish_from_result(steps)
            return
        self.vision_status.emit("Ready")
        self.current_step.emit("OUTLOOK_READY")
        self.log_message.emit("Outlook launch verified (ready for interaction).")

        # --- Phase 3: find target email ---
        self.current_step.emit("FINDING_EMAIL")
        self.log_message.emit(f"Locating target email: subject={steps.result.target_subject!r} sender={steps.result.target_sender!r}")
        self.vision_status.emit("Calling Claude (email target grounding)")
        if not steps.find_target_email():
            self.vision_status.emit("Idle")
            self._finish_from_result(steps)
            return
        self.vision_status.emit("Target found")
        self.metrics_update.emit(steps.result.model_dump(mode="json"))

        self.log_message.emit("Clicking the target email row")
        if not steps.click_target_email():
            self._finish_from_result(steps)
            return
        self.metrics_update.emit(steps.result.model_dump(mode="json"))

        # --- Phase 4: verify correct email opened ---
        self.vision_status.emit("Calling Claude (email-open verification)")
        if not steps.verify_email_opened():
            self.vision_status.emit("Idle")
            self._finish_from_result(steps)
            return
        self.vision_status.emit("Verified")

        self.current_step.emit("EMAIL_OPENED")
        self.safety_status.emit("Safe")
        self.log_message.emit("Correct target email confirmed open. RND-009C complete.")
        steps.finalize_session()
        self.success.emit(steps.result.model_dump(mode="json"))

    def _finish_from_result(self, steps: FindOpenEmailSteps) -> None:
        steps.finalize_session()
        result = steps.result
        self.metrics_update.emit(result.model_dump(mode="json"))
        if result.result == "ABORTED":
            self.aborted.emit(result.failure_reason or LaunchFailureReason.USER_ABORTED)
        else:
            self.failure.emit(result.failure_reason or LaunchFailureReason.TECHNICAL_PROVIDER_ERROR, result.notes)
