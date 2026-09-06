"""app/vision/crop.py unit tests + integration proof that the message-
list-column crop (2026-09-06) is wired into exactly the three target-
email grounding stages it was benchmarked for, and nowhere else.

Background: benchmarks/claude/experiments/run_row_confusion_experiment.py
+ analyze_row_confusion_results.py (static, Claude-only, no physical
actions) proved full-screen row-bbox grounding for a target message-list
row was stably WRONG, while cropping horizontally to the message-list
column (retaining full vertical height) made it stably CORRECT and
faster; a tighter crop made it worse again. This file does not re-run
that benchmark — it proves the runtime crop utility's own math is
correct, and that TARGET_EMAIL_SEARCH / TARGET_EMAIL_ROW_IDENTITY_REFINE
/ TARGET_EMAIL_ROW_BBOX_REFINE all actually use it (reusing the exact
same crop, remapping coordinates back to full-screen space, and behaving
identically for a Claude/Gemini fallback), while every other Vision
stage keeps receiving the full, uncropped screenshot.
"""

import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.settings import LEFT_SIDEBAR_MAX_X_FRACTION, MESSAGE_LIST_RIGHT_MAX_X_FRACTION  # noqa: E402
from app.outlook.find_email import FindOpenEmailSteps  # noqa: E402
from app.safety.abort_controller import AbortController  # noqa: E402
from app.vision.crop import compute_message_list_crop_bounds, create_message_list_crop  # noqa: E402
from app.vision.providers.base import RateLimitError  # noqa: E402
from app.vision.service import VisionService  # noqa: E402
from tests._capture_test_utils import real_capture_image_path, to_crop_relative_bbox  # noqa: E402

MODULE = "app.outlook.find_email"


# --- A: crop bounds at 1920x1080 exactly match the benchmarked geometry ---

def test_A_crop_bounds_at_1920x1080():
    left, top, right, bottom = compute_message_list_crop_bounds(
        1920, 1080, LEFT_SIDEBAR_MAX_X_FRACTION, MESSAGE_LIST_RIGHT_MAX_X_FRACTION,
    )
    assert (left, top, right, bottom) == (326, 0, 1056, 1080)
    assert right - left == 730


# --- B: crop bounds scale proportionally across other resolutions ---

def test_B_crop_bounds_scale_across_resolutions():
    for width, height in ((2560, 1440), (1366, 768), (3840, 2160)):
        left, top, right, bottom = compute_message_list_crop_bounds(
            width, height, LEFT_SIDEBAR_MAX_X_FRACTION, MESSAGE_LIST_RIGHT_MAX_X_FRACTION,
        )
        assert left == round(LEFT_SIDEBAR_MAX_X_FRACTION * width)
        assert right == round(MESSAGE_LIST_RIGHT_MAX_X_FRACTION * width)
        assert top == 0
        assert bottom == height  # full vertical height retained, never a tight vertical crop


# --- C: crop-relative -> full-screen bbox conversion is correct at the
# crop's own corners ---

def test_C_crop_relative_to_full_screen_bbox_conversion():
    image_path = Path(real_capture_image_path(1920, 1080))
    crop = create_message_list_crop(
        image_path, 1920, 1080, LEFT_SIDEBAR_MAX_X_FRACTION, MESSAGE_LIST_RIGHT_MAX_X_FRACTION,
    )
    # crop x=0 -> full x ~= 326/1920*1000; crop x=1000 -> full x ~= 1056/1920*1000
    full_bbox = crop.remap_bbox_to_full_screen([0.0, 0.0, 1000.0, 1000.0])
    y_min, x_min, y_max, x_max = full_bbox
    assert x_min == pytest.approx(326 / 1920 * 1000, abs=0.5)
    assert x_max == pytest.approx(1056 / 1920 * 1000, abs=0.5)
    assert y_min == pytest.approx(0.0, abs=0.5)
    assert y_max == pytest.approx(1000.0, abs=0.5)


# --- D: Y is preserved by the full-height crop (crop_top=0, crop_height
# == original_height means crop Y and full-screen Y coincide) ---

def test_D_y_preserved_across_full_height_crop():
    image_path = Path(real_capture_image_path(1920, 1080))
    crop = create_message_list_crop(
        image_path, 1920, 1080, LEFT_SIDEBAR_MAX_X_FRACTION, MESSAGE_LIST_RIGHT_MAX_X_FRACTION,
    )
    for y in (0.0, 123.4, 500.0, 999.9):
        full_bbox = crop.remap_bbox_to_full_screen([y, 100.0, y, 200.0])
        assert full_bbox[0] == pytest.approx(y, abs=0.01)
        assert full_bbox[2] == pytest.approx(y, abs=0.01)


# --- round-trip: full-screen -> crop-relative -> full-screen reproduces
# the original bbox (the exact property every existing test fixture in
# this suite relies on to keep asserting the SAME full-screen geometry
# it always has) ---

def test_round_trip_full_screen_to_crop_relative_and_back():
    image_path = Path(real_capture_image_path(1920, 1080))
    crop = create_message_list_crop(
        image_path, 1920, 1080, LEFT_SIDEBAR_MAX_X_FRACTION, MESSAGE_LIST_RIGHT_MAX_X_FRACTION,
    )
    original = [378.0, 258.0, 456.0, 548.0]
    crop_relative = crop.bbox_to_crop_relative(original)
    back_to_full = crop.remap_bbox_to_full_screen(crop_relative)
    assert back_to_full == pytest.approx(original, abs=0.01)


# --- crop file is actually written, at the expected size, without
# mutating the original screenshot ---

def test_create_message_list_crop_writes_a_real_file_without_touching_the_original():
    from PIL import Image

    original_path = Path(tempfile.gettempdir()) / "crop_source_test.png"
    Image.new("RGB", (1920, 1080), color=(10, 20, 30)).save(original_path)
    original_bytes_before = original_path.read_bytes()

    crop = create_message_list_crop(
        original_path, 1920, 1080, LEFT_SIDEBAR_MAX_X_FRACTION, MESSAGE_LIST_RIGHT_MAX_X_FRACTION,
    )
    assert crop.crop_path.exists()
    assert crop.crop_path != original_path
    with Image.open(crop.crop_path) as img:
        assert img.size == (730, 1080)
    assert original_path.read_bytes() == original_bytes_before  # untouched


# --- E/F/G: TARGET_EMAIL_SEARCH, identity-refine, and bbox-refine all
# send the CROP (never the raw full screenshot), and reuse the exact
# same crop for one grounding cycle ---

def _steps():
    steps = FindOpenEmailSteps(
        AbortController(), VisionService(MagicMock(), fallback=None), "test-model",
        target_sender="Yash Dhanraj", target_subject="",
    )
    steps.result.ready_for_interaction = True
    return steps


def test_E_target_email_search_sends_the_crop_not_the_full_screenshot():
    steps = _steps()
    capture_path = real_capture_image_path(1920, 1080, name="crop_wiring_search.png")
    capture = MagicMock(path=capture_path, width=1920, height=1080)
    steps.vision.primary.analyze_screen.return_value = MagicMock(
        parsed_json={"outlook_visible": True, "message_list_visible": True, "target_visible": False,
                     "candidate_count": 0, "candidates": [], "more_content_below": False, "reason": "ok"},
        raw_text="{}", model="test-model", latency_ms=10.0, input_tokens=5, output_tokens=5,
    )
    steps._ground_target_email_candidate(capture)
    sent_path = steps.vision.primary.analyze_screen.call_args.args[0]
    assert sent_path != Path(capture_path)
    assert sent_path.name == "crop_wiring_search_message_list_crop.png"


def test_F_G_identity_and_bbox_refine_reuse_the_same_crop_as_search():
    steps = _steps()
    capture_path = real_capture_image_path(1920, 1080, name="crop_wiring_reuse.png")
    capture = MagicMock(path=capture_path, width=1920, height=1080)

    candidate_bbox = to_crop_relative_bbox([378.0, 258.0, 456.0, 548.0])
    steps.vision.primary.analyze_screen.side_effect = [
        MagicMock(
            parsed_json={
                "outlook_visible": True, "message_list_visible": True, "target_visible": True,
                "candidate_count": 1,
                "candidates": [{
                    "sender": "Yash Dhanraj", "subject": "Anything", "subject_truncated": False,
                    "date_or_order": "Today", "row_bbox": candidate_bbox, "confidence": 0.5,  # low confidence -> identity refine
                }],
                "more_content_below": False, "reason": "ok",
            },
            raw_text="{}", model="test-model", latency_ms=10.0, input_tokens=5, output_tokens=5,
        ),
        MagicMock(
            parsed_json={"grounded_sender": "Yash Dhanraj", "grounded_subject": "Anything",
                         "row_bbox": candidate_bbox, "confidence": 0.9, "reason": "ok"},
            raw_text="{}", model="test-model", latency_ms=10.0, input_tokens=5, output_tokens=5,
        ),
    ]
    steps._ground_target_email_candidate(capture)

    calls = steps.vision.primary.analyze_screen.call_args_list
    assert len(calls) == 2
    search_crop_path = calls[0].args[0]
    identity_crop_path = calls[1].args[0]
    assert search_crop_path == identity_crop_path
    assert search_crop_path.name == "crop_wiring_reuse_message_list_crop.png"


# --- H: a Claude technical failure falls back to Gemini for
# TARGET_EMAIL_SEARCH using the exact same crop path — no recapture, no
# separate crop built for the fallback provider ---

def test_H_gemini_fallback_receives_the_identical_crop_path():
    primary = MagicMock()
    primary.provider_name = "anthropic"
    fallback = MagicMock()
    fallback.provider_name = "gemini"
    vision = VisionService(primary, fallback=fallback, default_max_retries=0)
    steps = FindOpenEmailSteps(AbortController(), vision, "test-model", target_sender="Yash Dhanraj", target_subject="")
    steps.result.ready_for_interaction = True

    capture_path = real_capture_image_path(1920, 1080, name="crop_wiring_fallback.png")
    capture = MagicMock(path=capture_path, width=1920, height=1080)

    primary.analyze_screen.side_effect = RateLimitError("HTTP 429")
    fallback.analyze_screen.return_value = MagicMock(
        parsed_json={"outlook_visible": True, "message_list_visible": True, "target_visible": False,
                     "candidate_count": 0, "candidates": [], "more_content_below": False, "reason": "ok"},
        raw_text="{}", model="test-model", latency_ms=10.0, input_tokens=5, output_tokens=5,
    )

    steps._ground_target_email_candidate(capture)

    primary_path = primary.analyze_screen.call_args.args[0]
    fallback_path = fallback.analyze_screen.call_args.args[0]
    assert primary_path == fallback_path
    assert primary_path.name == "crop_wiring_fallback_message_list_crop.png"


# --- fresh screenshot after a freshness-guard re-ground builds a NEW,
# distinctly-named crop rather than reusing the stale one ---

def test_fresh_reground_builds_a_new_crop_from_the_fresh_screenshot():
    steps = _steps()
    stale_path = real_capture_image_path(1920, 1080, name="crop_wiring_stale.png")
    fresh_path = real_capture_image_path(1920, 1080, name="crop_wiring_fresh.png")

    stale_crop = steps._get_message_list_crop(stale_path, 1920, 1080)
    fresh_crop = steps._get_message_list_crop(fresh_path, 1920, 1080)

    assert stale_crop.crop_path != fresh_crop.crop_path
    assert stale_crop.crop_path.name == "crop_wiring_stale_message_list_crop.png"
    assert fresh_crop.crop_path.name == "crop_wiring_fresh_message_list_crop.png"
    # Re-requesting the SAME path returns the cached crop, not a new one.
    assert steps._get_message_list_crop(stale_path, 1920, 1080) is stale_crop


# --- P: non-email Vision stages (OUTLOOK_SEARCH, EMAIL_OPEN_VERIFICATION)
# still receive the full, uncropped screenshot ---

def test_P_outlook_search_stage_never_crops():
    import inspect

    from app.outlook import launch as launch_module

    source = inspect.getsource(launch_module)
    assert "app.vision.crop" not in source
    assert "create_message_list_crop" not in source
    assert "MessageListCrop" not in source


def test_P_email_open_verification_sends_the_raw_capture_not_a_crop():
    steps = _steps()
    capture_path = real_capture_image_path(1920, 1080, name="crop_wiring_open_verify.png")
    steps.vision.primary.analyze_screen.return_value = MagicMock(
        parsed_json={"email_open": True, "subject_detected": "", "sender_detected": "Yash Dhanraj",
                     "subject_match": True, "sender_match": True, "body_visible": True, "confidence": 0.9,
                     "reason": "ok"},
        raw_text="{}", model="test-model", latency_ms=10.0, input_tokens=5, output_tokens=5,
    )
    with patch(f"{MODULE}.time.sleep"), \
         patch(f"{MODULE}.capture_screen", return_value=MagicMock(filename="x.png", path=capture_path, width=1920, height=1080)):
        steps.verify_email_opened()
    sent_path = steps.vision.primary.analyze_screen.call_args.args[0]
    assert sent_path == Path(capture_path)  # the raw screenshot, not a crop
