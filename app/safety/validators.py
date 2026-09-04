"""Shared, deterministic grounding-result validation.

Extracted from app/playbook/find_open_email_steps.py::ground_target_email()
(RND-009C/D) into one reusable function — the box_2d self-consistency
check, pixel-bounds check, sidebar-fraction rejection, and confidence
threshold were previously inline in that one method; they are the same
checks any bbox-grounded click target (email row, Reply, Send) needs,
so they now live in one place instead of being re-copied per target.

Per the "bbox over loose points" rule: a model-provided loose x/y is
never the strongest source of truth when a validated bbox is available.
If Vision returns a box_2d, the click point is computed from that box's
center by THIS function — never taken as Vision's raw x/y directly.
When no box_2d is provided, the raw x/y is validated the same way
(bounds/sidebar/confidence) but flagged `used_bbox=False` so callers can
apply a stricter confidence bar to that fallback path if they choose.

The sidebar-fraction check is a SECONDARY safety heuristic only — an
independent, code-side sanity backstop that fires regardless of what
Vision claims, never the primary source of where UI regions are (that
comes from Vision's own per-request report of the current screenshot).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from app.vision.grounding import coordinate_in_image_bounds, normalize_1000_to_pixels


class GroundingCheckFailure(str, Enum):
    INVALID_BBOX = "invalid_bbox"
    DEGENERATE_BBOX = "degenerate_bbox"
    OUT_OF_NORMALIZED_RANGE = "out_of_normalized_range"
    POINT_OUTSIDE_BBOX = "point_outside_bbox"
    OUT_OF_BOUNDS = "out_of_bounds"
    SIDEBAR_REJECTED = "sidebar_rejected"
    LOW_CONFIDENCE = "low_confidence"
    MISSING_COORDINATES = "missing_coordinates"


NORMALIZED_MIN = 0.0
NORMALIZED_MAX = 1000.0


@dataclass
class GroundingValidationResult:
    valid: bool
    converted_x: Optional[int] = None
    converted_y: Optional[int] = None
    used_bbox: bool = False
    failure: Optional[GroundingCheckFailure] = None
    notes: str = ""


def validate_grounding(
    raw_x: Optional[float],
    raw_y: Optional[float],
    box_2d: Optional[list[float]],
    confidence: float,
    image_width: int,
    image_height: int,
    confidence_threshold: float,
    sidebar_max_x_fraction: Optional[float] = None,
    prefer_provided_point: bool = False,
) -> GroundingValidationResult:
    """box_2d, if present, is [y_min, x_min, y_max, x_max] in the same
    0-1000 normalized space as raw_x/raw_y. sidebar_max_x_fraction is
    optional — pass it only for targets where the sidebar-rejection
    heuristic is meaningful (e.g. the message-list pane); omit it for
    targets (e.g. Reply, Send) where the sidebar is never a plausible
    location and the check would be meaningless.

    prefer_provided_point (2026-09-04, default False — every existing
    caller's behavior is UNCHANGED): the "bbox over loose points" rule
    below exists to protect against trusting VISION's own self-reported
    (raw_x, raw_y) over its own bbox. It was never meant to override a
    point the CALLING CODE itself deterministically computed from the
    bbox (e.g. a click point deliberately biased toward a row's text
    region — see app/outlook/find_email.py's MESSAGE_ROW_CLICK_X_
    PROPORTION). Passing True here means: raw_x/raw_y is a chosen point,
    already required (by the point-in-box check below) to lie inside
    box_2d — use IT as the click point, not the bbox's plain center."""
    if raw_x is None or raw_y is None:
        return GroundingValidationResult(valid=False, failure=GroundingCheckFailure.MISSING_COORDINATES)

    # Never trust a provider's self-described coordinate convention —
    # historical R&D established Gemini's is 0-1000 normalized, and every
    # value returned (loose point AND bbox) is checked against that
    # range explicitly, before any conversion is attempted.
    if not (NORMALIZED_MIN <= raw_x <= NORMALIZED_MAX and NORMALIZED_MIN <= raw_y <= NORMALIZED_MAX):
        return GroundingValidationResult(
            valid=False,
            failure=GroundingCheckFailure.OUT_OF_NORMALIZED_RANGE,
            notes=f"Point ({raw_x}, {raw_y}) is outside the expected 0-1000 normalized range.",
        )

    used_bbox = False
    click_raw_x, click_raw_y = raw_x, raw_y

    if box_2d is not None:
        if len(box_2d) != 4:
            return GroundingValidationResult(
                valid=False,
                failure=GroundingCheckFailure.INVALID_BBOX,
                notes=f"box_2d did not have exactly 4 values: {box_2d!r}.",
            )
        y_min, x_min, y_max, x_max = box_2d
        if not all(NORMALIZED_MIN <= v <= NORMALIZED_MAX for v in box_2d):
            return GroundingValidationResult(
                valid=False,
                failure=GroundingCheckFailure.OUT_OF_NORMALIZED_RANGE,
                notes=f"box_2d {box_2d!r} has a component outside the expected 0-1000 normalized range.",
            )
        if not (x_max > x_min and y_max > y_min):
            return GroundingValidationResult(
                valid=False,
                failure=GroundingCheckFailure.DEGENERATE_BBOX,
                notes=f"box_2d {box_2d!r} has zero or negative width/height — not a real clickable region.",
            )
        point_in_box = x_min <= raw_x <= x_max and y_min <= raw_y <= y_max
        if not point_in_box:
            return GroundingValidationResult(
                valid=False,
                failure=GroundingCheckFailure.POINT_OUTSIDE_BBOX,
                notes=f"Returned point ({raw_x}, {raw_y}) falls outside its own returned box_2d {box_2d!r}.",
            )
        # Bbox is validated and self-consistent. By default the click
        # point is computed from the bbox CENTER, not the raw loose
        # point, per the "bbox over loose points" rule — UNLESS the
        # caller explicitly opted in to trusting its own already-
        # validated interior point instead (see prefer_provided_point's
        # docstring above).
        if not prefer_provided_point:
            click_raw_x = (x_min + x_max) / 2
            click_raw_y = (y_min + y_max) / 2
        used_bbox = True

    cx, cy = normalize_1000_to_pixels(click_raw_x, click_raw_y, image_width, image_height)
    cx, cy = round(cx), round(cy)

    if not coordinate_in_image_bounds(cx, cy, image_width, image_height):
        return GroundingValidationResult(
            valid=False, converted_x=cx, converted_y=cy, used_bbox=used_bbox,
            failure=GroundingCheckFailure.OUT_OF_BOUNDS,
        )

    if sidebar_max_x_fraction is not None:
        sidebar_boundary_x = image_width * sidebar_max_x_fraction
        if cx <= sidebar_boundary_x:
            return GroundingValidationResult(
                valid=False, converted_x=cx, converted_y=cy, used_bbox=used_bbox,
                failure=GroundingCheckFailure.SIDEBAR_REJECTED,
                notes=(
                    f"Converted coordinate ({cx}, {cy}) falls within the left "
                    f"navigation/sidebar exclusion zone (x <= {sidebar_boundary_x:.0f}, "
                    f"{sidebar_max_x_fraction:.0%} of {image_width}px width). "
                    "Rejected regardless of Vision's reported confidence."
                ),
            )

    if confidence < confidence_threshold:
        return GroundingValidationResult(
            valid=False, converted_x=cx, converted_y=cy, used_bbox=used_bbox,
            failure=GroundingCheckFailure.LOW_CONFIDENCE,
            notes=f"Confidence {confidence} below threshold {confidence_threshold}.",
        )

    return GroundingValidationResult(valid=True, converted_x=cx, converted_y=cy, used_bbox=used_bbox)
