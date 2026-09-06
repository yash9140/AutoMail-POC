"""Target-email click-X policy replacement regression tests (2026-09-06,
same-day follow-up to the message-list-right-boundary calibration).

Supersedes tests/test_find_email_click_geometry.py (deleted) — that
file's entire subject, `x_min + MESSAGE_ROW_CLICK_X_PROPORTION *
bbox_width` (a click-X derived from each candidate's own reported bbox
width), is now replaced.

Live evidence: Claude (primary) and Gemini (historical successful run)
each produced an independently-VALID, geometry-passing row_bbox for the
same kind of target-email row, but with different reported widths —
Claude's this run: raw_bbox=[378,258,456,548] (screen x-range
495..1052); Gemini's earlier successful run: reconstructed screen
x-range ~328..712. The old policy converged these to different click
points (x=829 for Claude — did NOT open the email; x=559 for Gemini —
DID). Deriving click-X from Vision's self-reported bbox width is
unreliable whenever providers disagree on it, even though every
existing geometry check (message-list-right boundary, width/height
plausibility) independently passed for both.

Fix: click-X is now MESSAGE_ROW_CLICK_SAFE_ZONE_FRACTION (0.35) applied
to the KNOWN message-list column bounds (LEFT_SIDEBAR_MAX_X_FRACTION ..
MESSAGE_LIST_RIGHT_MAX_X_FRACTION) — independent of any one candidate's
own bbox width — then clamped to lie within that candidate's own
validated bbox. Y is unchanged: still the validated bbox's own vertical
center, still Vision-derived.

No real mouse/keyboard/network/provider call happens anywhere in this
file, and no live Outlook run is performed.
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
from app.vision.models import EmailCandidate  # noqa: E402
from app.vision.service import VisionService  # noqa: E402
from tests._capture_test_utils import real_capture_image_path  # noqa: E402

IMAGE_WIDTH = 1920
IMAGE_HEIGHT = 1080
SCREENSHOT_PATH = real_capture_image_path(IMAGE_WIDTH, IMAGE_HEIGHT)

# The exact live geometries under comparison.
CLAUDE_LIVE_BBOX = [378.0, 258.0, 456.0, 548.0]           # this task's live evidence


def _gemini_like_bbox():
    # Reconstructed from the historical successful run's reported
    # screen_bbox=[499,328,589,712] on a 1920x1080 screen.
    return [
        499 / IMAGE_HEIGHT * 1000, 328 / IMAGE_WIDTH * 1000,
        589 / IMAGE_HEIGHT * 1000, 712 / IMAGE_WIDTH * 1000,
    ]


def _steps(target_sender: str = "Yash Dhanraj", target_subject: str = "") -> FindOpenEmailSteps:
    return FindOpenEmailSteps(
        AbortController(), VisionService(MagicMock(), fallback=None), "test-model",
        target_sender=target_sender, target_subject=target_subject,
    )


def _candidate(row_bbox, sender="Yash Dhanraj", subject="Quick Question Regarding Project",
               subject_truncated=False, confidence=0.92, date_or_order="Today") -> EmailCandidate:
    return EmailCandidate(
        sender=sender, subject=subject, subject_truncated=subject_truncated,
        date_or_order=date_or_order, row_bbox=row_bbox, confidence=confidence,
    )


# --- A: Claude live-like bbox -> deterministic click, NOT the old x=829 ---

def test_A_claude_live_bbox_lands_in_deterministic_safe_zone_not_829():
    steps = _steps()
    candidate = _candidate(CLAUDE_LIVE_BBOX)
    assert steps._validate_and_record_candidate(candidate, IMAGE_WIDTH, IMAGE_HEIGHT, SCREENSHOT_PATH) is True
    assert steps.result.email_converted_x != 829
    # Deterministic safe-zone fraction (0.35 across the list column),
    # converted to pixels — see app/config/settings.py for the formula.
    expected_x_normalized = (LEFT_SIDEBAR_MAX_X_FRACTION + MESSAGE_ROW_CLICK_SAFE_ZONE_FRACTION * (
        MESSAGE_LIST_RIGHT_MAX_X_FRACTION - LEFT_SIDEBAR_MAX_X_FRACTION
    )) * 1000
    assert steps.result.email_converted_x == round(expected_x_normalized / 1000 * IMAGE_WIDTH)


# --- B: historical Gemini-like bbox -> SAME (or nearly same) click X ---

def test_B_gemini_like_bbox_converges_to_the_same_click_x_as_claude():
    steps_claude = _steps()
    steps_claude._validate_and_record_candidate(_candidate(CLAUDE_LIVE_BBOX), IMAGE_WIDTH, IMAGE_HEIGHT, SCREENSHOT_PATH)

    steps_gemini = _steps()
    steps_gemini._validate_and_record_candidate(_candidate(_gemini_like_bbox()), IMAGE_WIDTH, IMAGE_HEIGHT, SCREENSHOT_PATH)

    assert steps_claude.result.email_converted_x == steps_gemini.result.email_converted_x


# --- C: Y still comes from each bbox's own vertical center ---

def test_C_click_y_still_derived_from_each_bboxs_own_vertical_center():
    steps = _steps()
    candidate = _candidate([200.0, 300.0, 260.0, 500.0])  # y_center normalized = 230
    assert steps._validate_and_record_candidate(candidate, IMAGE_WIDTH, IMAGE_HEIGHT, SCREENSHOT_PATH) is True
    assert steps.result.email_converted_y == round(230 / 1000 * IMAGE_HEIGHT)

    steps2 = _steps()
    candidate2 = _candidate([600.0, 300.0, 660.0, 500.0])  # different row, y_center normalized = 630
    assert steps2._validate_and_record_candidate(candidate2, IMAGE_WIDTH, IMAGE_HEIGHT, SCREENSHOT_PATH) is True
    assert steps2.result.email_converted_y == round(630 / 1000 * IMAGE_HEIGHT)
    assert steps2.result.email_converted_y != steps.result.email_converted_y


# --- D: click X is always inside the message-list column bounds ---

def test_D_click_x_always_strictly_inside_message_list_bounds():
    message_list_left_px = LEFT_SIDEBAR_MAX_X_FRACTION * IMAGE_WIDTH
    message_list_right_px = MESSAGE_LIST_RIGHT_MAX_X_FRACTION * IMAGE_WIDTH

    for bbox in (CLAUDE_LIVE_BBOX, _gemini_like_bbox(), [300.0, 180.0, 340.0, 540.0]):
        steps = _steps()
        ok = steps._validate_and_record_candidate(_candidate(bbox), IMAGE_WIDTH, IMAGE_HEIGHT, SCREENSHOT_PATH)
        assert ok is True, bbox
        assert message_list_left_px < steps.result.email_converted_x < message_list_right_px, bbox


# --- E: a safe interior margin from both column edges is respected ---

def test_E_safe_zone_point_keeps_a_meaningful_margin_from_both_edges():
    steps = _steps()
    candidate = _candidate(CLAUDE_LIVE_BBOX)
    assert steps._validate_and_record_candidate(candidate, IMAGE_WIDTH, IMAGE_HEIGHT, SCREENSHOT_PATH) is True

    sidebar_px = LEFT_SIDEBAR_MAX_X_FRACTION * IMAGE_WIDTH
    list_right_px = MESSAGE_LIST_RIGHT_MAX_X_FRACTION * IMAGE_WIDTH
    margin_from_sidebar = steps.result.email_converted_x - sidebar_px
    margin_from_right = list_right_px - steps.result.email_converted_x
    assert margin_from_sidebar > 50   # comfortably clear of the sidebar, not a hairline pass
    assert margin_from_right > 50     # comfortably clear of the reading-pane boundary too


# --- F: a clearly invalid bbox never reaches click-point computation ---

def test_F_invalid_bbox_never_reaches_click_computation():
    steps = _steps()
    # Extends deep into the reading pane -> rejected by validate_email_row_bbox().
    # This reason IS refinement-eligible (see MAX_EMAIL_ROW_BBOX_REFINEMENTS),
    # so the one bounded refinement attempt is mocked to also decline
    # (target_visible=False) — proving the bbox stays rejected end-to-end
    # and click computation is never reached either way.
    steps.vision.primary.analyze_screen.return_value = MagicMock(
        parsed_json={"target_visible": False, "row_bbox": None, "confidence": 0.5, "reason": "no longer visible"},
        raw_text="{}", model="test-model", latency_ms=10.0, input_tokens=5, output_tokens=5,
    )
    candidate = _candidate([378.0, 258.0, 456.0, 900.0])
    assert steps._validate_and_record_candidate(candidate, IMAGE_WIDTH, IMAGE_HEIGHT, SCREENSHOT_PATH) is False
    assert steps.result.failure_reason == LaunchFailureReason.EMAIL_ROW_BBOX_IMPLAUSIBLE
    assert steps.result.email_converted_x is None
    assert steps.result.email_converted_y is None


# --- G: no provider parameter/branch anywhere in the click-point derivation ---

def test_G_no_provider_name_branch_in_click_point_derivation():
    import inspect

    from app.outlook.find_email import FindOpenEmailSteps as FOES

    source = inspect.getsource(FOES._validate_and_record_candidate)
    for literal in ('"gemini"', "'gemini'", '"claude"', "'claude'", '"anthropic"', "'anthropic'"):
        assert literal not in source


# --- H: exactly one physical click, regardless of which bbox produced the point ---

def test_H_exactly_one_click_for_the_deterministic_safe_zone_point():
    steps = _steps()
    candidate = _candidate(CLAUDE_LIVE_BBOX)
    assert steps._validate_and_record_candidate(candidate, IMAGE_WIDTH, IMAGE_HEIGHT, SCREENSHOT_PATH) is True

    with patch("app.outlook.find_email.pyautogui") as mock_pyautogui, \
         patch("app.outlook.find_email.get_foreground_window_title", return_value="Mail - Outlook"):
        mock_pyautogui.FAILSAFE = True
        assert steps.click_target_email() is True
        mock_pyautogui.click.assert_called_once_with()
        mock_pyautogui.moveTo.assert_called_once()
    assert steps.result.email_click_count == 1


# --- I: post-click verification failure never triggers a re-click ---

def test_I_verification_failure_never_reclicks_with_the_new_click_point():
    steps = _steps()
    candidate = _candidate(CLAUDE_LIVE_BBOX)
    assert steps._validate_and_record_candidate(candidate, IMAGE_WIDTH, IMAGE_HEIGHT, SCREENSHOT_PATH) is True

    with patch("app.outlook.find_email.pyautogui") as mock_pyautogui, \
         patch("app.outlook.find_email.get_foreground_window_title", return_value="Mail - Outlook"):
        mock_pyautogui.FAILSAFE = True
        assert steps.click_target_email() is True

    not_matching = MagicMock(
        parsed_json={"email_open": False, "subject_detected": "", "sender_detected": "", "subject_match": False,
                     "sender_match": False, "body_visible": False, "confidence": 0.5, "reason": "still empty"},
        raw_text="{}", model="test-model", latency_ms=50.0, input_tokens=5, output_tokens=5,
    )
    steps.vision.primary.analyze_screen.return_value = not_matching
    with patch("app.outlook.find_email.time.sleep"), \
         patch("app.outlook.find_email.capture_screen", return_value=MagicMock(filename="x.png", path=SCREENSHOT_PATH)), \
         patch("app.outlook.find_email.pyautogui") as mock_pyautogui2:
        mock_pyautogui2.FAILSAFE = True
        assert steps.verify_email_opened() is False
        mock_pyautogui2.click.assert_not_called()
        mock_pyautogui2.moveTo.assert_not_called()
    assert steps.result.failure_reason == LaunchFailureReason.EMAIL_OPEN_VERIFICATION_FAILED
    assert steps.result.email_click_count == 1
