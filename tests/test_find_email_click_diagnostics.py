"""Target-email physical-click observability regression tests
(2026-09-06 live-diagnosis follow-up).

A live run reached EMAIL_LAYOUT_SAFETY safe=True (bbox valid, click
point computed) but then EMAIL_OPEN_VERIFICATION_FAILED — and the log
had no explicit line proving pyautogui.click() actually executed at the
expected coordinates, so it was impossible to tell from logs alone
whether the click never fired, fired at an unhelpful point, was blocked
by foreground/abort state, or executed but Outlook didn't react.

click_target_email() itself was ALREADY structurally single-click-only
(no retry, no branch that returns True without calling pyautogui.click()
first) — this file both proves that unconditionally and pins the new
diagnostic log lines (EMAIL_CLICK_PRECHECK / EMAIL_MOUSE_MOVE_ABOUT_TO_
EXECUTE / EMAIL_MOUSE_MOVE_EXECUTED / EMAIL_MOUSE_POSITION_CONFIRMED /
EMAIL_CLICK_ABOUT_TO_EXECUTE / EMAIL_CLICK_EXECUTED /
EMAIL_POST_CLICK_VERIFICATION_START) that make this observable going
forward. No real mouse/keyboard/network/provider call happens anywhere
in this file, and no live Outlook run is performed.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.settings import (  # noqa: E402
    LEFT_SIDEBAR_MAX_X_FRACTION,
    MESSAGE_LIST_RIGHT_MAX_X_FRACTION,
    MESSAGE_ROW_CLICK_SAFE_ZONE_FRACTION,
)
from app.outlook.find_email import FindOpenEmailSteps  # noqa: E402
from app.playbook.failure_reasons import LaunchFailureReason  # noqa: E402
from app.safety.abort_controller import AbortController  # noqa: E402
from app.vision.grounding import normalize_1000_to_pixels  # noqa: E402
from app.vision.models import EmailCandidate  # noqa: E402
from app.vision.service import VisionService  # noqa: E402

MODULE = "app.outlook.find_email"

IMAGE_WIDTH = 1920
IMAGE_HEIGHT = 1080

# The exact live geometry from this task's report.
LIVE_REFINED_BBOX = [378.0, 258.0, 456.0, 548.0]


def _steps(target_sender="Yash Dhanraj", target_subject="") -> FindOpenEmailSteps:
    return FindOpenEmailSteps(
        AbortController(), VisionService(MagicMock(), fallback=None), "test-model",
        target_sender=target_sender, target_subject=target_subject,
    )


def _steps_with_click_point(x: int, y: int) -> FindOpenEmailSteps:
    steps = _steps()
    steps.result.email_converted_x, steps.result.email_converted_y = x, y
    return steps


def _capture(width=IMAGE_WIDTH, height=IMAGE_HEIGHT, filename="inbox.png"):
    return MagicMock(filename=filename, path=filename, width=width, height=height)


# --- A: safe geometry + foreground valid -> one move, exactly one click ---

def test_A_valid_foreground_moves_once_and_clicks_exactly_once(caplog):
    steps = _steps_with_click_point(829, 450)
    with patch(f"{MODULE}.pyautogui") as mock_pyautogui, \
         patch(f"{MODULE}.get_foreground_window_title", return_value="Mail - Outlook"), \
         caplog.at_level("INFO", logger="app.outlook.find_email.grounding"):
        mock_pyautogui.FAILSAFE = True
        assert steps.click_target_email() is True
        mock_pyautogui.moveTo.assert_called_once_with(829, 450, duration=__import__(
            "app.config.settings", fromlist=["MOVE_DURATION_SECONDS"]).MOVE_DURATION_SECONDS)
        mock_pyautogui.click.assert_called_once_with()
    assert steps.result.email_click_count == 1
    assert steps.result.mouse_click_count == 1

    messages = [r.getMessage() for r in caplog.records]
    for expected in (
        "EMAIL_CLICK_PRECHECK foreground_ok=True abort_requested=False screen_x=829 screen_y=450",
        "EMAIL_MOUSE_MOVE_ABOUT_TO_EXECUTE screen_x=829 screen_y=450",
        "EMAIL_MOUSE_MOVE_EXECUTED screen_x=829 screen_y=450",
        "EMAIL_CLICK_ABOUT_TO_EXECUTE screen_x=829 screen_y=450",
        "EMAIL_CLICK_EXECUTED screen_x=829 screen_y=450",
    ):
        assert any(expected in m for m in messages), f"missing log: {expected}"

    # Log ORDER matters: precheck -> move-about -> move-executed -> click-about -> click-executed.
    order = [m for m in messages if m.split(" ")[0].startswith("EMAIL_")]
    click_related = [
        m for m in order
        if m.startswith((
            "EMAIL_CLICK_PRECHECK", "EMAIL_MOUSE_MOVE_ABOUT_TO_EXECUTE", "EMAIL_MOUSE_MOVE_EXECUTED",
            "EMAIL_CLICK_ABOUT_TO_EXECUTE", "EMAIL_CLICK_EXECUTED",
        ))
    ]
    assert [m.split(" ")[0] for m in click_related] == [
        "EMAIL_CLICK_PRECHECK", "EMAIL_MOUSE_MOVE_ABOUT_TO_EXECUTE", "EMAIL_MOUSE_MOVE_EXECUTED",
        "EMAIL_CLICK_ABOUT_TO_EXECUTE", "EMAIL_CLICK_EXECUTED",
    ]


def test_A_cursor_position_confirmed_logged_diagnostically(caplog):
    steps = _steps_with_click_point(829, 450)
    with patch(f"{MODULE}.pyautogui") as mock_pyautogui, \
         patch(f"{MODULE}.get_foreground_window_title", return_value="Mail - Outlook"), \
         caplog.at_level("INFO", logger="app.outlook.find_email.grounding"):
        mock_pyautogui.FAILSAFE = True
        mock_pyautogui.position.return_value = (829, 450)
        assert steps.click_target_email() is True
    messages = [r.getMessage() for r in caplog.records]
    assert any("EMAIL_MOUSE_POSITION_CONFIRMED" in m and "matches=True" in m for m in messages)


def test_A_cursor_position_mismatch_is_diagnostic_only_never_blocks_click():
    """A wildly different pyautogui.position() read (e.g. the mock's
    default, non-tuple return) must never raise out of click_target_email()
    or prevent the click — this confirmation is diagnostic-only."""
    steps = _steps_with_click_point(829, 450)
    with patch(f"{MODULE}.pyautogui") as mock_pyautogui, \
         patch(f"{MODULE}.get_foreground_window_title", return_value="Mail - Outlook"):
        mock_pyautogui.FAILSAFE = True
        # Default MagicMock().position() return value is not a 2-tuple —
        # proves the try/except around it never breaks the click flow.
        assert steps.click_target_email() is True
        mock_pyautogui.click.assert_called_once_with()


# --- B: foreground invalid -> zero move, zero click ---

def test_B_foreground_invalid_before_move_zero_move_zero_click(caplog):
    steps = _steps_with_click_point(829, 450)
    with patch(f"{MODULE}.pyautogui") as mock_pyautogui, \
         patch(f"{MODULE}.get_foreground_window_title", return_value="Visual Studio Code"), \
         caplog.at_level("INFO", logger="app.outlook.find_email.grounding"):
        mock_pyautogui.FAILSAFE = True
        assert steps.click_target_email() is False
        mock_pyautogui.moveTo.assert_not_called()
        mock_pyautogui.click.assert_not_called()
    assert steps.result.failure_reason == LaunchFailureReason.OUTLOOK_FOREGROUND_LOST
    assert steps.result.email_click_count == 0
    messages = [r.getMessage() for r in caplog.records]
    assert any("EMAIL_CLICK_PRECHECK foreground_ok=False" in m for m in messages)
    assert not any("EMAIL_MOUSE_MOVE_ABOUT_TO_EXECUTE" in m for m in messages)


def test_B_foreground_lost_between_move_and_click_zero_click():
    steps = _steps_with_click_point(829, 450)
    titles = iter(["Mail - Outlook", "Visual Studio Code"])
    with patch(f"{MODULE}.pyautogui") as mock_pyautogui, \
         patch(f"{MODULE}.get_foreground_window_title", side_effect=lambda: next(titles)):
        mock_pyautogui.FAILSAFE = True
        assert steps.click_target_email() is False
        mock_pyautogui.moveTo.assert_called_once()
        mock_pyautogui.click.assert_not_called()
    assert steps.result.failure_reason == LaunchFailureReason.OUTLOOK_FOREGROUND_LOST
    assert steps.result.email_click_count == 0


# --- C: abort requested -> zero click ---

def test_C_abort_requested_before_move_zero_click():
    steps = _steps_with_click_point(829, 450)
    steps.abort_controller.request_abort()
    with patch(f"{MODULE}.pyautogui") as mock_pyautogui, \
         patch(f"{MODULE}.get_foreground_window_title", return_value="Mail - Outlook"):
        mock_pyautogui.FAILSAFE = True
        assert steps.click_target_email() is False
        mock_pyautogui.moveTo.assert_not_called()
        mock_pyautogui.click.assert_not_called()
    assert steps.result.failure_reason == LaunchFailureReason.USER_ABORTED
    assert steps.result.email_click_count == 0


# --- D: normalized-to-screen transform, verified mathematically ---
#
# 2026-09-06 (same-day follow-up): click X is no longer derived from
# the candidate's own bbox width (`x_min + 0.6 * bbox_width`) — that
# policy was replaced by a deterministic message-list safe zone (see
# app/config/settings.py::MESSAGE_ROW_CLICK_SAFE_ZONE_FRACTION) after
# live evidence showed it converged to unsafe click points whenever
# Claude/Gemini disagreed on reported bbox width. This test now verifies
# the NEW transform's math instead.

def test_D_normalized_click_point_transform_matches_expected_pixels():
    # Live example: raw bbox [378, 258, 456, 548].
    x_min, x_max = 258.0, 548.0
    y_min, y_max = 378.0, 456.0

    message_list_left_normalized = LEFT_SIDEBAR_MAX_X_FRACTION * 1000
    message_list_right_normalized = MESSAGE_LIST_RIGHT_MAX_X_FRACTION * 1000
    safe_zone_x = message_list_left_normalized + MESSAGE_ROW_CLICK_SAFE_ZONE_FRACTION * (
        message_list_right_normalized - message_list_left_normalized
    )
    click_raw_x = min(max(safe_zone_x, x_min), x_max)  # this bbox comfortably contains the safe-zone point
    click_raw_y = (y_min + y_max) / 2

    assert click_raw_x == 303.0
    assert click_raw_y == 417.0

    px, py = normalize_1000_to_pixels(click_raw_x, click_raw_y, IMAGE_WIDTH, IMAGE_HEIGHT)
    assert round(px) == 582
    assert round(py) == 450


# --- E: current Claude live-like bbox -> deterministic safe-zone click,
# NOT the old bbox-width-derived x=829 ---

def test_E_claude_live_like_bbox_produces_deterministic_safe_zone_click_point():
    steps = _steps()
    candidate = EmailCandidate(
        sender="Yash Dhanraj", subject="Quick Question Regarding...", subject_truncated=True,
        date_or_order="Wed 9/2", row_bbox=LIVE_REFINED_BBOX, confidence=0.92,
    )
    assert steps._validate_and_record_candidate(candidate, IMAGE_WIDTH, IMAGE_HEIGHT, "x.png") is True
    assert steps.result.email_converted_x == 582
    assert steps.result.email_converted_x != 829  # the old, bbox-width-derived (and non-working) click point
    assert steps.result.email_converted_y == 450


# --- F: historical successful Gemini-like bbox -> SAME deterministic
# safe-zone click X as Claude's bbox above ---

def test_F_historical_gemini_like_bbox_converges_to_the_same_safe_zone_click_x():
    """Provider geometry convergence: Claude's and Gemini's differently-
    shaped (but both valid) bboxes for the SAME kind of row now produce
    the SAME click X, because X no longer depends on either provider's
    reported bbox width at all — only on the known message-list column
    bounds. No provider-specific logic exists or is added anywhere."""
    x_min_norm = 328 / IMAGE_WIDTH * 1000
    x_max_norm = 712 / IMAGE_WIDTH * 1000
    y_min_norm = 499 / IMAGE_HEIGHT * 1000
    y_max_norm = 589 / IMAGE_HEIGHT * 1000

    steps = _steps()
    candidate = EmailCandidate(
        sender="Yash Dhanraj", subject="Mail for project", subject_truncated=False,
        date_or_order="Today", row_bbox=[y_min_norm, x_min_norm, y_max_norm, x_max_norm], confidence=0.9,
    )
    assert steps._validate_and_record_candidate(candidate, IMAGE_WIDTH, IMAGE_HEIGHT, "x.png") is True
    assert steps.result.email_converted_x == 582  # identical to Claude's bbox result above


# --- G: post-click verification failure -> no second click ---

def test_G_post_click_verification_failure_never_reclicks():
    steps = _steps_with_click_point(829, 450)
    steps.result.email_click_count = 1  # simulating click_target_email() already ran

    not_matching = MagicMock(
        parsed_json={"email_open": False, "subject_detected": "", "sender_detected": "", "subject_match": False,
                     "sender_match": False, "body_visible": False, "confidence": 0.5, "reason": "still loading"},
        raw_text="{}", model="test-model", latency_ms=50.0, input_tokens=5, output_tokens=5,
    )
    steps.vision.primary.analyze_screen.return_value = not_matching
    with patch(f"{MODULE}.time.sleep"), patch(f"{MODULE}.capture_screen", return_value=_capture()), \
         patch(f"{MODULE}.pyautogui") as mock_pyautogui:
        mock_pyautogui.FAILSAFE = True
        assert steps.verify_email_opened() is False
    assert steps.result.failure_reason == LaunchFailureReason.EMAIL_OPEN_VERIFICATION_FAILED
    assert steps.result.email_click_count == 1  # unchanged — verification never re-clicks
    mock_pyautogui.click.assert_not_called()
    mock_pyautogui.moveTo.assert_not_called()


# --- H: verification observation retry -> no repeated physical action ---

def test_H_verification_retry_logs_each_observation_without_reclicking(caplog):
    steps = _steps_with_click_point(829, 450)
    steps.result.email_click_count = 1
    not_yet = MagicMock(
        parsed_json={"email_open": False, "subject_detected": "", "sender_detected": "", "subject_match": False,
                     "sender_match": False, "body_visible": False, "confidence": 0.5, "reason": "still loading"},
        raw_text="{}", model="test-model", latency_ms=50.0, input_tokens=5, output_tokens=5,
    )
    confirmed = MagicMock(
        parsed_json={"email_open": True, "subject_detected": "Mail for project", "sender_detected": "Yash Dhanraj",
                     "subject_match": True, "sender_match": True, "body_visible": True, "confidence": 0.98,
                     "reason": "open"},
        raw_text="{}", model="test-model", latency_ms=50.0, input_tokens=5, output_tokens=5,
    )
    steps.result.target_sender = "Yash Dhanraj"
    steps.vision.primary.analyze_screen.side_effect = [not_yet, confirmed]
    with patch(f"{MODULE}.time.sleep"), patch(f"{MODULE}.capture_screen", return_value=_capture()), \
         patch(f"{MODULE}.pyautogui") as mock_pyautogui, \
         caplog.at_level("INFO", logger="app.outlook.find_email.open_verification"):
        mock_pyautogui.FAILSAFE = True
        assert steps.verify_email_opened() is True
    mock_pyautogui.click.assert_not_called()
    mock_pyautogui.moveTo.assert_not_called()
    assert steps.result.email_click_count == 1  # unchanged across both observations

    messages = [r.getMessage() for r in caplog.records]
    start_lines = [m for m in messages if "EMAIL_POST_CLICK_VERIFICATION_START" in m]
    assert len(start_lines) == 2
    assert "observation_attempt=1" in start_lines[0]
    assert "observation_attempt=2" in start_lines[1]


# --- Provider-neutral: the boundary/proportion this diagnostic run must
# NOT have changed ---

def test_boundary_unchanged_click_policy_now_deterministic_safe_zone():
    """2026-09-06 same-day update: MESSAGE_LIST_RIGHT_MAX_X_FRACTION
    (the geometry-validation boundary, calibrated in the prior task) is
    unchanged; MESSAGE_ROW_CLICK_X_PROPORTION (the bbox-width-derived
    click policy) has been REPLACED by MESSAGE_ROW_CLICK_SAFE_ZONE_
    FRACTION — see test_find_email_click_safe_zone.py for the full
    replacement test matrix."""
    assert MESSAGE_LIST_RIGHT_MAX_X_FRACTION == 0.55
    assert MESSAGE_ROW_CLICK_SAFE_ZONE_FRACTION == 0.35
