"""RND-009D QThread worker — wraps ReplyDraftSteps for the real GUI.

Same pattern as OutlookLaunchWorker/FindOpenEmailWorker: runs on a
background QThread, never touches a Qt widget directly, only emits
signals. Bounded session approval (one upfront yes) covers the whole
chained sequence through DRAFT_READY — no mid-run pause, no Send code
path anywhere in this worker or ReplyDraftSteps.
"""

from __future__ import annotations

from app.playbook.failure_reasons import LaunchFailureReason
from app.outlook.draft import ReplyDraftSteps
from app.outlook.launch import _provider
from app.safety.abort_controller import AbortController
from PySide6.QtCore import QObject, Signal


class ReplyDraftWorker(QObject):
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

        steps = ReplyDraftSteps(self.abort_controller, provider, model)

        if not self.bounded_approval_granted:
            self.log_message.emit("Bounded approval was not granted for this session.")
            steps.result.result = "ABORTED"
            steps.result.failure_reason = LaunchFailureReason.HUMAN_REJECTED_TARGET
            self._finish_from_result(steps)
            return

        steps.start_session()

        # --- Phases 1-4: find + open email (reused RND-009C logic) ---
        self.log_message.emit("Pressing Windows key")
        self.vision_status.emit("Calling Claude (Windows Search grounding)")
        ok = steps.run_find_and_open_email()
        self.metrics_update.emit(steps.result.model_dump(mode="json"))
        if not ok:
            self.vision_status.emit("Idle")
            self._finish_from_result(steps)
            return
        self.current_step.emit("OUTLOOK_READY")
        self.current_step.emit("FINDING_EMAIL")
        self.current_step.emit("EMAIL_OPENED")
        self.log_message.emit("Target email confirmed open.")

        # --- Phase 5: email understanding ---
        self.current_step.emit("READING_EMAIL")
        self.vision_status.emit("Calling Claude (email understanding)")
        if not steps.understand_email():
            self.vision_status.emit("Idle")
            self._finish_from_result(steps)
            return
        # content_complete is guaranteed True here — understand_email()
        # never returns True otherwise (see app/outlook/read_email.py).
        # No separate step is emitted for this — "EMAIL_CONTENT_COMPLETE"
        # is not a PlaybookState (live-run bug, 2026-09-04: the engine's
        # own vocabulary in app/playbook/states.py never included it, so
        # every real run aborted here with "Unrecognized playbook step"
        # the moment reading finished; the fully-mocked worker-only tests
        # never caught it because they don't route current_step through
        # AutomationController's PlaybookState validation). READING_EMAIL
        # -> FINDING_REPLY is already a valid LINEAR_ORDER transition.
        self.metrics_update.emit(steps.result.model_dump(mode="json"))
        self.log_message.emit(
            f"Email understood: reply_expectation={steps.result.reply_expectation} "
            f"requires_user_decision={steps.result.requires_user_decision}"
        )

        # --- Phases 6-7: prepare reply editor (state check, ground+click if needed) ---
        self.current_step.emit("FINDING_REPLY")
        self.current_step.emit("REPLY_EDITOR_OPEN")
        self.vision_status.emit("Calling Claude (reply editor state / grounding)")
        if not steps.prepare_reply_editor():
            self.vision_status.emit("Idle")
            self._finish_from_result(steps)
            return
        self.metrics_update.emit(steps.result.model_dump(mode="json"))

        # --- Phase 8: reply editor verification ---
        self.vision_status.emit("Calling Claude (reply editor verification)")
        if not steps.verify_reply_editor():
            self.vision_status.emit("Idle")
            self._finish_from_result(steps)
            return
        self.metrics_update.emit(steps.result.model_dump(mode="json"))

        # --- Phase 9: draft generation ---
        self.current_step.emit("GENERATING_DRAFT")
        self.vision_status.emit("Calling Claude (reply generation)")
        if not steps.generate_draft():
            self.vision_status.emit("Idle")
            self._finish_from_result(steps)
            return
        self.vision_status.emit("Idle")
        self.metrics_update.emit(steps.result.model_dump(mode="json"))
        self.log_message.emit("Draft generated and passed local quality gate.")

        # --- Phase 11: controlled multiline typing ---
        self.current_step.emit("TYPING_DRAFT")
        self.log_message.emit("Typing draft (segmented writes, explicit Enter, foreground-checked)")
        if not steps.type_draft():
            self._finish_from_result(steps)
            return
        self.metrics_update.emit(steps.result.model_dump(mode="json"))

        # --- Phases 12-14: post-typing stabilization + verification ---
        self.current_step.emit("VERIFYING_DRAFT")
        self.vision_status.emit("Calling Claude (draft verification)")
        if not steps.verify_draft():
            self.vision_status.emit("Idle")
            self._finish_from_result(steps)
            return
        self.vision_status.emit("Verified")

        self.current_step.emit("DRAFT_READY")
        self.safety_status.emit("Safe")
        self.log_message.emit("Draft verified. RND-009D complete — STOPPING before Send.")
        steps.finalize_session()
        self.success.emit(steps.result.model_dump(mode="json"))

    def _finish_from_result(self, steps: ReplyDraftSteps) -> None:
        steps.finalize_session()
        result = steps.result
        self.metrics_update.emit(result.model_dump(mode="json"))
        if result.result == "ABORTED":
            self.aborted.emit(result.failure_reason or LaunchFailureReason.USER_ABORTED)
        else:
            self.failure.emit(result.failure_reason or LaunchFailureReason.TECHNICAL_PROVIDER_ERROR, result.notes)
