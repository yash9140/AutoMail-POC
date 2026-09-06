"""Full-pipeline (SendWorker) tests proving the Claude-primary/Gemini-
fallback architecture end to end — not just at the VisionService unit
level (see test_vision_service.py for the exhaustive A-U fallback-
condition matrix in isolation).

These four tests exercise the REAL composition root
(app.vision.service.get_vision_service(), driven by PRIMARY_VISION_
PROVIDER=anthropic / FALLBACK_VISION_PROVIDER=gemini env vars) through
the real SendWorker/Outlook step chain, with only AnthropicProvider.
analyze_screen / GeminiProvider.analyze_screen mocked at the class
level — proving:

  1. A mid-pipeline technical failure on the primary makes the fallback
     provider carry that ONE stage (same screenshot, no recapture), and
     the rest of the pipeline continues normally.
  2. A technical failure at SEND_GROUNDING may fall back BEFORE the
     physical Send click — but the click still happens at most once.
  3. A technical failure at SEND_VERIFICATION (AFTER the physical Send
     click already happened) may fall back for verification only — and
     Send is never clicked a second time, regardless of outcome.
  4. A semantic/safety disagreement (multiple ambiguous candidates) NEVER
     triggers fallback — Gemini is not even constructed enough to be
     asked, because get_vision_service() only builds it as a fallback
     provider (a technical-failure escape hatch), never as a second
     opinion on an already-answered semantic question.

No real Vision/API calls, no pyautogui, no live Outlook — everything
physical is mocked, mirroring test_provider_migration.py's own
_patch_physical_actions() pattern.
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
from app.vision.providers.base import NetworkError, ProviderCallResult  # noqa: E402
from app.vision.providers.gemini_provider import GeminiProvider  # noqa: E402
from app.workers.send_worker import SendWorker  # noqa: E402
from tests._capture_test_utils import real_capture_image_path, to_crop_relative_bbox  # noqa: E402

LAUNCH_MODULE = "app.outlook.launch"
FIND_MODULE = "app.outlook.find_email"
READ_MODULE = "app.outlook.read_email"
REPLY_MODULE = "app.outlook.reply"
DRAFT_MODULE = "app.outlook.draft"
SEND_MODULE = "app.outlook.send"

DRAFT_TEXT = "Hi Yash,\n\nThank you for reaching out.\n\nBest regards,"


def _result(parsed_json: dict, model: str = "claude-sonnet-5") -> ProviderCallResult:
    return ProviderCallResult(
        raw_text="{}", parsed_json=parsed_json, model=model, latency_ms=10.0, input_tokens=5, output_tokens=5,
    )


def _search_grounding():
    return _result({"search_visible": True, "target_visible": True, "target_type": "desktop_app", "visible_label": "Outlook",
                     "bbox": [250.0, 400.0, 350.0, 600.0], "confidence": 0.95, "reason": "ok"})


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


def _email_grounding_ambiguous_multiple_exact_matches():
    """Two DIFFERENT rows both exactly matching target_sender/target_subject
    — MULTIPLE_TARGET_EMAILS_FOUND, and _try_resolve_latest() can't
    disambiguate because neither date_or_order says "today"/"newest"."""
    row = {
        "sender": TARGET_EMAIL_SENDER, "subject": TARGET_EMAIL_SUBJECT, "subject_truncated": False,
        "date_or_order": "Yesterday", "row_bbox": to_crop_relative_bbox([480.0, 300.0, 520.0, 480.0]), "confidence": 0.95,
    }
    row2 = dict(row, row_bbox=to_crop_relative_bbox([540.0, 300.0, 580.0, 480.0]), date_or_order="2 days ago")
    return _result({
        "outlook_visible": True, "message_list_visible": True, "target_visible": True, "candidate_count": 2,
        "candidates": [row, row2], "more_content_below": False, "reason": "ok",
    })


def _email_open_verified():
    return _result({"email_open": True, "subject_detected": TARGET_EMAIL_SUBJECT, "sender_detected": TARGET_EMAIL_SENDER,
                     "subject_match": True, "sender_match": True, "body_visible": True, "confidence": 0.95, "reason": "ok"})


def _understanding():
    return _result({"extracted_visible_content": "greeting", "overlap_text": "", "important_points": [],
                     "requested_actions": [], "names_entities": [], "dates": [], "commitments": [],
                     "more_content_below": False, "end_of_message_visible": True, "no_new_content": False,
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


def _titles():
    return itertools.chain(["Search", "Search", "Search"], itertools.repeat("Inbox - Outlook"))


def _full_chain_side_effects():
    return [
        _search_grounding(), _readiness_ready(), _email_grounding(), _email_open_verified(),
        _understanding(), _understanding(),
        _state_check(True), _state_check(True),
        _draft_generation(), _draft_verification(True),
        _send_composer_localization(), _send_search(), _sent_verification(True),
    ]


def _run_worker(monkeypatch, anthropic_side_effect, gemini_side_effect):
    monkeypatch.setenv("PRIMARY_VISION_PROVIDER", "anthropic")
    monkeypatch.setenv("FALLBACK_VISION_PROVIDER", "gemini")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-anthropic-key")
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-sonnet-5")
    monkeypatch.setenv("GEMINI_API_KEY", "fake-gemini-key")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.6-flash")

    signals = {"success": [], "failure": [], "aborted": []}
    worker = SendWorker(AbortController(), bounded_approval_granted=True, send_approval_granted=True)
    worker.success.connect(lambda r: signals["success"].append(r))
    worker.failure.connect(lambda *a: signals["failure"].append(a))
    worker.aborted.connect(lambda *a: signals["aborted"].append(a))

    with patch.object(AnthropicProvider, "analyze_screen", side_effect=anthropic_side_effect) as mock_anthropic, \
         patch.object(GeminiProvider, "analyze_screen", side_effect=gemini_side_effect) as mock_gemini, \
         ExitStack() as stack:
        mocks = _patch_physical_actions(stack, _titles())
        worker.run()

    return signals, mocks, mock_anthropic, mock_gemini


# --- 1: mid-pipeline technical failure -> fallback carries that ONE
# stage, same screenshot, pipeline continues and completes ---

def test_claude_outlook_search_timeout_gemini_fallback_same_screenshot_pipeline_succeeds(monkeypatch):
    anthropic_effects = list(_full_chain_side_effects())
    # PROVIDER_RETRY_COUNT=1 means 2 total same-provider attempts before
    # the primary is considered exhausted and fallback triggers — both
    # must fail for OUTLOOK_SEARCH specifically.
    anthropic_effects[0] = NetworkError("timed out")
    anthropic_effects.insert(1, NetworkError("timed out again"))
    gemini_effects = [_search_grounding()]  # fallback carries ONLY that one stage

    signals, mocks, mock_anthropic, mock_gemini = _run_worker(monkeypatch, anthropic_effects, gemini_effects)

    assert signals["success"], f"expected success, got failure={signals['failure']}"
    result = signals["success"][0]
    assert result["result"] == "PASS"
    assert result["fallback_uses"] == 1
    assert result["send_click_count"] == 1
    assert mock_gemini.call_count == 1  # fallback used exactly once, for exactly the failed stage
    # The Outlook-result ACTIVATION (Enter key, 2026-09-06 keyboard-
    # activation fix, not a click) still only happens once — fallback
    # never doubles a physical action.
    assert mocks["launch"].click.call_count == 0
    assert mocks["launch"].press.call_count == 2  # Windows key + Enter


# --- 2: SEND_GROUNDING technical failure -> fallback BEFORE the Send
# click; the click itself still happens at most once ---

def test_send_grounding_technical_failure_falls_back_before_click_one_click_max(monkeypatch):
    anthropic_effects = list(_full_chain_side_effects())
    # SEND_COMPOSER_LOCALIZATION (index 10) succeeds normally on Claude;
    # SEND_GROUNDING (index 11, 2026-09-06 two-stage architecture) fails
    # BOTH same-provider attempts before fallback is considered.
    anthropic_effects[11] = NetworkError("timed out")
    anthropic_effects.insert(12, NetworkError("timed out again"))
    gemini_effects = [_send_search()]  # fallback grounds Send instead

    signals, mocks, mock_anthropic, mock_gemini = _run_worker(monkeypatch, anthropic_effects, gemini_effects)

    assert signals["success"], f"expected success, got failure={signals['failure']}"
    result = signals["success"][0]
    assert result["result"] == "PASS"
    assert result["send_click_count"] == 1
    assert mocks["send"].click.call_count == 1  # exactly one physical Send click, never zero, never two
    assert mock_gemini.call_count == 1


# --- 3: SEND_VERIFICATION technical failure (AFTER the physical Send
# click already happened) -> fallback may verify, Send is NEVER
# clicked again regardless of what verification finds ---

def test_send_verification_technical_failure_after_click_falls_back_never_resends(monkeypatch):
    anthropic_effects = list(_full_chain_side_effects())
    # SEND_VERIFICATION (index 12, the LAST call — Send has already been
    # physically clicked by this point) fails BOTH same-provider attempts
    # before fallback is considered.
    anthropic_effects[12] = NetworkError("timed out")
    anthropic_effects.insert(13, NetworkError("timed out again"))
    gemini_effects = [_sent_verification(True)]  # fallback confirms sent

    signals, mocks, mock_anthropic, mock_gemini = _run_worker(monkeypatch, anthropic_effects, gemini_effects)

    assert signals["success"], f"expected success, got failure={signals['failure']}"
    result = signals["success"][0]
    assert result["result"] == "PASS"
    assert result["send_click_count"] == 1  # Send physically clicked exactly once, ever
    assert mocks["send"].click.call_count == 1
    assert mock_gemini.call_count == 1


# --- 4: semantic ambiguity (multiple exact-match candidates) NEVER
# triggers fallback — Gemini's analyze_screen is never even called ---

def test_multiple_ambiguous_candidates_never_triggers_fallback_safe_stop(monkeypatch):
    anthropic_effects = [
        _search_grounding(), _readiness_ready(), _email_grounding_ambiguous_multiple_exact_matches(),
    ]
    gemini_effects: list = []  # Gemini must never be asked to "pick one" — schema-valid ambiguity safe-stops

    signals, mocks, mock_anthropic, mock_gemini = _run_worker(monkeypatch, anthropic_effects, gemini_effects)

    assert not signals["success"]
    failure_reason, _message = signals["failure"][0]
    assert failure_reason == LaunchFailureReason.MULTIPLE_TARGET_EMAILS_FOUND
    mock_gemini.assert_not_called()
    mocks["find"].click.assert_not_called()
    mocks["send"].click.assert_not_called()
