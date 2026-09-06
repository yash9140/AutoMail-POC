"""Deterministic message-list crop utility (2026-09-06).

Static benchmark evidence (benchmarks/claude/experiments/
run_row_confusion_experiment.py + analyze_row_confusion_results.py, run
against the exact screenshot from a live wrong-email failure) showed
Claude's row/subject TEXT reading is reliable at full-screen resolution,
but its BBOX spatial grounding for a target message-list row is NOT:
0/5 trials correct, stably wrong by exactly one row. Cropping the
screenshot horizontally to ONLY the Outlook message-list column — while
retaining the FULL vertical image height — made bbox grounding 5/5
correct and faster. A tighter crop around just the target row's
neighborhood was tested too and made grounding WORSE (1/5, unstable) —
so this crop is deliberately never tighter than the message-list column
alone.

This module is provider-neutral and contains no Outlook business logic:
it only crops an existing screenshot file and converts coordinates
between the crop's own 0-1000 normalized space and the full screenshot's
0-1000 normalized space. It never mutates or overwrites the original
screenshot — that file is still needed unchanged for pre-click freshness
comparison, debugging, and full-screen coordinate remapping.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CROP_OUTPUT_DIR = PROJECT_ROOT / "screenshots" / "crops"


@dataclass
class MessageListCrop:
    """The result of cropping one screenshot to its message-list column.

    left/top/right/bottom are native PIXEL coordinates in the ORIGINAL
    (full-screen) image. width/height are the crop image's own pixel
    dimensions (right-left, bottom-top) — these are the dimensions Vision
    is told about and the space its returned bbox/points are relative to.
    original_width/original_height are the full screenshot's own pixel
    dimensions, needed to convert back."""

    crop_path: Path
    left: int
    top: int
    right: int
    bottom: int
    width: int
    height: int
    original_width: int
    original_height: int
    source_path: str

    def remap_bbox_to_full_screen(self, bbox_normalized: list[float]) -> list[float]:
        """Converts a [y_min, x_min, y_max, x_max] bbox, 0-1000 normalized
        relative to THIS crop, into the equivalent 0-1000 normalized bbox
        relative to the full original screenshot. Deterministic Python
        arithmetic only — never delegated to Vision."""
        y_min, x_min, y_max, x_max = bbox_normalized
        full_y_min, full_x_min = self._crop_normalized_to_full_normalized(y_min, x_min)
        full_y_max, full_x_max = self._crop_normalized_to_full_normalized(y_max, x_max)
        return [full_y_min, full_x_min, full_y_max, full_x_max]

    def remap_point_to_full_screen(self, x_normalized: float, y_normalized: float) -> tuple[float, float]:
        """Same conversion as remap_bbox_to_full_screen, for a single
        (x, y) point instead of a bbox. Returns (full_x_normalized,
        full_y_normalized)."""
        full_y, full_x = self._crop_normalized_to_full_normalized(y_normalized, x_normalized)
        return full_x, full_y

    def bbox_to_crop_relative(self, bbox_full_normalized: list[float]) -> list[float]:
        """The inverse of remap_bbox_to_full_screen — converts a bbox
        already expressed in full-screen 0-1000 normalized space into
        this crop's own 0-1000 normalized space. Used only to phrase a
        PREVIOUS full-screen bbox (e.g. for a refinement prompt's "here
        is what was reported before" context) in terms of the same crop
        image Vision is being shown this call — never used to feed a
        runtime click point."""
        y_min, x_min, y_max, x_max = bbox_full_normalized
        crop_y_min, crop_x_min = self._full_normalized_to_crop_normalized(y_min, x_min)
        crop_y_max, crop_x_max = self._full_normalized_to_crop_normalized(y_max, x_max)
        return [crop_y_min, crop_x_min, crop_y_max, crop_x_max]

    def _crop_normalized_to_full_normalized(self, y_normalized: float, x_normalized: float) -> tuple[float, float]:
        crop_px_x = x_normalized / 1000 * self.width
        crop_px_y = y_normalized / 1000 * self.height
        full_px_x = self.left + crop_px_x
        full_px_y = self.top + crop_px_y
        return (full_px_y / self.original_height * 1000, full_px_x / self.original_width * 1000)

    def _full_normalized_to_crop_normalized(self, y_normalized: float, x_normalized: float) -> tuple[float, float]:
        full_px_x = x_normalized / 1000 * self.original_width
        full_px_y = y_normalized / 1000 * self.original_height
        crop_px_x = full_px_x - self.left
        crop_px_y = full_px_y - self.top
        return (crop_px_y / self.height * 1000, crop_px_x / self.width * 1000)


def compute_message_list_crop_bounds(
    image_width: int, image_height: int,
    left_sidebar_max_x_fraction: float, message_list_right_max_x_fraction: float,
) -> tuple[int, int, int, int]:
    """Pure geometry — returns (left, top, right, bottom) native pixel
    bounds for the message-list column crop, retaining the FULL vertical
    image height. Split out from create_message_list_crop() so the
    bounds math can be tested/verified without touching the filesystem."""
    left = round(left_sidebar_max_x_fraction * image_width)
    right = round(message_list_right_max_x_fraction * image_width)
    return (left, 0, right, image_height)


def create_message_list_crop(
    image_path: Path,
    image_width: int,
    image_height: int,
    left_sidebar_max_x_fraction: float,
    message_list_right_max_x_fraction: float,
    output_dir: Path = DEFAULT_CROP_OUTPUT_DIR,
) -> MessageListCrop:
    """Crops ONLY the horizontal message-list column out of image_path,
    retaining the full vertical height, and saves it as a new file under
    output_dir — image_path itself is never modified.

    left_sidebar_max_x_fraction / message_list_right_max_x_fraction are
    the SAME existing normalized layout constants already used for click-
    X safe-zone and row-bbox geometry validation (app/config/settings.py)
    — this crop introduces no new/separate geometry threshold."""
    from PIL import Image

    left, top, right, bottom = compute_message_list_crop_bounds(
        image_width, image_height, left_sidebar_max_x_fraction, message_list_right_max_x_fraction,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    with Image.open(image_path) as source:
        cropped = source.convert("RGB").crop((left, top, right, bottom))
        crop_path = output_dir / f"{Path(image_path).stem}_message_list_crop.png"
        cropped.save(crop_path, "PNG")

    return MessageListCrop(
        crop_path=crop_path,
        left=left, top=top, right=right, bottom=bottom,
        width=right - left, height=bottom - top,
        original_width=image_width, original_height=image_height,
        source_path=str(image_path),
    )


# --- Generic full-height horizontal Vision crop (2026-09-06) --------------
# Added for REPLY_SEARCH (see app/config/settings.py::
# REPLY_VISION_CROP_LEFT_FRACTION for the calibration evidence) without
# touching MessageListCrop/create_message_list_crop above — TARGET_EMAIL_
# SEARCH's message-list crop and REPLY_SEARCH's reply-region crop are two
# independently-calibrated, independently-proven visual tasks that must
# never be confused with or accidentally substituted for each other, even
# though both are "crop horizontally, keep full height, remap the bbox
# back" — the same general pattern, reused here as a distinct type rather
# than by editing the existing one.


def _horizontal_crop_normalized_to_full_normalized(
    y_normalized: float, x_normalized: float,
    left: int, top: int, crop_width: int, crop_height: int, original_width: int, original_height: int,
) -> tuple[float, float]:
    crop_px_x = x_normalized / 1000 * crop_width
    crop_px_y = y_normalized / 1000 * crop_height
    full_px_x = left + crop_px_x
    full_px_y = top + crop_px_y
    return (full_px_y / original_height * 1000, full_px_x / original_width * 1000)


def _horizontal_full_normalized_to_crop_normalized(
    y_normalized: float, x_normalized: float,
    left: int, top: int, crop_width: int, crop_height: int, original_width: int, original_height: int,
) -> tuple[float, float]:
    full_px_x = x_normalized / 1000 * original_width
    full_px_y = y_normalized / 1000 * original_height
    crop_px_x = full_px_x - left
    crop_px_y = full_px_y - top
    return (crop_px_y / crop_height * 1000, crop_px_x / crop_width * 1000)


@dataclass
class HorizontalVisionCrop:
    """A generic full-height, horizontally-bounded Vision-input crop —
    the reusable shape behind REPLY_SEARCH's reply-region crop (and,
    structurally, MessageListCrop above, kept as its own separate type).

    left/top/right/bottom are native PIXEL coordinates in the ORIGINAL
    (full-screen) image. width/height are the crop image's own pixel
    dimensions — the dimensions Vision is told about and the space its
    returned bbox/points are relative to. original_width/original_height
    are the full screenshot's own pixel dimensions, needed to convert
    back."""

    crop_path: Path
    left: int
    top: int
    right: int
    bottom: int
    width: int
    height: int
    original_width: int
    original_height: int
    source_path: str

    def remap_bbox_to_full_screen(self, bbox_normalized: list[float]) -> list[float]:
        """Converts a [y_min, x_min, y_max, x_max] bbox, 0-1000 normalized
        relative to THIS crop, into the equivalent 0-1000 normalized bbox
        relative to the full original screenshot. Deterministic Python
        arithmetic only — never delegated to Vision."""
        y_min, x_min, y_max, x_max = bbox_normalized
        full_y_min, full_x_min = self._to_full(y_min, x_min)
        full_y_max, full_x_max = self._to_full(y_max, x_max)
        return [full_y_min, full_x_min, full_y_max, full_x_max]

    def remap_point_to_full_screen(self, x_normalized: float, y_normalized: float) -> tuple[float, float]:
        full_y, full_x = self._to_full(y_normalized, x_normalized)
        return full_x, full_y

    def bbox_to_crop_relative(self, bbox_full_normalized: list[float]) -> list[float]:
        """The inverse of remap_bbox_to_full_screen — converts a bbox
        already expressed in full-screen 0-1000 normalized space into
        this crop's own 0-1000 normalized space. Never used to feed a
        runtime click point."""
        y_min, x_min, y_max, x_max = bbox_full_normalized
        crop_y_min, crop_x_min = self._to_crop(y_min, x_min)
        crop_y_max, crop_x_max = self._to_crop(y_max, x_max)
        return [crop_y_min, crop_x_min, crop_y_max, crop_x_max]

    def _to_full(self, y_normalized: float, x_normalized: float) -> tuple[float, float]:
        return _horizontal_crop_normalized_to_full_normalized(
            y_normalized, x_normalized, self.left, self.top, self.width, self.height,
            self.original_width, self.original_height,
        )

    def _to_crop(self, y_normalized: float, x_normalized: float) -> tuple[float, float]:
        return _horizontal_full_normalized_to_crop_normalized(
            y_normalized, x_normalized, self.left, self.top, self.width, self.height,
            self.original_width, self.original_height,
        )


def compute_horizontal_vision_crop_bounds(
    image_width: int, image_height: int, left_fraction: float,
) -> tuple[int, int, int, int]:
    """Pure geometry — returns (left, top, right, bottom) native pixel
    bounds for a full-height crop starting at left_fraction of screen
    width and extending to the full right edge (top=0, bottom=height).
    Split out from create_reply_vision_crop() so the bounds math can be
    tested/verified without touching the filesystem."""
    left = round(left_fraction * image_width)
    return (left, 0, image_width, image_height)


def create_horizontal_vision_crop(
    image_path: Path,
    image_width: int,
    image_height: int,
    left_fraction: float,
    output_dir: Path = DEFAULT_CROP_OUTPUT_DIR,
    filename_suffix: str = "horizontal_vision_crop",
) -> HorizontalVisionCrop:
    """Generic full-height, right-of-left_fraction crop — the shared
    utility behind create_reply_vision_crop() below and any other Vision
    stage that wants this SAME crop geometry (a normalized left
    boundary, full height, full right edge) with its OWN left_fraction/
    output filename — e.g. SEND_COMPOSER_LOCALIZATION (app/outlook/
    send.py), which uses its own independent
    SEND_COMPOSER_READING_PANE_LEFT_FRACTION constant rather than
    REPLY_SEARCH's, even though both currently happen to be calibrated
    to the same value. Never shares a cache or business meaning across
    call sites — only this deterministic crop-and-remap math.
    image_path itself is never modified."""
    from PIL import Image

    left, top, right, bottom = compute_horizontal_vision_crop_bounds(image_width, image_height, left_fraction)

    output_dir.mkdir(parents=True, exist_ok=True)
    with Image.open(image_path) as source:
        cropped = source.convert("RGB").crop((left, top, right, bottom))
        crop_path = output_dir / f"{Path(image_path).stem}_{filename_suffix}.png"
        cropped.save(crop_path, "PNG")

    return HorizontalVisionCrop(
        crop_path=crop_path,
        left=left, top=top, right=right, bottom=bottom,
        width=right - left, height=bottom - top,
        original_width=image_width, original_height=image_height,
        source_path=str(image_path),
    )


def create_reply_vision_crop(
    image_path: Path,
    image_width: int,
    image_height: int,
    left_fraction: float,
    output_dir: Path = DEFAULT_CROP_OUTPUT_DIR,
) -> HorizontalVisionCrop:
    """Crops the full-height, right-of-left_fraction region out of
    image_path for REPLY_SEARCH's Vision input — see app/config/
    settings.py::REPLY_VISION_CROP_LEFT_FRACTION for the calibration
    evidence behind left_fraction. image_path itself is never modified.

    Thin, behavior-preserving wrapper around create_horizontal_vision_
    crop() (2026-09-06) — same crop_path filename suffix
    ("_reply_vision_crop.png") and identical output as before this was
    factored out, verified unchanged by the existing Reply crop tests."""
    return create_horizontal_vision_crop(
        image_path, image_width, image_height, left_fraction, output_dir, filename_suffix="reply_vision_crop",
    )


# --- Generic, fully arbitrary rectangular Vision crop (2026-09-06) --------
# Added for SEND_GROUNDING's dynamically-derived Stage-2 composer-area
# crop (app/outlook/send.py) — unlike HorizontalVisionCrop (always
# top=0, full height, full right edge), every one of this crop's four
# bounds is independently computed at runtime from a coarse Stage-1
# localization result, never a fixed geometry. Kept as its own type
# (own private remap helpers, not literally shared with
# HorizontalVisionCrop/MessageListCrop) so a dynamically-derived crop
# can never be confused with — or accidentally substituted for — the
# fixed-geometry ones.


def _rectangular_crop_normalized_to_full_normalized(
    y_normalized: float, x_normalized: float,
    left: int, top: int, crop_width: int, crop_height: int, original_width: int, original_height: int,
) -> tuple[float, float]:
    crop_px_x = x_normalized / 1000 * crop_width
    crop_px_y = y_normalized / 1000 * crop_height
    full_px_x = left + crop_px_x
    full_px_y = top + crop_px_y
    return (full_px_y / original_height * 1000, full_px_x / original_width * 1000)


def _rectangular_full_normalized_to_crop_normalized(
    y_normalized: float, x_normalized: float,
    left: int, top: int, crop_width: int, crop_height: int, original_width: int, original_height: int,
) -> tuple[float, float]:
    full_px_x = x_normalized / 1000 * original_width
    full_px_y = y_normalized / 1000 * original_height
    crop_px_x = full_px_x - left
    crop_px_y = full_px_y - top
    return (crop_px_y / crop_height * 1000, crop_px_x / crop_width * 1000)


@dataclass
class RectangularVisionCrop:
    """The result of a fully arbitrary (all four bounds independently
    computed) Vision-input crop — e.g. SEND_GROUNDING's Stage-2
    dynamically-derived composer-area crop.

    left/top/right/bottom are native PIXEL coordinates in the ORIGINAL
    (full-screen) image. width/height are the crop image's own pixel
    dimensions — the dimensions Vision is told about and the space its
    returned bbox/points are relative to. original_width/original_height
    are the full screenshot's own pixel dimensions, needed to convert
    back."""

    crop_path: Path
    left: int
    top: int
    right: int
    bottom: int
    width: int
    height: int
    original_width: int
    original_height: int
    source_path: str

    def remap_bbox_to_full_screen(self, bbox_normalized: list[float]) -> list[float]:
        """Converts a [y_min, x_min, y_max, x_max] bbox, 0-1000 normalized
        relative to THIS crop, into the equivalent 0-1000 normalized bbox
        relative to the full original screenshot. Deterministic Python
        arithmetic only — never delegated to Vision."""
        y_min, x_min, y_max, x_max = bbox_normalized
        full_y_min, full_x_min = self._to_full(y_min, x_min)
        full_y_max, full_x_max = self._to_full(y_max, x_max)
        return [full_y_min, full_x_min, full_y_max, full_x_max]

    def remap_point_to_full_screen(self, x_normalized: float, y_normalized: float) -> tuple[float, float]:
        full_y, full_x = self._to_full(y_normalized, x_normalized)
        return full_x, full_y

    def bbox_to_crop_relative(self, bbox_full_normalized: list[float]) -> list[float]:
        """The inverse of remap_bbox_to_full_screen — never used to feed
        a runtime click point."""
        y_min, x_min, y_max, x_max = bbox_full_normalized
        crop_y_min, crop_x_min = self._to_crop(y_min, x_min)
        crop_y_max, crop_x_max = self._to_crop(y_max, x_max)
        return [crop_y_min, crop_x_min, crop_y_max, crop_x_max]

    def _to_full(self, y_normalized: float, x_normalized: float) -> tuple[float, float]:
        return _rectangular_crop_normalized_to_full_normalized(
            y_normalized, x_normalized, self.left, self.top, self.width, self.height,
            self.original_width, self.original_height,
        )

    def _to_crop(self, y_normalized: float, x_normalized: float) -> tuple[float, float]:
        return _rectangular_full_normalized_to_crop_normalized(
            y_normalized, x_normalized, self.left, self.top, self.width, self.height,
            self.original_width, self.original_height,
        )


def derive_send_composer_crop_bounds_px(
    action_bar_bbox_full_px: tuple[float, float, float, float],
    horizontal_expansion_fraction: float,
    vertical_expansion_fraction: float,
    clamp_bounds_px: tuple[int, int, int, int],
) -> tuple[int, int, int, int]:
    """Deterministic "Policy A" crop derivation for SEND_GROUNDING's
    Stage-2 crop — reproduces benchmarks/claude/experiments/
    run_dynamic_send_grounding_experiment.py::derive_stage2_crop_px()'s
    EXACT math (the only one of three tested expansion policies proven
    to reproduce reliable exact-Send spatial grounding — see
    app/config/settings.py::SEND_DYNAMIC_CROP_HORIZONTAL_EXPANSION_
    FRACTION/_VERTICAL_EXPANSION_FRACTION for the evidence).

    action_bar_bbox_full_px is [y_min, x_min, y_max, x_max] in FULL-
    SCREEN PIXELS (already remapped from Stage 1's crop-relative
    normalized bbox). Padding is applied per side, from that bbox's OWN
    width/height — never a fixed pixel amount:
      horizontal padding = horizontal_expansion_fraction * bbox width,
        subtracted from left AND added to right, independently
      vertical padding = vertical_expansion_fraction * bbox height,
        subtracted from top AND added to bottom, independently
    The result is then clamped inside clamp_bounds_px (the reading-pane
    crop's own full-screen pixel bounds) — this can never extend beyond
    the reading-pane crop that produced Stage 1's own localization."""
    y_min, x_min, y_max, x_max = action_bar_bbox_full_px
    width = x_max - x_min
    height = y_max - y_min
    h_pad = horizontal_expansion_fraction * width
    v_pad = vertical_expansion_fraction * height

    left = x_min - h_pad
    right = x_max + h_pad
    top = y_min - v_pad
    bottom = y_max + v_pad

    clamp_left, clamp_top, clamp_right, clamp_bottom = clamp_bounds_px
    left = max(clamp_left, left)
    top = max(clamp_top, top)
    right = min(clamp_right, right)
    bottom = min(clamp_bottom, bottom)
    return (round(left), round(top), round(right), round(bottom))


def create_rectangular_vision_crop(
    image_path: Path,
    image_width: int,
    image_height: int,
    left: int,
    top: int,
    right: int,
    bottom: int,
    output_dir: Path = DEFAULT_CROP_OUTPUT_DIR,
    filename_suffix: str = "rectangular_vision_crop",
) -> RectangularVisionCrop:
    """Crops the given arbitrary (left, top, right, bottom) native-pixel
    region out of image_path — image_path itself is never modified.
    Rejects a degenerate (zero/negative-area) crop rather than silently
    producing an unusable image."""
    if right <= left or bottom <= top:
        raise ValueError(
            f"Degenerate crop bounds: left={left} top={top} right={right} bottom={bottom} "
            f"(width={right - left}, height={bottom - top})"
        )

    from PIL import Image

    output_dir.mkdir(parents=True, exist_ok=True)
    with Image.open(image_path) as source:
        cropped = source.convert("RGB").crop((left, top, right, bottom))
        crop_path = output_dir / f"{Path(image_path).stem}_{filename_suffix}.png"
        cropped.save(crop_path, "PNG")

    return RectangularVisionCrop(
        crop_path=crop_path,
        left=left, top=top, right=right, bottom=bottom,
        width=right - left, height=bottom - top,
        original_width=image_width, original_height=image_height,
        source_path=str(image_path),
    )
