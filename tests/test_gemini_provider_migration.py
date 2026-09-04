"""Gemini demo-restoration tests (2026-09-04).

AI_PROVIDER=gemini must be a fully first-class runtime choice: the
whole find/read/reply/draft/send pipeline routes through GeminiProvider
only, with zero Anthropic/OpenAI construction or calls, no fallback,
and the restored Gemini-specific OUTLOOK_SEARCH point-based grounding
path (see app/outlook/launch.py::_ground_search_result_gemini). All
other stages (readiness, email search, understanding, reply, draft,
send) are provider-independent — same prompt/schema regardless of
which provider is configured — so their fixtures are identical in
shape to the Claude-mode tests, just served through GeminiProvider
here.

Mirrors tests/test_provider_migration.py's structure and
tests/test_e2e_full_send_flow_simulation.py's E2E pattern, for the
Gemini side of the same provider-selection architecture.
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
from app.vision.providers.openai_provider import OpenAIProvider  # noqa: E402
from app.workers.send_worker import SendWorker  # noqa: E402

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


def _result(parsed_json: dict, model: str = "gemini-3.6-flash") -> ProviderCallResult:
    return ProviderCallResult(
        raw_text="{}", parsed_json=parsed_json, model=model, latency_ms=10.0, input_tokens=5, output_tokens=5,
    )


def _search_grounding():
    # Gemini's OWN restored contract — a loose point, not a bbox (see
    # rnd/prompts/windows_search_grounding_v1.txt / WindowsSearchGroundingResponse).
    return _result({"search_visible": True, "outlook_result_visible": True, "result_label": "Outlook",
                     "x": 500.0, "y": 300.0, "confidence": 0.95, "reason": "ok"})


def _readiness_ready():
    return _result({"application": "Outlook", "outlook_visible": True, "splash_screen_visible": False,
                     "ready_for_interaction": True, "detected_state": "inbox", "confidence": 0.95, "reason": "ok"})


def _email_grounding():
    return _result({
        "outlook_visible": True, "message_list_visible": True, "target_visible": True, "candidate_count": 1,
        "candidates": [{
            "sender": TARGET_EMAIL_SENDER, "subject": TARGET_EMAIL_SUBJECT, "subject_truncated": False,
            "date_or_order": "Today", "row_bbox": [480.0, 300.0, 520.0, 900.0], "confidence": 0.95,
        }],
        "more_content_below": False, "reason": "ok",
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


def _send_search():
    return _result({"outlook_visible": True, "send_visible": True, "control_identity": "Send",
                     "control_type": "button", "bbox": [400.0, 800.0, 440.0, 900.0], "confidence": 0.95, "reason": "ok"})


def _sent_verification(verified=True):
    return _result({"verified": verified, "detected_state": "inbox, no compose box", "confidence": 0.95,
                     "visual_evidence": "no compose box visible", "reason": "ok"})


def _full_chain_side_effects():
    return [
        _search_grounding(), _readiness_ready(), _email_grounding(), _email_open_verified(),
        _understanding(), _understanding(),  # extraction call, then holistic-assessment call (2026-09-04 split)
        _state_check(True), _state_check(True),
        _draft_generation(), _draft_verification(True),
        _send_search(), _sent_verification(True),
    ]


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


# --- A: AI_PROVIDER=gemini -> GeminiProvider returned (also covered in
# tests/test_settings.py::test_get_provider_constructs_gemini_provider) ---

def test_A_ai_provider_gemini_returns_gemini_provider(monkeypatch, tmp_path):
    from app.config import settings

    empty_env = tmp_path / ".env"
    empty_env.write_text("", encoding="utf-8")
    monkeypatch.setattr(settings, "PROJECT_ROOT", tmp_path)
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.6-flash")
    provider, model = settings.get_provider()
    assert isinstance(provider, GeminiProvider)
    assert provider.provider_name == "gemini"
    assert model == "gemini-3.6-flash"


# --- B/C/D/E/H/I: full mocked runtime, Gemini only, zero Anthropic/OpenAI,
# no fallback, generic safety unchanged, SEND_CLICK_MAX==1 ---

def test_BCDEHI_full_send_worker_flow_uses_only_gemini_every_stage(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "gemini")  # this suite asserts Gemini-only behavior regardless of the real .env
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.6-flash")

    from app.config.settings import SEND_CLICK_MAX
    assert SEND_CLICK_MAX == 1  # I

    signals = {"success": [], "failure": [], "aborted": [], "current_step": []}
    worker = SendWorker(AbortController(), bounded_approval_granted=True, send_approval_granted=True)
    worker.success.connect(lambda r: signals["success"].append(r))
    worker.failure.connect(lambda *a: signals["failure"].append(a))
    worker.aborted.connect(lambda *a: signals["aborted"].append(a))
    worker.current_step.connect(lambda s: signals["current_step"].append(s))

    with patch.object(GeminiProvider, "analyze_screen", side_effect=_full_chain_side_effects()) as mock_analyze, \
         patch.object(AnthropicProvider, "__init__", side_effect=AssertionError("Anthropic must never be constructed")) as mock_anthropic_init, \
         patch.object(OpenAIProvider, "__init__", side_effect=AssertionError("OpenAI must never be constructed")) as mock_openai_init, \
         ExitStack() as stack:
        mocks = _patch_physical_actions(stack, _titles())
        worker.run()

    assert signals["success"], f"expected success, got failure={signals['failure']} aborted={signals['aborted']}"
    assert not signals["aborted"]  # H: zero unexpected aborts

    result = signals["success"][0]
    assert result["result"] == "PASS"
    assert result["send_click_count"] == 1  # I: one Send click max
    assert result["provider_retries"] == 0

    # H: correct state sequence unchanged from the Claude-mode E2E.
    assert signals["current_step"] == EXPECTED_HAPPY_PATH_STEP_SEQUENCE
    assert signals["current_step"][-1] == "COMPLETED"

    # B/D: every one of the 11 stages went through Gemini only — 12 calls
    # total (email understanding split into extraction + holistic
    # assessment, 2026-09-04 latency fix).
    assert mock_analyze.call_count == 12
    mock_anthropic_init.assert_not_called()  # C
    mock_openai_init.assert_not_called()     # D
    # E: no fallback — Gemini succeeded on every call, so retries stayed 0
    # and no other provider was ever touched (asserted above).

    assert mocks["launch"].click.call_count == 1
    assert mocks["find"].click.call_count == 1
    assert mocks["send"].click.call_count == 1


def test_E_transient_gemini_error_retries_gemini_only_no_fallback_no_physical_repeat(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.6-flash")

    side_effects = list(_full_chain_side_effects())
    side_effects[0] = NetworkError("[Errno 10054] connection reset")
    side_effects.insert(1, _search_grounding())

    signals = {"success": [], "failure": []}
    worker = SendWorker(AbortController(), bounded_approval_granted=True, send_approval_granted=True)
    worker.success.connect(lambda r: signals["success"].append(r))
    worker.failure.connect(lambda *a: signals["failure"].append(a))

    with patch.object(GeminiProvider, "analyze_screen", side_effect=side_effects), \
         patch.object(AnthropicProvider, "__init__", side_effect=AssertionError("must not construct Anthropic")), \
         patch.object(OpenAIProvider, "__init__", side_effect=AssertionError("must not construct OpenAI")), \
         ExitStack() as stack:
        mocks = _patch_physical_actions(stack, _titles())
        worker.run()

    assert signals["success"], f"expected success, got failure={signals['failure']}"
    result = signals["success"][0]
    assert result["provider_retries"] == 1  # retried Gemini only — no fallback to another provider
    assert mocks["launch"].click.call_count == 1  # never repeated the physical click


# --- F/G: Gemini OUTLOOK_SEARCH uses the reconstructed point-based path;
# Claude-specific refine logic is never invoked ---

def test_FG_gemini_outlook_search_uses_point_based_path_not_claude_refine():
    from app.outlook.launch import OutlookLaunchSteps
    from app.safety.abort_controller import AbortController as AC

    steps = OutlookLaunchSteps(AC(), MagicMock(provider_name="gemini"), "gemini-3.6-flash")
    steps.result.screen_width, steps.result.screen_height = 1920, 1080
    capture = MagicMock(filename="s.png", path="s.png", width=1920, height=1080)
    steps.provider.analyze_screen.return_value = _search_grounding()

    with patch(f"{LAUNCH_MODULE}.get_environment_info",
               return_value={"pyautogui_width": 1920, "pyautogui_height": 1080, "dimensions_match": True}):
        assert steps.ground_search_result(capture) is True

    # F: the point-based Gemini contract was used — self.result.grounding
    # (the ORIGINAL RND009BResult field) is populated with an x/y point.
    assert steps.result.grounding is not None
    assert steps.result.grounding.x == 500.0
    assert steps.result.grounding.y == 300.0
    assert steps.result.converted_x == round(500.0 / 1000 * 1920)
    assert steps.result.converted_y == round(300.0 / 1000 * 1080)

    # G: the Claude-only bbox/refine fields were never touched.
    assert steps.result.search_grounding is None
    assert steps.result.search_grounding_refine is None
    assert steps.result.refine_pass_used is False
    # Only ONE Vision call total — the Claude path's bounded second-pass
    # refine call was never reachable/invoked in Gemini mode.
    assert steps.provider.analyze_screen.call_count == 1
