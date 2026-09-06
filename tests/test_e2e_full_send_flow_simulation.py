"""Part 11 — fully mocked SendWorker end-to-end simulation (2026-09-03).

Two scenarios:

1. The full happy path: Windows Search -> Outlook result grounding ->
   Outlook launch/readiness -> target email search -> target email open
   verification -> email understanding -> Reply grounding -> editor
   verification -> draft generation -> typing -> draft verification ->
   Send grounding -> one Send click -> sent verification -> COMPLETED.
   Asserts the exact current_step sequence, Claude-only provider usage
   (no Gemini/OpenAI construction), no action repeated by a provider
   retry, exactly one Send click, final state COMPLETED, and zero
   aborts.

2. One failure-path simulation: a long email that never confirms its
   true end (CONTENT_NOT_FULLY_READ) — asserts the run stops there with
   zero Reply click, zero typing, and zero Send click.

No real mouse/keyboard/network/provider call happens anywhere in this
file, and no live Outlook run is performed.
"""

import itertools
import sys
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.outlook.find_email import TARGET_EMAIL_SENDER, TARGET_EMAIL_SUBJECT  # noqa: E402
from app.playbook.failure_reasons import LaunchFailureReason  # noqa: E402
from app.safety.abort_controller import AbortController  # noqa: E402
from app.vision.providers.anthropic_provider import AnthropicProvider  # noqa: E402
from app.vision.providers.base import ProviderCallResult  # noqa: E402
from app.vision.providers.gemini_provider import GeminiProvider  # noqa: E402
from app.vision.providers.openai_provider import OpenAIProvider  # noqa: E402
from app.workers.send_worker import SendWorker  # noqa: E402
from tests._capture_test_utils import real_capture_image_path, to_crop_relative_bbox  # noqa: E402

LAUNCH_MODULE = "app.outlook.launch"
FIND_MODULE = "app.outlook.find_email"
READ_MODULE = "app.outlook.read_email"
REPLY_MODULE = "app.outlook.reply"
DRAFT_MODULE = "app.outlook.draft"
SEND_MODULE = "app.outlook.send"

DRAFT_TEXT = "Hi Yash,\n\nThank you for reaching out.\n\nBest regards,"

EXPECTED_HAPPY_PATH_STEP_SEQUENCE = [
    "OUTLOOK_READY", "FINDING_EMAIL", "EMAIL_OPENED",
    "READING_EMAIL",
    "FINDING_REPLY", "REPLY_EDITOR_OPEN",
    "GENERATING_DRAFT", "TYPING_DRAFT", "VERIFYING_DRAFT", "DRAFT_READY",
    "WAITING_FOR_SEND_APPROVAL", "SENDING", "VERIFYING_SEND", "COMPLETED",
]


def _result(parsed_json: dict, model: str = "claude-sonnet-4-6") -> ProviderCallResult:
    return ProviderCallResult(
        raw_text="{}", parsed_json=parsed_json, model=model, latency_ms=10.0, input_tokens=5, output_tokens=5,
    )


def _search_grounding():
    return _result({"search_visible": True, "target_visible": True, "target_type": "desktop_app",
                     "visible_label": "Outlook", "bbox": [250.0, 400.0, 350.0, 600.0],
                     "confidence": 0.95, "reason": "ok"})


def _readiness_ready():
    return _result({"application": "Outlook", "outlook_visible": True, "splash_screen_visible": False,
                     "ready_for_interaction": True, "detected_state": "inbox", "confidence": 0.95, "reason": "ok"})


def _email_grounding():
    return _result({
        "outlook_visible": True, "message_list_visible": True, "target_visible": True, "candidate_count": 1,
        "candidates": [{
            "sender": TARGET_EMAIL_SENDER, "subject": TARGET_EMAIL_SUBJECT, "subject_truncated": False,
            "date_or_order": "Today", "row_bbox": to_crop_relative_bbox([480.0, 300.0, 520.0, 480.0]), "confidence": 0.95,
        }],
        "more_content_below": False, "reason": "ok",
    })


def _email_open_verified():
    return _result({"email_open": True, "subject_detected": TARGET_EMAIL_SUBJECT, "sender_detected": TARGET_EMAIL_SENDER,
                     "subject_match": True, "sender_match": True, "body_visible": True, "confidence": 0.95, "reason": "ok"})


def _understanding(more_content_below=False, end_of_message_visible=True, no_new_content=False, content="greeting"):
    return _result({"extracted_visible_content": content, "overlap_text": "", "important_points": [],
                     "requested_actions": [], "names_entities": [], "dates": [], "commitments": [],
                     "more_content_below": more_content_below, "end_of_message_visible": end_of_message_visible,
                     "no_new_content": no_new_content,
                     "reply_expectation": "MUST_REPLY", "requires_user_decision": False, "sender_intent": "check-in",
                     "requested_action_summary": "", "confidence": 0.9, "reason": "ok"})


def _state_check(verified):
    return _result({"verified": verified, "detected_state": "editor state", "confidence": 0.9,
                     "visual_evidence": "ok", "reason": "ok"})


def _draft_generation():
    return _result({"draft_reply": DRAFT_TEXT, "reasoning_summary": "r", "confidence": 0.95})


def _draft_verification(semantic_match=True):
    return _result({"reply_editor_open": True, "semantic_match": semantic_match, "detected_draft": DRAFT_TEXT,
                     "confidence": 0.95, "reason": "ok"})


def _send_composer_localization():
    return _result({"composer_visible": True, "action_bar_visible": True,
                     "action_bar_bbox": [900.0, 350.0, 980.0, 550.0], "composer_bbox": None,
                     "confidence": 0.95, "reason": "ok"})


def _send_search():
    return _result({"outlook_visible": True, "send_visible": True, "control_identity": "Send",
                     "control_type": "button", "bbox": [619.0, 385.0, 837.0, 616.0], "confidence": 0.95, "reason": "ok"})


def _sent_verification(verified=True):
    return _result({"verified": verified, "detected_state": "inbox, no compose box", "confidence": 0.95,
                     "visual_evidence": "no compose box visible", "reason": "ok"})


def _titles():
    return itertools.chain(["Search", "Search", "Search"], itertools.repeat("Inbox - Outlook"))


def _patch_physical_actions(stack: ExitStack, titles) -> dict:
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
            return_value=MagicMock(
                filename=f"{name}.png", path=real_capture_image_path(1920, 1080, name=f"{name}.png"),
                width=1920, height=1080,
            ),
        ))

    return pyautogui_mocks


def _happy_path_side_effects():
    return [
        _search_grounding(), _readiness_ready(), _email_grounding(), _email_open_verified(),
        _understanding(), _understanding(),  # extraction call, then holistic-assessment call (2026-09-04 split)
        _state_check(True),          # prepare_reply_editor: already open (skip click)
        _state_check(True),          # verify_reply_editor
        _draft_generation(),
        _draft_verification(True),
        _send_composer_localization(),
        _send_search(),
        _sent_verification(True),
    ]


def test_full_happy_path_e2e_simulation_reaches_completed(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "anthropic")  # this E2E asserts Claude-only behavior regardless of the real .env
    # 2026-09-06: isolate from the real .env, which now sets these two
    # — this test asserts single-provider (AI_PROVIDER-only) behavior.
    monkeypatch.setenv("PRIMARY_VISION_PROVIDER", "")
    monkeypatch.setenv("FALLBACK_VISION_PROVIDER", "")
    signals = {"success": [], "failure": [], "aborted": [], "current_step": []}
    worker = SendWorker(AbortController(), bounded_approval_granted=True, send_approval_granted=True)
    worker.success.connect(lambda r: signals["success"].append(r))
    worker.failure.connect(lambda *a: signals["failure"].append(a))
    worker.aborted.connect(lambda *a: signals["aborted"].append(a))
    worker.current_step.connect(lambda s: signals["current_step"].append(s))

    with patch.object(AnthropicProvider, "analyze_screen", side_effect=_happy_path_side_effects()) as mock_analyze, \
         patch.object(GeminiProvider, "__init__", side_effect=AssertionError("Gemini must never be constructed")), \
         patch.object(OpenAIProvider, "__init__", side_effect=AssertionError("OpenAI must never be constructed")), \
         ExitStack() as stack:
        mocks = _patch_physical_actions(stack, _titles())
        worker.run()

    assert signals["success"], f"expected success, got failure={signals['failure']} aborted={signals['aborted']}"
    assert not signals["aborted"]  # zero aborts

    result = signals["success"][0]
    assert result["result"] == "PASS"
    assert result["send_click_count"] == 1  # Send clicked exactly once
    assert result["provider_retries"] == 0  # no retry needed -> no repeated action

    # Exact expected stage/state sequence — not just "it succeeded".
    assert signals["current_step"] == EXPECTED_HAPPY_PATH_STEP_SEQUENCE
    assert signals["current_step"][-1] == "COMPLETED"

    # Every Vision call went through Claude — 12 stages, 13 calls (email
    # understanding is split into an extraction + a holistic-assessment
    # call as of the 2026-09-04 latency fix — see app/outlook/read_email.py;
    # Send grounding is split into SEND_COMPOSER_LOCALIZATION + SEND_
    # GROUNDING as of the 2026-09-06 two-stage Send-grounding architecture
    # — see app/outlook/send.py).
    assert mock_analyze.call_count == 13

    # Physical action counts: Outlook-result ACTIVATION is a keyboard
    # Enter press (2026-09-06 keyboard-activation fix), not a click; one
    # email-row click, one Send click; no reply-control click (editor
    # was already open in this fixture).
    assert mocks["launch"].click.call_count == 0
    assert mocks["find"].click.call_count == 1
    assert mocks["send"].click.call_count == 1


def test_failure_path_e2e_simulation_long_email_incomplete_stops_before_reply(monkeypatch):
    """A long email whose true end is never confirmed within the bounded
    scroll-attempt budget must stop with CONTENT_NOT_FULLY_READ — never
    proceeding to Reply, typing, or Send."""
    from app.config.settings import MAX_EMAIL_BODY_SCROLL_ATTEMPTS

    monkeypatch.setenv("AI_PROVIDER", "anthropic")  # this E2E asserts Claude-only behavior regardless of the real .env
    # 2026-09-06: isolate from the real .env, which now sets these two
    # — this test asserts single-provider (AI_PROVIDER-only) behavior.
    monkeypatch.setenv("PRIMARY_VISION_PROVIDER", "")
    monkeypatch.setenv("FALLBACK_VISION_PROVIDER", "")
    signals = {"success": [], "failure": [], "current_step": []}
    worker = SendWorker(AbortController(), bounded_approval_granted=True, send_approval_granted=True)
    worker.success.connect(lambda r: signals["success"].append(r))
    worker.failure.connect(lambda *a: signals["failure"].append(a))
    worker.current_step.connect(lambda s: signals["current_step"].append(s))

    # Every section report claims more content remains AND never confirms
    # the true end — exhausts the bounded scroll budget without ever
    # setting content_complete=True.
    incomplete_sections = [
        _understanding(more_content_below=True, end_of_message_visible=False, content=f"section {i} text")
        for i in range(1, MAX_EMAIL_BODY_SCROLL_ATTEMPTS + 1)
    ]
    side_effects = [_search_grounding(), _readiness_ready(), _email_grounding(), _email_open_verified()] + incomplete_sections

    with patch.object(AnthropicProvider, "analyze_screen", side_effect=side_effects), \
         patch("app.automation.scrolling.pyautogui") as mock_scroll_pyautogui, \
         ExitStack() as stack:
        mock_scroll_pyautogui.FAILSAFE = True
        mocks = _patch_physical_actions(stack, _titles())
        worker.run()

    assert not signals["success"]
    assert signals["failure"][0][0] == LaunchFailureReason.CONTENT_NOT_FULLY_READ

    # Never reached Reply, typing, or Send.
    assert "FINDING_REPLY" not in signals["current_step"]
    assert "GENERATING_DRAFT" not in signals["current_step"]
    assert "SENDING" not in signals["current_step"]
    mocks["reply"].click.assert_not_called()
    mocks["draft"].write.assert_not_called()
    mocks["send"].click.assert_not_called()
