"""app/safety/validators.py::validate_grounding() and
validate_email_row_bbox() unit tests."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.settings import (  # noqa: E402
    EMAIL_ROW_MAX_HEIGHT_FRACTION,
    EMAIL_ROW_MAX_WIDTH_FRACTION,
    EMAIL_ROW_MIN_HEIGHT_FRACTION,
    EMAIL_ROW_MIN_WIDTH_FRACTION,
    LEFT_SIDEBAR_MAX_X_FRACTION,
    MESSAGE_LIST_RIGHT_MAX_X_FRACTION,
)
from app.safety.validators import GroundingCheckFailure, validate_email_row_bbox, validate_grounding  # noqa: E402

IMAGE_WIDTH = 1920
IMAGE_HEIGHT = 1080


def _validate_row(row_bbox, image_width=IMAGE_WIDTH, image_height=IMAGE_HEIGHT):
    return validate_email_row_bbox(
        row_bbox=row_bbox, image_width=image_width, image_height=image_height,
        message_list_right_max_x_fraction=MESSAGE_LIST_RIGHT_MAX_X_FRACTION,
        min_row_width_fraction=EMAIL_ROW_MIN_WIDTH_FRACTION,
        max_row_width_fraction=EMAIL_ROW_MAX_WIDTH_FRACTION,
        min_row_height_fraction=EMAIL_ROW_MIN_HEIGHT_FRACTION,
        max_row_height_fraction=EMAIL_ROW_MAX_HEIGHT_FRACTION,
        sidebar_max_x_fraction=LEFT_SIDEBAR_MAX_X_FRACTION,
    )


def test_missing_coordinates_rejected():
    result = validate_grounding(
        raw_x=None, raw_y=None, box_2d=None, confidence=0.9,
        image_width=IMAGE_WIDTH, image_height=IMAGE_HEIGHT, confidence_threshold=0.6,
    )
    assert result.valid is False
    assert result.failure == GroundingCheckFailure.MISSING_COORDINATES


def test_malformed_bbox_rejected():
    result = validate_grounding(
        raw_x=500, raw_y=500, box_2d=[1, 2, 3], confidence=0.9,
        image_width=IMAGE_WIDTH, image_height=IMAGE_HEIGHT, confidence_threshold=0.6,
    )
    assert result.valid is False
    assert result.failure == GroundingCheckFailure.INVALID_BBOX


def test_point_outside_own_bbox_rejected():
    result = validate_grounding(
        raw_x=100, raw_y=100, box_2d=[600, 600, 700, 700], confidence=0.9,
        image_width=IMAGE_WIDTH, image_height=IMAGE_HEIGHT, confidence_threshold=0.6,
    )
    assert result.valid is False
    assert result.failure == GroundingCheckFailure.POINT_OUTSIDE_BBOX


def test_valid_bbox_computes_click_point_from_center_not_raw_point():
    # Raw point is inside the box but NOT at its center — the validated
    # click point must come from the box's center, per the "bbox over
    # loose points" rule, not from the raw x/y directly.
    result = validate_grounding(
        raw_x=610, raw_y=610, box_2d=[600, 600, 700, 700], confidence=0.9,
        image_width=IMAGE_WIDTH, image_height=IMAGE_HEIGHT, confidence_threshold=0.6,
    )
    assert result.valid is True
    assert result.used_bbox is True
    expected_center_x, expected_center_y = round(650 / 1000 * IMAGE_WIDTH), round(650 / 1000 * IMAGE_HEIGHT)
    assert result.converted_x == expected_center_x
    assert result.converted_y == expected_center_y


def test_out_of_bounds_rejected():
    # raw=1000 is the top of the valid 0-1000 normalized range (passes
    # the range check) but converts to pixel == image_width exactly,
    # which is one past the last valid pixel index (0 <= x < width) —
    # a genuine pixel-bounds failure, distinct from an out-of-range
    # normalized value.
    result = validate_grounding(
        raw_x=1000, raw_y=500, box_2d=None, confidence=0.9,
        image_width=IMAGE_WIDTH, image_height=IMAGE_HEIGHT, confidence_threshold=0.6,
    )
    assert result.valid is False
    assert result.failure == GroundingCheckFailure.OUT_OF_BOUNDS


def test_out_of_normalized_range_rejected_before_conversion():
    result = validate_grounding(
        raw_x=2000, raw_y=500, box_2d=None, confidence=0.9,
        image_width=IMAGE_WIDTH, image_height=IMAGE_HEIGHT, confidence_threshold=0.6,
    )
    assert result.valid is False
    assert result.failure == GroundingCheckFailure.OUT_OF_NORMALIZED_RANGE


def test_negative_normalized_value_rejected():
    result = validate_grounding(
        raw_x=-10, raw_y=500, box_2d=None, confidence=0.9,
        image_width=IMAGE_WIDTH, image_height=IMAGE_HEIGHT, confidence_threshold=0.6,
    )
    assert result.valid is False
    assert result.failure == GroundingCheckFailure.OUT_OF_NORMALIZED_RANGE


def test_degenerate_bbox_rejected():
    result = validate_grounding(
        raw_x=500, raw_y=500, box_2d=[500, 500, 500, 500], confidence=0.9,
        image_width=IMAGE_WIDTH, image_height=IMAGE_HEIGHT, confidence_threshold=0.6,
    )
    assert result.valid is False
    assert result.failure == GroundingCheckFailure.DEGENERATE_BBOX


def test_bbox_component_out_of_normalized_range_rejected():
    result = validate_grounding(
        raw_x=500, raw_y=500, box_2d=[400, 400, 600, 1500], confidence=0.9,
        image_width=IMAGE_WIDTH, image_height=IMAGE_HEIGHT, confidence_threshold=0.6,
    )
    assert result.valid is False
    assert result.failure == GroundingCheckFailure.OUT_OF_NORMALIZED_RANGE


def test_sidebar_rejected_regardless_of_confidence():
    result = validate_grounding(
        raw_x=50, raw_y=500, box_2d=None, confidence=1.0,
        image_width=IMAGE_WIDTH, image_height=IMAGE_HEIGHT, confidence_threshold=0.6,
        sidebar_max_x_fraction=0.17,
    )
    assert result.valid is False
    assert result.failure == GroundingCheckFailure.SIDEBAR_REJECTED


def test_sidebar_check_skipped_when_fraction_not_given():
    # Same coordinate as the sidebar-rejection case above, but without a
    # sidebar_max_x_fraction (e.g. Reply/Send grounding, where the
    # sidebar heuristic is not meaningful) — should pass.
    result = validate_grounding(
        raw_x=50, raw_y=500, box_2d=None, confidence=0.9,
        image_width=IMAGE_WIDTH, image_height=IMAGE_HEIGHT, confidence_threshold=0.6,
    )
    assert result.valid is True


# ==================================================================
# validate_email_row_bbox() — message-list row-shape plausibility
# (2026-09-06 live fix: a live run's row_bbox extended past the real
# message-list column into the reading pane). See app/outlook/
# find_email.py::_validate_and_record_candidate() for the call site.
# ==================================================================

# --- A: live-failure reproduction — the exact reported geometry ---

def test_A_live_failure_bbox_extends_beyond_message_list_rejected():
    # Live evidence: raw_bbox=[418.0, 253.0, 492.0, 572.0] on a 1920x1080
    # screen — x_max=572 (57.2% of screen width) extends well past
    # MESSAGE_LIST_RIGHT_MAX_X_FRACTION (52.5%, the midpoint between the
    # already-established message-list and reading-pane scroll anchors).
    result = _validate_row([418.0, 253.0, 492.0, 572.0])
    assert result.valid is False
    assert result.reason == "bbox_extends_beyond_message_list"
    assert result.bbox_right_x > result.message_list_right_x


def test_A_old_logic_would_have_accepted_this_geometry():
    """Documents the regression this fix closes: the pre-fix pipeline had
    no check at all on the row_bbox's own right-edge extent — only the
    DERIVED CLICK POINT's sidebar safety was checked, and this bbox's
    click point (x_min + 0.6*width ≈ 44% of screen width) sits nowhere
    near the sidebar, so nothing previously would have rejected it."""
    x_min, x_max = 253.0, 572.0
    old_click_x_fraction = (x_min + 0.6 * (x_max - x_min)) / 1000.0
    assert old_click_x_fraction > LEFT_SIDEBAR_MAX_X_FRACTION + 0.10  # comfortably clear of the (only) old check
    # Yet the bbox's own right edge is clearly past the message-list column:
    assert (x_max / 1000.0) > MESSAGE_LIST_RIGHT_MAX_X_FRACTION


# --- B: row extends too far into the reading pane (general case) ---

def test_B_row_extends_too_far_into_reading_pane_rejected():
    result = _validate_row([400.0, 350.0, 440.0, 700.0])  # x_max=700 -> 70%, deep in reading-pane territory
    assert result.valid is False
    assert result.reason == "bbox_extends_beyond_message_list"


# --- D: avatar/checkbox-only sliver (too narrow) ---

def test_D_avatar_only_sliver_too_narrow_rejected():
    result = _validate_row([400.0, 200.0, 440.0, 230.0])  # width=30 -> 3%, well under the 8% floor
    assert result.valid is False
    assert result.reason == "bbox_too_narrow"


# --- E: excessively wide row ---

def test_E_excessively_wide_row_rejected():
    result = _validate_row([400.0, 180.0, 440.0, 500.0])  # width=320 -> 32%, still under both boundaries
    assert result.valid is True  # sanity: this one IS plausible
    result_too_wide = _validate_row([400.0, 20.0, 440.0, 520.0])  # width=500 -> 50%, implausibly wide
    assert result_too_wide.valid is False
    assert result_too_wide.reason == "bbox_too_wide"


# --- F: implausible height (too short / too tall) ---

def test_F_sliver_height_too_short_rejected():
    result = _validate_row([400.0, 200.0, 405.0, 450.0])  # height=5 -> 0.5%, far below the 2% floor
    assert result.valid is False
    assert result.reason == "bbox_too_short"


def test_F_implausibly_tall_row_rejected():
    result = _validate_row([200.0, 200.0, 700.0, 450.0])  # height=500 -> 50%, spans half the screen
    assert result.valid is False
    assert result.reason == "bbox_too_tall"


# --- G: resolution-independence — same normalized bbox, different screens ---

def test_G_same_normalized_outcome_across_resolutions():
    row_bbox = [418.0, 253.0, 492.0, 572.0]  # the live-failure geometry
    for width, height in [(1920, 1080), (2560, 1440), (1366, 768)]:
        result = _validate_row(row_bbox, image_width=width, image_height=height)
        assert result.valid is False, (width, height)
        assert result.reason == "bbox_extends_beyond_message_list", (width, height)


# --- 11: good-row fixture (qualitative R&D ground-truth geometry) ---

def test_good_row_fixture_inside_message_list_column_is_accepted():
    """A well-formed row fixture representing the annotated R&D
    ground-truth geometry described in the live task: confined to the
    message-list column (avatar, sender, subject, preview, date/time),
    not extending into the reading pane. Exact historical pixel values
    are not available/used — only the qualitative shape (right of the
    sidebar, comfortably left of the reading pane, plausible row
    width/height) is asserted, per this fix's own policy of never
    hardcoding one screenshot's coordinates into production or test
    logic."""
    result = _validate_row([300.0, 200.0, 350.0, 480.0])
    assert result.valid is True
    assert result.reason is None
    assert result.bbox_right_x <= result.message_list_right_x


# --- degenerate/out-of-range bboxes are NOT this function's concern ---

def test_malformed_bbox_deferred_to_validate_grounding():
    """A degenerate or out-of-normalized-range bbox is validate_grounding()'s
    established concern (DEGENERATE_BBOX / OUT_OF_NORMALIZED_RANGE) — this
    function must not steal or reclassify that failure; it defers
    (valid=True, reason=None) on anything malformed."""
    assert _validate_row([500.0, 500.0, 500.0, 500.0]).valid is True  # degenerate
    assert _validate_row([480.0, 300.0, 520.0, 1500.0]).valid is True  # out of normalized range


# --- sidebar-adjacent left edge is NOT rejected at the bbox level ---

def test_left_edge_at_or_before_sidebar_boundary_not_rejected_by_this_check():
    """A row's bbox left edge sitting at/near the sidebar boundary is
    normal (Outlook's own checkbox/avatar strip) and is handled by
    biasing the CLICK POINT rightward (see app/outlook/find_email.py),
    never by rejecting the bbox itself here — this function only judges
    the RIGHT-side extent and overall shape."""
    result = _validate_row([400.0, 50.0, 440.0, 280.0])  # x_min=50, well left of the 170 sidebar boundary
    assert result.valid is True


# ==================================================================
# MESSAGE_LIST_RIGHT_MAX_X_FRACTION calibration (2026-09-06, same day
# follow-up): live evidence with prompt-hardened, geometry-refined
# Vision output showed a schema-valid, correctly-measured bbox (right
# edge placed at the last visible character of the row's own date
# field) still rejected by the original 0.525 midpoint-estimate
# boundary — its x_max landed at ~0.541 of screen width (1039px of
# 1920px). Recalibrated to 0.55: a small, deliberate step just past
# that observed real edge, not a broad relaxation. All other geometry
# rules (width/height bounds, malformed-bbox deferral, left-edge/
# sidebar policy) are unaffected and unchanged — see tests above.
# ==================================================================

def test_bbox_ending_just_inside_calibrated_boundary_is_valid():
    result = _validate_row([400.0, 250.0, 440.0, 545.0])  # x_max=545 -> 0.545, just inside 0.55
    assert result.valid is True
    assert result.reason is None


def test_bbox_ending_exactly_at_calibrated_boundary_is_valid():
    """Inclusive boundary policy (unchanged by this calibration): the
    check only rejects STRICTLY beyond the fraction (x_max_fraction >
    message_list_right_max_x_fraction), so a bbox landing exactly on
    the boundary is still valid."""
    result = _validate_row([400.0, 250.0, 440.0, 550.0])  # x_max=550 -> exactly 0.55
    assert result.valid is True
    assert result.reason is None


def test_bbox_slightly_beyond_calibrated_boundary_is_invalid():
    result = _validate_row([400.0, 250.0, 440.0, 560.0])  # x_max=560 -> 0.56, just past 0.55
    assert result.valid is False
    assert result.reason == "bbox_extends_beyond_message_list"


def test_latest_live_refined_bbox_now_passes_the_calibrated_boundary():
    """The exact refined-bbox geometry from the live evidence this
    calibration is based on: raw_bbox=[369, 258, 452, 541] on a
    1920x1080 screen (x_max=541 -> 0.541, screen x2 ~= 1039px) — Vision
    correctly measured the right edge to the row's own date text, and
    the calibrated boundary must now accept it."""
    result = _validate_row([369.0, 258.0, 452.0, 541.0])
    assert result.valid is True
    assert result.reason is None
    assert result.bbox_right_x == round(541 / 1000 * IMAGE_WIDTH)


def test_grossly_oversized_bbox_still_invalid_after_calibration():
    """Calibration only moved the boundary a small, deliberate amount —
    a bbox clearly extending deep into the reading pane must still be
    rejected."""
    result = _validate_row([385.0, 255.0, 460.0, 800.0])  # x_max=800 -> 0.8, deep in reading-pane territory
    assert result.valid is False
    assert result.reason == "bbox_extends_beyond_message_list"


def test_max_width_rule_still_independently_rejects_oversized_boxes_after_calibration():
    """EMAIL_ROW_MAX_WIDTH_FRACTION is untouched by this calibration —
    a bbox whose x_max is comfortably inside the new 0.55 boundary can
    still be rejected purely for being too wide."""
    result = _validate_row([400.0, 20.0, 440.0, 520.0])  # x_max=520 -> 0.52 (inside boundary), width=500 -> 0.50 (too wide)
    assert result.valid is False
    assert result.reason == "bbox_too_wide"


def test_calibrated_boundary_is_provider_neutral_by_construction():
    """validate_email_row_bbox() takes no provider identity/name
    parameter at all — the calibrated boundary is applied identically
    regardless of which Vision provider produced the bbox being
    validated, structurally, not by convention."""
    import inspect

    from app.safety.validators import validate_email_row_bbox as fn

    params = list(inspect.signature(fn).parameters)
    assert not any("provider" in p.lower() for p in params)

    # Same bbox, validated twice, standing in for "Claude's bbox" and
    # "Gemini's bbox" — identical outcome either way.
    claude_like = _validate_row([369.0, 258.0, 452.0, 541.0])
    gemini_like = _validate_row([369.0, 258.0, 452.0, 541.0])
    assert claude_like.valid == gemini_like.valid is True


def test_low_confidence_rejected():
    result = validate_grounding(
        raw_x=500, raw_y=500, box_2d=None, confidence=0.1,
        image_width=IMAGE_WIDTH, image_height=IMAGE_HEIGHT, confidence_threshold=0.6,
    )
    assert result.valid is False
    assert result.failure == GroundingCheckFailure.LOW_CONFIDENCE


def test_valid_grounding_without_bbox_uses_raw_point():
    result = validate_grounding(
        raw_x=500, raw_y=500, box_2d=None, confidence=0.9,
        image_width=IMAGE_WIDTH, image_height=IMAGE_HEIGHT, confidence_threshold=0.6,
    )
    assert result.valid is True
    assert result.used_bbox is False
    assert result.converted_x == round(500 / 1000 * IMAGE_WIDTH)
