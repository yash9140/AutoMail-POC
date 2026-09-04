"""SendWorker QThread worker — wraps SendFlowSteps for the real GUI.

Same pattern as ReplyDraftWorker/FindOpenEmailWorker: runs on a
background QThread, never touches a Qt widget directly, only emits
signals. Continues the full Phases 1-6 chain (identical to
ReplyDraftWorker) through DRAFT_READY, then Phase 7: reuses the SAME
PlaybookState vocabulary app/playbook/states.py already reserved for
Send (WAITING_FOR_SEND_APPROVAL -> SENDING -> VERIFYING_SEND ->
COMPLETED) as its current_step values — NOT ad hoc names of its own.
That vocabulary alignment is what makes the PlaybookEngine's SEND_STATE
guard ("SENDING is only reachable from WAITING_FOR_SEND_APPROVAL")
actually mean something for this worker's real run, instead of every
Phase-7 step being an unrecognized string the engine silently couldn't
track — the exact defect behind the previously reported "DRAFT_READY ->
VERIFYING_SEND invalid transition."

send_approval_granted is a constructor argument — exactly like
ReplyDraftWorker's bounded_approval_granted, it must already be true
BEFORE run() is called. There is no mid-run approval dialog anywhere in
this worker or in SendFlowSteps.
"""

from __future__ import annotations

import logging

from app.playbook.failure_reasons import LaunchFailureReason
from app.outlook.find_email import TARGET_EMAIL_SENDER, TARGET_EMAIL_SUBJECT
from app.outlook.launch import _provider
from app.outlook.send import SendFlowSteps
from app.safety.abort_controller import AbortController
from PySide6.QtCore import QObject, QThread, Signal

# Temporary thread-lifecycle diagnostics — see the matching logger in
# app/controllers/automation_controller.py. Never logs secrets.
_thread_logger = logging.getLogger("app.workers.send_worker.thread_lifecycle")
if not _thread_logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s [THREAD] %(message)s"))
    _thread_logger.addHandler(_handler)
    _thread_logger.setLevel(logging.DEBUG)
    _thread_logger.propagate = False


class SendWorker(QObject):
    status = Signal(str)
    current_step = Signal(str)
    vision_status = Signal(str)
    safety_status = Signal(str)
    log_message = Signal(str)
    success = Signal(dict)
    failure = Signal(str, str)  # (failure_reason, message)
    aborted = Signal(str)
    metrics_update = Signal(dict)
    # Qt-lifecycle signal — distinct from success/failure/aborted (which
    # carry the BUSINESS outcome). finished fires exactly once, no matter
    # which of those paths run() took, via the try/finally below — this
    # is what the controller's thread-teardown chain (worker.finished ->
    # thread.quit() -> ... -> thread.finished) hooks into. Never carries
    # a payload; only means "this worker's run() has returned."
    finished = Signal()

    def __init__(
        self, abort_controller: AbortController, bounded_approval_granted: bool, send_approval_granted: bool,
        target_sender: str = TARGET_EMAIL_SENDER, target_subject: str = TARGET_EMAIL_SUBJECT,
    ) -> None:
        """target_sender/target_subject default to the dev/test constants
        ONLY as a fallback — the real UI (app/controllers/automation_
        controller.py) always passes the actual user-entered values
        explicitly, so those defaults are never silently used there."""
        super().__init__()
        self.abort_controller = abort_controller
        self.bounded_approval_granted = bounded_approval_granted
        self.send_approval_granted = send_approval_granted
        self.target_sender = target_sender
        self.target_subject = target_subject

    def run(self) -> None:
        """Thin wrapper — the real work is in _run_impl(). The
        try/finally guarantees self.finished is emitted exactly once on
        EVERY exit path (every early return in _run_impl(), or even an
        uncaught exception), so the controller's signal-driven thread
        teardown (worker.finished -> thread.quit() -> ...) always fires
        no matter how this run ends."""
        _thread_logger.debug(
            "WORKER_RUN_STARTED current_thread=%r", QThread.currentThread(),
        )
        try:
            self._run_impl()
        finally:
            _thread_logger.debug(
                "WORKER_RUN_FINISHED current_thread=%r", QThread.currentThread(),
            )
            self.finished.emit()

    def _run_impl(self) -> None:
        self.status.emit("Running")
        self.safety_status.emit("Armed")

        try:
            vision, model = _provider()
        except SystemExit as exc:
            self.failure.emit(LaunchFailureReason.TECHNICAL_PROVIDER_ERROR, str(exc))
            return

        steps = SendFlowSteps(
            self.abort_controller, vision, model, self.send_approval_granted,
            target_sender=self.target_sender, target_subject=self.target_subject,
        )

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

        # --- Phases 6-7: prepare reply editor ---
        self.current_step.emit("FINDING_REPLY")
        self.current_step.emit("REPLY_EDITOR_OPEN")
        self.vision_status.emit("Calling Claude (reply editor state / grounding)")
        if not steps.prepare_reply_editor():
            self.vision_status.emit("Idle")
            self._finish_from_result(steps)
            return
        self.metrics_update.emit(steps.result.model_dump(mode="json"))

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
        self.metrics_update.emit(steps.result.model_dump(mode="json"))
        self.log_message.emit("Draft verified.")

        # --- Phase 7: pre-send validation. Emits the PlaybookState name
        # (WAITING_FOR_SEND_APPROVAL) this phase actually corresponds to
        # — required for the engine's SEND_STATE guard to ever be
        # reachable/meaningful for this worker's real run. ---
        self.current_step.emit("WAITING_FOR_SEND_APPROVAL")
        if not steps.validate_send_preconditions():
            self._finish_from_result(steps)
            return
        self.metrics_update.emit(steps.result.model_dump(mode="json"))

        # --- Phase 7: ground + click Send (max one click, ever) ---
        self.vision_status.emit("Calling Claude (Send grounding)")
        if not steps.ground_and_click_send():
            self.vision_status.emit("Idle")
            self._finish_from_result(steps)
            return
        self.vision_status.emit("Idle")
        self.current_step.emit("SENDING")
        self.safety_status.emit("Send clicked once — no further Send clicks possible this run.")
        self.metrics_update.emit(steps.result.model_dump(mode="json"))

        # --- Phase 7: observation-only sent verification (never resends) ---
        self.current_step.emit("VERIFYING_SEND")
        self.vision_status.emit("Calling Claude (sent-state verification)")
        sent_ok = steps.verify_sent()
        self.vision_status.emit("Idle")
        self.metrics_update.emit(steps.result.model_dump(mode="json"))
        if not sent_ok:
            self._finish_from_result(steps)
            return

        self.current_step.emit("COMPLETED")
        self.safety_status.emit("Safe")
        self.log_message.emit("Send verified. Phase 7 complete.")
        steps.finalize_session()
        self.success.emit(steps.result.model_dump(mode="json"))

    def _finish_from_result(self, steps: SendFlowSteps) -> None:
        steps.finalize_session()
        result = steps.result
        self.metrics_update.emit(result.model_dump(mode="json"))
        if result.result == "ABORTED":
            self.aborted.emit(result.failure_reason or LaunchFailureReason.USER_ABORTED)
        else:
            self.failure.emit(result.failure_reason or LaunchFailureReason.TECHNICAL_PROVIDER_ERROR, result.notes)
