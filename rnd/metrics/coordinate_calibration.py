"""RND-005A coordinate-system calibration — pure geometry, no AI, no new
API calls. Tests the hypothesis that Gemini's `ui_grounding_v1` coordinate
predictions are normalized to a 0-1000 space (Google's documented
convention for Gemini spatial/bounding-box output — see
docs/07A_Coordinate_Calibration.md for the citation) rather than native
screenshot pixels, which is what RND-005's original PASS/FAIL scoring
assumed.
"""

from __future__ import annotations


def normalize_1000_to_pixels(raw_x: float, raw_y: float, image_width: int, image_height: int) -> tuple[float, float]:
    """Converts a coordinate in Gemini's documented 0-1000 normalized space
    to native pixel coordinates for the given image size.

        pixel = raw / 1000 * image_dimension

    raw=0 maps to pixel 0; raw=1000 maps to the image's far edge (i.e.
    image_width/image_height exactly — NOT the last valid pixel index,
    which is image_width-1/image_height-1); raw=500 maps to the image
    center.
    """
    pixel_x = raw_x / 1000 * image_width
    pixel_y = raw_y / 1000 * image_height
    return pixel_x, pixel_y
