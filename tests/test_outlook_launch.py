"""app/outlook/launch.py unit tests (final POC — moved from RND-009B's
app/playbook/outlook_launch_steps.py). pyautogui, the vision provider,
screen capture, and foreground checks are ALL mocked — no real
mouse/keyboard/network/provider call happens anywhere in this file.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.outlook.launch import OutlookLaunchSteps, TYPE_INTERVAL_SECONDS  # noqa: E402
from app.playbook.failure_reasons import LaunchFailureReason  # noqa: E402
from app.safety.abort_controller import AbortController  # noqa: E402
from app.vision.service import VisionService  # noqa: E402

MODULE = "app.outlook.launch"


def _steps() -> OutlookLaunchSteps:
    return OutlookLaunchSteps(AbortController(), VisionService(MagicMock(), fallback=None), "gemini-3.6-flash")


def _capture(width=1920, height=1080, filename="search.png"):
    return MagicMock(filename=filename, path=filename, width=width, height=height)


# --- Windows key / typing: exactly once, no Enter ---

def test_windows_key_issued_once():
    steps = _steps()
    with patch(f"{MODULE}.pyautogui") as mock_pyautogui, patch(f"{MODULE}.time.sleep"):
        mock_pyautogui.FAILSAFE = True
        assert steps.press_windows_key() is True
        mock_pyautogui.press.assert_called_once_with("win")
    assert steps.result.windows_key_timestamp is not None
    assert steps.result.keyboard_action_count == 1


def test_outlook_typed_exactly_once_no_enter():
    steps = _steps()
    with patch(f"{MODULE}.pyautogui") as mock_pyautogui, patch(f"{MODULE}.time.sleep"):
        mock_pyautogui.FAILSAFE = True
        assert steps.type_search_query("Outlook") is True
        mock_pyautogui.write.assert_called_once_with("Outlook", interval=TYPE_INTERVAL_SECONDS)
        mock_pyautogui.press.assert_not_called()  # no Enter anywhere in typing


def test_no_enter_pressed_before_vision_verification():
    steps = _steps()
    with patch(f"{MODULE}.pyautogui") as mock_pyautogui, patch(f"{MODULE}.time.sleep"), \
         patch(f"{MODULE}.get_foreground_window_title", return_value="Search"):
        mock_pyautogui.FAILSAFE = True
        steps.press_windows_key()
        steps.type_search_query("Outlook")

        press_calls = [c.args[0] for c in mock_pyautogui.press.call_args_list]
        assert "enter" not in press_calls
        assert press_calls == ["win"]


# --- Vision grounding: SEMANTIC verification only (2026-09-06 keyboard-
# activation fix) — no coordinate conversion happens for this stage
# anymore; bbox is accepted/logged for diagnostics only. ---

def test_grounding_never_computes_a_click_point():
    """bbox is diagnostic-only for OUTLOOK_SEARCH now — raw_x/raw_y/
    converted_x/converted_y must stay None even on a valid, schema-valid
    desktop_app result, proving the bbox is genuinely non-actionable."""
    steps = _steps()
    steps.result.screen_width, steps.result.screen_height = 1920, 1080
    call = MagicMock(
        parsed_json={"search_visible": True, "target_visible": True, "target_type": "desktop_app",
                     "visible_label": "Outlook",
                     "bbox": [200.0, 400.0, 300.0, 600.0], "confidence": 0.9, "reason": "clear match"},
        raw_text="{}", model="gemini-3.6-flash", latency_ms=100.0, input_tokens=10, output_tokens=5,
    )
    steps.vision.primary.analyze_screen.return_value = call

    assert steps.ground_search_result(_capture()) is True
    assert steps.result.search_grounding.bbox == [200.0, 400.0, 300.0, 600.0]  # reported/logged
    assert steps.result.raw_x is None
    assert steps.result.raw_y is None
    assert steps.result.converted_x is None
    assert steps.result.converted_y is None


def test_low_confidence_rejected():
    steps = _steps()
    steps.result.screen_width, steps.result.screen_height = 1920, 1080
    call = MagicMock(
        parsed_json={"search_visible": True, "target_visible": True, "target_type": "desktop_app",
                     "visible_label": "Outlook",
                     "bbox": [200.0, 400.0, 300.0, 600.0], "confidence": 0.1, "reason": "unsure"},
        raw_text="{}", model="gemini-3.6-flash", latency_ms=100.0, input_tokens=10, output_tokens=5,
    )
    steps.vision.primary.analyze_screen.return_value = call

    assert steps.ground_search_result(_capture()) is False
    assert steps.result.failure_reason == LaunchFailureReason.GROUNDING_INVALID


def test_non_outlook_result_rejected():
    steps = _steps()
    steps.result.screen_width, steps.result.screen_height = 1920, 1080
    call = MagicMock(
        parsed_json={"search_visible": True, "target_visible": False, "target_type": "other",
                     "visible_label": "Notepad",
                     "bbox": [200.0, 400.0, 300.0, 600.0], "confidence": 0.9, "reason": "wrong app"},
        raw_text="{}", model="gemini-3.6-flash", latency_ms=100.0, input_tokens=10, output_tokens=5,
    )
    steps.vision.primary.analyze_screen.return_value = call

    assert steps.ground_search_result(_capture()) is False
    assert steps.result.failure_reason == LaunchFailureReason.OUTLOOK_RESULT_NOT_FOUND


# --- Activation gating: no Enter without approval, foreground check ---

def test_activation_refused_without_human_approval():
    steps = _steps()
    with pytest.raises(RuntimeError):
        steps.activate_outlook_result()


def test_activation_blocked_when_search_state_lost():
    steps = _steps()
    steps.result.human_target_approved = True
    with patch(f"{MODULE}.pyautogui") as mock_pyautogui, \
         patch(f"{MODULE}.get_foreground_window_title", return_value="Visual Studio Code"):
        mock_pyautogui.FAILSAFE = True
        assert steps.activate_outlook_result() is False
        mock_pyautogui.press.assert_not_called()
    assert steps.result.failure_reason == LaunchFailureReason.SEARCH_STATE_LOST_BEFORE_CLICK


def test_activation_presses_enter_exactly_once_when_search_state_holds():
    steps = _steps()
    steps.result.human_target_approved = True
    with patch(f"{MODULE}.pyautogui") as mock_pyautogui, \
         patch(f"{MODULE}.get_foreground_window_title", return_value="Search"):
        mock_pyautogui.FAILSAFE = True
        assert steps.activate_outlook_result() is True
        mock_pyautogui.press.assert_called_once_with("enter")
        mock_pyautogui.moveTo.assert_not_called()
        mock_pyautogui.click.assert_not_called()
    assert steps.result.keyboard_action_count == 1
    assert steps.result.mouse_click_count == 0


# --- Readiness: splash screen never counted as ready ---

def test_splash_screen_not_counted_as_ready():
    steps = _steps()
    steps.result.foreground_verified = True
    call = MagicMock(
        parsed_json={
            "application": "Microsoft Outlook", "outlook_visible": True, "splash_screen_visible": True,
            "ready_for_interaction": False, "detected_state": "splash", "confidence": 0.95, "reason": "loading",
        },
        raw_text="{}", model="gemini-3.6-flash", latency_ms=100.0, input_tokens=10, output_tokens=5,
    )
    steps.vision.primary.analyze_screen.return_value = call
    with patch(f"{MODULE}.time.sleep"), patch(f"{MODULE}.get_foreground_window_title", return_value="Outlook"), \
         patch(f"{MODULE}.capture_screen", return_value=_capture()):
        assert steps.verify_outlook_readiness() is False
    assert steps.result.failure_reason == LaunchFailureReason.OUTLOOK_READY_TIMEOUT
    assert len(steps.result.readiness_attempts) == 2  # both bounded attempts exhausted


def test_ready_for_interaction_passes_on_first_attempt():
    steps = _steps()
    steps.result.foreground_verified = True
    call = MagicMock(
        parsed_json={
            "application": "Microsoft Outlook", "outlook_visible": True, "splash_screen_visible": False,
            "ready_for_interaction": True, "detected_state": "interactive", "confidence": 0.98, "reason": "loaded",
        },
        raw_text="{}", model="gemini-3.6-flash", latency_ms=100.0, input_tokens=10, output_tokens=5,
    )
    steps.vision.primary.analyze_screen.return_value = call
    with patch(f"{MODULE}.time.sleep"), patch(f"{MODULE}.get_foreground_window_title", return_value="Outlook"), \
         patch(f"{MODULE}.capture_screen", return_value=_capture()):
        assert steps.verify_outlook_readiness() is True
    assert steps.result.ready_for_interaction is True
    assert len(steps.result.readiness_attempts) == 1


# --- Abort ---

def test_abort_blocks_before_windows_key():
    controller = AbortController()
    controller.request_abort()
    steps = OutlookLaunchSteps(controller, VisionService(MagicMock(), fallback=None), "gemini-3.6-flash")
    assert steps.press_windows_key() is False
    assert steps.result.result == "ABORTED"
    assert steps.result.failure_reason == LaunchFailureReason.USER_ABORTED
