"""OUTLOOK_SEARCH keyboard-activation regression tests (2026-09-06).

Root cause: a live Claude-primary run reproduced this project's
long-standing finding that Claude's OUTLOOK_SEARCH semantic recognition
is reliable but its bbox is not — the resulting click landed above the
actual result and Outlook never got foreground
(OUTLOOK_FOREGROUND_VERIFICATION_FAILED). Fix: physical activation for
this stage no longer depends on ANY Vision-reported coordinate —
ground_search_result() only verifies the result semantically;
activate_outlook_result() presses Enter exactly once, the same
user-equivalent action a human would use, identical regardless of which
provider (Claude or Gemini, primary or fallback) produced the semantic
verification.

Covers the 12-point regression list from the keyboard-activation task:
  1.  Claude schema-valid result -> Enter once, no mouse click
  2.  Gemini schema-valid result -> identical behavior
  3.  Claude technical timeout -> Gemini fallback, SAME screenshot -> Enter once
  4.  Claude semantic wrong target_type=web -> no fallback, no Enter, safe stop
  5.  Outlook result not visible -> no Enter
  6.  label not Outlook -> no Enter
  7.  abort before action -> no Enter
  8.  foreground/context invalid before action -> no Enter
  9.  post-Enter verification failure -> no second Enter, safe stop
  10. provider-independent: no provider-name branch in the execution code
  11. pyautogui.click is never called during OUTLOOK_SEARCH activation
  12. email/Reply/Send mouse-based grounding remains unchanged

No real Vision/API calls, no live Outlook, no real mouse/keyboard input
— pyautogui is always mocked.
"""

import inspect
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.outlook.launch import OutlookLaunchSteps  # noqa: E402
from app.playbook.failure_reasons import LaunchFailureReason  # noqa: E402
from app.safety.abort_controller import AbortController  # noqa: E402
from app.vision.providers.base import ProviderCallResult, ProviderTimeoutError  # noqa: E402
from app.vision.service import VisionService  # noqa: E402

MODULE = "app.outlook.launch"


def _provider(name: str) -> MagicMock:
    mock = MagicMock()
    mock.provider_name = name
    return mock


def _call(model: str, **overrides) -> ProviderCallResult:
    payload = {
        "search_visible": True, "target_visible": True, "target_type": "desktop_app",
        "visible_label": "Outlook", "visible_sublabel": "App",
        "bbox": [300.0, 400.0, 360.0, 700.0], "bbox_tightly_scoped": True,
        "confidence": 0.95, "reason": "ok",
    }
    payload.update(overrides)
    return ProviderCallResult(
        raw_text="{}", parsed_json=payload, model=model, latency_ms=10.0, input_tokens=5, output_tokens=5,
    )


def _capture(width=1920, height=1080, filename="search.png"):
    return MagicMock(filename=filename, path=filename, width=width, height=height)


def _steps(primary, fallback=None, default_max_retries=1) -> OutlookLaunchSteps:
    steps = OutlookLaunchSteps(AbortController(), VisionService(primary, fallback, default_max_retries), "model")
    steps.result.screen_width, steps.result.screen_height = 1920, 1080
    return steps


# --- 1/2: identical behavior regardless of provider — schema-valid
# desktop result -> Enter once, zero mouse click ---

def test_1_claude_schema_valid_result_enters_once_no_click():
    primary = _provider("anthropic")
    primary.analyze_screen.return_value = _call("claude-sonnet-5")
    steps = _steps(primary)

    assert steps.ground_search_result(_capture()) is True
    steps.record_human_approval(True)
    with patch(f"{MODULE}.pyautogui") as mock_pyautogui, \
         patch(f"{MODULE}.get_foreground_window_title", return_value="Search"):
        mock_pyautogui.FAILSAFE = True
        assert steps.activate_outlook_result() is True
        mock_pyautogui.press.assert_called_once_with("enter")
        mock_pyautogui.click.assert_not_called()
        mock_pyautogui.moveTo.assert_not_called()


def test_2_gemini_schema_valid_result_identical_behavior():
    primary = _provider("gemini")
    primary.analyze_screen.return_value = _call("gemini-3.6-flash")
    steps = _steps(primary)

    assert steps.ground_search_result(_capture()) is True
    steps.record_human_approval(True)
    with patch(f"{MODULE}.pyautogui") as mock_pyautogui, \
         patch(f"{MODULE}.get_foreground_window_title", return_value="Search"):
        mock_pyautogui.FAILSAFE = True
        assert steps.activate_outlook_result() is True
        mock_pyautogui.press.assert_called_once_with("enter")
        mock_pyautogui.click.assert_not_called()
        mock_pyautogui.moveTo.assert_not_called()


# --- 3: Claude technical timeout -> Gemini fallback, SAME screenshot -> Enter once ---

def test_3_claude_timeout_gemini_fallback_same_screenshot_enters_once():
    shot = _capture(filename="the_one_screenshot.png")
    primary = _provider("anthropic")
    primary.analyze_screen.side_effect = ProviderTimeoutError("timed out")
    fallback = _provider("gemini")
    fallback.analyze_screen.return_value = _call("gemini-3.6-flash")

    steps = _steps(primary, fallback, default_max_retries=0)
    assert steps.ground_search_result(shot) is True
    assert steps.result.fallback_uses == 1

    primary_path = primary.analyze_screen.call_args[0][0]
    fallback_path = fallback.analyze_screen.call_args[0][0]
    assert primary_path == fallback_path == Path(shot.path)  # same screenshot, no recapture

    steps.record_human_approval(True)
    with patch(f"{MODULE}.pyautogui") as mock_pyautogui, \
         patch(f"{MODULE}.get_foreground_window_title", return_value="Search"):
        mock_pyautogui.FAILSAFE = True
        assert steps.activate_outlook_result() is True
        mock_pyautogui.press.assert_called_once_with("enter")


# --- 4: semantic wrong target_type -> NO fallback, NO Enter, safe stop ---

def test_4_wrong_target_type_no_fallback_no_enter_safe_stop():
    primary = _provider("anthropic")
    primary.analyze_screen.return_value = _call("claude-sonnet-5", target_type="web_result")
    fallback = _provider("gemini")

    steps = _steps(primary, fallback)
    assert steps.ground_search_result(_capture()) is False
    assert steps.result.failure_reason == LaunchFailureReason.OUTLOOK_RESULT_WRONG_TYPE
    fallback.analyze_screen.assert_not_called()  # semantic answer, never a technical-failure fallback trigger


# --- 5: Outlook result not visible -> no Enter (never reaches activation) ---

def test_5_target_not_visible_blocks_before_activation():
    primary = _provider("anthropic")
    primary.analyze_screen.return_value = _call(
        "claude-sonnet-5", target_visible=False, target_type="other", visible_label="", bbox=None,
    )
    steps = _steps(primary)
    assert steps.ground_search_result(_capture()) is False
    assert steps.result.failure_reason == LaunchFailureReason.OUTLOOK_RESULT_NOT_FOUND


# --- 6: label doesn't mention Outlook -> no Enter ---

def test_6_label_not_outlook_blocks_before_activation():
    primary = _provider("anthropic")
    primary.analyze_screen.return_value = _call("claude-sonnet-5", visible_label="Microsoft Word")
    steps = _steps(primary)
    assert steps.ground_search_result(_capture()) is False
    assert steps.result.failure_reason == LaunchFailureReason.OUTLOOK_RESULT_NOT_FOUND


# --- 7: abort requested before activation -> no Enter ---

def test_7_abort_before_activation_blocks_enter():
    primary = _provider("anthropic")
    primary.analyze_screen.return_value = _call("claude-sonnet-5")
    steps = _steps(primary)

    assert steps.ground_search_result(_capture()) is True
    steps.record_human_approval(True)
    steps.abort_controller.request_abort()

    with patch(f"{MODULE}.pyautogui") as mock_pyautogui, \
         patch(f"{MODULE}.get_foreground_window_title", return_value="Search"):
        mock_pyautogui.FAILSAFE = True
        assert steps.activate_outlook_result() is False
        mock_pyautogui.press.assert_not_called()
    assert steps.result.failure_reason == LaunchFailureReason.USER_ABORTED


# --- 8: foreground/context invalid before activation -> no Enter ---

def test_8_invalid_foreground_context_blocks_enter():
    primary = _provider("anthropic")
    primary.analyze_screen.return_value = _call("claude-sonnet-5")
    steps = _steps(primary)

    assert steps.ground_search_result(_capture()) is True
    steps.record_human_approval(True)

    with patch(f"{MODULE}.pyautogui") as mock_pyautogui, \
         patch(f"{MODULE}.get_foreground_window_title", return_value="Visual Studio Code"):
        mock_pyautogui.FAILSAFE = True
        assert steps.activate_outlook_result() is False
        mock_pyautogui.press.assert_not_called()
    assert steps.result.failure_reason == LaunchFailureReason.SEARCH_STATE_LOST_BEFORE_CLICK


# --- 9: post-Enter verification failure -> no second Enter, safe stop ---

def test_9_post_enter_verification_failure_never_presses_enter_again():
    primary = _provider("anthropic")
    primary.analyze_screen.return_value = _call("claude-sonnet-5")
    steps = _steps(primary)

    assert steps.ground_search_result(_capture()) is True
    steps.record_human_approval(True)

    with patch(f"{MODULE}.pyautogui") as mock_pyautogui, \
         patch(f"{MODULE}.get_foreground_window_title", return_value="Search"):
        mock_pyautogui.FAILSAFE = True
        assert steps.activate_outlook_result() is True
        assert mock_pyautogui.press.call_count == 1

        # Now Outlook never becomes foreground during the bounded poll —
        # verification fails, but activate_outlook_result() is never
        # called again, so Enter is never pressed a second time.
        with patch(f"{MODULE}.time.sleep"), \
             patch(f"{MODULE}.OUTLOOK_LAUNCH_TIMEOUT_SECONDS", 0.01), \
             patch(f"{MODULE}.get_foreground_window_title", return_value="Untitled - Notepad"):
            assert steps.poll_for_outlook_foreground() is False
        assert steps.result.failure_reason == LaunchFailureReason.OUTLOOK_LAUNCH_TIMEOUT
        assert mock_pyautogui.press.call_count == 1  # still exactly one — never repeated


# --- 10: provider-independent — no provider-name branch anywhere in the
# execution path (ground_search_result / activate_outlook_result) ---

def test_10_no_provider_name_branch_in_outlook_search_execution_code():
    import app.outlook.launch as mod

    source = inspect.getsource(mod.OutlookLaunchSteps.ground_search_result)
    source += inspect.getsource(mod.OutlookLaunchSteps.activate_outlook_result)
    for needle in ('"gemini"', "'gemini'", '"anthropic"', "'anthropic'", '"claude"', "'claude'", "provider_name =="):
        assert needle not in source, f"found provider-specific branch marker {needle!r}"


# --- 11: pyautogui.click is never called during OUTLOOK_SEARCH activation ---

def test_11_pyautogui_click_never_called_during_activation():
    import app.outlook.launch as mod

    source = inspect.getsource(mod.OutlookLaunchSteps.activate_outlook_result)
    assert "pyautogui.click(" not in source
    assert "pyautogui.moveTo(" not in source
    assert "pyautogui.press(\"enter\")" in source or "pyautogui.press('enter')" in source


# --- 12: existing mouse-based grounding for email/Reply/Send is unchanged ---

def test_12_email_reply_send_mouse_click_grounding_unchanged():
    import app.outlook.find_email as find_email_mod
    import app.outlook.reply as reply_mod
    import app.outlook.send as send_mod

    assert "pyautogui.click(" in inspect.getsource(find_email_mod)
    assert "pyautogui.click(" in inspect.getsource(reply_mod)
    assert "pyautogui.click(" in inspect.getsource(send_mod)
    # And each still goes through the shared deterministic validator —
    # OUTLOOK_SEARCH stopping its own use of it doesn't touch these.
    assert "validate_grounding(" in inspect.getsource(find_email_mod)
    assert "validate_grounding(" in inspect.getsource(reply_mod)
    assert "validate_grounding(" in inspect.getsource(send_mod)
