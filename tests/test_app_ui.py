"""RND-009A/009B UI + controller tests, via pytest-qt's qtbot (simulates
Qt events at the Qt level — not real OS-level mouse/keyboard input) and
direct calls to controller signal-handler methods (bypassing the real
QThread entirely, so the worker's real pyautogui/Gemini code path never
executes in this file — see test_outlook_launch_steps.py for that
module's own thoroughly-mocked unit tests).
"""

import inspect
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QLineEdit  # noqa: E402

from app.controllers.automation_controller import AutomationController  # noqa: E402
from app.playbook.states import PlaybookState  # noqa: E402
from app.ui.automation_page import AutomationPage  # noqa: E402
from app.ui.login_page import LoginPage  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402
from app.ui.permissions_page import PermissionsPage  # noqa: E402


def test_application_starts(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    assert window.windowTitle() == "Outlook Vision AI Automation POC"
    assert window.stack.currentWidget() is window.login_page


def test_empty_login_blocked(qtbot):
    page = LoginPage()
    qtbot.addWidget(page)

    received = []
    page.login_succeeded.connect(lambda: received.append(True))

    qtbot.mouseClick(page.login_button, __import__("PySide6.QtCore", fromlist=["Qt"]).Qt.MouseButton.LeftButton)

    assert received == []
    assert page.validation_label.text() != ""


def test_non_empty_login_accepted_without_authentication(qtbot):
    page = LoginPage()
    qtbot.addWidget(page)

    received = []
    page.login_succeeded.connect(lambda: received.append(True))

    page.email_input.setText("user@example.com")
    page.password_input.setText("hunter2")

    from PySide6.QtCore import Qt

    qtbot.mouseClick(page.login_button, Qt.MouseButton.LeftButton)

    assert received == [True]
    assert page.validation_label.text() == ""

    # No authentication call of any kind happened — this is verified
    # structurally in test_login_page_source_contains_no_auth_or_network_call.


def test_password_masked(qtbot):
    page = LoginPage()
    qtbot.addWidget(page)
    assert page.password_input.echoMode() == QLineEdit.EchoMode.Password


def test_credentials_not_persisted(qtbot):
    page = LoginPage()
    qtbot.addWidget(page)

    page.email_input.setText("user@example.com")
    page.password_input.setText("hunter2")

    from PySide6.QtCore import Qt

    qtbot.mouseClick(page.login_button, Qt.MouseButton.LeftButton)

    # password field is cleared immediately after a successful "login"
    assert page.password_input.text() == ""
    # LoginPage stores no attribute holding the credentials anywhere
    stored_attrs = [a for a in vars(page) if "password" in a.lower() or "credential" in a.lower()]
    assert stored_attrs == ["password_input"]  # only the widget itself, not a stored value


def test_login_page_source_contains_no_auth_or_network_call():
    import app.ui.login_page as mod

    source = inspect.getsource(mod)
    for forbidden in ("requests.", "urllib", "http.client", "socket.", "open(", "write("):
        assert forbidden not in source


def test_all_permission_boxes_required(qtbot):
    page = PermissionsPage()
    qtbot.addWidget(page)
    assert page.continue_button.isEnabled() is False

    page.checkbox_observe.setChecked(True)
    assert page.continue_button.isEnabled() is False

    page.checkbox_control.setChecked(True)
    assert page.continue_button.isEnabled() is False

    page.checkbox_vision_ai.setChecked(True)
    assert page.continue_button.isEnabled() is True

    page.checkbox_observe.setChecked(False)
    assert page.continue_button.isEnabled() is False


def _fill_required_inputs(page: AutomationPage, sender: str = "Yash", subject: str = "Mail for project") -> None:
    """Target Sender + Send-approval checkbox are BOTH required before
    Start will proceed past the two blocking validations added in front
    of the bounded-approval dialog — see start_automation()."""
    page.sender_input.setText(sender)
    page.subject_input.setText(subject)
    page.send_approval_checkbox.setChecked(True)


def test_start_shows_bounded_approval_dialog_before_anything_else(qtbot):
    """Start Automation shows ONE upfront bounded-approval dialog before
    creating the worker/thread at all. QMessageBox.question is mocked
    here (never shows a real dialog in tests) to return Yes."""
    from PySide6.QtWidgets import QMessageBox

    page = AutomationPage()
    qtbot.addWidget(page)
    controller = AutomationController(page)
    controller.initialize()
    _fill_required_inputs(page)

    with patch("app.controllers.automation_controller.QMessageBox.question",
               return_value=QMessageBox.StandardButton.Yes) as mock_question, \
         patch("app.controllers.automation_controller.QThread") as mock_thread_cls, \
         patch("app.controllers.automation_controller.SendWorker") as mock_worker_cls:
        mock_thread_cls.return_value = MagicMock()
        mock_worker_cls.return_value = MagicMock()
        controller.start_automation()

        mock_question.assert_called_once()
        # bounded_approval_granted=True and send_approval_granted=True were
        # both passed to the worker (two separate flags, both true here).
        _, kwargs = mock_worker_cls.call_args
        assert kwargs.get("bounded_approval_granted") is True
        assert kwargs.get("send_approval_granted") is True

    assert controller.engine.current_state == PlaybookState.LAUNCHING_OUTLOOK
    assert "LAUNCHING_OUTLOOK" in page.current_step_label.text()


def test_start_does_not_actually_launch_outlook(qtbot):
    """start_automation() spins a real QThread running the worker, which
    presses real keys via pyautogui once it executes. QThread itself is
    mocked here so that worker code never actually runs in this test —
    this proves the CONTROLLER's own synchronous part (the READY ->
    LAUNCHING_OUTLOOK transition) neither launches Outlook directly nor
    lets the thread start for real."""
    from PySide6.QtWidgets import QMessageBox

    page = AutomationPage()
    qtbot.addWidget(page)
    controller = AutomationController(page)
    controller.initialize()
    _fill_required_inputs(page)

    with patch("os.startfile", create=True) as mock_startfile, \
         patch("subprocess.Popen") as mock_popen, \
         patch("app.controllers.automation_controller.QMessageBox.question",
               return_value=QMessageBox.StandardButton.Yes), \
         patch("app.controllers.automation_controller.QThread") as mock_thread_cls, \
         patch("app.controllers.automation_controller.SendWorker") as mock_worker_cls:
        # Both QThread and the worker are mocked — moveToThread is a
        # PySide6-typed method that rejects a non-QThread argument, so the
        # WORKER must be the mock (its moveToThread call then does no real
        # type-checking) to avoid ever spinning up a real background
        # thread or executing any real worker code in this test.
        mock_thread_cls.return_value = MagicMock()
        mock_worker_cls.return_value = MagicMock()
        controller.start_automation()

        mock_startfile.assert_not_called()
        mock_popen.assert_not_called()
        mock_thread_cls.return_value.start.assert_called_once()  # thread was asked to start...
        # ...but since QThread itself is mocked, no real thread body ever executed.

    assert controller.engine.current_state == PlaybookState.LAUNCHING_OUTLOOK
    assert "LAUNCHING_OUTLOOK" in page.current_step_label.text()


def test_start_declined_at_bounded_approval_does_not_transition_or_create_worker(qtbot):
    from PySide6.QtWidgets import QMessageBox

    page = AutomationPage()
    qtbot.addWidget(page)
    controller = AutomationController(page)
    controller.initialize()
    _fill_required_inputs(page)

    with patch("app.controllers.automation_controller.QMessageBox.question",
               return_value=QMessageBox.StandardButton.No), \
         patch("app.controllers.automation_controller.QThread") as mock_thread_cls, \
         patch("app.controllers.automation_controller.SendWorker") as mock_worker_cls:
        controller.start_automation()

        mock_thread_cls.assert_not_called()
        mock_worker_cls.assert_not_called()

    assert controller.engine.current_state == PlaybookState.READY
    assert controller._thread is None


def test_start_blocked_when_target_sender_empty(qtbot):
    """Requirement: sender.strip() cannot be empty — Start must refuse
    to proceed (no dialog, no worker) with a clear log message, even
    when Send approval IS checked."""
    from PySide6.QtWidgets import QMessageBox

    page = AutomationPage()
    qtbot.addWidget(page)
    controller = AutomationController(page)
    controller.initialize()
    page.sender_input.setText("   ")  # whitespace-only — must count as empty
    page.send_approval_checkbox.setChecked(True)

    with patch("app.controllers.automation_controller.QMessageBox.question") as mock_question, \
         patch("app.controllers.automation_controller.SendWorker") as mock_worker_cls:
        controller.start_automation()

        mock_question.assert_not_called()
        mock_worker_cls.assert_not_called()

    assert controller.engine.current_state == PlaybookState.READY
    assert "Target Sender is required" in page.activity_log.item(page.activity_log.count() - 1).text()


def test_start_blocked_when_send_approval_not_checked(qtbot):
    """Requirement: Start is blocked with a clear message when the
    dedicated Send-approval checkbox is not checked — even with a valid
    sender filled in. The general PermissionsPage approval is a
    completely separate concept and never substitutes for this."""
    page = AutomationPage()
    qtbot.addWidget(page)
    controller = AutomationController(page)
    controller.initialize()
    page.sender_input.setText("Yash")
    assert page.send_approval_checkbox.isChecked() is False  # unchecked by default

    with patch("app.controllers.automation_controller.QMessageBox.question") as mock_question, \
         patch("app.controllers.automation_controller.SendWorker") as mock_worker_cls:
        controller.start_automation()

        mock_question.assert_not_called()
        mock_worker_cls.assert_not_called()

    assert controller.engine.current_state == PlaybookState.READY
    assert "Send approval is required" in page.activity_log.item(page.activity_log.count() - 1).text()


# --- Target Sender/Subject: UI -> controller -> SendWorker wiring ---

def test_ui_target_values_reach_send_worker_exactly(qtbot):
    from PySide6.QtWidgets import QMessageBox

    page = AutomationPage()
    qtbot.addWidget(page)
    controller = AutomationController(page)
    controller.initialize()
    _fill_required_inputs(page, sender="Priya", subject="Status Update")

    with patch("app.controllers.automation_controller.QMessageBox.question",
               return_value=QMessageBox.StandardButton.Yes), \
         patch("app.controllers.automation_controller.QThread") as mock_thread_cls, \
         patch("app.controllers.automation_controller.SendWorker") as mock_worker_cls:
        mock_thread_cls.return_value = MagicMock()
        mock_worker_cls.return_value = MagicMock()
        controller.start_automation()

        _, kwargs = mock_worker_cls.call_args
        assert kwargs.get("target_sender") == "Priya"
        assert kwargs.get("target_subject") == "Status Update"


def test_ui_empty_subject_stays_optional(qtbot):
    from PySide6.QtWidgets import QMessageBox

    page = AutomationPage()
    qtbot.addWidget(page)
    controller = AutomationController(page)
    controller.initialize()
    _fill_required_inputs(page, sender="Yash", subject="")

    with patch("app.controllers.automation_controller.QMessageBox.question",
               return_value=QMessageBox.StandardButton.Yes), \
         patch("app.controllers.automation_controller.QThread") as mock_thread_cls, \
         patch("app.controllers.automation_controller.SendWorker") as mock_worker_cls:
        mock_thread_cls.return_value = MagicMock()
        mock_worker_cls.return_value = MagicMock()
        controller.start_automation()

        _, kwargs = mock_worker_cls.call_args
        assert kwargs.get("target_sender") == "Yash"
        assert kwargs.get("target_subject") == ""


def test_hardcoded_defaults_do_not_override_ui_provided_values(qtbot):
    """Regression: TARGET_EMAIL_SENDER/TARGET_EMAIL_SUBJECT are real,
    different strings than what's entered here — proving the UI's actual
    values reach the worker rather than silently falling back to the
    module-level dev/test defaults."""
    from PySide6.QtWidgets import QMessageBox

    from app.outlook.find_email import TARGET_EMAIL_SENDER, TARGET_EMAIL_SUBJECT

    ui_sender, ui_subject = "Someone Else Entirely", "A Completely Different Subject"
    assert ui_sender != TARGET_EMAIL_SENDER
    assert ui_subject != TARGET_EMAIL_SUBJECT

    page = AutomationPage()
    qtbot.addWidget(page)
    controller = AutomationController(page)
    controller.initialize()
    _fill_required_inputs(page, sender=ui_sender, subject=ui_subject)

    with patch("app.controllers.automation_controller.QMessageBox.question",
               return_value=QMessageBox.StandardButton.Yes), \
         patch("app.controllers.automation_controller.QThread") as mock_thread_cls, \
         patch("app.controllers.automation_controller.SendWorker") as mock_worker_cls:
        mock_thread_cls.return_value = MagicMock()
        mock_worker_cls.return_value = MagicMock()
        controller.start_automation()

        _, kwargs = mock_worker_cls.call_args
        assert kwargs.get("target_sender") == ui_sender
        assert kwargs.get("target_subject") == ui_subject
        assert kwargs.get("target_sender") != TARGET_EMAIL_SENDER
        assert kwargs.get("target_subject") != TARGET_EMAIL_SUBJECT


def test_abort_click_sets_flag_without_a_running_worker(qtbot):
    """Clicking Abort with no worker running (or one that hasn't reached a
    checkpoint yet) only sets the shared flag and logs — it does not
    unilaterally force the engine into ABORTED; that only happens once
    the worker itself confirms it stopped (see _on_worker_aborted below),
    consistent with 'later automation workers will inspect this abort
    flag between actions' rather than an immediate forced transition."""
    page = AutomationPage()
    qtbot.addWidget(page)
    controller = AutomationController(page)
    controller.initialize()
    page.set_running()  # Abort is only ever clickable once Start has enabled it

    from PySide6.QtCore import Qt

    qtbot.mouseClick(page.abort_button, Qt.MouseButton.LeftButton)

    assert controller.abort_controller.is_abort_requested() is True
    assert "Abort requested" in page.activity_log.item(page.activity_log.count() - 1).text()


def test_on_worker_aborted_moves_engine_to_aborted_and_updates_dashboard(qtbot):
    """Simulates the worker actually confirming an abort (what
    OutlookLaunchWorker.aborted.emit(...) would trigger) without needing
    a real background thread to run and reach that point."""
    page = AutomationPage()
    qtbot.addWidget(page)
    controller = AutomationController(page)
    controller.initialize()
    controller.engine.start()  # READY -> LAUNCHING_OUTLOOK, as start_automation() would do
    controller.abort_controller.request_abort()

    controller._on_worker_aborted("USER_ABORTED")

    assert controller.engine.current_state == PlaybookState.ABORTED
    assert "ABORTED" in page.current_step_label.text()
    assert page.start_button.isEnabled() is True
    assert page.abort_button.isEnabled() is False


def test_on_worker_success_advances_engine_to_outlook_ready(qtbot):
    page = AutomationPage()
    qtbot.addWidget(page)
    controller = AutomationController(page)
    controller.initialize()
    controller.engine.start()  # READY -> LAUNCHING_OUTLOOK

    controller._on_worker_current_step("OUTLOOK_READY")
    controller._on_worker_success({
        "total_vision_calls": 2, "total_input_tokens": 100, "total_output_tokens": 20,
        "total_estimated_cost": 0.001, "total_latency_ms": 500.0,
    })

    assert controller.engine.current_state == PlaybookState.OUTLOOK_READY
    assert controller.metrics.vision_calls == 2
    assert page.start_button.isEnabled() is True  # re-enabled, ready for a future stage


def test_on_worker_failure_does_not_advance_past_launching_outlook(qtbot):
    page = AutomationPage()
    qtbot.addWidget(page)
    controller = AutomationController(page)
    controller.initialize()
    controller.engine.start()  # READY -> LAUNCHING_OUTLOOK

    controller._on_worker_failure("OUTLOOK_LAUNCH_TIMEOUT", "Outlook foreground not detected within 18.0s.")

    assert controller.engine.current_state == PlaybookState.FAILED_SAFE
    assert controller.engine.current_state != PlaybookState.OUTLOOK_READY
    assert controller.metrics.failed_step == "OUTLOOK_LAUNCH_TIMEOUT"


def test_rnd009b_never_advances_to_finding_email():
    """Structural: neither the worker nor the step logic mentions
    FINDING_EMAIL anywhere — this stage stops at OUTLOOK_READY."""
    import app.outlook.launch as steps_mod
    import app.workers.outlook_launch_worker as worker_mod

    for module in (steps_mod, worker_mod):
        source = inspect.getsource(module)
        assert "FINDING_EMAIL" not in source


def test_outlook_launch_steps_pyautogui_usage_is_tightly_scoped():
    """Structural safety proof for the one module in app/ that legitimately
    touches pyautogui: exactly one Windows-key press, one text-write call,
    one mouse move, one click — no Send-related hotkey anywhere."""
    import app.outlook.launch as mod

    source = inspect.getsource(mod)
    normalized = source.replace('"', "'")
    assert normalized.count("pyautogui.press('win')") == 1
    assert normalized.count("pyautogui.write(") == 1
    assert source.count("pyautogui.moveTo(") == 1
    assert normalized.count("pyautogui.click()") == 1
    assert "hotkey('ctrl', 'enter')" not in normalized
    assert "hotkey('alt', 's')" not in normalized


def _controller_with_running_worker(qtbot):
    """A controller in the 'automation running' state, as if
    start_automation() had already created a thread/worker — without
    actually spinning a real QThread or running any real worker code.
    Used by the QThread-lifecycle tests below (A-J)."""
    page = AutomationPage()
    qtbot.addWidget(page)
    controller = AutomationController(page)
    controller.initialize()
    controller.engine.start()  # READY -> LAUNCHING_OUTLOOK
    mock_thread = MagicMock()
    mock_thread.isRunning.return_value = True
    mock_worker = MagicMock()
    controller._thread = mock_thread
    controller._worker = mock_worker
    page.set_running()
    return controller, page, mock_thread, mock_worker


# --- A/B/C: intermediate progress signals never tear down the thread ---

def test_intermediate_email_opened_keeps_thread_alive(qtbot):
    controller, _page, mock_thread, mock_worker = _controller_with_running_worker(qtbot)
    controller._on_worker_current_step("EMAIL_OPENED")
    assert controller._thread is mock_thread
    assert controller._worker is mock_worker
    mock_thread.quit.assert_not_called()
    mock_thread.wait.assert_not_called()


def test_email_understood_progress_keeps_thread_alive(qtbot):
    controller, _page, mock_thread, _mock_worker = _controller_with_running_worker(qtbot)
    controller._on_worker_current_step("READING_EMAIL")
    assert controller._thread is mock_thread
    mock_thread.quit.assert_not_called()


def test_draft_ready_progress_keeps_thread_alive(qtbot):
    controller, _page, mock_thread, _mock_worker = _controller_with_running_worker(qtbot)
    for step in (
        "OUTLOOK_READY", "FINDING_EMAIL", "EMAIL_OPENED", "READING_EMAIL",
        "FINDING_REPLY", "REPLY_EDITOR_OPEN", "GENERATING_DRAFT", "TYPING_DRAFT",
        "VERIFYING_DRAFT", "DRAFT_READY",
    ):
        controller._on_worker_current_step(step)
    assert controller._thread is mock_thread
    mock_thread.quit.assert_not_called()
    mock_thread.wait.assert_not_called()


# --- D: only final worker.finished triggers thread.quit() ---

def test_only_worker_finished_triggers_thread_quit(qtbot):
    controller, _page, mock_thread, _mock_worker = _controller_with_running_worker(qtbot)
    controller._on_worker_current_step("EMAIL_OPENED")
    controller._on_worker_success({
        "total_vision_calls": 1, "total_input_tokens": 1, "total_output_tokens": 1,
        "total_estimated_cost": 0.0, "total_latency_ms": 1.0,
    })
    mock_thread.quit.assert_not_called()  # success alone (business signal) still doesn't tear down
    controller._on_worker_finished()
    mock_thread.quit.assert_called_once()


# --- E: cleanup code never calls wait() from any slot ---

def test_no_wait_call_anywhere_in_controller_source():
    import app.controllers.automation_controller as mod

    source = inspect.getsource(mod)
    assert ".wait()" not in source


# --- F/G: thread/worker references cleared only after thread.finished ---

def test_thread_reference_survives_worker_finished_until_thread_finished(qtbot):
    controller, _page, mock_thread, mock_worker = _controller_with_running_worker(qtbot)
    controller._on_worker_finished()
    assert controller._thread is mock_thread  # worker.finished alone does NOT clear it
    assert controller._worker is mock_worker
    controller._on_thread_finished()
    assert controller._thread is None
    assert controller._worker is None


# --- H: abort leads to graceful exit + the same signal-driven cleanup ---

def test_abort_leads_to_graceful_signal_driven_cleanup(qtbot):
    controller, _page, mock_thread, _mock_worker = _controller_with_running_worker(qtbot)
    controller.abort_automation()
    assert controller.abort_controller.is_abort_requested() is True
    mock_thread.quit.assert_not_called()
    mock_thread.wait.assert_not_called()

    controller._on_worker_aborted("USER_ABORTED")
    mock_thread.quit.assert_not_called()  # still not — only worker.finished tears down

    controller._on_worker_finished()
    mock_thread.quit.assert_called_once()
    mock_thread.wait.assert_not_called()

    controller._on_thread_finished()
    assert controller._thread is None
    assert controller._worker is None


# --- I: deleteLater() is only ever called from the two *_finished slots ---

def test_delete_later_only_called_from_finished_slots():
    import app.controllers.automation_controller as mod

    full_source = inspect.getsource(mod)
    finished_slots_source = (
        inspect.getsource(mod.AutomationController._on_worker_finished)
        + inspect.getsource(mod.AutomationController._on_thread_finished)
    )
    assert full_source.count("deleteLater()") == finished_slots_source.count("deleteLater()")
    assert full_source.count("deleteLater()") == 2  # worker.deleteLater() + thread.deleteLater()


# --- J: Start button re-enabled only after thread.finished ---

def test_start_button_reenabled_only_after_thread_finished(qtbot):
    controller, page, _mock_thread, _mock_worker = _controller_with_running_worker(qtbot)
    assert page.start_button.isEnabled() is False

    controller._on_worker_success({
        "total_vision_calls": 1, "total_input_tokens": 1, "total_output_tokens": 1,
        "total_estimated_cost": 0.0, "total_latency_ms": 1.0,
    })
    assert page.start_button.isEnabled() is False  # worker signaled — thread hasn't finished yet

    controller._on_worker_finished()
    assert page.start_button.isEnabled() is False  # quit() requested, but thread.finished hasn't fired

    controller._on_thread_finished()
    assert page.start_button.isEnabled() is True


# --- Real QThread integration: proves the actual fix, not just mocks ---

def test_real_qthread_lifecycle_completes_without_self_wait(qtbot):
    """Spins a GENUINE QThread with a real SendWorker (only _provider is
    patched, so no real Gemini/network call happens; bounded_approval_
    granted=False makes run() take its earliest exit path before any
    pyautogui action). If the pre-fix bug (AutomationController not
    being a QObject, so its slots ran on the worker thread) were still
    present, this would either hang, emit a Qt "wait on itself" warning,
    or leave controller._thread non-None after thread.finished. None of
    that happens here — the strongest available proof this fix works,
    short of a live UI run."""
    from PySide6.QtCore import QThread

    from app.safety.abort_controller import AbortController
    from app.workers.send_worker import SendWorker

    page = AutomationPage()
    qtbot.addWidget(page)
    controller = AutomationController(page)
    controller.initialize()

    thread = QThread()
    worker = SendWorker(
        AbortController(), bounded_approval_granted=False, send_approval_granted=False,
        target_sender="Yash",
    )
    worker.moveToThread(thread)
    controller._thread = thread
    controller._worker = worker

    thread.started.connect(controller._on_thread_started)
    thread.started.connect(worker.run)
    worker.aborted.connect(controller._on_worker_aborted)
    worker.finished.connect(controller._on_worker_finished)
    thread.finished.connect(controller._on_thread_finished)

    with patch("app.workers.send_worker._provider", return_value=(MagicMock(), "gemini-3.6-flash")), \
         qtbot.waitSignal(thread.finished, timeout=5000):
        thread.start()

    assert controller._thread is None
    assert controller._worker is None
    assert thread.isRunning() is False


# --- Phase-7 state-machine sequencing/authority fix ---
#
# Root cause of the previously reported "DRAFT_READY -> VERIFYING_SEND
# invalid transition": SendWorker used to emit ad hoc step names
# (PRE_SEND_VALIDATION, SEND_EXECUTED, SENT_VERIFIED) that don't exist
# in PlaybookState. Two of those three fell through the "unrecognized
# name -> just return" branch, silently leaving engine.current_state
# stuck at DRAFT_READY while the real worker kept running; the third
# (VERIFYING_SEND) then failed as an invalid transition FROM DRAFT_READY
# — logged and ignored while Send had already been clicked and verified.
# Fixed by (1) SendWorker now emitting PlaybookState's own pre-existing
# Send vocabulary (WAITING_FOR_SEND_APPROVAL -> SENDING -> VERIFYING_SEND
# -> COMPLETED), and (2) any rejected/unrecognized transition now also
# requesting an abort (via the same AbortController every SendFlowSteps
# phase already checks), so it can never again be merely logged while
# physical execution continues.

_VALID_PHASE7_SEQUENCE = (
    "OUTLOOK_READY", "FINDING_EMAIL", "EMAIL_OPENED", "READING_EMAIL",
    "FINDING_REPLY", "REPLY_EDITOR_OPEN", "GENERATING_DRAFT", "TYPING_DRAFT",
    "VERIFYING_DRAFT", "DRAFT_READY",
    "WAITING_FOR_SEND_APPROVAL", "SENDING", "VERIFYING_SEND", "COMPLETED",
)


def test_valid_phase7_transition_sequence_reaches_completed(qtbot):
    page = AutomationPage()
    qtbot.addWidget(page)
    controller = AutomationController(page)
    controller.initialize()
    controller.engine.start()  # READY -> LAUNCHING_OUTLOOK
    # Mirrors what start_automation() does once the dedicated Send
    # checkbox was validated checked — a second, independent guard
    # app/playbook/engine.py enforces before WAITING_FOR_SEND_APPROVAL
    # -> SENDING, regardless of sequencing.
    controller.engine.approve_send()

    for step in _VALID_PHASE7_SEQUENCE:
        controller._on_worker_current_step(step)

    assert controller.engine.current_state == PlaybookState.COMPLETED
    assert controller.abort_controller.is_abort_requested() is False
    log_text = "\n".join(page.activity_log.item(i).text() for i in range(page.activity_log.count()))
    assert "rejected" not in log_text.lower()


def test_invalid_transition_requests_abort_and_stops_execution(qtbot):
    """The exact previously reported scenario: engine sitting at
    DRAFT_READY, then a transition straight to VERIFYING_SEND (skipping
    WAITING_FOR_SEND_APPROVAL/SENDING) is attempted. Must be rejected
    AND must actually stop further physical execution — proven here by
    the abort flag being set, which is the same flag SendFlowSteps'
    check_abort() consults at every safety checkpoint."""
    page = AutomationPage()
    qtbot.addWidget(page)
    controller = AutomationController(page)
    controller.initialize()
    controller.engine.start()
    controller.engine.approve_send()  # isolate the failure to sequencing, not approval

    for step in ("OUTLOOK_READY", "FINDING_EMAIL", "EMAIL_OPENED", "READING_EMAIL",
                 "FINDING_REPLY", "REPLY_EDITOR_OPEN", "GENERATING_DRAFT", "TYPING_DRAFT",
                 "VERIFYING_DRAFT", "DRAFT_READY"):
        controller._on_worker_current_step(step)
    assert controller.engine.current_state == PlaybookState.DRAFT_READY
    assert controller.abort_controller.is_abort_requested() is False

    controller._on_worker_current_step("VERIFYING_SEND")  # skips the two required predecessors

    assert controller.engine.current_state == PlaybookState.DRAFT_READY  # rejected — never advanced
    assert controller.abort_controller.is_abort_requested() is True  # NOT merely logged — enforced
    log_text = "\n".join(page.activity_log.item(i).text() for i in range(page.activity_log.count()))
    assert "rejected" in log_text.lower()


def test_start_automation_approves_send_on_the_engine(qtbot):
    """Regression for the send_approved guard gap: app/playbook/engine.py
    independently requires approve_send() to have been called before it
    will EVER allow WAITING_FOR_SEND_APPROVAL -> SENDING, regardless of
    step-name sequencing being correct. Without this, every real run
    would still be rejected at that exact transition."""
    from PySide6.QtWidgets import QMessageBox

    page = AutomationPage()
    qtbot.addWidget(page)
    controller = AutomationController(page)
    controller.initialize()
    _fill_required_inputs(page)
    assert controller.engine.send_approved is False

    with patch("app.controllers.automation_controller.QMessageBox.question",
               return_value=QMessageBox.StandardButton.Yes), \
         patch("app.controllers.automation_controller.QThread") as mock_thread_cls, \
         patch("app.controllers.automation_controller.SendWorker") as mock_worker_cls:
        mock_thread_cls.return_value = MagicMock()
        mock_worker_cls.return_value = MagicMock()
        controller.start_automation()

    assert controller.engine.send_approved is True


def test_unrecognized_step_name_also_requests_abort(qtbot):
    page = AutomationPage()
    qtbot.addWidget(page)
    controller = AutomationController(page)
    controller.initialize()
    controller.engine.start()

    assert controller.abort_controller.is_abort_requested() is False
    controller._on_worker_current_step("SOME_FUTURE_UNRECOGNIZED_STEP")
    assert controller.abort_controller.is_abort_requested() is True


def test_every_worker_emitted_current_step_is_a_known_playbook_state():
    """Live-run regression (2026-09-04): SendWorker/ReplyDraftWorker both
    emitted a literal "EMAIL_CONTENT_COMPLETE" current_step that was
    never a member of PlaybookState — every real run aborted the instant
    email reading finished, with "Unrecognized playbook step" (see
    AutomationController._on_worker_current_step / _reject_transition).
    The fully-mocked worker-level tests never caught this because they
    collect current_step signals directly, never through the real
    AutomationController/PlaybookState validation path — only
    test_unrecognized_step_name_also_requests_abort above exercises that
    path, and only with a synthetic name, not the workers' own literals.

    This statically scans both real worker source files for every
    self.current_step.emit("...") literal and asserts each one is a
    valid PlaybookState value — so a future stray/renamed step name
    fails at test time, not on a live run."""
    import app.workers.reply_draft_worker as reply_worker_module
    import app.workers.send_worker as send_worker_module

    known_states = {state.value for state in PlaybookState}
    pattern = re.compile(r'self\.current_step\.emit\("([^"]+)"\)')

    for module in (reply_worker_module, send_worker_module):
        source = inspect.getsource(module)
        emitted = pattern.findall(source)
        assert emitted, f"{module.__name__}: expected current_step.emit(...) calls, found none"
        for step_name in emitted:
            assert step_name in known_states, (
                f"{module.__name__} emits current_step {step_name!r}, "
                f"which is not a PlaybookState member — every live run "
                f"would abort here with 'Unrecognized playbook step'."
            )


def test_no_pyautogui_actions_occur_outside_the_named_outlook_modules():
    """Structural proof that pyautogui is used ONLY in the app/outlook/
    *.py step modules (launch.py, find_email.py, reply.py, draft.py)
    and app/automation/scrolling.py (Phase 3's controlled message-list
    scroll) — never in any worker, controller, UI, safety, config,
    vision, fallback, or playbook-scaffolding module, keeping the
    pyautogui surface area reviewable in exactly those five files."""
    import app.controllers.app_controller as c1
    import app.controllers.automation_controller as c2
    import app.main as c3
    import app.metrics.session_metrics as c4
    import app.metrics.step_metrics as c4b
    import app.playbook.engine as c5
    import app.playbook.failure_reasons as c6
    import app.playbook.models as c7
    import app.playbook.states as c8
    import app.playbook.context as c8b
    import app.playbook.step_contract as c8c
    import app.safety.abort_controller as c9
    import app.safety.foreground as c10
    import app.safety.validators as c10b
    import app.fallback.classifications as c10c
    import app.config.settings as c10d
    import app.vision.grounding as c10e
    import app.vision.verification as c10f
    import app.ui.automation_page as c11
    import app.ui.login_page as c12
    import app.ui.main_window as c13
    import app.ui.permissions_page as c14
    import app.ui.result_page as c15
    import app.workers.find_open_email_worker as c16
    import app.workers.outlook_launch_worker as c17
    import app.workers.reply_draft_worker as c18
    import app.workers.send_worker as c19

    for module in (
        c1, c2, c3, c4, c4b, c5, c6, c7, c8, c8b, c8c, c9, c10, c10b, c10c, c10d, c10e, c10f,
        c11, c12, c13, c14, c15, c16, c17, c18, c19,
    ):
        source = inspect.getsource(module)
        # Checks for actual usage, not the word "pyautogui" in prose —
        # several modules' own docstrings/comments explain that pyautogui
        # is intentionally NOT used (here) / IS used elsewhere, which would
        # otherwise self-trip a naive substring check.
        assert "import pyautogui" not in source, f"{module.__name__} imports pyautogui"
        assert "pyautogui." not in source, f"{module.__name__} calls pyautogui"
