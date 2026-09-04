"""Deterministic evaluation-only metrics.

Everything here is for REPORTING evaluation quality — never consumed by
any runtime safety/click decision. Bbox format matches the rest of the
project: [y_min, x_min, y_max, x_max], normalized 0-1000.

Thresholds here (GROUNDING_IOU_PASS_THRESHOLD, etc.) are evaluation
report-quality bars only, chosen to be legible in a summary table —
they are NOT the runtime confidence/geometry thresholds
(app/config/settings.py::VISION_CONFIDENCE_THRESHOLD,
app/outlook/launch.py::MAX_PLAUSIBLE_RESULT_BBOX_NORMALIZED_SPAN), and
changing them has zero effect on runtime behavior.
"""

from __future__ import annotations

import statistics
from typing import Any, Optional

NORMALIZED_MIN = 0.0
NORMALIZED_MAX = 1000.0

# Evaluation-report-only bars — see module docstring.
GROUNDING_IOU_PASS_THRESHOLD = 0.30
HIGH_QUALITY_IOU_THRESHOLD = 0.70


def bbox_valid(bbox: Optional[list[float]]) -> bool:
    """Structural validity only: 4 values, each in [0, 1000], non-degenerate."""
    if bbox is None or len(bbox) != 4:
        return False
    y_min, x_min, y_max, x_max = bbox
    if not all(NORMALIZED_MIN <= v <= NORMALIZED_MAX for v in bbox):
        return False
    return x_max > x_min and y_max > y_min


def bbox_center(bbox: list[float]) -> tuple[float, float]:
    """Returns (x, y) center of a [y_min, x_min, y_max, x_max] bbox."""
    y_min, x_min, y_max, x_max = bbox
    return (x_min + x_max) / 2, (y_min + y_max) / 2


def point_in_bbox(point: tuple[float, float], bbox: list[float]) -> bool:
    x, y = point
    y_min, x_min, y_max, x_max = bbox
    return x_min <= x <= x_max and y_min <= y <= y_max


def point_valid(point: Optional[list[float]]) -> bool:
    """Structural validity for a [y, x] point (Experiments P1-P3): 2
    values, each in [0, 1000]."""
    if point is None or len(point) != 2:
        return False
    y, x = point
    return NORMALIZED_MIN <= y <= NORMALIZED_MAX and NORMALIZED_MIN <= x <= NORMALIZED_MAX


def point_grounding_metrics(
    predicted_point: Optional[list[float]], expected_bbox: Optional[list[float]],
    image_width: int, image_height: int,
) -> dict[str, Any]:
    """Point-in-target metrics for the interior-point experiments —
    predicted_point is [y, x] (0-1000 normalized, matching every other
    coordinate convention in this project); expected_bbox is
    [y_min, x_min, y_max, x_max]. Primary success metric is
    point_inside_expected_row per instruction — distances are secondary
    evidence, not a pass/fail bar on their own."""
    predicted_valid = point_valid(predicted_point)
    expected_valid = bbox_valid(expected_bbox)

    point_inside_expected_row = None
    normalized_distance_to_row_center = None
    pixel_distance_to_row_center = None

    if predicted_valid and expected_valid:
        py, px = predicted_point  # contract is [y, x]
        point_inside_expected_row = point_in_bbox((px, py), expected_bbox)
        cx, cy = bbox_center(expected_bbox)
        normalized_distance_to_row_center = ((px - cx) ** 2 + (py - cy) ** 2) ** 0.5
        px_pixels, py_pixels = px / 1000 * image_width, py / 1000 * image_height
        cx_pixels, cy_pixels = cx / 1000 * image_width, cy / 1000 * image_height
        pixel_distance_to_row_center = ((px_pixels - cx_pixels) ** 2 + (py_pixels - cy_pixels) ** 2) ** 0.5

    return {
        "predicted_point_valid": predicted_valid,
        "expected_bbox_valid": expected_valid,
        "point_inside_expected_row": point_inside_expected_row,
        "normalized_distance_to_row_center": (
            round(normalized_distance_to_row_center, 2) if normalized_distance_to_row_center is not None else None
        ),
        "pixel_distance_to_row_center": (
            round(pixel_distance_to_row_center, 1) if pixel_distance_to_row_center is not None else None
        ),
    }


def bbox_area(bbox: list[float]) -> float:
    y_min, x_min, y_max, x_max = bbox
    return max(0.0, x_max - x_min) * max(0.0, y_max - y_min)


def union_bbox(boxes: list[list[float]]) -> Optional[list[float]]:
    """Smallest [y_min, x_min, y_max, x_max] enclosing every valid box in
    `boxes`. None if no valid box is given. Used by the component/
    landmark grounding experiment to derive a "visual cluster" bbox from
    independently-located icon/label/sublabel boxes — evaluation-only,
    never a runtime click source."""
    valid = [b for b in boxes if bbox_valid(b)]
    if not valid:
        return None
    y_min = min(b[0] for b in valid)
    x_min = min(b[1] for b in valid)
    y_max = max(b[2] for b in valid)
    x_max = max(b[3] for b in valid)
    return [y_min, x_min, y_max, x_max]


def intersection_area(bbox_a: list[float], bbox_b: list[float]) -> float:
    y_min_a, x_min_a, y_max_a, x_max_a = bbox_a
    y_min_b, x_min_b, y_max_b, x_max_b = bbox_b
    ix_min, ix_max = max(x_min_a, x_min_b), min(x_max_a, x_max_b)
    iy_min, iy_max = max(y_min_a, y_min_b), min(y_max_a, y_max_b)
    return max(0.0, ix_max - ix_min) * max(0.0, iy_max - iy_min)


def iou(bbox_a: Optional[list[float]], bbox_b: Optional[list[float]]) -> Optional[float]:
    """Intersection-over-union of two [y_min, x_min, y_max, x_max] bboxes
    in the same normalized space. None if either bbox is missing/invalid."""
    if not bbox_valid(bbox_a) or not bbox_valid(bbox_b):
        return None
    inter = intersection_area(bbox_a, bbox_b)
    union = bbox_area(bbox_a) + bbox_area(bbox_b) - inter
    if union <= 0:
        return None
    return inter / union


def overlap_percentage(predicted_bbox: Optional[list[float]], expected_bbox: Optional[list[float]]) -> Optional[float]:
    """What fraction of the EXPECTED target's area is covered by the
    predicted bbox — distinct from IoU (which also penalizes a
    predicted bbox being too large). None if either bbox is missing/invalid."""
    if not bbox_valid(predicted_bbox) or not bbox_valid(expected_bbox):
        return None
    expected_area = bbox_area(expected_bbox)
    if expected_area <= 0:
        return None
    return intersection_area(predicted_bbox, expected_bbox) / expected_area


def grounding_metrics(predicted_bbox: Optional[list[float]], expected_bbox: Optional[list[float]]) -> dict[str, Any]:
    """Full deterministic bbox-comparison metric bundle for one case.
    Evaluation-report metrics only — see module docstring."""
    predicted_valid = bbox_valid(predicted_bbox)
    expected_valid = bbox_valid(expected_bbox)

    predicted_center_in_expected = None
    expected_center_in_predicted = None
    iou_value = None
    overlap_pct = None

    if predicted_valid and expected_valid:
        predicted_center_in_expected = point_in_bbox(bbox_center(predicted_bbox), expected_bbox)
        expected_center_in_predicted = point_in_bbox(bbox_center(expected_bbox), predicted_bbox)
        iou_value = iou(predicted_bbox, expected_bbox)
        overlap_pct = overlap_percentage(predicted_bbox, expected_bbox)

    grounding_pass = bool(iou_value is not None and iou_value >= GROUNDING_IOU_PASS_THRESHOLD)
    high_quality_grounding = bool(iou_value is not None and iou_value >= HIGH_QUALITY_IOU_THRESHOLD)

    return {
        "predicted_bbox_valid": predicted_valid,
        "expected_bbox_valid": expected_valid,
        "predicted_center_inside_expected": predicted_center_in_expected,
        "expected_center_inside_predicted": expected_center_in_predicted,
        "iou": iou_value,
        "overlap_percentage": overlap_pct,
        "grounding_pass": grounding_pass,
        "high_quality_grounding": high_quality_grounding,
    }


def semantic_pass(actual: dict[str, Any], expected: dict[str, Any]) -> bool:
    """True only if every key in `expected` matches `actual` exactly
    (case-insensitive for string values, since label text formatting can
    legitimately vary). Missing keys in `actual` fail the check."""
    for key, expected_value in expected.items():
        actual_value = actual.get(key)
        if isinstance(expected_value, str) and isinstance(actual_value, str):
            if actual_value.strip().lower() != expected_value.strip().lower():
                return False
        elif actual_value != expected_value:
            return False
    return True


def latency_summary(latencies_ms: list[float]) -> dict[str, Optional[float]]:
    values = [v for v in latencies_ms if v is not None]
    if not values:
        return {"count": 0, "min_ms": None, "max_ms": None, "median_ms": None, "avg_ms": None}
    return {
        "count": len(values),
        "min_ms": round(min(values), 1),
        "max_ms": round(max(values), 1),
        "median_ms": round(statistics.median(values), 1),
        "avg_ms": round(statistics.mean(values), 1),
    }


def consistency_across_runs(run_results: list[dict[str, Any]]) -> dict[str, Any]:
    """Given multiple runs of the SAME case (same screenshot, same
    prompt), measures whether Claude's response stayed materially the
    same. Only meaningful with 2+ runs. Compares schema validity,
    identity fields (any key present in every run's parsed_json that
    looks like an identity field — target_visible/target_type/
    visible_label/etc.), and pairwise bbox IoU."""
    if len(run_results) < 2:
        return {"runs": len(run_results), "applicable": False}

    schema_valid_flags = [r.get("schema_valid") for r in run_results]
    schema_consistent = len(set(schema_valid_flags)) == 1

    identity_keys = ["target_visible", "target_type", "visible_label", "visible_sublabel", "search_visible"]
    identity_consistency: dict[str, bool] = {}
    for key in identity_keys:
        values = [
            (r.get("parsed_json") or {}).get(key)
            for r in run_results if r.get("parsed_json") is not None
        ]
        if values:
            identity_consistency[key] = len(set(values)) == 1

    bboxes = [
        (r.get("parsed_json") or {}).get("bbox")
        for r in run_results if r.get("parsed_json") is not None
    ]
    pairwise_ious = []
    for i in range(len(bboxes)):
        for j in range(i + 1, len(bboxes)):
            pair_iou = iou(bboxes[i], bboxes[j])
            if pair_iou is not None:
                pairwise_ious.append(pair_iou)

    confidences = [
        (r.get("parsed_json") or {}).get("confidence")
        for r in run_results if r.get("parsed_json") is not None
    ]
    confidences = [c for c in confidences if isinstance(c, (int, float))]

    return {
        "runs": len(run_results),
        "applicable": True,
        "schema_valid_consistent": schema_consistent,
        "identity_field_consistency": identity_consistency,
        "bbox_pairwise_min_iou": round(min(pairwise_ious), 3) if pairwise_ious else None,
        "bbox_pairwise_avg_iou": round(statistics.mean(pairwise_ious), 3) if pairwise_ious else None,
        "confidence_min": min(confidences) if confidences else None,
        "confidence_max": max(confidences) if confidences else None,
        "confidence_spread": round(max(confidences) - min(confidences), 3) if len(confidences) >= 2 else None,
    }
