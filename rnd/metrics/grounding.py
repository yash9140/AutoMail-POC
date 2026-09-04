"""RND-005 local grounding evaluator — deterministic geometry only, no AI.

PASS rule (inclusive boundaries, per RND-005 spec):
    bbox.x1 <= x <= bbox.x2  AND  bbox.y1 <= y <= bbox.y2
A point exactly on the boundary counts as PASS, not FAIL — this is a
closed rectangle, not an open one.
"""

from __future__ import annotations

import math
from typing import Optional

from rnd.models.dataset_manifest import BoundingBox

HIGH_CONFIDENCE_THRESHOLD = 0.8


def point_in_bbox(x: int, y: int, bbox: BoundingBox) -> bool:
    """Inclusive-boundary containment check — the RND-005 PASS rule."""
    return bbox.x1 <= x <= bbox.x2 and bbox.y1 <= y <= bbox.y2


def coordinate_in_image_bounds(x: int, y: int, image_width: int, image_height: int) -> bool:
    return 0 <= x < image_width and 0 <= y < image_height


def distance_to_bbox_px(x: int, y: int, bbox: BoundingBox) -> float:
    """Euclidean distance from (x, y) to the nearest point on the bbox
    rectangle. 0.0 if the point is inside (or on the boundary of) the bbox.
    """
    clamped_x = min(max(x, bbox.x1), bbox.x2)
    clamped_y = min(max(y, bbox.y1), bbox.y2)
    return math.hypot(x - clamped_x, y - clamped_y)


def distance_to_bbox_center_px(x: int, y: int, bbox: BoundingBox) -> float:
    center_x = (bbox.x1 + bbox.x2) / 2
    center_y = (bbox.y1 + bbox.y2) / 2
    return math.hypot(x - center_x, y - center_y)


def classify_grounding_result(x: Optional[int], y: Optional[int], bbox: Optional[BoundingBox]) -> str:
    """Returns "PASS", "FAIL", or "ERROR" (no coordinate to evaluate)."""
    if x is None or y is None or bbox is None:
        return "ERROR"
    return "PASS" if point_in_bbox(x, y, bbox) else "FAIL"


def is_high_confidence_failure(
    confidence: Optional[float], grounding_result: str, threshold: float = HIGH_CONFIDENCE_THRESHOLD
) -> bool:
    if confidence is None:
        return False
    return confidence >= threshold and grounding_result == "FAIL"


def aggregate_grounding(results: list[dict]) -> dict:
    """results: list of {"target": str, "grounding_result": "PASS"|"FAIL"|"ERROR"}.

    Returns per-target results plus an overall N/total, with N explicitly
    stated per the RND-005 instruction not to imply a broad benchmark.
    """
    per_target = {r["target"]: r["grounding_result"] for r in results}
    total = len(results)
    passed = sum(1 for r in results if r["grounding_result"] == "PASS")
    return {
        "per_target": per_target,
        "passed": passed,
        "total": total,
        "n": total,
        "percentage": round((passed / total) * 100, 1) if total else 0.0,
    }
