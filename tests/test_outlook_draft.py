"""app/outlook/draft.py unit tests (final POC — moved from RND-009D's
app/playbook/reply_draft_steps.py). Covers draft generation, the
segmented-typing safety method, and draft verification. All external
calls are mocked.
"""

import itertools
import sys
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.settings import MAX_DRAFT_VERIFICATION_ATTEMPTS, TYPE_INTERVAL_SECONDS  # noqa: E402
from app.outlook.draft import REPLY_GENERATION_PROMPT_PATH, ReplyDraftSteps, _validate_draft_quality  # noqa: E402
from app.outlook.find_email import TARGET_EMAIL_SENDER, TARGET_EMAIL_SUBJECT  # noqa: E402
from app.playbook.failure_reasons import LaunchFailureReason  # noqa: E402
from app.safety.abort_controller import AbortController  # noqa: E402
from app.vision.providers.base import NetworkError  # noqa: E402

MODULE = "app.outlook.draft"
LAUNCH_MODULE = "app.outlook.launch"
FIND_MODULE = "app.outlook.find_email"


def _steps() -> ReplyDraftSteps:
    return ReplyDraftSteps(AbortController(), MagicMock(), "gemini-3.6-flash")


def _capture(width=1920, height=1080, filename="draft.png"):
    return MagicMock(filename=filename, path=filename, width=width, height=height)


def _seed_understanding(steps: ReplyDraftSteps) -> None:
    steps.result.email_understanding_summary = "Please review by EOD"
    steps.result.email_understanding_sender_intent = "request action"
    steps.result.email_understanding_requested_action = "review"
    steps.result.email_understanding_important_points = ["deadline: EOD"]


# --- Local draft-quality gate (no provider call) ---

def test_empty_draft_rejected():
    ok, _ = _validate_draft_quality("")
    assert ok is False


def test_placeholder_marker_rejected():
    ok, notes = _validate_draft_quality("Hi [insert name], thanks!")
    assert ok is False
    assert "placeholder" in notes.lower()


def test_reasonable_draft_passes():
    ok, _ = _validate_draft_quality("Sure, I will complete this by EOD today.")
    assert ok is True


# --- generate_draft(): schema-valid response required, quality gate enforced ---

def test_generated_draft_from_understanding_passes_quality_gate():
    steps = _steps()
    _seed_understanding(steps)
    call = MagicMock(
        parsed_json={"draft_reply": "Sure, I will complete this by EOD today.",
                     "reasoning_summary": "acknowledges the request", "confidence": 0.9},
        raw_text="{}", model="gemini-3.6-flash", latency_ms=50.0, input_tokens=5, output_tokens=5,
    )
    steps.provider.analyze_screen.return_value = call
    with patch(f"{MODULE}.capture_screen", return_value=_capture()):
        assert steps.generate_draft() is True
    assert steps.result.draft_reply == "Sure, I will complete this by EOD today."
    assert steps.result.draft_quality_passed is True


def test_empty_generated_draft_fails_quality_gate():
    steps = _steps()
    _seed_understanding(steps)
    call = MagicMock(
        parsed_json={"draft_reply": "", "reasoning_summary": "nothing", "confidence": 0.9},
        raw_text="{}", model="gemini-3.6-flash", latency_ms=50.0, input_tokens=5, output_tokens=5,
    )
    steps.provider.analyze_screen.return_value = call
    with patch(f"{MODULE}.capture_screen", return_value=_capture()):
        assert steps.generate_draft() is False
    assert steps.result.failure_reason == LaunchFailureReason.DRAFT_VALIDATION_FAILED


# --- generate_draft(): transient-failure retry (live-run bug, 2026-09-04) ---
# generate_draft() used to pass max_retries=0 — the only single-shot
# Vision call in the whole pipeline with zero retries — so one transient
# provider timeout was instantly fatal with no fallback, unlike every
# other stage. It now uses the default PROVIDER_RETRY_COUNT like the
# rest of the pipeline; these two tests pin that behavior directly
# against generate_draft(), mirroring the existing grounding-retry
# tests (test_provider_retries_one_when_first_grounding_call_fails_
# then_succeeds / ..._and_failure_retained_when_both_attempts_fail).

def test_generate_draft_retries_once_after_a_transient_failure_then_succeeds():
    steps = _steps()
    _seed_understanding(steps)
    good_call = MagicMock(
        parsed_json={"draft_reply": "Sure, I will complete this by EOD today.",
                     "reasoning_summary": "acknowledges the request", "confidence": 0.9},
        raw_text="{}", model="gemini-3.6-flash", latency_ms=50.0, input_tokens=5, output_tokens=5,
    )
    steps.provider.analyze_screen.side_effect = [NetworkError("timed out"), good_call]
    with patch(f"{MODULE}.capture_screen", return_value=_capture()):
        assert steps.generate_draft() is True
    assert steps.result.draft_reply == "Sure, I will complete this by EOD today."
    assert steps.provider.analyze_screen.call_count == 2
    assert steps.result.provider_retries == 1


def test_generate_draft_fails_safe_when_both_attempts_time_out():
    steps = _steps()
    _seed_understanding(steps)
    steps.provider.analyze_screen.side_effect = [NetworkError("timed out"), NetworkError("timed out")]
    with patch(f"{MODULE}.capture_screen", return_value=_capture()):
        assert steps.generate_draft() is False
    assert steps.result.failure_reason == LaunchFailureReason.TECHNICAL_PROVIDER_ERROR
    assert steps.provider.analyze_screen.call_count == 2
    assert steps.result.draft_reply is None


# --- type_draft(): segmented typing safety ---

def _typeable_steps(draft_text: str) -> ReplyDraftSteps:
    steps = _steps()
    steps.result.draft_reply = draft_text
    steps.result.draft_quality_passed = True
    return steps


def test_type_draft_raises_if_quality_gate_not_passed():
    steps = _steps()
    steps.result.draft_reply = "hello"
    steps.result.draft_quality_passed = False
    try:
        steps.type_draft()
        assert False, "expected RuntimeError"
    except RuntimeError:
        pass


def test_write_never_receives_embedded_newline():
    steps = _typeable_steps("Line one\nLine two\nLine three")
    with patch(f"{MODULE}.pyautogui") as mock_pyautogui, patch(f"{MODULE}.time.sleep"), \
         patch(f"{MODULE}.get_foreground_window_title", return_value="Outlook"):
        mock_pyautogui.FAILSAFE = True
        assert steps.type_draft() is True
        for call in mock_pyautogui.write.call_args_list:
            assert "\n" not in call.args[0]
        assert mock_pyautogui.write.call_count == 3
        assert mock_pyautogui.press.call_count == 2  # Enter between segments, never after the last
        for call in mock_pyautogui.press.call_args_list:
            assert call.args[0] == "enter"
    assert steps.result.typing_result == "PASS"
    assert steps.result.typed_text == "Line one\nLine two\nLine three"


def test_foreground_checked_before_every_segment_and_enter():
    steps = _typeable_steps("A\nB")
    title_calls = []

    def _fake_title():
        title_calls.append(1)
        return "Outlook"

    with patch(f"{MODULE}.pyautogui") as mock_pyautogui, patch(f"{MODULE}.time.sleep"), \
         patch(f"{MODULE}.get_foreground_window_title", side_effect=_fake_title):
        mock_pyautogui.FAILSAFE = True
        assert steps.type_draft() is True
    # One check before typing starts, one before each of the 2 segments,
    # one before the 1 Enter between them = 4 total.
    assert len(title_calls) == 4


def test_foreground_drift_before_write_aborts_immediately():
    steps = _typeable_steps("Line one\nLine two")
    titles = iter(["Outlook", "Visual Studio Code"])  # pre-typing check ok, then drift before segment 0
    with patch(f"{MODULE}.pyautogui") as mock_pyautogui, patch(f"{MODULE}.time.sleep"), \
         patch(f"{MODULE}.get_foreground_window_title", side_effect=lambda: next(titles)):
        mock_pyautogui.FAILSAFE = True
        assert steps.type_draft() is False
        mock_pyautogui.write.assert_not_called()
        mock_pyautogui.press.assert_not_called()
    assert steps.result.typing_result == "ABORTED"
    assert steps.result.failure_reason == LaunchFailureReason.FOREGROUND_CHANGED_DURING_TYPING
    assert steps.result.typing_abort_stage == "before_write"
    assert steps.result.segments_typed_before_abort == 0


def test_foreground_drift_before_enter_stops_after_partial_typing():
    steps = _typeable_steps("Line one\nLine two")
    # ok before typing, ok before segment 0 write, then drift before the Enter after segment 0
    titles = iter(["Outlook", "Outlook", "Visual Studio Code"])
    with patch(f"{MODULE}.pyautogui") as mock_pyautogui, patch(f"{MODULE}.time.sleep"), \
         patch(f"{MODULE}.get_foreground_window_title", side_effect=lambda: next(titles)):
        mock_pyautogui.FAILSAFE = True
        assert steps.type_draft() is False
        mock_pyautogui.write.assert_called_once_with("Line one", interval=TYPE_INTERVAL_SECONDS)
        mock_pyautogui.press.assert_not_called()
    assert steps.result.typing_abort_stage == "before_enter"
    assert steps.result.segments_typed_before_abort == 1
    assert steps.result.typed_text == "Line one"


def test_draft_typed_exactly_once_no_retry_after_success():
    steps = _typeable_steps("Hello there")
    with patch(f"{MODULE}.pyautogui") as mock_pyautogui, patch(f"{MODULE}.time.sleep"), \
         patch(f"{MODULE}.get_foreground_window_title", return_value="Outlook"):
        mock_pyautogui.FAILSAFE = True
        assert steps.type_draft() is True
        assert steps.type_draft() is True  # calling again is a caller error in real flow, but must never auto-fire
    assert mock_pyautogui.write.call_count == 2  # once per call — no internal retry logic exists


# --- verify_draft(): requires typed text first, gates on editor-open AND semantic-match ---

def test_verify_raises_if_nothing_typed():
    steps = _steps()
    try:
        steps.verify_draft()
        assert False, "expected RuntimeError"
    except RuntimeError:
        pass


def test_draft_verification_full_content_passes():
    steps = _steps()
    steps.result.text_entry_executed = True
    steps.result.draft_reply = "Sure, will do."
    call = MagicMock(
        parsed_json={"reply_editor_open": True, "semantic_match": True, "detected_draft": "Sure, will do.",
                     "confidence": 0.95, "reason": "matches"},
        raw_text="{}", model="gemini-3.6-flash", latency_ms=50.0, input_tokens=5, output_tokens=5,
    )
    steps.provider.analyze_screen.return_value = call
    with patch(f"{MODULE}.time.sleep"), patch(f"{MODULE}.get_foreground_window_title", return_value="Outlook"), \
         patch(f"{MODULE}.capture_screen", return_value=_capture()):
        assert steps.verify_draft() is True
    assert steps.result.result == "PASS"
    assert steps.result.exact_match is True


def test_failed_verification_does_not_retype():
    steps = _steps()
    steps.result.text_entry_executed = True
    steps.result.draft_reply = "Sure, will do."
    call = MagicMock(
        parsed_json={"reply_editor_open": True, "semantic_match": False, "detected_draft": "garbled text",
                     "confidence": 0.4, "reason": "mismatch"},
        raw_text="{}", model="gemini-3.6-flash", latency_ms=50.0, input_tokens=5, output_tokens=5,
    )
    steps.provider.analyze_screen.return_value = call
    with patch(f"{MODULE}.time.sleep"), patch(f"{MODULE}.get_foreground_window_title", return_value="Outlook"), \
         patch(f"{MODULE}.capture_screen", return_value=_capture()), \
         patch(f"{MODULE}.pyautogui") as mock_pyautogui:
        mock_pyautogui.FAILSAFE = True
        assert steps.verify_draft() is False
        mock_pyautogui.write.assert_not_called()
    assert steps.result.failure_reason == LaunchFailureReason.DRAFT_VERIFICATION_FAILED


# --- Structural: no Send code path anywhere in this stage ---

def test_send_click_count_never_incremented():
    steps = _steps()
    assert steps.result.send_click_count == 0


def test_no_send_related_source_in_reply_draft_modules():
    import inspect

    import app.outlook.draft as draft_mod
    import app.outlook.reply as reply_mod

    for module in (draft_mod, reply_mod):
        source = inspect.getsource(module).lower()
        assert "pyautogui.hotkey('ctrl', 'enter')" not in source.replace('"', "'")
        assert "hotkey('alt', 's')" not in source.replace('"', "'")


# --- verify_draft(): bounded observation-only retry (Phase 6) ---

def _verify_call(semantic_match: bool, detected: str = "Sure, will do.", reply_editor_open: bool = True):
    return MagicMock(
        parsed_json={"reply_editor_open": reply_editor_open, "semantic_match": semantic_match,
                     "detected_draft": detected, "confidence": 0.9, "reason": "r"},
        raw_text="{}", model="gemini-3.6-flash", latency_ms=50.0, input_tokens=5, output_tokens=5,
    )


def _verifiable_steps() -> ReplyDraftSteps:
    steps = _steps()
    steps.result.text_entry_executed = True
    steps.result.draft_reply = "Sure, will do."
    return steps


def test_verification_succeeds_on_second_attempt():
    steps = _verifiable_steps()
    steps.provider.analyze_screen.side_effect = [
        _verify_call(False, detected="garbled text"),
        _verify_call(True, detected="Sure, will do."),
    ]
    captures = [_capture(filename="v1.png"), _capture(filename="v2.png")]
    with patch(f"{MODULE}.time.sleep"), patch(f"{MODULE}.get_foreground_window_title", return_value="Outlook"), \
         patch(f"{MODULE}.capture_screen", side_effect=captures):
        assert steps.verify_draft() is True
    assert steps.result.result == "PASS"
    assert steps.provider.analyze_screen.call_count == 2
    assert len(steps.result.draft_verification_attempts) == 2
    assert steps.result.draft_verification_attempts[0].semantic_match is False
    assert steps.result.draft_verification_attempts[1].semantic_match is True
    assert steps.result.draft_verified_at is not None


def test_verification_exhausts_attempts_and_fails_safely():
    steps = _verifiable_steps()
    steps.provider.analyze_screen.side_effect = [
        _verify_call(False, detected="garbled 1"),
        _verify_call(False, detected="garbled 2"),
    ]
    captures = [_capture(filename="v1.png"), _capture(filename="v2.png")]
    with patch(f"{MODULE}.time.sleep"), patch(f"{MODULE}.get_foreground_window_title", return_value="Outlook"), \
         patch(f"{MODULE}.capture_screen", side_effect=captures):
        assert steps.verify_draft() is False
    assert steps.result.result == "FAIL"
    assert steps.result.failure_reason == LaunchFailureReason.DRAFT_VERIFICATION_FAILED
    assert steps.provider.analyze_screen.call_count == MAX_DRAFT_VERIFICATION_ATTEMPTS
    assert len(steps.result.draft_verification_attempts) == MAX_DRAFT_VERIFICATION_ATTEMPTS


def test_verification_retries_never_retype_or_reclick():
    steps = _verifiable_steps()
    steps.provider.analyze_screen.side_effect = [
        _verify_call(False, detected="garbled text"),
        _verify_call(True, detected="Sure, will do."),
    ]
    captures = [_capture(filename="v1.png"), _capture(filename="v2.png")]
    with patch(f"{MODULE}.time.sleep"), patch(f"{MODULE}.get_foreground_window_title", return_value="Outlook"), \
         patch(f"{MODULE}.capture_screen", side_effect=captures), \
         patch(f"{MODULE}.pyautogui") as mock_pyautogui:
        mock_pyautogui.FAILSAFE = True
        assert steps.verify_draft() is True
    mock_pyautogui.write.assert_not_called()
    mock_pyautogui.press.assert_not_called()
    mock_pyautogui.click.assert_not_called()
    mock_pyautogui.moveTo.assert_not_called()


# --- Phase 6 metrics: generation, typing, verification ---

def test_phase6_generation_typing_verification_metrics_populated():
    steps = _steps()
    _seed_understanding(steps)
    gen_call = MagicMock(
        parsed_json={"draft_reply": "Sure, I will complete this by EOD today.",
                     "reasoning_summary": "acknowledges the request", "confidence": 0.9},
        raw_text="{}", model="gemini-3.6-flash", latency_ms=50.0, input_tokens=5, output_tokens=5,
    )
    steps.provider.analyze_screen.return_value = gen_call
    with patch(f"{MODULE}.capture_screen", return_value=_capture()):
        assert steps.generate_draft() is True
    assert steps.result.draft_generation_started_at is not None
    assert steps.result.draft_generated_at is not None
    assert steps.result.draft_generation_ms is not None and steps.result.draft_generation_ms >= 0

    with patch(f"{MODULE}.pyautogui") as mock_pyautogui, patch(f"{MODULE}.time.sleep"), \
         patch(f"{MODULE}.get_foreground_window_title", return_value="Outlook"):
        mock_pyautogui.FAILSAFE = True
        assert steps.type_draft() is True
    assert steps.result.typing_started_at is not None
    assert steps.result.typing_completed_at is not None
    assert steps.result.typing_ms is not None and steps.result.typing_ms >= 0
    assert steps.result.typing_segment_count == 1
    assert steps.result.partial_typing is False

    verify_call = MagicMock(
        parsed_json={"reply_editor_open": True, "semantic_match": True,
                     "detected_draft": steps.result.draft_reply, "confidence": 0.95, "reason": "matches"},
        raw_text="{}", model="gemini-3.6-flash", latency_ms=50.0, input_tokens=5, output_tokens=5,
    )
    steps.provider.analyze_screen.return_value = verify_call
    with patch(f"{MODULE}.time.sleep"), patch(f"{MODULE}.get_foreground_window_title", return_value="Outlook"), \
         patch(f"{MODULE}.capture_screen", return_value=_capture()):
        assert steps.verify_draft() is True
    assert steps.result.draft_verified_at is not None
    assert steps.result.draft_ready_ms is not None and steps.result.draft_ready_ms >= 0
    assert len(steps.result.draft_verification_attempts) == 1


# --- partial_typing: set only once the typing loop has actually begun ---

def test_partial_typing_true_when_interrupted_before_write():
    steps = _typeable_steps("Line one\nLine two")
    titles = iter(["Outlook", "Visual Studio Code"])  # pre-typing ok, drift before segment 0 write
    with patch(f"{MODULE}.pyautogui") as mock_pyautogui, patch(f"{MODULE}.time.sleep"), \
         patch(f"{MODULE}.get_foreground_window_title", side_effect=lambda: next(titles)):
        mock_pyautogui.FAILSAFE = True
        assert steps.type_draft() is False
    assert steps.result.partial_typing is True


def test_partial_typing_true_when_interrupted_before_enter():
    steps = _typeable_steps("Line one\nLine two")
    titles = iter(["Outlook", "Outlook", "Visual Studio Code"])  # ok, ok, drift before the Enter
    with patch(f"{MODULE}.pyautogui") as mock_pyautogui, patch(f"{MODULE}.time.sleep"), \
         patch(f"{MODULE}.get_foreground_window_title", side_effect=lambda: next(titles)):
        mock_pyautogui.FAILSAFE = True
        assert steps.type_draft() is False
    assert steps.result.partial_typing is True


def test_partial_typing_false_when_foreground_lost_before_typing_begins():
    steps = _typeable_steps("Hello there")
    with patch(f"{MODULE}.pyautogui") as mock_pyautogui, patch(f"{MODULE}.time.sleep"), \
         patch(f"{MODULE}.get_foreground_window_title", return_value="Visual Studio Code"):
        mock_pyautogui.FAILSAFE = True
        assert steps.type_draft() is False
    assert steps.result.partial_typing is False
    assert steps.result.typing_started_at is None  # the typing loop never started


# --- Reply-generation prompt regression: no-invented-facts / no-unsupported-commitments ---

def test_reply_generation_prompt_forbids_invented_facts_and_commitments():
    text = REPLY_GENERATION_PROMPT_PATH.read_text(encoding="utf-8").lower()
    assert "does not invent facts" in text
    assert "does not make commitments beyond" in text


# --- run_find_and_open_email(): provider_retries propagation (Phase 8 fix) ---
#
# Root cause under test: provider_retries is declared on FindEmailResult
# (app/outlook/find_email.py, an app-level addition), NOT on the
# historical rnd.models.find_open_email.RND009CResult the field-copy
# loop iterates over — so it was silently dropped exactly at the
# fo.result -> self.result hand-off. Fixed by an explicit fold-in line,
# mirroring FindOpenEmailSteps.run_launch_and_readiness()'s own
# `r.provider_retries += lr.provider_retries` one layer down.

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


def _patch_launch_and_find(stack: ExitStack, titles) -> dict:
    pyautogui_mocks = {
        "launch": stack.enter_context(patch(f"{LAUNCH_MODULE}.pyautogui")),
        "find": stack.enter_context(patch(f"{FIND_MODULE}.pyautogui")),
    }
    for mock_pg in pyautogui_mocks.values():
        mock_pg.FAILSAFE = True

    for module in (LAUNCH_MODULE, FIND_MODULE):
        stack.enter_context(patch(f"{module}.time.sleep"))

    stack.enter_context(patch(f"{LAUNCH_MODULE}.get_foreground_window_title", side_effect=titles))
    stack.enter_context(patch(f"{LAUNCH_MODULE}.get_foreground_hwnd", return_value=12345))
    stack.enter_context(patch(f"{LAUNCH_MODULE}.is_maximized", return_value=True))
    stack.enter_context(patch(f"{LAUNCH_MODULE}.maximize"))
    stack.enter_context(patch(
        f"{LAUNCH_MODULE}.get_environment_info",
        return_value={"pyautogui_width": 1920, "pyautogui_height": 1080, "dimensions_match": True},
    ))
    stack.enter_context(patch(f"{FIND_MODULE}.get_foreground_window_title", return_value="Inbox - Outlook"))

    stack.enter_context(patch(
        f"{LAUNCH_MODULE}.capture_screen",
        return_value=MagicMock(filename="a.png", path="a.png", width=1920, height=1080),
    ))
    stack.enter_context(patch(
        f"{FIND_MODULE}.capture_screen",
        return_value=MagicMock(filename="b.png", path="b.png", width=1920, height=1080),
    ))
    return pyautogui_mocks


def _titles():
    return itertools.chain(["Search", "Search", "Search"], itertools.repeat("Inbox - Outlook"))


def test_provider_retries_zero_when_first_call_succeeds():
    steps = _steps()
    steps.provider.analyze_screen.side_effect = [
        _search_grounding(), _readiness_ready(), _email_grounding(), _email_open_verified(),
    ]
    with ExitStack() as stack:
        _patch_launch_and_find(stack, _titles())
        assert steps.run_find_and_open_email() is True
    assert steps.result.provider_retries == 0


def test_provider_retries_one_when_first_grounding_call_fails_then_succeeds():
    steps = _steps()
    steps.provider.analyze_screen.side_effect = [
        NetworkError("[Errno 10054] connection reset"),  # 1st attempt at Windows-Search grounding fails
        _search_grounding(),                              # retry succeeds
        _readiness_ready(), _email_grounding(), _email_open_verified(),
    ]
    with ExitStack() as stack:
        mocks = _patch_launch_and_find(stack, _titles())
        assert steps.run_find_and_open_email() is True
    assert steps.result.provider_retries == 1
    assert mocks["launch"].click.call_count == 1  # click still happens exactly once, after the retry resolved


def test_provider_retries_one_and_failure_retained_when_both_attempts_fail():
    steps = _steps()
    steps.provider.analyze_screen.side_effect = [
        NetworkError("[Errno 10054] connection reset"),
        NetworkError("[Errno 10054] connection reset"),
    ]
    with ExitStack() as stack:
        mocks = _patch_launch_and_find(stack, _titles())
        assert steps.run_find_and_open_email() is False
    assert steps.result.provider_retries == 1  # retries_used == max_retries on exhaustion
    assert steps.result.result == "ERROR"
    assert steps.result.failure_reason == LaunchFailureReason.TECHNICAL_PROVIDER_ERROR
    mocks["launch"].click.assert_not_called()
    mocks["find"].click.assert_not_called()


def test_provider_retry_never_repeats_windows_key_search_typing_or_click():
    steps = _steps()
    steps.provider.analyze_screen.side_effect = [
        NetworkError("[Errno 10054] connection reset"),
        _search_grounding(),
        _readiness_ready(), _email_grounding(), _email_open_verified(),
    ]
    with ExitStack() as stack:
        mocks = _patch_launch_and_find(stack, _titles())
        assert steps.run_find_and_open_email() is True
    assert mocks["launch"].press.call_count == 1   # Windows key — pressed once, never repeated by the retry
    assert mocks["launch"].write.call_count == 1   # "Outlook" typed once
    assert mocks["launch"].click.call_count == 1   # Outlook-result click — once, after grounding resolved
    assert mocks["find"].click.call_count == 1     # target-email-row click — once
