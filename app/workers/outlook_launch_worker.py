"""RND-009B QThread worker — wraps OutlookLaunchSteps for the real GUI.

Runs on a background QThread so the UI thread stays responsive during
waits, screenshot capture, Gemini calls, and mouse/keyboard actions.
Never touches a Qt widget directly — only emits signals; the receiving
side (AutomationController, running on the UI thread) does all widget
updates, per instruction.

BOUNDED SESSION APPROVAL (revised from an earlier mid-run design): a
single upfront approval — obtained BEFORE this worker is even
constructed, while nothing time-sensitive is happening yet — covers
the entire press-key-through-verify sequence. There is deliberately no
mid-run approval pause anymore: any interruption (a modal dialog, a
window switch) between grounding and the click would itself steal
foreground away from Windows Search, invalidating the exact state
being validated — which is exactly what broke RND-009B's first live
attempt. Safety during the uninterrupted run is enforced entirely by
OutlookLaunchSteps' own validation gates (confidence threshold, bbox
check, repeated search-state checks, abort-flag checks) — not by a
human answering a question mid-flight.
"""

from __future__ import annotations

from app.playbook.failure_reasons import LaunchFailureReason
from app.outlook.launch import OutlookLaunchSteps, _provider
from app.safety.abort_controller import AbortController
from PySide6.QtCore import QObject, Signal


class OutlookLaunchWorker(QObject):
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
        # Set once, before run() starts, from the single upfront approval
        # dialog — never toggled mid-run. If somehow False (defensive; the
        # controller should never construct this worker without it),
        # grounding still runs but the click gate refuses, same as an
        # explicit human rejection would have.
        self.bounded_approval_granted = bounded_approval_granted

    def run(self) -> None:
        # The controller already transitioned READY -> LAUNCHING_OUTLOOK
        # before starting this thread, so no current_step signal is
        # emitted here — the only new transition this worker is
        # responsible for is the final LAUNCHING_OUTLOOK -> OUTLOOK_READY
        # on success (see success.emit below).
        self.status.emit("Running")
        self.safety_status.emit("Armed")

        try:
            vision, model = _provider()
        except SystemExit as exc:
            self.failure.emit(LaunchFailureReason.TECHNICAL_PROVIDER_ERROR, str(exc))
            return

        steps = OutlookLaunchSteps(self.abort_controller, vision, model)

        self.log_message.emit("Pressing Windows key")
        if not steps.press_windows_key():
            self._finish_from_result(steps)
            return
        self.metrics_update.emit(steps.result.model_dump(mode="json"))

        self.log_message.emit('Typing search query "Outlook" (Enter is never pressed)')
        if not steps.type_search_query("Outlook"):
            self._finish_from_result(steps)
            return
        self.metrics_update.emit(steps.result.model_dump(mode="json"))

        self.log_message.emit("Capturing Windows Search screenshot")
        capture = steps.capture_search_screenshot()
        if capture is None:
            self._finish_from_result(steps)
            return

        self.vision_status.emit("Calling Claude (Windows Search grounding)")
        if not steps.ground_search_result(capture):
            self.vision_status.emit("Idle")
            self._finish_from_result(steps)
            return
        self.vision_status.emit("Grounded")
        self.metrics_update.emit(steps.result.model_dump(mode="json"))

        # No mid-run pause here: the single bounded approval obtained
        # before this worker started already covers proceeding to click
        # the validated result. Grounding's own validation gates (search
        # visible, label is Outlook, confidence threshold, in-bounds) are
        # what actually gate the click now, not a human answering a
        # question at this exact moment.
        steps.record_human_approval(self.bounded_approval_granted)
        if not self.bounded_approval_granted:
            self.log_message.emit("Bounded approval was not granted for this session.")
            self._finish_from_result(steps)
            return

        self.log_message.emit("Clicking the detected Outlook result")
        if not steps.click_outlook_result():
            self._finish_from_result(steps)
            return
        self.metrics_update.emit(steps.result.model_dump(mode="json"))

        self.log_message.emit("Waiting for Outlook to launch (bounded polling)")
        if not steps.poll_for_outlook_foreground():
            self._finish_from_result(steps)
            return

        self.log_message.emit("Ensuring Outlook is maximized")
        if not steps.enforce_maximized():
            self._finish_from_result(steps)
            return
        self.metrics_update.emit(steps.result.model_dump(mode="json"))

        self.vision_status.emit("Calling Claude (Outlook readiness verification)")
        if not steps.verify_outlook_readiness():
            self.vision_status.emit("Idle")
            self._finish_from_result(steps)
            return
        self.vision_status.emit("Ready")
        self.metrics_update.emit(steps.result.model_dump(mode="json"))

        self.current_step.emit("OUTLOOK_READY")
        self.safety_status.emit("Safe")
        self.log_message.emit("Outlook launch verified (ready for interaction, not just splash screen). RND-009B complete.")
        self.success.emit(steps.result.model_dump(mode="json"))

    def _finish_from_result(self, steps: OutlookLaunchSteps) -> None:
        result = steps.result
        self.metrics_update.emit(result.model_dump(mode="json"))
        if result.result == "ABORTED":
            self.aborted.emit(result.failure_reason or LaunchFailureReason.USER_ABORTED)
        else:
            self.failure.emit(result.failure_reason or LaunchFailureReason.TECHNICAL_PROVIDER_ERROR, result.notes)
