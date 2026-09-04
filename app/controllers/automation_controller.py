"""Automation controller — Phases 1-7, target-dynamic UI wiring.

Owns the PlaybookEngine, AbortController, and SessionMetrics for one
session. Start Automation launches a real QThread (SendWorker) so the
UI thread stays responsive during the full chained Windows Search ->
Outlook -> find email -> open email -> understand -> reply -> draft ->
verify -> Send -> verify-sent sequence; this controller only reacts to
the worker's signals — it never touches pyautogui/Vision/playbook step
logic directly, and the page widgets are only ever updated from this
(UI-thread) controller, never from the worker thread itself.

TWO SEPARATE APPROVALS, never merged into one:
  1. General automation approval (screen/mouse/keyboard) — granted once
     per session on PermissionsPage, unchanged by this controller.
  2. Single-Send approval — AutomationPage.send_approval_checkbox,
     explicit and per-run, read fresh at Start time. Start is blocked
     with a clear log message (never a silent no-op, never a mid-run
     prompt) if it isn't checked — see the validation block in
     start_automation() below. Checking checkbox #1 (or the bounded
     approval dialog below) never implies or grants #2.

BOUNDED SESSION APPROVAL: a single approval dialog is ALSO shown before
the worker is created (in addition to, never instead of, the Send
checkbox above) — while nothing time-sensitive has started, so it's
safe to block here. Once approved, the entire chained sequence runs
with no further human interaction; there is no mid-run approval pause,
because any interruption mid-flight (including a modal dialog) would
itself steal foreground away from the exact desktop state being
validated — the lesson RND-009B's first live attempt already taught.

TARGET SENDER/SUBJECT: read from AutomationPage.sender_input/
subject_input at Start time and passed through explicitly to SendWorker
-> SendFlowSteps -> find_target_email(). TARGET_EMAIL_SENDER/
TARGET_EMAIL_SUBJECT (app/outlook/find_email.py) remain only as
dev/test-fixture defaults for callers that don't supply their own —
this controller always passes the UI's actual values, so those
defaults are never used for a real UI run. Sender is validated
non-empty before Start is allowed to proceed; subject may be empty
(existing sender-only matching behavior, unchanged).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from PySide6.QtCore import QObject, QThread
from PySide6.QtWidgets import QMessageBox, QWidget

from app.metrics.session_metrics import SessionMetrics
from app.playbook.engine import InvalidTransitionError, PlaybookEngine
from app.playbook.states import PlaybookState
from app.safety.abort_controller import AbortController
from app.ui.automation_page import AutomationPage
from app.workers.send_worker import SendWorker

# Temporary thread-lifecycle diagnostics (debugging pass only — safe to
# remove once the QThread lifecycle fix is confirmed stable across a
# real UI run). Logs event/thread-identity/playbook-state/isRunning()
# for every lifecycle transition below; never logs secrets (API keys,
# draft/email content) — only structural identifiers and state names.
_thread_logger = logging.getLogger("app.controllers.automation_controller.thread_lifecycle")
if not _thread_logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s [THREAD] %(message)s"))
    _thread_logger.addHandler(_handler)
    _thread_logger.setLevel(logging.DEBUG)
    _thread_logger.propagate = False

BOUNDED_APPROVAL_MESSAGE = (
    "Approve this automation run? This permits the application to launch Outlook through "
    "Windows Search, locate and open the target email, understand the visible email "
    "content, click Reply, generate a contextual draft, type it into the reply editor, "
    "verify the draft, ground the Send control, click Send exactly once, and verify the "
    "sent state — since you have already granted explicit Send approval for this run."
)


class AutomationController(QObject):
    """QObject subclass — REQUIRED for correct Qt signal thread-affinity.

    A plain (non-QObject) Python receiver has no thread affinity Qt can
    detect, so PySide6's Auto Connection falls back to calling the slot
    directly/synchronously on the EMITTING thread — meaning, before this
    fix, every slot connected to a SendWorker signal (current_step,
    success, failure, aborted, ...) actually ran on the WORKER thread,
    not the UI thread. That is the exact, confirmed root cause of the
    observed "QThread::wait: Thread tried to wait on itself" (the old
    _teardown_thread()'s blocking wait on self._thread was being called FROM
    self._thread itself, inside _on_worker_success/_failure/_aborted)
    and very likely also of "no further automation occurred after
    EMAIL_OPENED" (QWidget methods on self.page being mutated from a
    non-GUI thread is undefined Qt behavior). Subclassing QObject here
    gives every bound slot below correct thread affinity (the thread
    this controller was constructed on — the main/UI thread), so Qt's
    Auto Connection now correctly uses a QueuedConnection when a
    SendWorker signal (emitted from the worker thread) is delivered to
    it, safely marshaling the call onto the UI thread's event loop."""

    def __init__(self, page: AutomationPage) -> None:
        super().__init__()
        self.page = page
        self.engine = PlaybookEngine()
        self.abort_controller = AbortController()
        self.metrics = SessionMetrics()

        self._thread: Optional[QThread] = None
        self._worker: Optional[SendWorker] = None

        self.page.start_button.clicked.connect(self.start_automation)
        self.page.abort_button.clicked.connect(self.abort_automation)

    def _log_thread_event(self, event: str) -> None:
        current = QThread.currentThread()
        worker_thread = self._thread
        _thread_logger.debug(
            "%s current_thread=%r worker_thread=%r playbook_state=%s thread_running=%s",
            event, current, worker_thread, self.engine.current_state.value,
            worker_thread.isRunning() if worker_thread is not None else None,
        )

    def initialize(self) -> None:
        self.page.append_log("Playbook initialized")
        self.page.set_current_step(self.engine.current_state.value)
        self.page.set_playbook_status("Not Started")
        self.page.set_application_status("Ready")
        self.page.set_vision_status("Idle")
        self.page.set_safety_status("Armed")

    def start_automation(self) -> None:
        if self._thread is not None and self._thread.isRunning():
            self.page.append_log("Start ignored — automation is already running.")
            return
        if self.abort_controller.is_abort_requested():
            self.page.append_log("Cannot start — abort already requested for this session.")
            return

        target_sender = self.page.sender_input.text().strip()
        target_subject = self.page.subject_input.text().strip()
        if not target_sender:
            self.page.append_log("Start blocked — Target Sender is required and cannot be empty.")
            return

        if not self.page.send_approval_checkbox.isChecked():
            self.page.append_log(
                "Start blocked — explicit Send approval is required for this run. "
                "Check \"I explicitly approve ONE real Send click for this automation run\" to proceed."
            )
            return

        parent: Optional[QWidget] = self.page.window()
        answer = QMessageBox.question(
            parent, "Approve RND-009D Playbook", BOUNDED_APPROVAL_MESSAGE,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        approved = answer == QMessageBox.StandardButton.Yes
        self.page.append_log(f"Bounded playbook approval: {'yes' if approved else 'no'}")
        if not approved:
            self.page.append_log("Playbook not approved — Start Automation had no effect.")
            return

        self.abort_controller.reset()
        # Mirrors the fact already validated above (the dedicated Send
        # checkbox was checked) into the engine's OWN send_approved flag
        # — a second, independent guard app/playbook/engine.py enforces
        # before it will ever allow WAITING_FOR_SEND_APPROVAL -> SENDING,
        # regardless of sequencing. Never set unconditionally/earlier —
        # only once this run is genuinely Send-approved.
        self.engine.approve_send()
        self.metrics = SessionMetrics(start_time=datetime.now().isoformat(), human_interventions=1)
        self.page.set_running()
        self.page.set_playbook_status("Running")

        try:
            step = self.engine.start()
        except InvalidTransitionError as exc:
            self.page.append_log(f"Start failed: {exc}")
            self.page.set_stopped()
            return

        self.page.set_current_step(step.expected_state.value)
        self.page.set_last_action("Start Automation clicked")
        self.page.append_log(f"State: {PlaybookState.READY.value} -> {step.expected_state.value}")

        self._thread = QThread()
        self._log_thread_event("THREAD_CREATED")
        self._worker = SendWorker(
            self.abort_controller, bounded_approval_granted=True, send_approval_granted=True,
            target_sender=target_sender, target_subject=target_subject,
        )
        self._worker.moveToThread(self._thread)
        self._log_thread_event("WORKER_MOVED_TO_THREAD")

        self._thread.started.connect(self._on_thread_started)
        self._thread.started.connect(self._worker.run)
        self._worker.status.connect(self.page.set_application_status)
        self._worker.current_step.connect(self._on_worker_current_step)
        self._worker.vision_status.connect(self.page.set_vision_status)
        self._worker.safety_status.connect(self.page.set_safety_status)
        self._worker.log_message.connect(self.page.append_log)
        self._worker.metrics_update.connect(self._on_metrics_update)
        self._worker.success.connect(self._on_worker_success)
        self._worker.failure.connect(self._on_worker_failure)
        self._worker.aborted.connect(self._on_worker_aborted)

        # Qt-native, signal-driven lifecycle teardown — NEVER a direct
        # wait() call from a slot that might itself be running on the
        # worker thread. worker.finished (a distinct Qt-lifecycle signal,
        # separate from the business-logic success/failure/aborted
        # signals) fires exactly once, at the very end of run() no matter
        # which path it took (see SendWorker.run()'s try/finally). Only
        # THAT triggers thread.quit() and the worker's delete-later cleanup;
        # only thread.finished
        # (fired once the worker thread's event loop has actually
        # stopped) triggers clearing this controller's own references.
        self._worker.finished.connect(self._on_worker_finished)
        self._thread.finished.connect(self._on_thread_finished)

        self._thread.start()
        self._log_thread_event("THREAD_START_REQUESTED")

    def abort_automation(self) -> None:
        self.abort_controller.request_abort()
        self.page.set_safety_status("Abort Requested")
        self.page.append_log("Abort requested — worker will stop at its next checkpoint.")
        # The playbook engine itself only moves to ABORTED once the worker
        # actually stops (see _on_worker_aborted) — abort_requested alone
        # does not retroactively rewrite an in-flight step's outcome.

    def _on_thread_started(self) -> None:
        self._log_thread_event("THREAD_STARTED")

    def _on_worker_current_step(self, step_name: str) -> None:
        self._log_thread_event(f"WORKER_PROGRESS step={step_name}")
        self.page.set_current_step(step_name)
        try:
            state = PlaybookState(step_name)
        except ValueError:
            # SendWorker now only ever emits names from PlaybookState's
            # own vocabulary (see app/workers/send_worker.py's module
            # docstring) — an unrecognized name here means the engine
            # cannot authorize whatever the worker is doing. Never just
            # logged and ignored: see _reject_transition().
            self._reject_transition(f"Unrecognized playbook step {step_name!r} reported by the worker.")
            return
        try:
            self.engine.advance(state)
        except InvalidTransitionError as exc:
            self._reject_transition(f"Playbook transition rejected: {exc}")

    def _reject_transition(self, message: str) -> None:
        """A rejected/unrecognized playbook transition must NEVER be just
        logged while physical execution continues (the previously
        reported DRAFT_READY -> VERIFYING_SEND defect: the engine's
        rejection was logged and the worker kept running regardless).
        This requests an abort through the SAME AbortController every
        SendFlowSteps phase already checks at its own safety checkpoints
        (check_abort()) — the worker stops itself, gracefully, at its
        very next checkpoint. No Send/grounding/verification/Outlook
        code is touched to achieve this; it reuses the existing,
        unmodified abort mechanism as the enforcement path."""
        self.page.append_log(message)
        self.page.set_safety_status("Aborted — invalid playbook transition")
        self.abort_controller.request_abort()

    def _on_metrics_update(self, result: dict) -> None:
        """Pre-RND-009D hardening: fires on every metrics_update emission,
        which the worker sends before EVERY terminal signal (success,
        failure, AND aborted) — so total_elapsed_ms (TOTAL E2E wall-clock
        time, distinct from total_latency_ms's Vision-only sum) reaches
        session metrics regardless of how the run ends, not just on PASS."""
        elapsed = result.get("total_elapsed_ms")
        if elapsed is not None:
            self.metrics.total_elapsed_ms = elapsed

    def _on_worker_success(self, result: dict) -> None:
        self._log_thread_event("WORKER_FINAL_RESULT outcome=success")
        self.metrics.end_time = datetime.now().isoformat()
        self.metrics.completed_steps += 1
        self.metrics.vision_calls = result.get("total_vision_calls", 0)
        self.metrics.input_tokens = result.get("total_input_tokens", 0)
        self.metrics.output_tokens = result.get("total_output_tokens", 0)
        self.metrics.estimated_cost = result.get("total_estimated_cost", 0.0)
        self.metrics.total_latency_ms = result.get("total_latency_ms", 0.0)
        self.metrics.retries = result.get("provider_retries", 0)
        self.metrics.fallback_count = result.get("fallback_uses", 0)
        self.page.set_playbook_status("Active")
        # Button re-enable/thread teardown are NOT done here — they only
        # happen once the worker.finished -> thread.finished signal chain
        # actually completes (see _on_worker_finished/_on_thread_finished
        # below), never synchronously from this outcome-specific handler.

    def _on_worker_failure(self, failure_reason: str, message: str) -> None:
        self._log_thread_event(f"WORKER_FINAL_RESULT outcome=failure reason={failure_reason}")
        self.metrics.end_time = datetime.now().isoformat()
        self.metrics.failed_step = failure_reason
        try:
            self.engine.fail(failure_reason)
        except InvalidTransitionError:
            pass
        self.page.set_current_step(self.engine.current_state.value)
        self.page.set_playbook_status("Failed")
        self.page.append_log(f"FAILED ({failure_reason}): {message}")

    def _on_worker_aborted(self, failure_reason: str) -> None:
        self._log_thread_event(f"WORKER_FINAL_RESULT outcome=aborted reason={failure_reason}")
        self.metrics.end_time = datetime.now().isoformat()
        self.metrics.safety_aborts += 1
        try:
            self.engine.abort()
        except InvalidTransitionError:
            pass
        self.page.set_current_step(self.engine.current_state.value)
        self.page.set_playbook_status("Aborted")
        self.page.set_safety_status("Aborted")
        self.page.append_log(f"ABORTED ({failure_reason}). No further advancement is possible.")

    def _on_worker_finished(self) -> None:
        """Connected to SendWorker.finished — a Qt-lifecycle signal,
        distinct from success/failure/aborted, that fires exactly once at
        the true end of run() (see SendWorker.run()'s try/finally). This
        slot runs on the UI thread (AutomationController is now a
        QObject — see the class docstring), so calling thread.quit() here
        is always safe: it is never the thread calling quit()/wait() on
        itself."""
        self._log_thread_event("WORKER_FINISHED")
        self._log_thread_event("THREAD_QUIT_REQUESTED")
        if self._thread is not None:
            self._thread.quit()
        self._log_thread_event("WORKER_DELETE_LATER")
        if self._worker is not None:
            self._worker.deleteLater()

    def _on_thread_finished(self) -> None:
        """Connected to QThread.finished — fires once the worker thread's
        event loop has actually stopped (after thread.quit() above is
        processed). ONLY here are the controller's own thread/worker
        references cleared, and only here is the Start button
        re-enabled — never earlier, and never from a slot that might run
        on the worker thread."""
        self._log_thread_event("THREAD_FINISHED")
        thread = self._thread
        self._log_thread_event("THREAD_DELETE_LATER")
        if thread is not None:
            thread.deleteLater()
        self.page.set_stopped()
        self._worker = None
        self._thread = None
        self._log_thread_event("CONTROLLER_REFERENCES_CLEARED")
