"""SendWorker tests (Phase 7 — final assembled Phases 1-7 chain).
Everything the worker touches (pyautogui, provider, foreground checks,
screen capture) is mocked; run() is called directly — no real
automation, no live Outlook, anywhere in this file.
"""

import inspect
import itertools
import sys
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.outlook.find_email import TARGET_EMAIL_SENDER, TARGET_EMAIL_SUBJECT  # noqa: E402
from app.safety.abort_controller import AbortController  # noqa: E402
from app.workers.send_worker import SendWorker  # noqa: E402

LAUNCH_MODULE = "app.outlook.launch"
FIND_MODULE = "app.outlook.find_email"
READ_MODULE = "app.outlook.read_email"
REPLY_MODULE = "app.outlook.reply"
DRAFT_MODULE = "app.outlook.draft"
SEND_MODULE = "app.outlook.send"
WORKER_MODULE = "app.workers.send_worker"

DRAFT_TEXT = "Hi Yash,\n\nThank you for reaching out.\n\nBest regards,"


def _search_grounding():
    return MagicMock(
        parsed_json={"search_visible": True, "target_visible": True, "target_type": "desktop_app", "visible_label": "Outlook",
                     "bbox": [250.0, 400.0, 350.0, 600.0], "confidence": 0.95, "reason": "ok"},
        raw_text="{}", model="gemini-3.6-flash", latency_ms=100.0, input_tokens=10, output_tokens=5,
    )


def _readiness_ready():
    return MagicMock(
        parsed_json={"application": "Outlook", "outlook_visible": True, "splash_screen_visible": False,
                     "ready_for_interaction": True, "detected_state": "inbox", "confidence": 0.95, "reason": "ok"},
        raw_text="{}", model="gemini-3.6-flash", latency_ms=100.0, input_tokens=10, output_tokens=5,
    )


def _email_grounding():
    return MagicMock(
        parsed_json={
            "outlook_visible": True, "message_list_visible": True, "target_visible": True,
            "candidate_count": 1,
            "candidates": [{
                "sender": TARGET_EMAIL_SENDER, "subject": TARGET_EMAIL_SUBJECT, "date_or_order": "Today",
                "row_bbox": [480.0, 300.0, 520.0, 900.0], "confidence": 0.95,
            }],
            "more_content_below": False, "reason": "ok",
        },
        raw_text="{}", model="gemini-3.6-flash", latency_ms=100.0, input_tokens=10, output_tokens=5,
    )


def _email_open_verified():
    return MagicMock(
        parsed_json={"email_open": True, "subject_detected": TARGET_EMAIL_SUBJECT, "sender_detected": TARGET_EMAIL_SENDER,
                     "subject_match": True, "sender_match": True, "body_visible": True, "confidence": 0.95, "reason": "ok"},
        raw_text="{}", model="gemini-3.6-flash", latency_ms=100.0, input_tokens=10, output_tokens=5,
    )


def _understanding(reply_expectation="MUST_REPLY", requires_user_decision=False):
    return MagicMock(
        parsed_json={"extracted_visible_content": "greeting", "overlap_text": "", "important_points": [],
                     "requested_actions": [], "names_entities": [], "dates": [], "commitments": [],
                     "more_content_below": False, "end_of_message_visible": True, "no_new_content": False,
                     "reply_expectation": reply_expectation,
                     "requires_user_decision": requires_user_decision, "sender_intent": "check-in",
                     "requested_action_summary": "", "confidence": 0.9, "reason": "ok"},
        raw_text="{}", model="gemini-3.6-flash", latency_ms=100.0, input_tokens=10, output_tokens=5,
    )


def _state_check(verified):
    return MagicMock(
        parsed_json={"verified": verified, "detected_state": "editor state", "confidence": 0.9,
                     "visual_evidence": "ok", "reason": "ok"},
        raw_text="{}", model="gemini-3.6-flash", latency_ms=100.0, input_tokens=10, output_tokens=5,
    )


def _draft_generation():
    return MagicMock(
        parsed_json={"draft_reply": DRAFT_TEXT, "reasoning_summary": "r", "confidence": 0.95},
        raw_text="{}", model="gemini-3.6-flash", latency_ms=100.0, input_tokens=10, output_tokens=5,
    )


def _draft_verification(semantic_match=True):
    return MagicMock(
        parsed_json={"reply_editor_open": True, "semantic_match": semantic_match, "detected_draft": DRAFT_TEXT,
                     "confidence": 0.95, "reason": "ok"},
        raw_text="{}", model="gemini-3.6-flash", latency_ms=100.0, input_tokens=10, output_tokens=5,
    )


def _send_search():
    return MagicMock(
        parsed_json={"outlook_visible": True, "send_visible": True, "control_identity": "Send",
                     "control_type": "button", "bbox": [400.0, 800.0, 440.0, 900.0], "confidence": 0.95,
                     "reason": "ok"},
        raw_text="{}", model="gemini-3.6-flash", latency_ms=100.0, input_tokens=10, output_tokens=5,
    )


def _sent_verification(verified=True):
    return MagicMock(
        parsed_json={"verified": verified, "detected_state": "inbox, no compose box", "confidence": 0.95,
                     "visual_evidence": "no compose box visible", "reason": "ok"},
        raw_text="{}", model="gemini-3.6-flash", latency_ms=100.0, input_tokens=10, output_tokens=5,
    )


def _patch_full_chain(stack: ExitStack, titles) -> dict:
    """Applies every mock the full launch->find->read->reply->draft->send
    chain needs, via an ExitStack (a single flat `with` statement patching
    this many targets hits Python's statically-nested-block compiler
    limit). Returns the per-module pyautogui mocks for call-count
    assertions."""
    pyautogui_mocks = {
        "launch": stack.enter_context(patch(f"{LAUNCH_MODULE}.pyautogui")),
        "find": stack.enter_context(patch(f"{FIND_MODULE}.pyautogui")),
        "reply": stack.enter_context(patch(f"{REPLY_MODULE}.pyautogui")),
        "draft": stack.enter_context(patch(f"{DRAFT_MODULE}.pyautogui")),
        "send": stack.enter_context(patch(f"{SEND_MODULE}.pyautogui")),
    }
    for mock_pg in pyautogui_mocks.values():
        mock_pg.FAILSAFE = True

    for module in (LAUNCH_MODULE, FIND_MODULE, REPLY_MODULE, DRAFT_MODULE, SEND_MODULE):
        stack.enter_context(patch(f"{module}.time.sleep"))

    stack.enter_context(patch(f"{LAUNCH_MODULE}.get_foreground_window_title", side_effect=titles))
    stack.enter_context(patch(f"{LAUNCH_MODULE}.get_foreground_hwnd", return_value=12345))
    stack.enter_context(patch(f"{LAUNCH_MODULE}.is_maximized", return_value=True))
    stack.enter_context(patch(f"{LAUNCH_MODULE}.maximize"))
    stack.enter_context(patch(
        f"{LAUNCH_MODULE}.get_environment_info",
        return_value={"pyautogui_width": 1920, "pyautogui_height": 1080, "dimensions_match": True},
    ))
    for module in (FIND_MODULE, READ_MODULE, REPLY_MODULE, DRAFT_MODULE, SEND_MODULE):
        stack.enter_context(patch(f"{module}.get_foreground_window_title", return_value="Inbox - Outlook"))

    stack.enter_context(patch(
        f"{LAUNCH_MODULE}.capture_screen",
        return_value=MagicMock(filename="a.png", path="a.png", width=1920, height=1080),
    ))
    for module, name in (
        (FIND_MODULE, "b"), (READ_MODULE, "d"), (REPLY_MODULE, "c"), (DRAFT_MODULE, "e"), (SEND_MODULE, "f"),
    ):
        stack.enter_context(patch(
            f"{module}.capture_screen",
            return_value=MagicMock(filename=f"{name}.png", path=f"{name}.png", width=1920, height=1080),
        ))

    return pyautogui_mocks


def test_full_chain_reaches_sent_verified():
    signals = {"success": [], "failure": [], "aborted": [], "current_step": []}
    worker = SendWorker(AbortController(), bounded_approval_granted=True, send_approval_granted=True)
    worker.success.connect(lambda r: signals["success"].append(r))
    worker.failure.connect(lambda *a: signals["failure"].append(a))
    worker.aborted.connect(lambda *a: signals["aborted"].append(a))
    worker.current_step.connect(lambda s: signals["current_step"].append(s))

    mock_provider = MagicMock()
    mock_provider.analyze_screen.side_effect = [
        _search_grounding(), _readiness_ready(), _email_grounding(), _email_open_verified(),
        _understanding(), _understanding(),  # extraction call, then holistic-assessment call (2026-09-04 split)
        _state_check(True),          # prepare_reply_editor: already open (skip click)
        _state_check(True),          # verify_reply_editor
        _draft_generation(),
        _draft_verification(True),
        _send_search(),
        _sent_verification(True),
    ]

    titles = itertools.chain(
        ["Search", "Search", "Search"],
        itertools.repeat("Inbox - Outlook"),
    )

    with patch(f"{WORKER_MODULE}._provider", return_value=(mock_provider, "gemini-3.6-flash")), ExitStack() as stack:
        mocks = _patch_full_chain(stack, titles)
        worker.run()

    assert signals["success"], f"expected success, got failure={signals['failure']} aborted={signals['aborted']}"
    result = signals["success"][0]
    assert result["result"] == "PASS"
    assert result["send_click_count"] == 1
    assert result["sent_verified"] is True
    assert result["send_execution_occurred"] is True
    # Phase 1-6 metrics propagated into the Phase 7 result:
    assert result["typing_result"] == "PASS"
    assert result["semantic_match"] is True
    assert result["reply_click_count"] == 0  # editor already open — Phase 5 metric
    assert result["total_elapsed_ms"] is not None
    # SendWorker emits PlaybookState's own Send vocabulary (see its
    # module docstring) — WAITING_FOR_SEND_APPROVAL -> SENDING ->
    # VERIFYING_SEND -> COMPLETED — not ad hoc names, so the
    # PlaybookEngine's SEND_STATE guard is actually meaningful for a
    # real run of this worker.
    assert "WAITING_FOR_SEND_APPROVAL" in signals["current_step"]
    assert "SENDING" in signals["current_step"]
    assert "VERIFYING_SEND" in signals["current_step"]
    assert "COMPLETED" in signals["current_step"]
    mocks["send"].click.assert_called_once()
    mocks["reply"].click.assert_not_called()  # reply editor was already open — no Reply click


def test_send_not_approved_zero_click():
    signals = {"success": [], "failure": []}
    worker = SendWorker(AbortController(), bounded_approval_granted=True, send_approval_granted=False)
    worker.success.connect(lambda r: signals["success"].append(r))
    worker.failure.connect(lambda *a: signals["failure"].append(a))

    mock_provider = MagicMock()
    mock_provider.analyze_screen.side_effect = [
        _search_grounding(), _readiness_ready(), _email_grounding(), _email_open_verified(),
        _understanding(), _understanding(),  # extraction call, then holistic-assessment call (2026-09-04 split)
        _state_check(True), _state_check(True), _draft_generation(), _draft_verification(True),
    ]
    titles = itertools.chain(["Search", "Search", "Search"], itertools.repeat("Inbox - Outlook"))

    with patch(f"{WORKER_MODULE}._provider", return_value=(mock_provider, "gemini-3.6-flash")), ExitStack() as stack:
        mocks = _patch_full_chain(stack, titles)
        worker.run()
        mocks["send"].click.assert_not_called()

    assert not signals["success"]
    assert signals["failure"][0][0] == "SEND_NOT_APPROVED"


def test_verification_uncertain_keeps_click_count_one_end_to_end():
    signals = {"success": [], "failure": []}
    worker = SendWorker(AbortController(), bounded_approval_granted=True, send_approval_granted=True)
    worker.success.connect(lambda r: signals["success"].append(r))
    worker.failure.connect(lambda *a: signals["failure"].append(a))

    mock_provider = MagicMock()
    mock_provider.analyze_screen.side_effect = [
        _search_grounding(), _readiness_ready(), _email_grounding(), _email_open_verified(),
        _understanding(), _understanding(),  # extraction call, then holistic-assessment call (2026-09-04 split)
        _state_check(True), _state_check(True), _draft_generation(), _draft_verification(True),
        _send_search(),
        _sent_verification(False), _sent_verification(False),  # both verification attempts inconclusive
    ]
    titles = itertools.chain(["Search", "Search", "Search"], itertools.repeat("Inbox - Outlook"))

    with patch(f"{WORKER_MODULE}._provider", return_value=(mock_provider, "gemini-3.6-flash")), ExitStack() as stack:
        mocks = _patch_full_chain(stack, titles)
        worker.run()

    assert not signals["success"]
    failure_reason, _ = signals["failure"][0]
    assert failure_reason == "SEND_VERIFICATION_UNCERTAIN"
    mocks["send"].click.assert_called_once()  # Send WAS clicked exactly once — never retried


def test_worker_has_no_resend_signal_or_method():
    for forbidden in ("resend", "click_send_again", "force_send"):
        assert not hasattr(SendWorker, forbidden)


def test_no_send_shortcut_in_worker_source():
    source = inspect.getsource(sys.modules[SendWorker.__module__])
    normalized = source.replace('"', "'").lower()
    assert "hotkey('ctrl', 'enter')" not in normalized
    assert "hotkey('alt', 's')" not in normalized
