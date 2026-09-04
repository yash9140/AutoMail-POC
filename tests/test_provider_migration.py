"""Provider migration tests — Claude/Anthropic is the SOLE runtime
Vision/LLM provider (app/config/settings.py::get_provider()). No
Gemini, no OpenAI, no fallback between providers. See:

- app/config/settings.py — get_provider() constructs AnthropicProvider only
- app/fallback/recovery.py — call_with_provider_retry() stage-tagged retry
- app/vision/providers/anthropic_provider.py — the live provider (unchanged
  by this migration; already existed, just now actually wired in)
- app/vision/providers/gemini_provider.py, openai_provider.py — retained
  for historical/R&D purposes only, never imported by the live runtime

test_settings.py covers item A (AI_PROVIDER == "anthropic") and part of
item I (get_provider() ignores GEMINI_*/OPENAI_* env vars). This file
covers B-L.
"""

import itertools
import sys
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.fallback.recovery import call_with_provider_retry  # noqa: E402
from app.outlook.find_email import TARGET_EMAIL_SENDER, TARGET_EMAIL_SUBJECT  # noqa: E402
from app.playbook.failure_reasons import LaunchFailureReason  # noqa: E402
from app.safety.abort_controller import AbortController  # noqa: E402
from app.vision.providers.anthropic_provider import AnthropicProvider  # noqa: E402
from app.vision.providers.base import NetworkError, ProviderCallResult  # noqa: E402
from app.vision.providers.gemini_provider import GeminiProvider  # noqa: E402
from app.vision.providers.openai_provider import OpenAIProvider  # noqa: E402
from app.vision.service import VisionService  # noqa: E402
from app.workers.send_worker import SendWorker  # noqa: E402

LAUNCH_MODULE = "app.outlook.launch"
FIND_MODULE = "app.outlook.find_email"
READ_MODULE = "app.outlook.read_email"
REPLY_MODULE = "app.outlook.reply"
DRAFT_MODULE = "app.outlook.draft"
SEND_MODULE = "app.outlook.send"
WORKER_MODULE = "app.workers.send_worker"

DRAFT_TEXT = "Hi Yash,\n\nThank you for reaching out.\n\nBest regards,"


def _result(parsed_json: dict, model: str = "claude-sonnet-4-20250514") -> ProviderCallResult:
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


def _patch_physical_actions(stack: ExitStack, titles) -> dict:
    """Mocks every physical action (pyautogui, foreground, capture) across
    all five outlook step modules — but NOT the provider. Mirrors
    test_send_worker.py's _patch_full_chain, minus the _provider() mock,
    so the REAL app.config.settings.get_provider() -> AnthropicProvider
    path actually runs (with AnthropicProvider.analyze_screen itself
    patched at the class level by each test — see _patch_anthropic_only)."""
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


def _titles():
    return itertools.chain(["Search", "Search", "Search"], itertools.repeat("Inbox - Outlook"))


def _full_chain_side_effects():
    return [
        _search_grounding(), _readiness_ready(), _email_grounding(), _email_open_verified(),
        _understanding(), _understanding(),  # extraction call, then holistic-assessment call (2026-09-04 split)
        _state_check(True),          # prepare_reply_editor: already open (skip click)
        _state_check(True),          # verify_reply_editor
        _draft_generation(),
        _draft_verification(True),
        _send_search(),
        _sent_verification(True),
    ]


# --- B/C/D/E: the REAL get_provider() path runs; only AnthropicProvider.analyze_screen
# is mocked (never GeminiProvider/OpenAIProvider) — end to end through every stage ---

def test_BCDE_full_send_worker_flow_uses_only_anthropic_every_stage(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "anthropic")  # this suite asserts Claude-only behavior regardless of the real .env
    signals = {"success": [], "failure": [], "current_step": []}
    worker = SendWorker(AbortController(), bounded_approval_granted=True, send_approval_granted=True)
    worker.success.connect(lambda r: signals["success"].append(r))
    worker.failure.connect(lambda *a: signals["failure"].append(a))
    worker.current_step.connect(lambda s: signals["current_step"].append(s))

    with patch.object(AnthropicProvider, "analyze_screen", side_effect=_full_chain_side_effects()) as mock_analyze, \
         patch.object(GeminiProvider, "__init__", side_effect=AssertionError("Gemini must never be constructed")) as mock_gemini_init, \
         patch.object(OpenAIProvider, "__init__", side_effect=AssertionError("OpenAI must never be constructed")) as mock_openai_init, \
         ExitStack() as stack:
        _patch_physical_actions(stack, _titles())
        worker.run()

    assert signals["success"], f"expected success, got failure={signals['failure']}"
    result = signals["success"][0]
    assert result["result"] == "PASS"
    assert result["send_click_count"] == 1
    # D: every one of the 11 stages went through Anthropic — 12 calls total
    # (Outlook search, readiness, email search, email-open verify, email
    # extraction + holistic assessment [2026-09-04 split], reply-editor-
    # already-open check, reply-editor verify, draft generation [E],
    # draft verification, Send grounding, sent verify).
    assert mock_analyze.call_count == 12
    mock_gemini_init.assert_not_called()   # B
    mock_openai_init.assert_not_called()   # C


# --- F: transient Claude error retries Claude only; H: no physical repeats ---

def test_F_H_transient_claude_error_retries_claude_only_no_physical_repeat(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "anthropic")  # this suite asserts Claude-only behavior regardless of the real .env
    side_effects = list(_full_chain_side_effects())
    # First call (Outlook search grounding) fails once transiently, then succeeds.
    side_effects[0] = NetworkError("[Errno 10054] connection reset")
    side_effects.insert(1, _search_grounding())

    signals = {"success": [], "failure": []}
    worker = SendWorker(AbortController(), bounded_approval_granted=True, send_approval_granted=True)
    worker.success.connect(lambda r: signals["success"].append(r))
    worker.failure.connect(lambda *a: signals["failure"].append(a))

    with patch.object(AnthropicProvider, "analyze_screen", side_effect=side_effects), \
         patch.object(GeminiProvider, "__init__", side_effect=AssertionError("must not construct Gemini")), \
         patch.object(OpenAIProvider, "__init__", side_effect=AssertionError("must not construct OpenAI")), \
         ExitStack() as stack:
        mocks = _patch_physical_actions(stack, _titles())
        worker.run()

    assert signals["success"], f"expected success, got failure={signals['failure']}"
    result = signals["success"][0]
    assert result["provider_retries"] == 1
    # The retry repeated ONLY the Claude request — the Outlook-result
    # click still happened exactly once, never twice.
    assert mocks["launch"].click.call_count == 1


# --- G: final Claude failure -> TECHNICAL_PROVIDER_ERROR ---

def test_G_persistent_claude_failure_yields_technical_provider_error(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "anthropic")  # this suite asserts Claude-only behavior regardless of the real .env
    signals = {"success": [], "failure": []}
    worker = SendWorker(AbortController(), bounded_approval_granted=True, send_approval_granted=True)
    worker.success.connect(lambda r: signals["success"].append(r))
    worker.failure.connect(lambda *a: signals["failure"].append(a))

    with patch.object(
        AnthropicProvider, "analyze_screen",
        side_effect=[NetworkError("down"), NetworkError("still down")],  # exhausts PROVIDER_RETRY_COUNT=1
    ), ExitStack() as stack:
        mocks = _patch_physical_actions(stack, _titles())
        worker.run()

    assert not signals["success"]
    failure_reason, _message = signals["failure"][0]
    assert failure_reason == LaunchFailureReason.TECHNICAL_PROVIDER_ERROR
    mocks["launch"].click.assert_not_called()  # zero physical action on total provider failure


# --- I: no CROSS-provider fallback, structurally/behaviorally ---
# (redefined 2026-09-04: Gemini is now a legitimate, explicit runtime
# choice via AI_PROVIDER=gemini — not a fallback — so get_provider()
# legitimately mentions GeminiProvider now. What must still never
# happen is resolving one provider by depending on, or falling back to,
# a DIFFERENT provider's env vars/construction.)

def test_I_gemini_mode_never_needs_anthropic_or_openai_env_vars(monkeypatch, tmp_path):
    """Resolving AI_PROVIDER=gemini must succeed using ONLY Gemini's own
    env vars — proves the gemini branch never depends on (or silently
    falls back to) Anthropic or OpenAI."""
    from app.config import settings

    empty_env = tmp_path / ".env"
    empty_env.write_text("", encoding="utf-8")
    monkeypatch.setattr(settings, "PROJECT_ROOT", tmp_path)
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-fake-key")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.6-flash")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)

    provider, model = settings.get_provider()
    assert model == "gemini-3.6-flash"
    assert provider.provider_name == "gemini"


def test_I_anthropic_mode_never_needs_gemini_or_openai_env_vars(monkeypatch, tmp_path):
    """Mirror of the above: resolving AI_PROVIDER=anthropic must succeed
    using ONLY Anthropic's own env vars."""
    from app.config import settings

    empty_env = tmp_path / ".env"
    empty_env.write_text("", encoding="utf-8")
    monkeypatch.setattr(settings, "PROJECT_ROOT", tmp_path)
    monkeypatch.setenv("AI_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-sonnet-5")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)

    provider, model = settings.get_provider()
    assert model == "claude-sonnet-5"
    assert provider.provider_name == "anthropic"


def test_I_openai_never_constructed_by_get_provider():
    """OpenAI has no legitimate branch at all — this is the one case a
    plain structural check is still the right tool."""
    import inspect

    from app.config import settings

    source = inspect.getsource(settings.get_provider)
    assert "OpenAIProvider" not in source
    assert 'os.environ.get("OPENAI' not in source


# --- J: existing Pydantic structured-response validation still works ---

def test_J_claude_shaped_response_validates_against_existing_schema():
    from app.vision.models import EmailSearchResponse

    structured = EmailSearchResponse.model_validate(_email_grounding().parsed_json)
    assert structured.target_visible is True
    assert structured.candidates[0].sender == TARGET_EMAIL_SENDER


# --- K: malformed Claude JSON/structured output fails safely ---

def test_K_malformed_claude_json_fails_safely_not_silently_repaired():
    """AnthropicProvider.analyze_screen() itself already sets parsed_json
    to None (never repairs/guesses) when the raw text isn't valid JSON —
    see app/vision/providers/anthropic_provider.py. This proves the
    downstream schema-validation call site treats that as a clean,
    typed technical failure, never a silent pass."""
    from app.outlook.find_email import FindOpenEmailSteps

    steps = FindOpenEmailSteps(AbortController(), VisionService(MagicMock(), fallback=None), "claude-sonnet-4-20250514")
    steps.result.ready_for_interaction = True
    malformed = ProviderCallResult(
        raw_text="not valid json at all", parsed_json=None, model="claude-sonnet-4-20250514",
        latency_ms=5.0, input_tokens=5, output_tokens=5,
    )
    steps.vision.primary.analyze_screen.return_value = malformed
    with patch(f"{FIND_MODULE}.get_foreground_window_title", return_value="Inbox - Outlook"), \
         patch(f"{FIND_MODULE}.capture_screen", return_value=MagicMock(filename="x.png", path="x.png", width=1920, height=1080)):
        assert steps.find_target_email() is False
    assert steps.result.result == "ERROR"
    assert steps.result.failure_reason == LaunchFailureReason.TECHNICAL_PROVIDER_ERROR
    assert "not schema-valid" in steps.result.notes


# --- L: SEND_CLICK_MAX remains 1 ---

def test_L_send_click_max_remains_one():
    from app.config.settings import SEND_CLICK_MAX

    assert SEND_CLICK_MAX == 1


# --- Structural: recovery.py's stage-tagged retry never touches physical actions ---

def test_call_with_provider_retry_never_imports_pyautogui():
    import inspect

    from app.fallback import recovery

    source = inspect.getsource(recovery)
    assert "pyautogui" not in source
