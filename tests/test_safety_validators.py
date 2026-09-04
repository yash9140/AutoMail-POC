"""app/safety/validators.py::validate_grounding() unit tests."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.safety.validators import GroundingCheckFailure, validate_grounding  # noqa: E402

IMAGE_WIDTH = 1920
IMAGE_HEIGHT = 1080


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
