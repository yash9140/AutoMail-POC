"""RND-009C FindOpenEmailWorker tests. Everything the worker touches
(pyautogui, provider, foreground checks, screen capture) is mocked;
run() is called directly — no real automation occurs anywhere here.
"""

import itertools
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.outlook.find_email import TARGET_EMAIL_SENDER, TARGET_EMAIL_SUBJECT  # noqa: E402
from app.safety.abort_controller import AbortController  # noqa: E402
from app.vision.service import VisionService  # noqa: E402
from app.workers.find_open_email_worker import FindOpenEmailWorker  # noqa: E402
from tests._capture_test_utils import real_capture_image_path, to_crop_relative_bbox  # noqa: E402

STEPS_MODULE = "app.outlook.find_email"
LAUNCH_MODULE = "app.outlook.launch"
WORKER_MODULE = "app.workers.find_open_email_worker"


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
                "row_bbox": to_crop_relative_bbox([480.0, 300.0, 520.0, 480.0]), "confidence": 0.95,
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


def test_worker_has_no_send_reply_or_typing_signal_or_method():
    for forbidden in ("send", "reply", "type_reply"):
        assert not hasattr(FindOpenEmailWorker, forbidden)


def test_bounded_approval_true_runs_end_to_end_to_success():
    signals = {"success": [], "failure": [], "aborted": [], "current_step": []}
    worker = FindOpenEmailWorker(AbortController(), bounded_approval_granted=True)
    worker.success.connect(lambda r: signals["success"].append(r))
    worker.failure.connect(lambda *a: signals["failure"].append(a))
    worker.aborted.connect(lambda *a: signals["aborted"].append(a))
    worker.current_step.connect(lambda s: signals["current_step"].append(s))

    mock_provider = MagicMock()
    mock_provider.analyze_screen.side_effect = [
        _search_grounding(), _readiness_ready(), _email_grounding(), _email_open_verified(),
    ]

    titles = itertools.chain(
        ["Search", "Search", "Search"],  # capture, before-move, before-click (Outlook search result)
        itertools.repeat("Inbox - Outlook"),  # poll, readiness, email grounding/click, verification
    )

    with patch(f"{WORKER_MODULE}._provider", return_value=(VisionService(mock_provider, fallback=None), "gemini-3.6-flash")), \
         patch(f"{LAUNCH_MODULE}.pyautogui") as mock_launch_pyautogui, \
         patch(f"{STEPS_MODULE}.pyautogui") as mock_email_pyautogui, \
         patch(f"{LAUNCH_MODULE}.time.sleep"), patch(f"{STEPS_MODULE}.time.sleep"), \
         patch(f"{LAUNCH_MODULE}.get_foreground_window_title", side_effect=titles), \
         patch(f"{STEPS_MODULE}.get_foreground_window_title", return_value="Inbox - Outlook"), \
         patch(f"{LAUNCH_MODULE}.get_foreground_hwnd", return_value=12345), \
         patch(f"{LAUNCH_MODULE}.is_maximized", return_value=True), \
         patch(f"{LAUNCH_MODULE}.maximize") as mock_maximize, \
         patch(f"{LAUNCH_MODULE}.capture_screen", return_value=MagicMock(filename="x.png", path="x.png", width=1920, height=1080)), \
         patch(f"{STEPS_MODULE}.capture_screen", return_value=MagicMock(filename="y.png", path=real_capture_image_path(1920, 1080, name="y.png"), width=1920, height=1080)):
        mock_launch_pyautogui.FAILSAFE = True
        mock_email_pyautogui.FAILSAFE = True
        worker.run()
        mock_maximize.assert_not_called()  # already maximized — no action taken

    assert signals["success"], f"expected success, got failure={signals['failure']} aborted={signals['aborted']}"
    result = signals["success"][0]
    assert result["result"] == "PASS"
    assert result["send_click_count"] == 0
    assert "FINDING_EMAIL" in signals["current_step"]
    assert "EMAIL_OPENED" in signals["current_step"]
    # pre-RND-009D hardening: total_elapsed_ms populated on PASS.
    # (The elapsed->=vision-latency relationship is rigorously covered
    # with controlled time.monotonic() values at the steps-unit level —
    # test_elapsed_ms_is_wall_clock_not_just_vision_latency in
    # test_find_open_email_steps.py — not here, where time.sleep is
    # mocked to a no-op and real wall-clock elapsed is just fast Python
    # execution, not comparable to the mocked call latencies.)
    assert result["total_elapsed_ms"] is not None
    assert result["total_elapsed_ms"] >= 0
    assert result["session_start"] is not None
    assert result["session_end"] is not None
    mock_email_pyautogui.click.assert_called_once()  # exactly one email-row click


def test_bounded_approval_false_never_reaches_email_finding():
    signals = {"success": [], "aborted": []}
    worker = FindOpenEmailWorker(AbortController(), bounded_approval_granted=False)
    worker.success.connect(lambda r: signals["success"].append(r))
    worker.aborted.connect(lambda *a: signals["aborted"].append(a))

    with patch(f"{WORKER_MODULE}._provider", return_value=(MagicMock(), "gemini-3.6-flash")):
        worker.run()

    assert not signals["success"]
    assert signals["aborted"]


def test_target_email_not_visible_stops_after_bounded_scroll():
    from app.config.settings import MAX_MESSAGE_LIST_SCROLL_ATTEMPTS

    signals = {"success": [], "failure": [], "metrics": []}
    worker = FindOpenEmailWorker(AbortController(), bounded_approval_granted=True)
    worker.success.connect(lambda r: signals["success"].append(r))
    worker.failure.connect(lambda *a: signals["failure"].append(a))
    worker.metrics_update.connect(lambda m: signals["metrics"].append(m))

    mock_provider = MagicMock()
    not_visible = MagicMock(
        parsed_json={"outlook_visible": True, "message_list_visible": True, "target_visible": False,
                     "candidate_count": 0, "candidates": [], "more_content_below": True,
                     "reason": "not in current view"},
        raw_text="{}", model="gemini-3.6-flash", latency_ms=100.0, input_tokens=10, output_tokens=5,
    )
    mock_provider.analyze_screen.side_effect = (
        [_search_grounding(), _readiness_ready()] + [not_visible] * MAX_MESSAGE_LIST_SCROLL_ATTEMPTS
    )

    titles = itertools.chain(["Search", "Search", "Search"], itertools.repeat("Inbox - Outlook"))

    with patch(f"{WORKER_MODULE}._provider", return_value=(VisionService(mock_provider, fallback=None), "gemini-3.6-flash")), \
         patch(f"{LAUNCH_MODULE}.pyautogui") as mock_launch_pyautogui, \
         patch(f"{STEPS_MODULE}.pyautogui") as mock_email_pyautogui, \
         patch("app.automation.scrolling.pyautogui") as mock_scroll_pyautogui, \
         patch(f"{LAUNCH_MODULE}.time.sleep"), patch(f"{STEPS_MODULE}.time.sleep"), \
         patch(f"{LAUNCH_MODULE}.get_foreground_window_title", side_effect=titles), \
         patch(f"{STEPS_MODULE}.get_foreground_window_title", return_value="Inbox - Outlook"), \
         patch(f"{LAUNCH_MODULE}.get_foreground_hwnd", return_value=12345), \
         patch(f"{LAUNCH_MODULE}.is_maximized", return_value=True), \
         patch(f"{LAUNCH_MODULE}.maximize"), \
         patch(f"{LAUNCH_MODULE}.capture_screen", return_value=MagicMock(filename="x.png", path="x.png", width=1920, height=1080)), \
         patch(f"{STEPS_MODULE}.capture_screen", return_value=MagicMock(filename="y.png", path=real_capture_image_path(1920, 1080, name="y.png"), width=1920, height=1080)):
        mock_launch_pyautogui.FAILSAFE = True
        mock_email_pyautogui.FAILSAFE = True
        mock_scroll_pyautogui.FAILSAFE = True
        worker.run()
        assert mock_scroll_pyautogui.scroll.call_count == MAX_MESSAGE_LIST_SCROLL_ATTEMPTS - 1

        mock_email_pyautogui.click.assert_not_called()

    assert not signals["success"]
    assert signals["failure"][0][0] == "TARGET_EMAIL_NOT_FOUND"
    # pre-RND-009D hardening: total_elapsed_ms populated on FAIL too, not just PASS
    final_metrics = signals["metrics"][-1]
    assert final_metrics["result"] == "FAIL"
    assert final_metrics["total_elapsed_ms"] is not None
    assert final_metrics["session_start"] is not None
    assert final_metrics["session_end"] is not None


def test_elapsed_ms_populated_on_abort():
    signals = {"success": [], "aborted": [], "metrics": []}
    worker = FindOpenEmailWorker(AbortController(), bounded_approval_granted=True)
    worker.success.connect(lambda r: signals["success"].append(r))
    worker.aborted.connect(lambda *a: signals["aborted"].append(a))
    worker.metrics_update.connect(lambda m: signals["metrics"].append(m))

    # Abort is requested before anything runs — the very first check_abort
    # inside run_launch_and_readiness() (press_windows_key) catches it.
    worker.abort_controller.request_abort()

    with patch(f"{WORKER_MODULE}._provider", return_value=(MagicMock(), "gemini-3.6-flash")):
        worker.run()

    assert not signals["success"]
    assert signals["aborted"]
    final_metrics = signals["metrics"][-1]
    assert final_metrics["result"] == "ABORTED"
    assert final_metrics["total_elapsed_ms"] is not None
    assert final_metrics["session_start"] is not None
    assert final_metrics["session_end"] is not None
