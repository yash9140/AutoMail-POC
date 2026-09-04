"""Phase 2 — Outlook launch + ready + maximize.

Covers app/outlook/launch.py::OutlookLaunchSteps.enforce_maximized() and
its integration with verify_outlook_readiness(). ctypes.windll.user32,
pyautogui, the vision provider, and screen capture are all mocked — no
real desktop/network activity happens anywhere in this file.
"""

import sys
import time as time_module
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.outlook.launch import OutlookLaunchSteps  # noqa: E402
from app.playbook.failure_reasons import LaunchFailureReason  # noqa: E402
from app.safety.abort_controller import AbortController  # noqa: E402
from app.vision.service import VisionService  # noqa: E402

MODULE = "app.outlook.launch"


def _steps() -> OutlookLaunchSteps:
    return OutlookLaunchSteps(AbortController(), VisionService(MagicMock(), fallback=None), "gemini-3.6-flash")


def _capture(width=1920, height=1080, filename="post_max.png"):
    return MagicMock(filename=filename, path=filename, width=width, height=height)


def _readiness_call(*, outlook_visible=True, splash=False, ready=True, confidence=0.95):
    return MagicMock(
        parsed_json={"application": "Microsoft Outlook", "outlook_visible": outlook_visible,
                     "splash_screen_visible": splash, "ready_for_interaction": ready,
                     "detected_state": "inbox with folder pane, message list, reading pane, ribbon",
                     "confidence": confidence, "reason": "ok"},
        raw_text="{}", model="gemini-3.6-flash", latency_ms=100.0, input_tokens=10, output_tokens=5,
    )


# --- A. Already maximized ---

def test_already_maximized_skips_maximize_call_and_continues():
    steps = _steps()
    with patch(f"{MODULE}.get_foreground_window_title", return_value="Outlook"), \
         patch(f"{MODULE}.get_foreground_hwnd", return_value=111), \
         patch(f"{MODULE}.is_maximized", return_value=True) as mock_is_max, \
         patch(f"{MODULE}.maximize") as mock_maximize, \
         patch(f"{MODULE}.time.sleep") as mock_sleep:
        assert steps.enforce_maximized() is True
        mock_is_max.assert_called_once_with(111)
        mock_maximize.assert_not_called()
        mock_sleep.assert_not_called()  # no stabilization wait when nothing changed
    assert steps.result.maximize_required is False
    assert steps.result.maximize_executed is False


# --- B. Restored -> maximize called exactly once, stabilization + fresh screenshot ---

def test_restored_window_triggers_maximize_exactly_once_with_stabilization():
    steps = _steps()
    with patch(f"{MODULE}.get_foreground_window_title", return_value="Outlook"), \
         patch(f"{MODULE}.get_foreground_hwnd", return_value=222), \
         patch(f"{MODULE}.is_maximized", return_value=False), \
         patch(f"{MODULE}.maximize") as mock_maximize, \
         patch(f"{MODULE}.time.sleep") as mock_sleep, \
         patch(f"{MODULE}.capture_screen", return_value=_capture()) as mock_capture:
        assert steps.enforce_maximized() is True
        mock_maximize.assert_called_once_with(222)
        mock_sleep.assert_called_once()  # stabilization wait
        mock_capture.assert_called_once()  # fresh screenshot taken afterward
    assert steps.result.maximize_required is True
    assert steps.result.maximize_executed is True
    assert steps.result.maximize_duration_ms is not None
    assert steps.result.post_maximize_screenshot == "post_max.png"


# --- C. Maximize never uses mouse coordinates ---

def test_maximize_never_calls_pyautogui():
    steps = _steps()
    with patch(f"{MODULE}.get_foreground_window_title", return_value="Outlook"), \
         patch(f"{MODULE}.get_foreground_hwnd", return_value=333), \
         patch(f"{MODULE}.is_maximized", return_value=False), \
         patch(f"{MODULE}.maximize"), \
         patch(f"{MODULE}.time.sleep"), \
         patch(f"{MODULE}.capture_screen", return_value=_capture()), \
         patch(f"{MODULE}.pyautogui") as mock_pyautogui:
        mock_pyautogui.FAILSAFE = True
        assert steps.enforce_maximized() is True
        mock_pyautogui.moveTo.assert_not_called()
        mock_pyautogui.click.assert_not_called()


def test_maximize_source_never_references_pyautogui_for_the_maximize_action():
    import inspect

    from app.safety import foreground

    source = inspect.getsource(foreground.maximize)
    assert "pyautogui" not in source
    assert "moveTo" not in source
    assert "click" not in source


# --- D. Foreground lost before maximize ---

def test_foreground_lost_before_maximize_blocks_action():
    steps = _steps()
    with patch(f"{MODULE}.get_foreground_window_title", return_value="Visual Studio Code"), \
         patch(f"{MODULE}.maximize") as mock_maximize:
        assert steps.enforce_maximized() is False
        mock_maximize.assert_not_called()
    assert steps.result.failure_reason == LaunchFailureReason.OUTLOOK_FOREGROUND_LOST
    assert steps.result.maximize_required is None  # never even checked


# --- E. Foreground lost after maximize (during stabilization) ---

def test_foreground_lost_after_maximize_blocks_readiness():
    steps = _steps()
    titles = iter(["Outlook", "Visual Studio Code"])  # ok before maximize, lost after stabilization
    with patch(f"{MODULE}.get_foreground_window_title", side_effect=lambda: next(titles)), \
         patch(f"{MODULE}.get_foreground_hwnd", return_value=444), \
         patch(f"{MODULE}.is_maximized", return_value=False), \
         patch(f"{MODULE}.maximize"), \
         patch(f"{MODULE}.time.sleep"), \
         patch(f"{MODULE}.capture_screen") as mock_capture:
        assert steps.enforce_maximized() is False
        mock_capture.assert_not_called()  # no post-maximize screenshot once foreground is lost
    assert steps.result.failure_reason == LaunchFailureReason.OUTLOOK_FOREGROUND_LOST
    assert steps.result.maximize_executed is True  # the action itself did happen


# --- F. Splash screen never counted as ready (combined with maximize step) ---

def test_splash_screen_after_maximize_does_not_reach_ready():
    steps = _steps()
    steps.result.foreground_verified = True
    with patch(f"{MODULE}.get_foreground_window_title", return_value="Outlook"), \
         patch(f"{MODULE}.get_foreground_hwnd", return_value=555), \
         patch(f"{MODULE}.is_maximized", return_value=True), \
         patch(f"{MODULE}.time.sleep"), \
         patch(f"{MODULE}.capture_screen", return_value=_capture()):
        assert steps.enforce_maximized() is True

    steps.vision.primary.analyze_screen.return_value = _readiness_call(outlook_visible=True, splash=True, ready=False)
    with patch(f"{MODULE}.time.sleep"), patch(f"{MODULE}.get_foreground_window_title", return_value="Outlook"), \
         patch(f"{MODULE}.capture_screen", return_value=_capture()):
        assert steps.verify_outlook_readiness() is False
    assert steps.result.ready_for_interaction is False
    assert steps.result.failure_reason == LaunchFailureReason.OUTLOOK_READY_TIMEOUT


# --- G. Fully loaded Outlook reaches OUTLOOK_READY-equivalent (ready_for_interaction) ---

def test_fully_loaded_outlook_after_maximize_reaches_ready():
    steps = _steps()
    steps.result.foreground_verified = True
    with patch(f"{MODULE}.get_foreground_window_title", return_value="Outlook"), \
         patch(f"{MODULE}.get_foreground_hwnd", return_value=666), \
         patch(f"{MODULE}.is_maximized", return_value=False), \
         patch(f"{MODULE}.maximize"), \
         patch(f"{MODULE}.time.sleep"), \
         patch(f"{MODULE}.capture_screen", return_value=_capture()):
        assert steps.enforce_maximized() is True

    steps.vision.primary.analyze_screen.return_value = _readiness_call(outlook_visible=True, splash=False, ready=True)
    with patch(f"{MODULE}.time.sleep"), patch(f"{MODULE}.get_foreground_window_title", return_value="Outlook"), \
         patch(f"{MODULE}.capture_screen", return_value=_capture()):
        assert steps.verify_outlook_readiness() is True
    assert steps.result.ready_for_interaction is True
    assert steps.result.result == "PASS"
    assert steps.result.outlook_ready_at is not None


# --- H. Readiness retry: no re-launch, no re-maximize ---

def test_readiness_retry_does_not_relaunch_or_remaximize():
    steps = _steps()
    steps.result.foreground_verified = True
    with patch(f"{MODULE}.get_foreground_window_title", return_value="Outlook"), \
         patch(f"{MODULE}.get_foreground_hwnd", return_value=777), \
         patch(f"{MODULE}.is_maximized", return_value=True), \
         patch(f"{MODULE}.maximize") as mock_maximize, \
         patch(f"{MODULE}.time.sleep"), \
         patch(f"{MODULE}.capture_screen", return_value=_capture()):
        assert steps.enforce_maximized() is True
        mock_maximize.assert_not_called()

    still_loading = _readiness_call(outlook_visible=True, splash=True, ready=False)
    now_ready = _readiness_call(outlook_visible=True, splash=False, ready=True)
    steps.vision.primary.analyze_screen.side_effect = [still_loading, now_ready]

    with patch(f"{MODULE}.pyautogui") as mock_pyautogui, patch(f"{MODULE}.time.sleep"), \
         patch(f"{MODULE}.get_foreground_window_title", return_value="Outlook"), \
         patch(f"{MODULE}.capture_screen", return_value=_capture()), \
         patch(f"{MODULE}.maximize") as mock_maximize2:
        mock_pyautogui.FAILSAFE = True
        assert steps.verify_outlook_readiness() is True
        mock_pyautogui.press.assert_not_called()  # no re-launch (Windows key)
        mock_pyautogui.click.assert_not_called()  # no re-click
        mock_maximize2.assert_not_called()  # no re-maximize
    assert len(steps.result.readiness_attempts) == 2


# --- I. Readiness timeout -> safe failure ---

def test_readiness_timeout_is_a_safe_failure():
    steps = _steps()
    steps.result.foreground_verified = True
    steps.vision.primary.analyze_screen.return_value = _readiness_call(outlook_visible=True, splash=True, ready=False)
    with patch(f"{MODULE}.time.sleep"), patch(f"{MODULE}.get_foreground_window_title", return_value="Outlook"), \
         patch(f"{MODULE}.capture_screen", return_value=_capture()):
        assert steps.verify_outlook_readiness() is False
    assert steps.result.result == "FAIL"
    assert steps.result.failure_reason == LaunchFailureReason.OUTLOOK_READY_TIMEOUT


# --- J. Provider error classified PROVIDER_ERROR, not a semantic readiness failure ---

def test_provider_error_during_readiness_classified_as_technical_not_semantic():
    from app.fallback.classifications import classify_terminal_state
    from app.playbook.states import PlaybookState
    from app.vision.providers.base import RateLimitError

    steps = _steps()
    steps.result.foreground_verified = True
    steps.vision.primary.analyze_screen.side_effect = RateLimitError("HTTP 429: rate limited")

    with patch(f"{MODULE}.time.sleep"), patch(f"{MODULE}.get_foreground_window_title", return_value="Outlook"), \
         patch(f"{MODULE}.capture_screen", return_value=_capture()):
        assert steps.verify_outlook_readiness() is False

    assert steps.result.result == "ERROR"
    assert steps.result.failure_reason == LaunchFailureReason.TECHNICAL_PROVIDER_ERROR
    assert classify_terminal_state(steps.result.failure_reason) == PlaybookState.PROVIDER_ERROR


def test_provider_error_during_maximize_screenshot_capture_is_technical():
    from app.automation.screen_capture import ScreenCaptureError

    steps = _steps()
    with patch(f"{MODULE}.get_foreground_window_title", return_value="Outlook"), \
         patch(f"{MODULE}.get_foreground_hwnd", return_value=888), \
         patch(f"{MODULE}.is_maximized", return_value=False), \
         patch(f"{MODULE}.maximize"), \
         patch(f"{MODULE}.time.sleep"), \
         patch(f"{MODULE}.capture_screen", side_effect=ScreenCaptureError("disk full")):
        assert steps.enforce_maximized() is False
    assert steps.result.failure_reason == LaunchFailureReason.TECHNICAL_PROVIDER_ERROR


def test_provider_retried_once_before_classified_as_error():
    from app.vision.providers.base import ProviderTimeoutError

    steps = _steps()
    steps.result.foreground_verified = True
    ready_response = _readiness_call()
    steps.vision.primary.analyze_screen.side_effect = [ProviderTimeoutError("timed out"), ready_response]

    with patch(f"{MODULE}.time.sleep"), patch(f"{MODULE}.get_foreground_window_title", return_value="Outlook"), \
         patch(f"{MODULE}.capture_screen", return_value=_capture()):
        assert steps.verify_outlook_readiness() is True
    assert steps.result.provider_retries == 1
    assert steps.result.result == "PASS"


# --- K. User abort during wait stops safely ---

def test_abort_during_maximize_stabilization_stops_safely():
    steps = _steps()
    controller = steps.abort_controller

    def _sleep_then_abort(_seconds):
        controller.request_abort()

    with patch(f"{MODULE}.get_foreground_window_title", return_value="Outlook"), \
         patch(f"{MODULE}.get_foreground_hwnd", return_value=999), \
         patch(f"{MODULE}.is_maximized", return_value=False), \
         patch(f"{MODULE}.maximize") as mock_maximize, \
         patch(f"{MODULE}.time.sleep", side_effect=_sleep_then_abort), \
         patch(f"{MODULE}.capture_screen") as mock_capture:
        assert steps.enforce_maximized() is False
        mock_capture.assert_not_called()  # aborted before the post-maximize screenshot
    mock_maximize.assert_called_once()  # the maximize action itself had already happened
    assert steps.result.result == "ABORTED"
    assert steps.result.failure_reason == LaunchFailureReason.USER_ABORTED


def test_abort_before_maximize_check_stops_before_any_action():
    steps = _steps()
    steps.abort_controller.request_abort()
    with patch(f"{MODULE}.maximize") as mock_maximize, patch(f"{MODULE}.get_foreground_window_title") as mock_title:
        assert steps.enforce_maximized() is False
        mock_maximize.assert_not_called()
        mock_title.assert_not_called()  # abort checked before even the foreground read
    assert steps.result.result == "ABORTED"


def test_abort_during_readiness_wait_stops_safely():
    steps = _steps()
    steps.result.foreground_verified = True
    controller = steps.abort_controller

    def _sleep_then_abort(_seconds):
        controller.request_abort()

    with patch(f"{MODULE}.time.sleep", side_effect=_sleep_then_abort), \
         patch(f"{MODULE}.get_foreground_window_title", return_value="Outlook"):
        assert steps.verify_outlook_readiness() is False
    assert steps.result.result == "ABORTED"
    assert steps.result.failure_reason == LaunchFailureReason.USER_ABORTED
    assert len(steps.result.readiness_attempts) == 0  # never even captured


# --- L. No transition to FINDING_EMAIL during Phase 2 ---

def test_launch_module_never_mentions_finding_email():
    import inspect

    from app.outlook import launch as launch_mod

    source = inspect.getsource(launch_mod)
    assert "FINDING_EMAIL" not in source
    assert "find_email" not in source.lower().replace("app.outlook.find_email", "")


# --- M. No email/Reply/type/Send actions reachable from this module ---

def test_launch_module_has_no_email_reply_type_or_send_methods():
    forbidden_methods = (
        "ground_target_email", "click_target_email", "verify_email_opened",
        "prepare_reply_editor", "generate_draft", "type_draft", "verify_draft",
        "ground_send", "execute_send", "click_send",
    )
    for name in forbidden_methods:
        assert not hasattr(OutlookLaunchSteps, name)


def test_launch_module_pyautogui_usage_unchanged_by_maximize_addition():
    """Structural: enforce_maximized() adds zero new pyautogui call
    sites — maximize is ctypes-only. The module's total pyautogui
    surface area (Windows-key press, one write, one moveTo, one click)
    stays exactly what Phase 1 already proved."""
    import inspect

    from app.outlook import launch as launch_mod

    source = inspect.getsource(launch_mod)
    normalized = source.replace('"', "'")
    assert normalized.count("pyautogui.press('win')") == 1
    assert normalized.count("pyautogui.write(") == 1
    assert source.count("pyautogui.moveTo(") == 1
    assert normalized.count("pyautogui.click()") == 1
