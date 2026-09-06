"""app/safety/screen_freshness.py unit tests — the deterministic, fully
local (no Vision call, no OCR, no network) pre-click screenshot-
freshness comparison (2026-09-06).

Uses real, small synthetic PNG files (via Pillow) written to pytest's
tmp_path, so the actual pixel-difference math is genuinely exercised —
not just mocked. No real screen capture happens anywhere in this file.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image  # noqa: E402

from app.safety.screen_freshness import (  # noqa: E402
    check_message_list_roi_freshness,
    compute_roi_difference_score,
    save_freshness_debug_artifact,
)

WIDTH, HEIGHT = 400, 300
ROI = (50, 100, 350, 200)  # left, top, right, bottom — inside the canvas


def _solid_image(path: Path, color=(30, 30, 30)) -> Path:
    img = Image.new("RGB", (WIDTH, HEIGHT), color=color)
    img.save(path)
    return path


def _image_with_patch(path: Path, base_color, patch_box, patch_color) -> Path:
    img = Image.new("RGB", (WIDTH, HEIGHT), color=base_color)
    for y in range(patch_box[1], patch_box[3]):
        for x in range(patch_box[0], patch_box[2]):
            img.putpixel((x, y), patch_color)
    img.save(path)
    return path


# --- A: identical images -> changed=False ---

def test_A_identical_images_are_unchanged(tmp_path):
    a = _solid_image(tmp_path / "a.png")
    b = _solid_image(tmp_path / "b.png")
    result = check_message_list_roi_freshness(a, b, ROI, threshold=6.0)
    assert result.changed is False
    assert result.difference_score == 0.0


def test_A_same_path_compared_to_itself_is_unchanged_without_opening_it():
    # The identity shortcut — no file needs to exist for this to be correct.
    score = compute_roi_difference_score("does_not_exist.png", "does_not_exist.png", ROI)
    assert score == 0.0


# --- B: tiny, insignificant pixel noise -> changed=False ---

def test_B_tiny_pixel_noise_within_roi_is_unchanged(tmp_path):
    a = _solid_image(tmp_path / "a.png", color=(100, 100, 100))
    # A small color shift confined to a modest sub-area of the ROI —
    # representative of PNG re-encoding/anti-aliasing noise, not a real
    # content change.
    b = _image_with_patch(tmp_path / "b.png", (100, 100, 100), (100, 130, 150, 150), (103, 103, 103))
    result = check_message_list_roi_freshness(a, b, ROI, threshold=6.0)
    assert result.changed is False


# --- C: target row shifted vertically -> changed=True ---

def test_C_large_content_difference_inside_roi_is_changed(tmp_path):
    a = _solid_image(tmp_path / "a.png", color=(20, 20, 20))
    # A large, high-contrast patch covering a meaningful fraction of the
    # ROI — representative of a genuinely different row's content (or
    # the same row's content having moved) now occupying this position.
    b = _image_with_patch(tmp_path / "b.png", (20, 20, 20), (60, 110, 340, 190), (220, 220, 220))
    result = check_message_list_roi_freshness(a, b, ROI, threshold=6.0)
    assert result.changed is True
    assert result.difference_score > result.threshold


# --- D: new row inserted above target -> changed=True ---

def test_D_new_row_inserted_above_target_is_changed(tmp_path):
    a = _solid_image(tmp_path / "a.png", color=(20, 20, 20))
    # Content change concentrated in the TOP band of the ROI (nearest
    # the target row's upper padding) — as if a new row was inserted
    # just above and shifted everything down.
    b = _image_with_patch(tmp_path / "b.png", (20, 20, 20), (60, 100, 340, 130), (200, 50, 50))
    result = check_message_list_roi_freshness(a, b, ROI, threshold=6.0)
    assert result.changed is True


# --- E: change OUTSIDE the message-list ROI is ignored ---

def test_E_change_outside_roi_is_ignored(tmp_path):
    a = _solid_image(tmp_path / "a.png", color=(20, 20, 20))
    # Patch is entirely outside the ROI bounds (50,100)-(350,200) —
    # e.g. in the toolbar/header area above y=100, or the sidebar left
    # of x=50.
    b = _image_with_patch(tmp_path / "b.png", (20, 20, 20), (0, 0, 40, 40), (255, 0, 0))
    result = check_message_list_roi_freshness(a, b, ROI, threshold=6.0)
    assert result.changed is False
    assert result.difference_score == 0.0


# --- Unreadable / mismatched comparison always treated as changed ---

def test_missing_file_is_treated_as_changed_never_silently_unchanged():
    score = compute_roi_difference_score("missing_a.png", "missing_b.png", ROI)
    assert score == 255.0


def test_result_carries_the_roi_and_threshold_for_logging():
    result = check_message_list_roi_freshness("a.png", "a.png", ROI, threshold=6.0)
    assert result.roi == list(ROI)
    assert result.threshold == 6.0


# --- debug artifact (opt-in, diagnostic only) ---

def test_debug_artifact_saved_when_images_differ(tmp_path, monkeypatch):
    import app.safety.screen_freshness as sf

    monkeypatch.setattr(sf, "DEBUG_OUTPUT_DIR", tmp_path / "debug_out")
    a = _solid_image(tmp_path / "a.png", color=(10, 10, 10))
    b = _image_with_patch(tmp_path / "b.png", (10, 10, 10), (60, 110, 340, 190), (250, 250, 250))
    out_dir = save_freshness_debug_artifact(a, b, ROI)
    assert out_dir == tmp_path / "debug_out"
    saved = list((tmp_path / "debug_out").glob("*.png"))
    assert len(saved) == 3  # original crop, fresh crop, diff


def test_debug_artifact_skipped_for_identical_path():
    assert save_freshness_debug_artifact("same.png", "same.png", ROI) is None


def test_debug_artifact_never_raises_on_bad_input():
    assert save_freshness_debug_artifact("missing_a.png", "missing_b.png", ROI) is None
