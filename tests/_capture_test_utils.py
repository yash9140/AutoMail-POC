"""Shared test-only helpers (2026-09-06, message-list crop rollout).

app.vision.crop.create_message_list_crop() opens the screenshot's own
file with Pillow — a MagicMock'd screen-capture result with a path that
doesn't exist on disk (the pre-crop convention throughout this test
suite, e.g. `MagicMock(path="inbox.png", ...)`) is no longer sufficient
for any test that exercises TARGET_EMAIL_SEARCH / row-identity-refine /
row-bbox-refine, since those now always crop the capture's own image
before sending it to Vision. real_capture_image_path() gives tests a
real, tiny, on-disk PNG at the requested pixel size instead.

to_crop_relative_bbox() lets existing test fixtures keep expressing
row_bbox values in the FULL-SCREEN normalized space they always have
(matching what validate_email_row_bbox/validate_grounding/click-point
math downstream still expects) while feeding the mocked Vision response
what it would actually return now: the SAME bbox expressed relative to
the message-list crop. Production remaps crop-relative bbox back to
full-screen via app.vision.crop.MessageListCrop.remap_bbox_to_full_screen()
— this is that same transform's exact mathematical inverse, so a bbox
run through to_crop_relative_bbox() and then through production's own
remap lands back on the original full-screen value (up to floating-
point rounding), meaning every existing test's INTENDED full-screen
geometry (valid or deliberately invalid) is preserved unchanged.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.settings import LEFT_SIDEBAR_MAX_X_FRACTION, MESSAGE_LIST_RIGHT_MAX_X_FRACTION  # noqa: E402
from app.vision.crop import compute_message_list_crop_bounds  # noqa: E402

_REAL_PNG_CACHE: dict[tuple[int, int], Path] = {}


def real_capture_image_path(width: int = 1920, height: int = 1080, name: Optional[str] = None) -> str:
    """Returns a real, on-disk PNG at the given pixel size — needed
    because app.vision.crop.create_message_list_crop() opens the capture
    path with Pillow; a MagicMock'd nonexistent path (this test suite's
    pre-crop convention) is no longer sufficient.

    When `name` is given, the file is written under that EXACT basename
    (so a test that needs capture.path to still be distinguishable by a
    specific logical filename — e.g. to prove a re-ground used a
    DIFFERENT, fresh screenshot rather than the original — gets a real,
    per-name file). Otherwise a shared, dimension-keyed file is reused
    across calls."""
    from PIL import Image

    if name is not None:
        path = Path(tempfile.gettempdir()) / name
        Image.new("RGB", (width, height), color=(32, 32, 32)).save(path)
        return str(path)

    cached = _REAL_PNG_CACHE.get((width, height))
    if cached is not None and cached.exists():
        return str(cached)
    path = Path(tempfile.gettempdir()) / f"autolook_test_capture_{width}x{height}.png"
    if not path.exists():
        Image.new("RGB", (width, height), color=(32, 32, 32)).save(path)
    _REAL_PNG_CACHE[(width, height)] = path
    return str(path)


def to_reply_crop_relative_bbox(full_screen_bbox, width: int = 1920, height: int = 1080) -> list[float]:
    """Same round-trip idea as to_crop_relative_bbox(), for REPLY_SEARCH's
    full-height, right-side crop (app.vision.crop.create_reply_vision_crop,
    left edge REPLY_VISION_CROP_LEFT_FRACTION, right edge = full width) —
    lets existing Reply test fixtures keep expressing bbox values in the
    FULL-SCREEN normalized space they've always intended, while feeding
    the mocked REPLY_SEARCH response what it actually returns now."""
    from app.config.settings import REPLY_VISION_CROP_LEFT_FRACTION

    return to_crop_relative_bbox(
        full_screen_bbox, width, height,
        left_sidebar_max_x_fraction=REPLY_VISION_CROP_LEFT_FRACTION, message_list_right_max_x_fraction=1.0,
    )


def to_crop_relative_bbox(
    full_screen_bbox,
    width: int = 1920,
    height: int = 1080,
    left_sidebar_max_x_fraction: float = LEFT_SIDEBAR_MAX_X_FRACTION,
    message_list_right_max_x_fraction: float = MESSAGE_LIST_RIGHT_MAX_X_FRACTION,
) -> list[float]:
    if full_screen_bbox is None:
        return None
    left, top, right, bottom = compute_message_list_crop_bounds(
        width, height, left_sidebar_max_x_fraction, message_list_right_max_x_fraction,
    )
    crop_width, crop_height = right - left, bottom - top
    y_min, x_min, y_max, x_max = full_screen_bbox

    def _convert(y: float, x: float) -> tuple[float, float]:
        full_px_x = x / 1000 * width
        full_px_y = y / 1000 * height
        crop_px_x = full_px_x - left
        crop_px_y = full_px_y - top
        return crop_px_y / crop_height * 1000, crop_px_x / crop_width * 1000

    y_min_c, x_min_c = _convert(y_min, x_min)
    y_max_c, x_max_c = _convert(y_max, x_max)
    return [y_min_c, x_min_c, y_max_c, x_max_c]
