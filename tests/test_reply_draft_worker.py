"""ReplyDraftWorker tests (final POC — moved from RND-009D). Everything
the worker touches (pyautogui, provider, foreground checks, screen
capture) is mocked; run() is called directly — no real automation
occurs anywhere here.

Reply discovery/click lives in app.outlook.reply, draft generation/
typing/verification in app.outlook.draft, email understanding in
app.outlook.read_email — each imports its own pyautogui/foreground/
capture references, so each needs its own patch target (unlike the
pre-split single-module reply_draft_steps.py).
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
from app.vision.service import VisionService  # noqa: E402
from app.workers.reply_draft_worker import ReplyDraftWorker  # noqa: E402
from tests._capture_test_utils import real_capture_image_path, to_crop_relative_bbox  # noqa: E402

LAUNCH_MODULE = "app.outlook.launch"
FIND_MODULE = "app.outlook.find_email"
READ_MODULE = "app.outlook.read_email"
REPLY_MODULE = "app.outlook.reply"
DRAFT_MODULE = "app.outlook.draft"
WORKER_MODULE = "app.workers.reply_draft_worker"

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
                "row_bbox": to_crop_relative_bbox([480.0, 300.0, 520.0, 480.0]), "confidence": 0.95,
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


def test_worker_has_no_send_signal_or_method():
    for forbidden in ("send", "reply_and_send", "click_send"):
        assert not hasattr(ReplyDraftWorker, forbidden)


def test_no_send_code_path_in_worker_source():
    source = inspect.getsource(sys.modules[ReplyDraftWorker.__module__])
    normalized = source.replace('"', "'")
    assert "hotkey('ctrl', 'enter')" not in normalized
    assert "hotkey('alt', 's')" not in normalized
    assert "'SENDING'" not in normalized  # never transitions to the SENDING playbook state


def _patch_full_chain(stack: ExitStack, titles) -> dict:
    """Applies every mock the full launch->find->read->reply->draft chain
    needs, via an ExitStack (a single flat `with` statement patching this
    many targets hits Python's statically-nested-block compiler limit).
    Returns the four per-module pyautogui mocks for call-count assertions.
    """
    pyautogui_mocks = {
        "launch": stack.enter_context(patch(f"{LAUNCH_MODULE}.pyautogui")),
        "find": stack.enter_context(patch(f"{FIND_MODULE}.pyautogui")),
        "reply": stack.enter_context(patch(f"{REPLY_MODULE}.pyautogui")),
        "draft": stack.enter_context(patch(f"{DRAFT_MODULE}.pyautogui")),
    }
    for mock_pg in pyautogui_mocks.values():
        mock_pg.FAILSAFE = True

    for module in (LAUNCH_MODULE, FIND_MODULE, REPLY_MODULE, DRAFT_MODULE):
        stack.enter_context(patch(f"{module}.time.sleep"))

    stack.enter_context(patch(f"{LAUNCH_MODULE}.get_foreground_window_title", side_effect=titles))
    stack.enter_context(patch(f"{LAUNCH_MODULE}.get_foreground_hwnd", return_value=12345))
    stack.enter_context(patch(f"{LAUNCH_MODULE}.is_maximized", return_value=True))
    stack.enter_context(patch(f"{LAUNCH_MODULE}.maximize"))
    for module in (FIND_MODULE, READ_MODULE, REPLY_MODULE, DRAFT_MODULE):
        stack.enter_context(patch(f"{module}.get_foreground_window_title", return_value="Inbox - Outlook"))

    stack.enter_context(patch(
        f"{LAUNCH_MODULE}.capture_screen",
        return_value=MagicMock(filename="a.png", path="a.png", width=1920, height=1080),
    ))
    for module, name in ((FIND_MODULE, "b"), (READ_MODULE, "d"), (REPLY_MODULE, "c"), (DRAFT_MODULE, "e")):
        stack.enter_context(patch(
            f"{module}.capture_screen",
            return_value=MagicMock(
                filename=f"{name}.png", path=real_capture_image_path(1920, 1080, name=f"{name}.png"),
                width=1920, height=1080,
            ),
        ))

    return pyautogui_mocks


def test_full_chain_reaches_draft_ready():
    signals = {"success": [], "failure": [], "aborted": [], "current_step": []}
    worker = ReplyDraftWorker(AbortController(), bounded_approval_granted=True)
    worker.success.connect(lambda r: signals["success"].append(r))
    worker.failure.connect(lambda *a: signals["failure"].append(a))
    worker.aborted.connect(lambda *a: signals["aborted"].append(a))
    worker.current_step.connect(lambda s: signals["current_step"].append(s))

    mock_provider = MagicMock()
    mock_provider.analyze_screen.side_effect = [
        _search_grounding(),      # Outlook grounding
        _readiness_ready(),       # readiness
        _email_grounding(),       # find target email
        _email_open_verified(),   # verify email opened
        _understanding(), _understanding(),  # email extraction, then holistic assessment (2026-09-04 split)
        _state_check(True),       # prepare_reply_editor: already open (skip click)
        _state_check(True),       # verify_reply_editor: its own separate confirmation call
        _draft_generation(),      # generate draft
        _draft_verification(True),  # verify typed draft
    ]

    titles = itertools.chain(
        ["Search", "Search", "Search"],           # Outlook search click gate (capture, pre-move, pre-click)
        itertools.repeat("Inbox - Outlook"),        # everything after: poll, readiness, email, reply, typing, verify
    )

    with patch(f"{WORKER_MODULE}._provider", return_value=(VisionService(mock_provider, fallback=None), "gemini-3.6-flash")), ExitStack() as stack:
        mocks = _patch_full_chain(stack, titles)
        worker.run()

    assert signals["success"], f"expected success, got failure={signals['failure']} aborted={signals['aborted']}"
    result = signals["success"][0]
    assert result["result"] == "PASS"
    assert result["send_click_count"] == 0
    assert result["typing_result"] == "PASS"
    assert result["semantic_match"] is True
    assert "GENERATING_DRAFT" in signals["current_step"]
    assert "DRAFT_READY" in signals["current_step"]
    assert "SENDING" not in signals["current_step"]
    assert "WAITING_FOR_SEND_APPROVAL" not in signals["current_step"]
    # pre-RND-009D-hardening elapsed tracking carried forward correctly
    assert result["total_elapsed_ms"] is not None
    mocks["reply"].click.assert_not_called()  # reply editor was already open — no Reply click


def test_optional_reply_continues_since_user_started_a_targeted_reply_run():
    """The core fix under test: an informational/status email with no
    explicit request (OPTIONAL_REPLY) must NOT block the flow — the user
    already expressed explicit intent by targeting this sender/subject
    and starting a reply run. Only SHOULD_NOT_REPLY blocks."""
    signals = {"success": [], "failure": [], "current_step": []}
    worker = ReplyDraftWorker(AbortController(), bounded_approval_granted=True)
    worker.success.connect(lambda r: signals["success"].append(r))
    worker.failure.connect(lambda *a: signals["failure"].append(a))
    worker.current_step.connect(lambda s: signals["current_step"].append(s))

    mock_provider = MagicMock()
    mock_provider.analyze_screen.side_effect = [
        _search_grounding(), _readiness_ready(), _email_grounding(), _email_open_verified(),
        _understanding(reply_expectation="OPTIONAL_REPLY"), _understanding(reply_expectation="OPTIONAL_REPLY"),
        _state_check(True), _state_check(True), _draft_generation(), _draft_verification(True),
    ]
    titles = itertools.chain(["Search", "Search", "Search"], itertools.repeat("Inbox - Outlook"))

    with patch(f"{WORKER_MODULE}._provider", return_value=(VisionService(mock_provider, fallback=None), "gemini-3.6-flash")), ExitStack() as stack:
        mocks = _patch_full_chain(stack, titles)
        worker.run()

    assert signals["success"], f"expected success, got failure={signals['failure']}"
    result = signals["success"][0]
    assert result["result"] == "PASS"
    assert result["reply_expectation"] == "OPTIONAL_REPLY"
    assert "DRAFT_READY" in signals["current_step"]
    mocks["reply"].click.assert_not_called()


def test_should_not_reply_stops_before_reply_click():
    """Only SHOULD_NOT_REPLY safely stops the flow — a normal Reply would
    be inappropriate/unsafe here (e.g. a no-reply system notification).
    OPTIONAL_REPLY does NOT stop — see test_full_chain_reaches_draft_ready,
    whose _understanding() default is MUST_REPLY, and the module-level
    coverage this migration added for OPTIONAL_REPLY continuing too."""
    signals = {"success": [], "failure": []}
    worker = ReplyDraftWorker(AbortController(), bounded_approval_granted=True)
    worker.success.connect(lambda r: signals["success"].append(r))
    worker.failure.connect(lambda *a: signals["failure"].append(a))

    mock_provider = MagicMock()
    not_appropriate = _understanding(reply_expectation="SHOULD_NOT_REPLY")
    mock_provider.analyze_screen.side_effect = [
        _search_grounding(), _readiness_ready(), _email_grounding(), _email_open_verified(),
        not_appropriate, not_appropriate,
    ]

    titles = itertools.chain(["Search", "Search", "Search"], itertools.repeat("Inbox - Outlook"))

    with patch(f"{WORKER_MODULE}._provider", return_value=(VisionService(mock_provider, fallback=None), "gemini-3.6-flash")), ExitStack() as stack:
        mocks = _patch_full_chain(stack, titles)
        worker.run()
        mocks["reply"].click.assert_not_called()

    assert not signals["success"]
    assert signals["failure"][0][0] == "REPLY_NOT_APPROPRIATE"
