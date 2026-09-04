"""app/vision/grounding.py unit tests — the app/ copy of
rnd/metrics/coordinate_calibration.py::normalize_1000_to_pixels() and
rnd/metrics/grounding.py::coordinate_in_image_bounds(). See
tests/test_coordinate_calibration.py for the equivalent rnd/ coverage
(left untouched, still validates the rnd/ originals used by
rnd/experiments/*.py scripts).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.vision.grounding import coordinate_in_image_bounds, normalize_1000_to_pixels  # noqa: E402

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


def test_coordinate_in_bounds():
    assert coordinate_in_image_bounds(0, 0, IMAGE_WIDTH, IMAGE_HEIGHT) is True
    assert coordinate_in_image_bounds(IMAGE_WIDTH - 1, IMAGE_HEIGHT - 1, IMAGE_WIDTH, IMAGE_HEIGHT) is True


def test_coordinate_out_of_bounds():
    assert coordinate_in_image_bounds(IMAGE_WIDTH, 0, IMAGE_WIDTH, IMAGE_HEIGHT) is False
    assert coordinate_in_image_bounds(-1, 0, IMAGE_WIDTH, IMAGE_HEIGHT) is False
