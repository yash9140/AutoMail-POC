"""Unit tests for RND-005A's coordinate calibration conversion
(rnd/metrics/coordinate_calibration.py). Pure math, no network, no AI.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rnd.metrics.coordinate_calibration import normalize_1000_to_pixels  # noqa: E402
from rnd.metrics.grounding import point_in_bbox  # noqa: E402
from rnd.models.dataset_manifest import BoundingBox  # noqa: E402

IMAGE_WIDTH = 1920
IMAGE_HEIGHT = 1080


def test_normalize_zero_maps_to_zero():
    px, py = normalize_1000_to_pixels(0, 0, IMAGE_WIDTH, IMAGE_HEIGHT)
    assert px == 0
    assert py == 0


def test_normalize_1000_maps_to_image_dimension_boundary():
    px, py = normalize_1000_to_pixels(1000, 1000, IMAGE_WIDTH, IMAGE_HEIGHT)
    assert px == IMAGE_WIDTH
    assert py == IMAGE_HEIGHT


def test_normalize_500_maps_to_image_center():
    px, py = normalize_1000_to_pixels(500, 500, IMAGE_WIDTH, IMAGE_HEIGHT)
    assert px == IMAGE_WIDTH / 2
    assert py == IMAGE_HEIGHT / 2


def test_normalize_x_scaling_independent_of_y():
    px, _ = normalize_1000_to_pixels(250, 0, IMAGE_WIDTH, IMAGE_HEIGHT)
    assert px == IMAGE_WIDTH * 0.25


def test_normalize_y_scaling_independent_of_x():
    _, py = normalize_1000_to_pixels(0, 750, IMAGE_WIDTH, IMAGE_HEIGHT)
    assert py == IMAGE_HEIGHT * 0.75


def test_normalize_scales_correctly_for_non_square_image():
    # A narrower/shorter image should scale x and y independently, not uniformly.
    px, py = normalize_1000_to_pixels(500, 500, 800, 400)
    assert px == 400
    assert py == 200


def test_bbox_evaluation_after_conversion_reply_target():
    # Real RND-005/RND-003 data: raw (478, 630) failed as native pixels
    # against the Reply bbox, but should PASS once converted from the
    # documented 0-1000 normalized space.
    bbox = BoundingBox(x1=866, y1=664, x2=973, y2=702)
    raw_x, raw_y = 478, 630

    # Native-pixel interpretation: FAIL (this is RND-005's original result).
    assert point_in_bbox(raw_x, raw_y, bbox) is False

    # Normalized interpretation: PASS.
    px, py = normalize_1000_to_pixels(raw_x, raw_y, IMAGE_WIDTH, IMAGE_HEIGHT)
    assert point_in_bbox(round(px), round(py), bbox) is True


def test_bbox_evaluation_after_conversion_send_target():
    bbox = BoundingBox(x1=800, y1=1022, x2=902, y2=1060)
    raw_x, raw_y = 443, 961

    assert point_in_bbox(raw_x, raw_y, bbox) is False

    px, py = normalize_1000_to_pixels(raw_x, raw_y, IMAGE_WIDTH, IMAGE_HEIGHT)
    assert point_in_bbox(round(px), round(py), bbox) is True
