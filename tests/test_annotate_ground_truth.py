"""Unit tests for the annotation tool's target-queue parsing (pure logic,
no GUI interaction needed).
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rnd.experiments.annotate_ground_truth import (  # noqa: E402
    compute_display_scale,
    parse_target_queue,
    to_original_bbox,
    to_original_coords,
)


def test_empty_spec_returns_empty_queue():
    assert parse_target_queue(None) == []
    assert parse_target_queue("") == []


def test_single_target_with_explicit_type():
    assert parse_target_queue("email_row:row") == [("email_row", "row")]


def test_single_target_defaults_to_button_type():
    assert parse_target_queue("Reply") == [("Reply", "button")]


def test_multiple_targets_preserve_order():
    assert parse_target_queue("Reply:button,ReplyAll:button,editor:text_area") == [
        ("Reply", "button"),
        ("ReplyAll", "button"),
        ("editor", "text_area"),
    ]


def test_invalid_type_raises():
    with pytest.raises(ValueError):
        parse_target_queue("Reply:not_a_real_type")


# --- Coordinate conversion (the OUTLOOK-005 clipping bug fix) ---
# A 1920x1080 screenshot fit into a smaller available area, e.g. 1600x900
# (matching the old hardcoded MAX_DISPLAY_WIDTH/HEIGHT), giving scale 0.833(3).

ORIG_W, ORIG_H = 1920, 1080
AVAIL_W, AVAIL_H = 1600, 900


def test_compute_display_scale_fits_aspect_ratio_preserving():
    scale = compute_display_scale(ORIG_W, ORIG_H, AVAIL_W, AVAIL_H)
    assert scale == pytest.approx(900 / 1080)  # height is the binding constraint here
    assert round(ORIG_W * scale) <= AVAIL_W
    assert round(ORIG_H * scale) <= AVAIL_H


def test_compute_display_scale_never_upscales_small_images():
    scale = compute_display_scale(100, 100, 1600, 900)
    assert scale == 1.0


def test_to_original_coords_top_left():
    scale = compute_display_scale(ORIG_W, ORIG_H, AVAIL_W, AVAIL_H)
    assert to_original_coords(0, 0, scale) == (0, 0)


def test_to_original_coords_center_round_trips_within_rounding_tolerance():
    scale = compute_display_scale(ORIG_W, ORIG_H, AVAIL_W, AVAIL_H)
    display_x, display_y = (ORIG_W * scale) / 2, (ORIG_H * scale) / 2
    ox, oy = to_original_coords(display_x, display_y, scale)
    assert ox == pytest.approx(ORIG_W / 2, abs=1)
    assert oy == pytest.approx(ORIG_H / 2, abs=1)


def test_to_original_coords_bottom_right_reaches_full_original_extent():
    scale = compute_display_scale(ORIG_W, ORIG_H, AVAIL_W, AVAIL_H)
    display_w, display_h = ORIG_W * scale, ORIG_H * scale
    ox, oy = to_original_coords(display_w, display_h, scale)
    # This is the exact bug: with the old fixed-size/DPI-unaware canvas, the
    # bottom edge of the display area never corresponded to the image's
    # true bottom edge on screen. Here the math itself must reach it exactly.
    assert ox == ORIG_W
    assert oy == ORIG_H


def test_to_original_bbox_converts_and_orders_all_four_corners():
    scale = compute_display_scale(ORIG_W, ORIG_H, AVAIL_W, AVAIL_H)
    # Drawn "backwards" (bottom-right to top-left), as a real drag might be.
    dx0, dy0 = ORIG_W * scale, ORIG_H * scale
    dx1, dy1 = 0, 0
    x0, y0, x1, y1 = to_original_bbox(dx0, dy0, dx1, dy1, scale)
    assert (x0, y0, x1, y1) == (0, 0, ORIG_W, ORIG_H)
    assert x0 < x1
    assert y0 < y1
