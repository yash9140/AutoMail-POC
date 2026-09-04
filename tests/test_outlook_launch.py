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

MODULE = "app.outlook.launch"


def _steps() -> OutlookLaunchSteps:
    return OutlookLaunchSteps(AbortController(), MagicMock(), "gemini-3.6-flash")


def _capture(width=1920, height=1080, filename="search.png"):
    return MagicMock(filename=filename, path=filename, width=width, height=height)


_ENV_INFO_MATCH = {"pyautogui_width": 1920, "pyautogui_height": 1080, "dimensions_match": True}


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


# --- Vision grounding: coordinate conversion, out-of-bounds, non-Outlook rejection ---

def test_grounding_converts_0_1000_coordinates_to_pixels():
    steps = _steps()
    steps.result.screen_width, steps.result.screen_height = 1920, 1080
    call = MagicMock(
        # bbox = [y_min, x_min, y_max, x_max] — center is (500, 250), same
        # as the old point this test replaces, so the expected converted_x/y
        # assertions below are unchanged.
        parsed_json={"search_visible": True, "target_visible": True, "target_type": "desktop_app",
                     "visible_label": "Outlook",
                     "bbox": [200.0, 400.0, 300.0, 600.0], "confidence": 0.9, "reason": "clear match"},
        raw_text="{}", model="gemini-3.6-flash", latency_ms=100.0, input_tokens=10, output_tokens=5,
    )
    steps.provider.analyze_screen.return_value = call

    with patch(f"{MODULE}.get_environment_info", return_value=_ENV_INFO_MATCH):
        assert steps.ground_search_result(_capture()) is True
    assert steps.result.raw_x == 500
    assert steps.result.raw_y == 250
    assert steps.result.converted_x == round(500 / 1000 * 1920)
    assert steps.result.converted_y == round(250 / 1000 * 1080)
    assert steps.result.coordinate_in_screen_bounds is True
    assert steps.result.search_grounding_bbox_pixels == [
        round(200 / 1000 * 1080), round(400 / 1000 * 1920), round(300 / 1000 * 1080), round(600 / 1000 * 1920),
    ]


def test_out_of_normalized_range_bbox_rejected():
    """A bbox with components outside the 0-1000 contract (i.e. Claude
    reporting something that looks like native pixels, or otherwise
    off-contract) must be rejected outright, never silently clamped or
    scaled — this is the case validate_grounding() catches before any
    pixel conversion is even attempted."""
    steps = _steps()
    steps.result.screen_width, steps.result.screen_height = 1920, 1080
    call = MagicMock(
        parsed_json={"search_visible": True, "target_visible": True, "target_type": "desktop_app",
                     "visible_label": "Outlook",
                     "bbox": [1200.0, 1200.0, 1400.0, 1400.0], "confidence": 0.9, "reason": "far off"},
        raw_text="{}", model="gemini-3.6-flash", latency_ms=100.0, input_tokens=10, output_tokens=5,
    )
    steps.provider.analyze_screen.return_value = call

    with patch(f"{MODULE}.get_environment_info", return_value=_ENV_INFO_MATCH):
        assert steps.ground_search_result(_capture()) is False
    assert steps.result.failure_reason == LaunchFailureReason.GROUNDING_INVALID


def test_low_confidence_rejected():
    steps = _steps()
    steps.result.screen_width, steps.result.screen_height = 1920, 1080
    call = MagicMock(
        parsed_json={"search_visible": True, "target_visible": True, "target_type": "desktop_app",
                     "visible_label": "Outlook",
                     "bbox": [200.0, 400.0, 300.0, 600.0], "confidence": 0.1, "reason": "unsure"},
        raw_text="{}", model="gemini-3.6-flash", latency_ms=100.0, input_tokens=10, output_tokens=5,
    )
    steps.provider.analyze_screen.return_value = call

    with patch(f"{MODULE}.get_environment_info", return_value=_ENV_INFO_MATCH):
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
    steps.provider.analyze_screen.return_value = call

    with patch(f"{MODULE}.get_environment_info", return_value=_ENV_INFO_MATCH):
        assert steps.ground_search_result(_capture()) is False
    assert steps.result.failure_reason == LaunchFailureReason.OUTLOOK_RESULT_NOT_FOUND


# --- Click gating: no click without approval, dual foreground checks ---

def test_click_refused_without_human_approval():
    steps = _steps()
    steps.result.converted_x, steps.result.converted_y = 100, 100
    with pytest.raises(RuntimeError):
        steps.click_outlook_result()


def test_click_blocked_when_search_state_lost_before_move():
    steps = _steps()
    steps.result.human_target_approved = True
    steps.result.converted_x, steps.result.converted_y = 100, 100
    with patch(f"{MODULE}.pyautogui") as mock_pyautogui, \
         patch(f"{MODULE}.get_foreground_window_title", return_value="Visual Studio Code"):
        mock_pyautogui.FAILSAFE = True
        assert steps.click_outlook_result() is False
        mock_pyautogui.moveTo.assert_not_called()
        mock_pyautogui.click.assert_not_called()
    assert steps.result.failure_reason == LaunchFailureReason.SEARCH_STATE_LOST_BEFORE_CLICK


def test_click_executed_once_when_search_state_holds():
    steps = _steps()
    steps.result.human_target_approved = True
    steps.result.converted_x, steps.result.converted_y = 100, 100
    with patch(f"{MODULE}.pyautogui") as mock_pyautogui, \
         patch(f"{MODULE}.get_foreground_window_title", return_value="Search"):
        mock_pyautogui.FAILSAFE = True
        assert steps.click_outlook_result() is True
        mock_pyautogui.moveTo.assert_called_once()
        mock_pyautogui.click.assert_called_once_with()
    assert steps.result.mouse_click_count == 1


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
    steps.provider.analyze_screen.return_value = call
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
    steps.provider.analyze_screen.return_value = call
    with patch(f"{MODULE}.time.sleep"), patch(f"{MODULE}.get_foreground_window_title", return_value="Outlook"), \
         patch(f"{MODULE}.capture_screen", return_value=_capture()):
        assert steps.verify_outlook_readiness() is True
    assert steps.result.ready_for_interaction is True
    assert len(steps.result.readiness_attempts) == 1


# --- Abort ---

def test_abort_blocks_before_windows_key():
    controller = AbortController()
    controller.request_abort()
    steps = OutlookLaunchSteps(controller, MagicMock(), "gemini-3.6-flash")
    assert steps.press_windows_key() is False
    assert steps.result.result == "ABORTED"
    assert steps.result.failure_reason == LaunchFailureReason.USER_ABORTED
