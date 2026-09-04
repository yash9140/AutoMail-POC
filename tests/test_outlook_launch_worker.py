"""RND-009B OutlookLaunchWorker tests — bounded session approval design.
Everything the worker touches (pyautogui, provider, foreground checks,
screen capture) is mocked; run() is called directly (no real QThread
needed to exercise its logic) — no real automation occurs anywhere here.
"""

import inspect
import itertools
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.safety.abort_controller import AbortController  # noqa: E402
from app.vision.service import VisionService  # noqa: E402
from app.workers.outlook_launch_worker import OutlookLaunchWorker  # noqa: E402

STEPS_MODULE = "app.outlook.launch"

_ENV_INFO_MATCH = {"pyautogui_width": 1920, "pyautogui_height": 1080, "dimensions_match": True}


def test_worker_has_no_mid_run_approval_signal_or_blocking_mechanism():
    """Structural: the earlier mid-run approval_required signal + blocking
    threading.Event design was removed in favor of bounded upfront
    approval — proves it stays removed."""
    assert not hasattr(OutlookLaunchWorker, "approval_required")
    source = inspect.getsource(sys.modules[OutlookLaunchWorker.__module__])
    assert "threading" not in source
    assert "_approval_event" not in source
    assert "submit_approval" not in source


def _mock_provider_ctx(grounding_ok=True, verify_ok=True):
    grounding_response = MagicMock(
        parsed_json={"search_visible": True, "target_visible": True, "target_type": "desktop_app",
                     "visible_label": "Outlook",
                     "bbox": [250.0, 400.0, 350.0, 600.0], "confidence": 0.95, "reason": "clear"},
        raw_text="{}", model="gemini-3.6-flash", latency_ms=100.0, input_tokens=10, output_tokens=5,
    )
    verify_response = MagicMock(
        parsed_json={"application": "Outlook", "outlook_visible": verify_ok, "splash_screen_visible": not verify_ok,
                     "ready_for_interaction": verify_ok, "detected_state": "inbox", "confidence": 0.95, "reason": "ok"},
        raw_text="{}", model="gemini-3.6-flash", latency_ms=100.0, input_tokens=10, output_tokens=5,
    )
    mock_provider = MagicMock()
    mock_provider.analyze_screen.side_effect = [grounding_response, verify_response]
    return mock_provider


def test_bounded_approval_granted_true_runs_end_to_end_to_success():
    signals = {"success": [], "failure": [], "aborted": []}
    worker = OutlookLaunchWorker(AbortController(), bounded_approval_granted=True)
    worker.success.connect(lambda r: signals["success"].append(r))
    worker.failure.connect(lambda *a: signals["failure"].append(a))
    worker.aborted.connect(lambda *a: signals["aborted"].append(a))

    # "Search" for the pre-click checks (capture, before-move, before-click),
    # then an Outlook-containing title once polling starts — using a single
    # constant title for the whole run would either fail the pre-click
    # search-state check or spin the poll loop in a real ~18s busy-wait
    # until OUTLOOK_LAUNCH_TIMEOUT_SECONDS actually elapses on the wall
    # clock (time.sleep is mocked to a no-op, but time.monotonic() is not).
    titles = itertools.chain(["Search", "Search", "Search"], itertools.repeat("Inbox - Yash Dhanraj - Outlook"))

    mock_provider = _mock_provider_ctx()
    with patch("app.workers.outlook_launch_worker._provider", return_value=(VisionService(mock_provider, fallback=None), "gemini-3.6-flash")), \
         patch(f"{STEPS_MODULE}.pyautogui") as mock_pyautogui, \
         patch(f"{STEPS_MODULE}.time.sleep"), \
         patch(f"{STEPS_MODULE}.get_foreground_window_title", side_effect=titles), \
         patch(f"{STEPS_MODULE}.get_foreground_hwnd", return_value=12345), \
         patch(f"{STEPS_MODULE}.is_maximized", return_value=True), \
         patch(f"{STEPS_MODULE}.maximize") as mock_maximize, \
         patch(f"{STEPS_MODULE}.get_environment_info", return_value=_ENV_INFO_MATCH), \
         patch(f"{STEPS_MODULE}.capture_screen", return_value=MagicMock(filename="x.png", path="x.png", width=1920, height=1080)):
        mock_pyautogui.FAILSAFE = True
        worker.run()
        mock_maximize.assert_not_called()  # already maximized — no action taken

    assert signals["success"], f"expected success, got failure={signals['failure']} aborted={signals['aborted']}"
    assert not signals["failure"]
    result = signals["success"][0]
    assert result["result"] == "PASS"
    assert result["send_click_count"] == 0


def test_bounded_approval_granted_false_stops_before_click():
    """bounded_approval_granted=False maps through record_human_approval()
    exactly like an explicit human rejection would (result="ABORTED"),
    since it represents the same thing: this run is not authorized to
    click anything — so the worker emits `aborted`, not `failure`."""
    signals = {"success": [], "aborted": []}
    worker = OutlookLaunchWorker(AbortController(), bounded_approval_granted=False)
    worker.success.connect(lambda r: signals["success"].append(r))
    worker.aborted.connect(lambda *a: signals["aborted"].append(a))

    mock_provider = _mock_provider_ctx()
    with patch("app.workers.outlook_launch_worker._provider", return_value=(VisionService(mock_provider, fallback=None), "gemini-3.6-flash")), \
         patch(f"{STEPS_MODULE}.pyautogui") as mock_pyautogui, \
         patch(f"{STEPS_MODULE}.time.sleep"), \
         patch(f"{STEPS_MODULE}.get_foreground_window_title", return_value="Search"), \
         patch(f"{STEPS_MODULE}.get_environment_info", return_value=_ENV_INFO_MATCH), \
         patch(f"{STEPS_MODULE}.capture_screen", return_value=MagicMock(filename="x.png", path="x.png", width=1920, height=1080)):
        mock_pyautogui.FAILSAFE = True
        worker.run()

        mock_pyautogui.click.assert_not_called()
        mock_pyautogui.moveTo.assert_not_called()

    assert not signals["success"]
    assert signals["aborted"]


def test_grounding_failure_stops_before_any_approval_is_even_considered():
    """If Windows Search isn't visible / no Outlook result is found, the
    run stops via OutlookLaunchSteps' own validation gates — bounded
    approval being granted doesn't override that."""
    signals = {"success": [], "failure": []}
    worker = OutlookLaunchWorker(AbortController(), bounded_approval_granted=True)
    worker.success.connect(lambda r: signals["success"].append(r))
    worker.failure.connect(lambda *a: signals["failure"].append(a))

    no_result_response = MagicMock(
        parsed_json={"search_visible": True, "target_visible": False, "target_type": "other", "visible_label": "",
                     "bbox": None, "confidence": 0.9, "reason": "no Outlook result found"},
        raw_text="{}", model="gemini-3.6-flash", latency_ms=100.0, input_tokens=10, output_tokens=5,
    )
    mock_provider = MagicMock()
    mock_provider.analyze_screen.return_value = no_result_response

    with patch("app.workers.outlook_launch_worker._provider", return_value=(VisionService(mock_provider, fallback=None), "gemini-3.6-flash")), \
         patch(f"{STEPS_MODULE}.pyautogui") as mock_pyautogui, \
         patch(f"{STEPS_MODULE}.time.sleep"), \
         patch(f"{STEPS_MODULE}.get_foreground_window_title", return_value="Search"), \
         patch(f"{STEPS_MODULE}.get_environment_info", return_value=_ENV_INFO_MATCH), \
         patch(f"{STEPS_MODULE}.capture_screen", return_value=MagicMock(filename="x.png", path="x.png", width=1920, height=1080)):
        mock_pyautogui.FAILSAFE = True
        worker.run()

        mock_pyautogui.click.assert_not_called()

    assert not signals["success"]
    assert signals["failure"][0][0] == "OUTLOOK_RESULT_NOT_FOUND"
