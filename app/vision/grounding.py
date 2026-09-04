"""Coordinate conversion + bounds checking for Vision-grounded clicks.

Merges rnd/metrics/coordinate_calibration.py::normalize_1000_to_pixels()
and rnd/metrics/grounding.py::coordinate_in_image_bounds() — the only
two functions from those modules actually used by the step logic (the
rnd/ originals also carry dataset-evaluation helpers tied to
BoundingBox/ground-truth datasets, which have no equivalent need in the
final POC runtime and are left behind in rnd/).
"""

from __future__ import annotations


def normalize_1000_to_pixels(raw_x: float, raw_y: float, image_width: int, image_height: int) -> tuple[float, float]:
    """Converts a coordinate in Gemini's documented 0-1000 normalized space
    to native pixel coordinates for the given image size.

        pixel = raw / 1000 * image_dimension

    raw=0 maps to pixel 0; raw=1000 maps to the image's far edge (i.e.
    image_width/image_height exactly — NOT the last valid pixel index);
    raw=500 maps to the image center.
    """
    pixel_x = raw_x / 1000 * image_width
    pixel_y = raw_y / 1000 * image_height
    return pixel_x, pixel_y


def coordinate_in_image_bounds(x: int, y: int, image_width: int, image_height: int) -> bool:
    return 0 <= x < image_width and 0 <= y < image_height
