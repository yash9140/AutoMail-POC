"""Deterministic, fully local pre-click screenshot-freshness comparison
(2026-09-06).

A live run proved click-point math and physical execution were both
correct, yet the wrong email opened — the grounding screenshot had gone
stale (~16s old, across three sequential Vision calls) by the time the
physical click executed, and Outlook's message list can change in that
window. Rather than adding another Vision call immediately before every
click (itself several seconds of latency, and a new staleness window),
this module does a fast, fully local, deterministic pixel-difference
comparison of a fresh screenshot against the original grounding
screenshot's own message-list region — never Vision, never OCR, never a
network call.

Uses only Pillow (already a project dependency) — no numpy, no OpenCV,
no new heavy dependency, per the task's own "prefer the simplest robust
method already supported" instruction.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, Union

from PIL import Image, ImageChops, ImageStat

PathLike = Union[str, Path]

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEBUG_OUTPUT_DIR = PROJECT_ROOT / "debug"


@dataclass
class FreshnessCheckResult:
    changed: bool
    difference_score: float
    threshold: float
    roi: list[int]  # [left, top, right, bottom], native screen pixels


def compute_roi_difference_score(image_a_path: PathLike, image_b_path: PathLike, roi: tuple[int, int, int, int]) -> float:
    """Mean absolute grayscale pixel difference over roi (0-255 scale).

    Comparing a path to itself (the common case when NOTHING physical
    has happened between two capture calls in tests, or the rare real
    case of a literal path collision) is always defined as 0.0 without
    opening any file — a trivial, always-correct identity shortcut.

    Returns the sentinel 255.0 (i.e. "maximally different" — always
    treated as CHANGED, never silently assumed unchanged) if the two
    images can't actually be opened/compared for any reason (missing
    file, mismatched crop size, decode failure) — a comparison that
    cannot be performed is never treated as reassuring."""
    if str(image_a_path) == str(image_b_path):
        return 0.0
    try:
        with Image.open(image_a_path) as img_a, Image.open(image_b_path) as img_b:
            crop_a = img_a.convert("L").crop(roi)
            crop_b = img_b.convert("L").crop(roi)
            if crop_a.size != crop_b.size or crop_a.size[0] <= 0 or crop_a.size[1] <= 0:
                return 255.0
            diff = ImageChops.difference(crop_a, crop_b)
            return ImageStat.Stat(diff).mean[0]
    except Exception:  # noqa: BLE001 — an unreadable comparison is always "changed", never masked
        return 255.0


def check_message_list_roi_freshness(
    original_screenshot_path: PathLike, fresh_screenshot_path: PathLike,
    roi: tuple[int, int, int, int], threshold: float,
) -> FreshnessCheckResult:
    """roi is (left, top, right, bottom) in native screen pixels — see
    app/outlook/find_email.py's ROI derivation (message-list column
    bounds x a padded band around the target row's own y-range)."""
    score = compute_roi_difference_score(original_screenshot_path, fresh_screenshot_path, roi)
    return FreshnessCheckResult(changed=score > threshold, difference_score=score, threshold=threshold, roi=list(roi))


def save_freshness_debug_artifact(
    image_a_path: PathLike, image_b_path: PathLike, roi: tuple[int, int, int, int], label: str = "preclick_freshness",
) -> Optional[Path]:
    """Diagnostic-only, opt-in (caller gates this on the SAME
    EMAIL_GROUNDING_DEBUG=1 flag used elsewhere) — saves the original
    ROI crop, the fresh ROI crop, and a difference visualization under
    debug/, purely for a human reviewer. Never used by any decision
    path, never enabled by default, never raises (returns None on any
    failure — a diagnostic artifact must never be able to break the
    real freshness check that called it). These crops are confined to
    the message-list ROI only (never the full screen), but can still
    show real visible email sender/subject text — this is why saving
    them is opt-in only, never automatic."""
    if str(image_a_path) == str(image_b_path):
        return None
    try:
        with Image.open(image_a_path) as img_a, Image.open(image_b_path) as img_b:
            crop_a = img_a.convert("RGB").crop(roi)
            crop_b = img_b.convert("RGB").crop(roi)
            diff = ImageChops.difference(crop_a.convert("L"), crop_b.convert("L"))

            DEBUG_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
            crop_a.save(DEBUG_OUTPUT_DIR / f"{label}_original_{timestamp}.png", "PNG")
            crop_b.save(DEBUG_OUTPUT_DIR / f"{label}_fresh_{timestamp}.png", "PNG")
            diff.save(DEBUG_OUTPUT_DIR / f"{label}_diff_{timestamp}.png", "PNG")
            return DEBUG_OUTPUT_DIR
    except Exception:  # noqa: BLE001 — a diagnostic artifact must never raise into the caller
        return None
