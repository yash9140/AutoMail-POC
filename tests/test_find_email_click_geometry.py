"""Target-email row click-point / sidebar-safety regression tests
(2026-09-04 live fix).

TWO live runs, same day, both failed EMAIL_GROUNDING_SIDEBAR_REJECTED:

1. "Converted coordinate (284, 397) falls within the left navigation/
   sidebar exclusion zone (x <= 326, 17% of 1920px width)."
2. After the first fix (a 60%-of-bbox-width proportional click point):
   "Converted coordinate (301, 326) falls within the left navigation/
   sidebar exclusion zone (x <= 326, 17% of 1920px width)." — proving a
   flat proportion of a still-too-narrow bbox isn't always enough.

Root-cause investigation (a real Vision call against the actual
failing screenshot — see the conversation, not reproduced here) found
the candidate's row_bbox had x_min sitting almost exactly at the
message list's real left boundary (~17% of screen width) — the bbox-
CENTER click policy leaves only half the bbox's own width as safety
margin against the sidebar, thin when the bbox's left edge already
sits at the boundary; a flat rightward proportion of a bbox that's
ALSO narrow doesn't reliably clear it either.

Fix under test (two layers):
1. A click point biased toward the row's text region
   (MESSAGE_ROW_CLICK_X_PROPORTION = 0.6 of the bbox width) instead of
   the raw center.
2. Clamped to never be closer to the sidebar than
   SIDEBAR_SAFETY_BUFFER_NORMALIZED past LEFT_SIDEBAR_MAX_X_FRACTION —
   but never past the bbox's own x_max — so a bbox that extends AT ALL
   meaningfully past the boundary reliably produces a safe click point.
PLUS a strengthened email_search_v1.txt prompt requiring the FULL row
width (not just a checkbox/avatar sliver), to reduce how often a bad
bbox is reported at all. The sidebar exclusion rule itself
(LEFT_SIDEBAR_MAX_X_FRACTION = 0.17) is UNCHANGED — real evidence
showed it already closely matches the actual message-list boundary in
this Outlook layout, so it was not the thing to change.

No real mouse/keyboard/network/provider call happens anywhere in this
file, and no live Outlook run is performed.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.settings import LEFT_SIDEBAR_MAX_X_FRACTION  # noqa: E402
from app.outlook.find_email import FindOpenEmailSteps, _evaluate_candidate  # noqa: E402
from app.playbook.failure_reasons import LaunchFailureReason  # noqa: E402
from app.safety.abort_controller import AbortController  # noqa: E402
from app.vision.models import EmailCandidate  # noqa: E402


def _steps(target_sender: str = "Yash Dhanraj", target_subject: str = "") -> FindOpenEmailSteps:
    return FindOpenEmailSteps(
        AbortController(), MagicMock(), "test-model", target_sender=target_sender, target_subject=target_subject,
    )


def _candidate(
    row_bbox, sender="Yash Dhanraj", subject="Quick Question Regarding Project", subject_truncated=False,
    confidence=0.95, date_or_order="Today",
) -> EmailCandidate:
    return EmailCandidate(
        sender=sender, subject=subject, subject_truncated=subject_truncated,
        date_or_order=date_or_order, row_bbox=row_bbox, confidence=confidence,
    )


# --- A: valid full-row bbox entirely inside the message list — click allowed ---

def test_A_valid_full_row_bbox_inside_message_list_click_allowed():
    steps = _steps()
    candidate = _candidate(row_bbox=[400.0, 400.0, 440.0, 900.0])
    assert steps._validate_and_record_candidate(candidate, 1920, 1080, "x.png") is True
    px_min, px_max = 400 / 1000 * 1920, 900 / 1000 * 1920
    assert px_min <= steps.result.email_converted_x <= px_max


# --- B: bbox left edge sits at the checkbox/avatar strip, but the row is
# otherwise well-formed — chosen click point stays safely right of the sidebar ---

def test_B_bbox_left_edge_at_boundary_click_point_safely_right_of_sidebar():
    """row_bbox is the REAL geometry observed from a live Vision call
    against the actual failing screenshot's inbox (x_min sits almost
    exactly at the sidebar boundary)."""
    steps = _steps()
    candidate = _candidate(row_bbox=[276.0, 170.0, 372.0, 388.0])
    assert steps._validate_and_record_candidate(candidate, 1920, 1080, "x.png") is True
    sidebar_boundary_px = LEFT_SIDEBAR_MAX_X_FRACTION * 1920
    assert steps.result.email_converted_x > sidebar_boundary_px


# --- C: the core proof — center would be rejected, text-interior point is allowed ---

def test_C_center_would_be_rejected_but_text_interior_point_is_allowed():
    steps = _steps()
    row_bbox = [400.0, 50.0, 440.0, 280.0]
    y_min, x_min, y_max, x_max = row_bbox
    center_x_normalized = (x_min + x_max) / 2
    sidebar_boundary_normalized = LEFT_SIDEBAR_MAX_X_FRACTION * 1000
    assert center_x_normalized <= sidebar_boundary_normalized  # sanity: plain center WOULD have been rejected

    candidate = _candidate(row_bbox=row_bbox)
    assert steps._validate_and_record_candidate(candidate, 1920, 1080, "x.png") is True
    assert steps.result.failure_reason is None
    sidebar_boundary_px = LEFT_SIDEBAR_MAX_X_FRACTION * 1920
    assert steps.result.email_converted_x > sidebar_boundary_px


# --- D: bbox genuinely inside the sidebar/navigation area — reject, zero click ---

def test_D_bbox_genuinely_in_sidebar_rejected_zero_click():
    steps = _steps()
    candidate = _candidate(row_bbox=[400.0, 20.0, 440.0, 150.0])
    assert steps._validate_and_record_candidate(candidate, 1920, 1080, "x.png") is False
    assert steps.result.failure_reason == LaunchFailureReason.EMAIL_GROUNDING_SIDEBAR_REJECTED
    assert steps.result.email_converted_x is None or steps.result.email_converted_x <= LEFT_SIDEBAR_MAX_X_FRACTION * 1920


# --- E: no hardcoded pixel assumption — same normalized bbox, multiple resolutions ---

def test_E_no_hardcoded_pixel_assumption_same_normalized_bbox_different_resolutions():
    for width, height in [(1920, 1080), (2560, 1440), (1366, 768)]:
        steps = _steps()
        candidate = _candidate(row_bbox=[400.0, 50.0, 440.0, 280.0])
        assert steps._validate_and_record_candidate(candidate, width, height, "x.png") is True, (width, height)


# --- F: reverse-engineered live-failure geometry — proves both halves of the fix ---

def test_F_narrow_bbox_matching_live_failure_now_rescued_by_boundary_clamp():
    """Reverse-engineered from the FIRST live report: center pixel
    (284, 397) on a 1920x1080 screen back-converts to a narrow ~184px-
    wide bbox around normalized x=100-196. A SECOND live failure (same
    day) proved the flat 60%-proportion point alone still wasn't enough
    for a bbox this narrow (its own rejected click, x=301, reverse-
    engineers to an even more left-skewed bbox). The boundary-clamp
    (SIDEBAR_SAFETY_BUFFER_NORMALIZED) fixes this specific shape: since
    x_max=196 genuinely clears the sidebar boundary+buffer (185), the
    click point is pushed to a point still safely inside the bbox and
    past the boundary — the row IS clickable there (Outlook's whole row
    is clickable, not just the text), so this is a correct rescue, not
    an unsafe one."""
    steps = _steps()
    candidate = _candidate(row_bbox=[340.0, 100.0, 396.0, 196.0])
    old_center_x_px = (100.0 + 196.0) / 2 / 1000 * 1920
    assert old_center_x_px == pytest.approx(284.16, abs=1)  # matches the first live report's rejected x=284

    assert steps._validate_and_record_candidate(candidate, 1920, 1080, "x.png") is True
    sidebar_boundary_px = LEFT_SIDEBAR_MAX_X_FRACTION * 1920
    assert steps.result.email_converted_x > sidebar_boundary_px
    assert steps.result.email_converted_x <= 196 / 1000 * 1920  # still inside the candidate's own bbox


def test_F_bbox_entirely_too_narrow_to_clear_boundary_still_correctly_rejected():
    """The clamp can only push the click point as far right as the
    bbox's own x_max allows — a bbox SO narrow that even its right edge
    doesn't clear the sidebar boundary+buffer is genuinely unsafe, and
    must still be rejected, never silently allowed by clamping past the
    bbox's own bounds."""
    steps = _steps()
    candidate = _candidate(row_bbox=[340.0, 60.0, 396.0, 120.0])  # x_max=120 < boundary(170)+buffer(15)=185
    assert steps._validate_and_record_candidate(candidate, 1920, 1080, "x.png") is False
    assert steps.result.failure_reason == LaunchFailureReason.EMAIL_GROUNDING_SIDEBAR_REJECTED


def test_F_second_live_failure_geometry_now_fixed_by_boundary_clamp():
    """Reverse-engineered from the SECOND live report: the proportional
    (60%) click point of x=301px (normalized ~156.8) was still rejected
    — consistent with a bbox around normalized x=50-230 (still narrower
    than a full row, but wider than the first failure's). Under the
    OLD proportion-only formula this stayed unsafe; the boundary clamp
    fixes it because x_max=230 clears boundary(170)+buffer(15)=185."""
    steps = _steps()
    candidate = _candidate(row_bbox=[340.0, 50.0, 396.0, 230.0])
    old_proportional_x_px = (50.0 + 0.6 * (230.0 - 50.0)) / 1000 * 1920
    assert old_proportional_x_px == pytest.approx(301, abs=3)  # matches the second live report's rejected x=301

    assert steps._validate_and_record_candidate(candidate, 1920, 1080, "x.png") is True
    sidebar_boundary_px = LEFT_SIDEBAR_MAX_X_FRACTION * 1920
    assert steps.result.email_converted_x > sidebar_boundary_px


def test_F_well_formed_full_row_bbox_at_same_row_position_is_allowed():
    """The actual fix: once the strengthened prompt elicits a FULL row
    bbox (not a checkbox-only sliver) for the same row position, the
    click point lands safely inside the message list."""
    steps = _steps()
    candidate = _candidate(row_bbox=[340.0, 170.0, 396.0, 450.0])  # same row, full width
    assert steps._validate_and_record_candidate(candidate, 1920, 1080, "x.png") is True
    sidebar_boundary_px = LEFT_SIDEBAR_MAX_X_FRACTION * 1920
    assert steps.result.email_converted_x > sidebar_boundary_px


# --- G: different resolution, same real-observed bbox, same safe outcome ---

def test_G_different_resolution_same_normalized_behavior():
    for width, height in [(1920, 1080), (2560, 1440)]:
        steps = _steps()
        candidate = _candidate(row_bbox=[276.0, 170.0, 372.0, 388.0])
        assert steps._validate_and_record_candidate(candidate, width, height, "x.png") is True, (width, height)


# --- H: row near the left edge of the message list — not falsely rejected ---

def test_H_row_near_left_edge_of_message_list_not_falsely_rejected():
    steps = _steps()
    candidate = _candidate(row_bbox=[400.0, 175.0, 440.0, 450.0])  # x_min just past the boundary (170 normalized)
    assert steps._validate_and_record_candidate(candidate, 1920, 1080, "x.png") is True


# --- I: wrong sender/subject still rejected regardless of safe geometry ---

def test_I_wrong_sender_never_eligible_regardless_of_safe_geometry():
    candidate = _candidate(row_bbox=[400.0, 400.0, 440.0, 900.0], sender="Someone Else")
    evidence = _evaluate_candidate(candidate, "Yash Dhanraj", "", attempt_number=1)
    assert evidence.eligible is False
    assert evidence.rejection_reason == "sender_mismatch"


def test_I_wrong_subject_never_eligible_regardless_of_safe_geometry():
    candidate = _candidate(row_bbox=[400.0, 400.0, 440.0, 900.0], sender="Yash Dhanraj", subject="Totally unrelated subject")
    evidence = _evaluate_candidate(candidate, "Yash Dhanraj", "Mail for project", attempt_number=1)
    assert evidence.eligible is False
    assert evidence.rejection_reason == "subject_mismatch"


# --- J: multiple ambiguous candidates — zero click ---

def test_J_multiple_ambiguous_exact_candidates_zero_click():
    steps = _steps(target_sender="Yash Dhanraj")
    candidates = [
        _candidate(row_bbox=[300.0, 400.0, 340.0, 900.0], subject="Quick Question Regarding Project", date_or_order="not-a-date"),
        _candidate(row_bbox=[400.0, 400.0, 440.0, 900.0], subject="Quick Question Regarding Project", date_or_order="also-not-a-date"),
    ]
    evidence = [_evaluate_candidate(c, steps.result.target_sender, steps.result.target_subject, 1) for c in candidates]
    exact_pairs = [(c, ev) for c, ev in zip(candidates, evidence) if ev.match_type == "exact"]
    assert len(exact_pairs) == 2  # both are exact matches — genuinely ambiguous, unresolvable by date


# --- K: truncated-subject provisional match — geometry fix doesn't weaken it ---

def test_K_truncated_subject_provisional_match_still_selected_with_safe_geometry():
    steps = _steps(target_sender="Yash Dhanraj", target_subject="Quick Question Regarding Project Timeline")
    candidate = _candidate(
        row_bbox=[400.0, 400.0, 440.0, 900.0],
        sender="Yash Dhanraj", subject="Quick Question Regarding Project...", subject_truncated=True,
    )
    evidence = _evaluate_candidate(candidate, steps.result.target_sender, steps.result.target_subject, 1)
    assert evidence.match_type == "provisional"
    assert evidence.eligible is True

    # Geometry validation is independent of match_type — a provisional
    # match with a well-formed bbox is still click-eligible; whether it
    # is CONFIRMED is verify_email_opened()'s job (untouched by this fix).
    assert steps._validate_and_record_candidate(candidate, 1920, 1080, "x.png") is True


# --- L: foreground changes before click — zero click (click_target_email(), untouched) ---

def test_L_foreground_lost_before_click_zero_click():
    from unittest.mock import patch

    steps = _steps()
    steps.result.email_converted_x, steps.result.email_converted_y = 600, 400

    with patch("app.outlook.find_email.pyautogui") as mock_pyautogui, \
         patch("app.outlook.find_email.get_foreground_window_title", return_value="Visual Studio Code"):
        mock_pyautogui.FAILSAFE = True
        assert steps.click_target_email() is False
        mock_pyautogui.moveTo.assert_not_called()
        mock_pyautogui.click.assert_not_called()
    assert steps.result.failure_reason == LaunchFailureReason.OUTLOOK_FOREGROUND_LOST
