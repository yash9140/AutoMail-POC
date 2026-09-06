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


@dataclass
class EmailRowBboxValidation:
    """Result of validate_email_row_bbox() — see that function's
    docstring. Pixel fields are always populated (best-effort) even when
    invalid, so callers can log the exact geometry that was rejected."""

    valid: bool
    reason: Optional[str] = None
    message_list_left_x: int = 0
    message_list_right_x: int = 0
    bbox_left_x: int = 0
    bbox_right_x: int = 0
    bbox_top_y: int = 0
    bbox_bottom_y: int = 0
    bbox_width_px: int = 0
    bbox_height_px: int = 0


# The subset of validate_email_row_bbox()'s failure `reason` values that
# describe a pure ROW-SHAPE/GEOMETRY problem with an otherwise-already-
# accepted candidate — as opposed to "no candidate", "wrong sender/
# subject", "ambiguous", or a malformed/technical response, none of
# which this set ever includes. Used by app/outlook/find_email.py to
# decide whether a bounded, same-screenshot bbox-refinement request is
# even eligible (2026-09-06) — see _refine_row_bbox()'s docstring there.
EMAIL_ROW_BBOX_GEOMETRY_REASONS = frozenset({
    "bbox_extends_beyond_message_list",
    "bbox_too_wide",
    "bbox_too_narrow",
    "bbox_too_tall",
    "bbox_too_short",
})


def validate_email_row_bbox(
    row_bbox: list[float],
    image_width: int,
    image_height: int,
    message_list_right_max_x_fraction: float,
    min_row_width_fraction: float,
    max_row_width_fraction: float,
    min_row_height_fraction: float,
    max_row_height_fraction: float,
    sidebar_max_x_fraction: Optional[float] = None,
) -> EmailRowBboxValidation:
    """Message-list ROW-SHAPE plausibility check — a distinct, additional
    concern from validate_grounding()'s self-consistency checks (degenerate/
    out-of-normalized-range bboxes stay validate_grounding()'s job; this
    function assumes row_bbox already has exactly 4 numeric values and
    quietly defers — returns valid=True — on anything malformed, so it
    never steals or reclassifies a failure that validate_grounding()
    already has an established, tested failure_reason for).

    Added 2026-09-06 after a live run: Vision returned a schema-valid,
    self-consistent row_bbox whose right edge (x_max) extended well past
    the real Outlook message-list column and into the reading pane
    (~57% of screen width) — clicking any fraction of that bbox risked
    landing outside the actual on-screen row. Nothing previously checked
    the bbox's own plausibility as a message-list row; only the
    DERIVED CLICK POINT's sidebar safety was checked (see
    LEFT_SIDEBAR_MAX_X_FRACTION / SIDEBAR_REJECTED above), which is a
    different, already-working check left completely unchanged here.

    Deliberately does NOT reject a bbox merely for having x_min left of
    the sidebar boundary — that is a separate, already-tested, working
    policy (see app/outlook/find_email.py's MESSAGE_ROW_CLICK_SAFE_ZONE_
    FRACTION-based click-point derivation / SIDEBAR_SAFETY_BUFFER_
    NORMALIZED): a row's bbox left edge sitting at or slightly before
    that boundary is normal (Outlook's own checkbox/avatar strip), and is
    handled by the CLICK POINT's own clamping, not by rejecting the
    bbox. This function only judges the bbox's RIGHT-side extent and
    overall shape — never its left edge.

    message_list_right_max_x_fraction, the width/height fraction bounds,
    and sidebar_max_x_fraction (diagnostic-only here, used only to report
    message_list_left_x) are all caller-supplied fractions of screen
    width/height — never fixed pixels — so behavior is identical at any
    resolution for the same normalized bbox."""
    y_min, x_min, y_max, x_max = row_bbox

    message_list_left_x = round((sidebar_max_x_fraction or 0.0) * image_width)
    message_list_right_x = round(message_list_right_max_x_fraction * image_width)
    bbox_left_x = round(x_min / 1000 * image_width)
    bbox_right_x = round(x_max / 1000 * image_width)
    bbox_top_y = round(y_min / 1000 * image_height)
    bbox_bottom_y = round(y_max / 1000 * image_height)

    diagnostics = dict(
        message_list_left_x=message_list_left_x, message_list_right_x=message_list_right_x,
        bbox_left_x=bbox_left_x, bbox_right_x=bbox_right_x,
        bbox_top_y=bbox_top_y, bbox_bottom_y=bbox_bottom_y,
        bbox_width_px=bbox_right_x - bbox_left_x, bbox_height_px=bbox_bottom_y - bbox_top_y,
    )

    well_formed = (
        all(NORMALIZED_MIN <= v <= NORMALIZED_MAX for v in row_bbox) and x_max > x_min and y_max > y_min
    )
    if not well_formed:
        # Not this function's concern — validate_grounding() classifies
        # degenerate/out-of-range bboxes with its own established reasons.
        return EmailRowBboxValidation(valid=True, reason=None, **diagnostics)

    width_fraction = (x_max - x_min) / 1000.0
    height_fraction = (y_max - y_min) / 1000.0
    x_max_fraction = x_max / 1000.0

    reason: Optional[str] = None
    if x_max_fraction > message_list_right_max_x_fraction:
        reason = "bbox_extends_beyond_message_list"
    elif width_fraction < min_row_width_fraction:
        reason = "bbox_too_narrow"
    elif width_fraction > max_row_width_fraction:
        reason = "bbox_too_wide"
    elif height_fraction < min_row_height_fraction:
        reason = "bbox_too_short"
    elif height_fraction > max_row_height_fraction:
        reason = "bbox_too_tall"

    return EmailRowBboxValidation(valid=(reason is None), reason=reason, **diagnostics)


@dataclass
class CrossStageValidationResult:
    """Result of validate_send_candidate_against_action_bar() — see that
    function's docstring for the invariant this enforces."""

    accepted: bool
    send_center_x: float
    send_center_y: float
    center_inside_action_bar: bool
    bbox_intersects_action_bar: bool
    reason: str = ""


def validate_send_candidate_against_action_bar(
    action_bar_bbox: list[float],
    send_bbox: list[float],
) -> CrossStageValidationResult:
    """Cross-stage consistency gate for the two-stage Send-grounding
    architecture (2026-09-06 follow-up to that architecture's own static
    production-path regression, which found Stage 2 correct in 4/5
    trials — the miss landed on a DIFFERENT nearby control ~65-70px away
    from the real Send button while Stage 2 still semantically reported
    "Send"). Stage 1 (SEND_COMPOSER_LOCALIZATION) already localizes the
    reply composer's action-bar ROW as a whole (Send + dropdown +
    Discard). Stage 2 (SEND_GROUNDING) reports one specific control it
    believes is the primary Send button. If Stage 2's own candidate
    falls outside the region Stage 1 itself identified as containing
    Send, the two Vision stages CONTRADICT each other — that is a
    semantic/safety disagreement between two independent observations,
    never a coordinate-precision issue, and must never become
    actionable.

    Both bboxes must already be in the SAME coordinate space/unit
    (either both full-screen pixels or both full-screen normalized
    0-1000) — the caller is responsible for that; this function is
    unit-agnostic pure geometry (no provider logic, no PyAutoGUI, no
    screenshot-specific hardcoded coordinates), each as
    [y_min, x_min, y_max, x_max].

    The primary (and currently sole) acceptance invariant is
    center_inside_action_bar: the Stage-2 bbox's own center point must
    lie inside the Stage-1 action-bar bbox. bbox_intersects_action_bar
    is reported alongside it purely as a diagnostic — a tightly-cropped
    Send button whose bbox is fully nested inside the (typically larger)
    action-bar region already satisfies the center-inside check without
    needing full-bbox intersection, so intersection is not required for
    acceptance; a future finding that intersection is itself the better
    invariant would be a deliberate, separately-evidenced change, not an
    accidental byproduct of this function's addition.

    NEVER adds any pixel tolerance, hand-measured Send coordinate, or
    screenshot-specific threshold — those exist only as evaluation-side
    facts in benchmarks/claude/experiments/, never as a runtime
    constant."""
    ab_y_min, ab_x_min, ab_y_max, ab_x_max = action_bar_bbox
    sb_y_min, sb_x_min, sb_y_max, sb_x_max = send_bbox

    center_x = (sb_x_min + sb_x_max) / 2
    center_y = (sb_y_min + sb_y_max) / 2

    center_inside = (ab_x_min <= center_x <= ab_x_max) and (ab_y_min <= center_y <= ab_y_max)
    intersects = not (
        sb_x_max < ab_x_min or sb_x_min > ab_x_max or sb_y_max < ab_y_min or sb_y_min > ab_y_max
    )

    accepted = center_inside
    reason = (
        "Send candidate center lies inside the Stage-1 action-bar region."
        if accepted else
        "Send candidate center lies OUTSIDE the Stage-1 action-bar region — "
        "Stage 1 and Stage 2 disagree on where Send is."
    )
    return CrossStageValidationResult(
        accepted=accepted, send_center_x=center_x, send_center_y=center_y,
        center_inside_action_bar=center_inside, bbox_intersects_action_bar=intersects, reason=reason,
    )
