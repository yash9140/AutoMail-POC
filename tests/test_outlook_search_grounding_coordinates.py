"""Live-bug-driven tests for the OUTLOOK_SEARCH grounding coordinate
contract (2026-09-02): the cursor moved after Claude's OUTLOOK_SEARCH
call but did not land on the actual Outlook search result. Root cause
was that this ONE stage never adopted the bbox + validate_grounding()
pattern every other grounded stage (email row, Reply, Send) already
uses — it trusted a single unverifiable (x, y) point. This file proves
the fix: bbox contract, coordinate-order/scale, conversion-formula
correctness (no axis swap), the mss-vs-pyautogui coordinate-space
audit, and that diagnostics never influence the physical click.

pyautogui and the vision provider are always mocked — no real
mouse/keyboard/network/provider call happens anywhere in this file, and
no live Outlook run is performed.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.outlook.launch import OutlookLaunchSteps  # noqa: E402
from app.playbook.failure_reasons import LaunchFailureReason  # noqa: E402
from app.safety.abort_controller import AbortController  # noqa: E402
from app.safety.validators import GroundingCheckFailure, validate_grounding  # noqa: E402

MODULE = "app.outlook.launch"

_ENV_INFO_MATCH = {"pyautogui_width": 1920, "pyautogui_height": 1080, "dimensions_match": True}


def _steps() -> OutlookLaunchSteps:
    steps = OutlookLaunchSteps(AbortController(), MagicMock(), "claude-sonnet-5")
    steps.result.screen_width, steps.result.screen_height = 1920, 1080
    return steps


def _capture(width=1920, height=1080, filename="search.png"):
    return MagicMock(filename=filename, path=filename, width=width, height=height)


def _call(bbox, confidence=0.95, target_visible=True, target_type="desktop_app", visible_label="Outlook"):
    return MagicMock(
        parsed_json={
            "search_visible": True, "target_visible": target_visible, "target_type": target_type,
            "visible_label": visible_label, "bbox": bbox, "confidence": confidence, "reason": "ok",
        },
        raw_text="{}", model="claude-sonnet-5", latency_ms=100.0, input_tokens=10, output_tokens=5,
    )


# --- A: known-screenshot bbox conversion produces the expected pixel rect + center ---

def test_A_known_screenshot_bbox_converts_to_expected_pixel_rect_and_click_point():
    steps = _steps()
    # bbox = [y_min, x_min, y_max, x_max], 0-1000 normalized — an
    # off-center, asymmetric box (not the image midpoint) so this proves
    # the general formula, not a coincidence at (500, 500).
    bbox = [100.0, 700.0, 180.0, 900.0]
    steps.provider.analyze_screen.return_value = _call(bbox)

    with patch(f"{MODULE}.get_environment_info", return_value=_ENV_INFO_MATCH):
        assert steps.ground_search_result(_capture(1920, 1080)) is True

    # center = (800, 140) normalized -> (1536, 151.2->151) pixels
    assert steps.result.converted_x == 1536
    assert steps.result.converted_y == 151
    assert steps.result.search_grounding_bbox_pixels == [108, 1344, 194, 1728]
    assert steps.result.coordinate_in_screen_bounds is True


# --- B: an axis-swapped conversion would land far from the correct point; prove it doesn't ---

def test_B_x_y_not_swapped_in_conversion():
    """A horizontal strip near the vertical middle, spanning nearly the
    full width, on a non-square 1920x1080 screenshot. If x and y were
    ever swapped in the conversion (raw_x scaled against height, raw_y
    scaled against width — a classic bug for this exact contract), the
    computed click point would land far from the correct location. This
    asserts the CORRECT point and explicitly rules out the swapped one."""
    steps = _steps()
    bbox = [400.0, 30.0, 440.0, 970.0]
    steps.provider.analyze_screen.return_value = _call(bbox)

    with patch(f"{MODULE}.get_environment_info", return_value=_ENV_INFO_MATCH):
        assert steps.ground_search_result(_capture(1920, 1080)) is True

    # correct: center = (500, 420) normalized -> (960, 454) pixels
    assert steps.result.converted_x == 960
    assert steps.result.converted_y == 454

    # what a swapped conversion (x against height, y against width) would
    # have produced instead — must NOT be what we got.
    swapped_x = round(420 / 1000 * 1920)
    swapped_y = round(500 / 1000 * 1080)
    assert (steps.result.converted_x, steps.result.converted_y) != (swapped_x, swapped_y)


# --- C: a bbox that looks like native pixels (out of the 0-1000 contract) is rejected ---

def test_C_native_pixel_looking_bbox_rejected_not_silently_scaled():
    steps = _steps()
    # x components (1400, 1800) are only plausible as native pixel
    # coordinates for a 1920-wide screenshot, never as 0-1000 normalized
    # values — must be rejected outright, never clamped or reinterpreted.
    bbox = [900.0, 1400.0, 960.0, 1800.0]
    steps.provider.analyze_screen.return_value = _call(bbox)

    with patch(f"{MODULE}.get_environment_info", return_value=_ENV_INFO_MATCH):
        assert steps.ground_search_result(_capture(1920, 1080)) is False
    assert steps.result.failure_reason == LaunchFailureReason.GROUNDING_INVALID
    assert steps.result.converted_x is None
    assert steps.result.converted_y is None


# --- D: valid normalized bboxes at different positions produce different, correct click points ---

def test_D_valid_bbox_produces_correct_click_point_dynamically_at_different_positions():
    """Proves the click point tracks wherever the bbox actually is —
    never a fixed/hardcoded screen location — by grounding two different
    result positions and checking both convert correctly and disagree
    with each other."""
    top_left = _steps()
    top_left.provider.analyze_screen.return_value = _call([50.0, 60.0, 90.0, 200.0])
    with patch(f"{MODULE}.get_environment_info", return_value=_ENV_INFO_MATCH):
        assert top_left.ground_search_result(_capture(1920, 1080)) is True
    assert (top_left.result.converted_x, top_left.result.converted_y) == (250, 76)

    bottom_right = _steps()
    bottom_right.provider.analyze_screen.return_value = _call([850.0, 800.0, 900.0, 950.0])
    with patch(f"{MODULE}.get_environment_info", return_value=_ENV_INFO_MATCH):
        assert bottom_right.ground_search_result(_capture(1920, 1080)) is True
    assert (bottom_right.result.converted_x, bottom_right.result.converted_y) == (1680, 945)

    assert (top_left.result.converted_x, top_left.result.converted_y) != (
        bottom_right.result.converted_x, bottom_right.result.converted_y,
    )


# --- E: screenshot vs pyautogui dimension mismatch is detected before any click is trusted ---

def test_E_screenshot_pyautogui_dimension_mismatch_detected_before_click():
    """Simulates a classic DPI-scaling symptom: pyautogui reports a
    logical resolution (1536x864) that disagrees with the screenshot's
    actual pixel dimensions (1920x1080). Must fail fast with
    COORDINATE_SPACE_MISMATCH, before the Vision call is even made, and
    must never move or click the mouse."""
    steps = _steps()
    mismatched_env = {"pyautogui_width": 1536, "pyautogui_height": 864, "dimensions_match": False}

    with patch(f"{MODULE}.get_environment_info", return_value=mismatched_env), \
         patch(f"{MODULE}.pyautogui") as mock_pyautogui:
        mock_pyautogui.FAILSAFE = True
        assert steps.ground_search_result(_capture(1920, 1080)) is False

    assert steps.result.failure_reason == LaunchFailureReason.COORDINATE_SPACE_MISMATCH
    assert steps.result.dimensions_match is False
    assert steps.result.pyautogui_width == 1536
    assert steps.result.pyautogui_height == 864
    steps.provider.analyze_screen.assert_not_called()


# --- F: a point outside its own bbox is rejected by the shared validator ---

def test_F_point_outside_bbox_rejected_by_shared_validator():
    """ground_search_result() always derives its click point from the
    bbox CENTER (see validate_grounding()'s "bbox over loose points"
    rule), so a point-outside-its-own-bbox response can never reach
    pyautogui from this stage by construction — there is no longer a
    separate loose x/y field for this stage to disagree with its bbox.
    This test exercises the shared validator directly to prove the
    safety net ground_search_result relies on actually rejects a
    mismatched point, guarding against a future regression that
    reintroduces a separate loose point for this stage."""
    result = validate_grounding(
        raw_x=50.0, raw_y=50.0,  # far outside the bbox below
        box_2d=[400.0, 400.0, 500.0, 600.0],
        confidence=0.95, image_width=1920, image_height=1080,
        confidence_threshold=0.7,
    )
    assert result.valid is False
    assert result.failure == GroundingCheckFailure.POINT_OUTSIDE_BBOX


# --- G: DPI/logging diagnostics never change the physical click ---

def test_G_debug_artifact_disabled_by_default_and_never_alters_click():
    steps = _steps()
    bbox = [100.0, 700.0, 180.0, 900.0]
    steps.provider.analyze_screen.return_value = _call(bbox)

    with patch(f"{MODULE}.get_environment_info", return_value=_ENV_INFO_MATCH), \
         patch(f"{MODULE}.save_grounding_debug_artifact") as mock_save:
        assert steps.ground_search_result(_capture(1920, 1080)) is True

    mock_save.assert_not_called()  # OUTLOOK_GROUNDING_DEBUG is unset by default
    assert steps.result.grounding_debug_artifact is None
    assert steps.result.converted_x == 1536
    assert steps.result.converted_y == 151


def test_G_debug_artifact_enabled_does_not_change_computed_click_point():
    """Even when the diagnostic overlay IS enabled, its (mocked) call
    must have zero influence on the coordinates already computed —
    diagnostics are observational only."""
    steps = _steps()
    bbox = [100.0, 700.0, 180.0, 900.0]
    steps.provider.analyze_screen.return_value = _call(bbox)

    with patch(f"{MODULE}.get_environment_info", return_value=_ENV_INFO_MATCH), \
         patch(f"{MODULE}._DEBUG_ARTIFACTS_ENABLED", True), \
         patch(f"{MODULE}.save_grounding_debug_artifact", return_value=Path("debug/fake.png")) as mock_save:
        assert steps.ground_search_result(_capture(1920, 1080)) is True

    mock_save.assert_called_once()
    assert steps.result.grounding_debug_artifact == str(Path("debug/fake.png"))
    assert steps.result.converted_x == 1536
    assert steps.result.converted_y == 151
