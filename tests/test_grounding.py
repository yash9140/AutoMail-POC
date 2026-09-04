"""Unit tests for the RND-005 local grounding evaluator
(rnd/metrics/grounding.py). Pure geometry, no network, no AI.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rnd.metrics.grounding import (  # noqa: E402
    aggregate_grounding,
    classify_grounding_result,
    coordinate_in_image_bounds,
    distance_to_bbox_center_px,
    distance_to_bbox_px,
    is_high_confidence_failure,
    point_in_bbox,
)
from rnd.models.dataset_manifest import BoundingBox  # noqa: E402

BBOX = BoundingBox(x1=866, y1=664, x2=973, y2=702)  # OUTLOOK-003 Reply, real ground truth


def test_point_inside_bbox():
    assert point_in_bbox(900, 680, BBOX) is True


def test_point_on_bbox_boundary_is_pass():
    assert point_in_bbox(866, 664, BBOX) is True  # top-left corner
    assert point_in_bbox(973, 702, BBOX) is True  # bottom-right corner
    assert point_in_bbox(866, 680, BBOX) is True  # left edge, mid-height
    assert point_in_bbox(900, 702, BBOX) is True  # bottom edge, mid-width


def test_point_outside_bbox():
    assert point_in_bbox(478, 630, BBOX) is False  # the real RND-003 miss
    assert point_in_bbox(974, 680, BBOX) is False  # one pixel past x2
    assert point_in_bbox(900, 703, BBOX) is False  # one pixel past y2


def test_coordinate_in_image_bounds_valid():
    assert coordinate_in_image_bounds(900, 680, 1920, 1080) is True
    assert coordinate_in_image_bounds(0, 0, 1920, 1080) is True


def test_coordinate_in_image_bounds_invalid():
    assert coordinate_in_image_bounds(1920, 680, 1920, 1080) is False
    assert coordinate_in_image_bounds(-1, 680, 1920, 1080) is False
    assert coordinate_in_image_bounds(900, 1080, 1920, 1080) is False


def test_distance_to_bbox_zero_when_inside():
    assert distance_to_bbox_px(900, 680, BBOX) == 0.0


def test_distance_to_bbox_zero_on_boundary():
    assert distance_to_bbox_px(866, 664, BBOX) == 0.0


def test_distance_to_bbox_positive_when_outside():
    # (478, 630): clamps to (866, 664) — the bbox's top-left corner.
    d = distance_to_bbox_px(478, 630, BBOX)
    expected = ((478 - 866) ** 2 + (630 - 664) ** 2) ** 0.5
    assert abs(d - expected) < 1e-6
    assert d > 0


def test_distance_to_bbox_center():
    center_x, center_y = (866 + 973) / 2, (664 + 702) / 2
    d = distance_to_bbox_center_px(478, 630, BBOX)
    expected = ((478 - center_x) ** 2 + (630 - center_y) ** 2) ** 0.5
    assert abs(d - expected) < 1e-6


def test_classify_grounding_result_pass():
    assert classify_grounding_result(900, 680, BBOX) == "PASS"


def test_classify_grounding_result_fail():
    assert classify_grounding_result(478, 630, BBOX) == "FAIL"


def test_classify_grounding_result_error_when_no_coordinate():
    assert classify_grounding_result(None, None, BBOX) == "ERROR"
    assert classify_grounding_result(900, 680, None) == "ERROR"


def test_high_confidence_failure_flagged():
    assert is_high_confidence_failure(0.95, "FAIL") is True
    assert is_high_confidence_failure(0.8, "FAIL") is True  # boundary, inclusive


def test_high_confidence_failure_not_flagged_when_pass():
    assert is_high_confidence_failure(0.95, "PASS") is False


def test_high_confidence_failure_not_flagged_below_threshold():
    assert is_high_confidence_failure(0.5, "FAIL") is False


def test_high_confidence_failure_not_flagged_when_no_confidence():
    assert is_high_confidence_failure(None, "FAIL") is False


def test_aggregate_grounding_all_pass():
    results = [
        {"target": "email_row", "grounding_result": "PASS"},
        {"target": "Reply", "grounding_result": "PASS"},
        {"target": "reply_editor", "grounding_result": "PASS"},
        {"target": "Send", "grounding_result": "PASS"},
    ]
    agg = aggregate_grounding(results)
    assert agg["passed"] == 4
    assert agg["total"] == 4
    assert agg["n"] == 4
    assert agg["percentage"] == 100.0


def test_aggregate_grounding_mixed():
    results = [
        {"target": "email_row", "grounding_result": "PASS"},
        {"target": "Reply", "grounding_result": "FAIL"},
        {"target": "reply_editor", "grounding_result": "PASS"},
        {"target": "Send", "grounding_result": "FAIL"},
    ]
    agg = aggregate_grounding(results)
    assert agg["passed"] == 2
    assert agg["total"] == 4
    assert agg["percentage"] == 50.0
    assert agg["per_target"] == {"email_row": "PASS", "Reply": "FAIL", "reply_editor": "PASS", "Send": "FAIL"}
