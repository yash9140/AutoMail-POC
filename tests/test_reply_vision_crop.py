"""REPLY_SEARCH Vision-input crop tests (2026-09-06).

Live evidence + two static Claude-only benchmarks (run_reply_grounding_
experiment.py, run_reply_crop_calibration.py) proved REPLY_SEARCH's bbox
grounding is unreliable at full-screen resolution (landing in unrelated
blank space, not merely confusing Reply with Forward) but reliable once
cropped to a full-height, right-side region starting at
REPLY_VISION_CROP_LEFT_FRACTION (calibrated at 0.35 — the widest of three
tested candidates that still grounded 3/3). This file proves the runtime
wiring (app/vision/crop.py::create_reply_vision_crop +
app/outlook/reply.py) matches that evidence: only REPLY_SEARCH is
cropped, REPLY_EDITOR_VERIFICATION and TARGET_EMAIL_SEARCH are
unaffected, remapping is correct, and every existing safety/validation/
click-count behavior is unchanged.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.settings import REPLY_VISION_CROP_LEFT_FRACTION  # noqa: E402
from app.outlook.draft import ReplyDraftSteps  # noqa: E402
from app.safety.abort_controller import AbortController  # noqa: E402
from app.vision.crop import (  # noqa: E402
    HorizontalVisionCrop,
    compute_horizontal_vision_crop_bounds,
    create_reply_vision_crop,
)
from app.vision.providers.base import RateLimitError  # noqa: E402
from app.vision.service import VisionService  # noqa: E402
from tests._capture_test_utils import real_capture_image_path, to_reply_crop_relative_bbox  # noqa: E402

REPLY_MODULE = "app.outlook.reply"


def _steps() -> ReplyDraftSteps:
    steps = ReplyDraftSteps(AbortController(), VisionService(MagicMock(), fallback=None), "test-model")
    steps.result.content_complete = True
    return steps


def _capture(width=1920, height=1080, filename="reply_email.png"):
    return MagicMock(filename=filename, path=real_capture_image_path(width, height, name=filename), width=width, height=height)


def _state_check_call(verified: bool):
    return MagicMock(
        parsed_json={"verified": verified, "detected_state": "x", "confidence": 0.9,
                     "visual_evidence": "x", "reason": "x"},
        raw_text="{}", model="test-model", latency_ms=50.0, input_tokens=5, output_tokens=5,
    )


def _reply_search_call(reply_visible=True, control_identity="Reply", control_type="button",
                        bbox=(400.0, 600.0, 440.0, 700.0), more_below=False, confidence=0.9):
    return MagicMock(
        parsed_json={
            "outlook_visible": True, "reply_visible": reply_visible,
            "control_identity": control_identity, "control_type": control_type,
            "bbox": to_reply_crop_relative_bbox(list(bbox)) if bbox is not None else None,
            "more_content_below": more_below, "confidence": confidence, "reason": "ok",
        },
        raw_text="{}", model="test-model", latency_ms=50.0, input_tokens=5, output_tokens=5,
    )


def _run_prepare(steps, capture=None):
    with patch(f"{REPLY_MODULE}.get_foreground_window_title", return_value="Outlook"), \
         patch(f"{REPLY_MODULE}.confirm_outlook_foreground_with_recheck", return_value="Outlook"), \
         patch(f"{REPLY_MODULE}.capture_screen", return_value=capture or _capture()), \
         patch(f"{REPLY_MODULE}.time.sleep"), \
         patch(f"{REPLY_MODULE}.pyautogui") as mock_pyautogui, \
         patch("app.automation.scrolling.pyautogui") as mock_scroll_pg:
        mock_pyautogui.FAILSAFE = True
        mock_scroll_pg.FAILSAFE = True
        result = steps.prepare_reply_editor()
    return result, mock_pyautogui, mock_scroll_pg


# --- A: calibrated normalized Reply crop bounds ---

def test_A_calibrated_crop_bounds_at_1920x1080():
    left, top, right, bottom = compute_horizontal_vision_crop_bounds(1920, 1080, REPLY_VISION_CROP_LEFT_FRACTION)
    assert left == round(REPLY_VISION_CROP_LEFT_FRACTION * 1920)
    assert (top, right, bottom) == (0, 1920, 1080)
    assert REPLY_VISION_CROP_LEFT_FRACTION == 0.35


# --- B: crop scaling at multiple resolutions ---

def test_B_crop_bounds_scale_across_resolutions():
    for width, height in ((2560, 1440), (1366, 768), (3840, 2160)):
        left, top, right, bottom = compute_horizontal_vision_crop_bounds(width, height, REPLY_VISION_CROP_LEFT_FRACTION)
        assert left == round(REPLY_VISION_CROP_LEFT_FRACTION * width)
        assert top == 0
        assert right == width   # full right edge retained
        assert bottom == height  # full vertical height retained


# --- C: crop-relative Reply bbox -> full-screen remap ---

def test_C_crop_relative_bbox_remaps_to_full_screen():
    image_path = Path(real_capture_image_path(1920, 1080))
    crop = create_reply_vision_crop(image_path, 1920, 1080, REPLY_VISION_CROP_LEFT_FRACTION)
    full_bbox = crop.remap_bbox_to_full_screen([0.0, 0.0, 1000.0, 1000.0])
    y_min, x_min, y_max, x_max = full_bbox
    assert x_min == pytest.approx(crop.left / 1920 * 1000, abs=0.5)
    assert x_max == pytest.approx(1920 / 1920 * 1000, abs=0.5)
    assert y_min == pytest.approx(0.0, abs=0.5)
    assert y_max == pytest.approx(1000.0, abs=0.5)


def test_C_round_trip_full_screen_to_crop_relative_and_back():
    image_path = Path(real_capture_image_path(1920, 1080))
    crop = create_reply_vision_crop(image_path, 1920, 1080, REPLY_VISION_CROP_LEFT_FRACTION)
    original = [632.4, 449.0, 669.4, 506.3]
    crop_relative = crop.bbox_to_crop_relative(original)
    back_to_full = crop.remap_bbox_to_full_screen(crop_relative)
    assert back_to_full == pytest.approx(original, abs=0.01)


# --- D: Y preservation (full-height crop, top=0) ---

def test_D_y_preserved_across_full_height_crop():
    image_path = Path(real_capture_image_path(1920, 1080))
    crop = create_reply_vision_crop(image_path, 1920, 1080, REPLY_VISION_CROP_LEFT_FRACTION)
    for y in (0.0, 250.5, 632.4, 999.9):
        full_bbox = crop.remap_bbox_to_full_screen([y, 100.0, y, 200.0])
        assert full_bbox[0] == pytest.approx(y, abs=0.01)
        assert full_bbox[2] == pytest.approx(y, abs=0.01)


def test_create_reply_vision_crop_writes_real_file_without_touching_original(tmp_path):
    from PIL import Image

    original_path = tmp_path / "reply_crop_source.png"
    Image.new("RGB", (1920, 1080), color=(5, 5, 5)).save(original_path)
    original_bytes_before = original_path.read_bytes()

    crop = create_reply_vision_crop(original_path, 1920, 1080, REPLY_VISION_CROP_LEFT_FRACTION)
    assert isinstance(crop, HorizontalVisionCrop)
    assert crop.crop_path.exists()
    assert crop.crop_path != original_path
    with Image.open(crop.crop_path) as img:
        assert img.size == (1920 - crop.left, 1080)
    assert original_path.read_bytes() == original_bytes_before


# --- E: REPLY_SEARCH receives the crop, not the raw screenshot ---

def test_E_reply_search_sends_the_crop_not_the_full_screenshot():
    steps = _steps()
    capture = _capture(filename="reply_search_crop_wiring.png")
    steps.vision.primary.analyze_screen.side_effect = [_state_check_call(False), _reply_search_call()]
    _run_prepare(steps, capture=capture)
    calls = steps.vision.primary.analyze_screen.call_args_list
    # calls[0] is REPLY_EDITOR_VERIFICATION (full screenshot); calls[1] is REPLY_SEARCH.
    reply_search_path = calls[1].args[0]
    assert reply_search_path != Path(capture.path)
    assert reply_search_path.name == "reply_search_crop_wiring_reply_vision_crop.png"


# --- F: REPLY_EDITOR_VERIFICATION remains full-screen (both the
# pre-search check and the post-click verification) ---

def test_F_reply_editor_verification_precheck_uses_full_screenshot():
    steps = _steps()
    capture = _capture(filename="reply_editor_check_full.png")
    steps.vision.primary.analyze_screen.side_effect = [_state_check_call(True)]
    _run_prepare(steps, capture=capture)
    calls = steps.vision.primary.analyze_screen.call_args_list
    assert calls[0].args[0] == Path(capture.path)  # raw screenshot, not a crop


def test_F_reply_editor_verification_post_click_uses_full_screenshot():
    steps = _steps()
    steps.result.reply_click_count = 1
    capture = _capture(filename="post_click_editor_check_full.png")
    steps.vision.primary.analyze_screen.return_value = _state_check_call(True)
    with patch(f"{REPLY_MODULE}.time.sleep"), \
         patch(f"{REPLY_MODULE}.get_foreground_window_title", return_value="Outlook"), \
         patch(f"{REPLY_MODULE}.capture_screen", return_value=capture):
        assert steps.verify_reply_editor() is True
    call_args = steps.vision.primary.analyze_screen.call_args
    assert call_args.args[0] == Path(capture.path)


# --- G: Claude technical failure falls back to Gemini with the SAME
# Reply crop — no recapture, no separate crop for the fallback provider ---

def test_G_gemini_fallback_receives_the_identical_reply_crop_path():
    primary = MagicMock()
    primary.provider_name = "anthropic"
    fallback = MagicMock()
    fallback.provider_name = "gemini"
    vision = VisionService(primary, fallback=fallback, default_max_retries=0)
    steps = ReplyDraftSteps(AbortController(), vision, "test-model")
    steps.result.content_complete = True

    capture = _capture(filename="reply_fallback_crop_wiring.png")
    # First call (REPLY_EDITOR_VERIFICATION) succeeds on primary; second
    # (REPLY_SEARCH) technically fails on primary and falls back.
    primary.analyze_screen.side_effect = [
        MagicMock(parsed_json={"verified": False, "detected_state": "x", "confidence": 0.9,
                                "visual_evidence": "x", "reason": "x"},
                   raw_text="{}", model="test-model", latency_ms=10.0, input_tokens=5, output_tokens=5),
        RateLimitError("HTTP 429"),
    ]
    fallback.analyze_screen.return_value = MagicMock(
        parsed_json={
            "outlook_visible": True, "reply_visible": True, "control_identity": "Reply",
            "control_type": "button", "bbox": to_reply_crop_relative_bbox([400.0, 600.0, 440.0, 700.0]),
            "more_content_below": False, "confidence": 0.9, "reason": "ok",
        },
        raw_text="{}", model="test-model", latency_ms=10.0, input_tokens=5, output_tokens=5,
    )

    with patch(f"{REPLY_MODULE}.get_foreground_window_title", return_value="Outlook"), \
         patch(f"{REPLY_MODULE}.confirm_outlook_foreground_with_recheck", return_value="Outlook"), \
         patch(f"{REPLY_MODULE}.capture_screen", return_value=capture), \
         patch(f"{REPLY_MODULE}.time.sleep"), \
         patch(f"{REPLY_MODULE}.pyautogui") as mock_pyautogui:
        mock_pyautogui.FAILSAFE = True
        ok = steps.prepare_reply_editor()

    assert ok is True
    primary_reply_search_path = primary.analyze_screen.call_args_list[1].args[0]
    fallback_path = fallback.analyze_screen.call_args_list[0].args[0]
    assert primary_reply_search_path == fallback_path
    assert primary_reply_search_path.name == "reply_fallback_crop_wiring_reply_vision_crop.png"


# --- H: the full-screen Reply prompt itself is unchanged ---

def test_H_reply_search_prompt_file_unchanged_contract():
    from app.outlook.reply import REPLY_SEARCH_PROMPT_PATH

    text = REPLY_SEARCH_PROMPT_PATH.read_text(encoding="utf-8")
    assert "Reply All" in text
    assert "Forward" in text
    assert "no markdown" in text.lower() or "no ``` code fences" in text


# --- I: semantic Reply-vs-Reply-All / Forward validation unchanged ---

def test_I_reply_all_still_rejected_with_crop_wiring():
    steps = _steps()
    steps.vision.primary.analyze_screen.side_effect = [
        _state_check_call(False),
        _reply_search_call(control_identity="Reply All"),
    ]
    ok, mock_pyautogui, _ = _run_prepare(steps)
    assert ok is False
    from app.playbook.failure_reasons import LaunchFailureReason
    assert steps.result.failure_reason == LaunchFailureReason.REPLY_TARGET_NOT_FOUND
    mock_pyautogui.click.assert_not_called()


# --- J: existing click-point code receives the REMAPPED (full-screen) bbox ---

def test_J_click_point_derived_from_remapped_full_screen_bbox():
    steps = _steps()
    target_full_screen_bbox = (400.0, 600.0, 440.0, 700.0)
    steps.vision.primary.analyze_screen.side_effect = [
        _state_check_call(False), _reply_search_call(bbox=target_full_screen_bbox),
    ]
    ok, mock_pyautogui, _ = _run_prepare(steps)
    assert ok is True
    y_min, x_min, y_max, x_max = target_full_screen_bbox
    expected_x = round((x_min + x_max) / 2 / 1000 * 1920)
    expected_y = round((y_min + y_max) / 2 / 1000 * 1080)
    assert steps.result.reply_converted_x == pytest.approx(expected_x, abs=1)
    assert steps.result.reply_converted_y == pytest.approx(expected_y, abs=1)


# --- K: no provider-specific branching anywhere in the new crop wiring ---

def test_K_no_provider_name_branch_in_reply_crop_wiring():
    import inspect

    from app.outlook import reply as reply_mod

    source = inspect.getsource(reply_mod.ReplyDiscoverySteps.prepare_reply_editor)
    source += inspect.getsource(reply_mod.ReplyDiscoverySteps._get_reply_vision_crop)
    for literal in ('"gemini"', "'gemini'", '"claude"', "'claude'", '"anthropic"', "'anthropic'"):
        assert literal not in source


# --- L: exactly one Reply click, even with the crop wiring in place ---

def test_L_exactly_one_reply_click():
    steps = _steps()
    steps.vision.primary.analyze_screen.side_effect = [_state_check_call(False), _reply_search_call()]
    ok, mock_pyautogui, _ = _run_prepare(steps)
    assert ok is True
    mock_pyautogui.click.assert_called_once_with()
    assert steps.result.reply_click_count == 1


# --- M: editor verification failure does not re-click ---

def test_M_editor_verification_failure_never_reclicks():
    steps = _steps()
    steps.result.reply_click_count = 1
    steps.vision.primary.analyze_screen.return_value = _state_check_call(False)
    with patch(f"{REPLY_MODULE}.time.sleep"), \
         patch(f"{REPLY_MODULE}.get_foreground_window_title", return_value="Outlook"), \
         patch(f"{REPLY_MODULE}.capture_screen", return_value=_capture()), \
         patch(f"{REPLY_MODULE}.pyautogui") as mock_pyautogui:
        mock_pyautogui.FAILSAFE = True
        assert steps.verify_reply_editor() is False
        mock_pyautogui.click.assert_not_called()
    assert steps.result.reply_click_count == 1


# --- N: TARGET_EMAIL_SEARCH's message-list crop is completely untouched ---

def test_N_message_list_crop_module_untouched_by_reply_crop_addition():
    import inspect

    from app.vision import crop as crop_mod

    source = inspect.getsource(crop_mod.MessageListCrop)
    source += inspect.getsource(crop_mod.create_message_list_crop)
    source += inspect.getsource(crop_mod.compute_message_list_crop_bounds)
    # MessageListCrop's own remap methods must still be fully self-
    # contained (never delegating to the new generic reply-crop helpers).
    assert "_horizontal_crop_normalized_to_full_normalized" not in source
    assert "_horizontal_full_normalized_to_crop_normalized" not in source


def test_N_find_email_module_still_uses_message_list_crop_only():
    import inspect

    from app.outlook import find_email as find_email_mod

    source = inspect.getsource(find_email_mod)
    assert "create_reply_vision_crop" not in source
    assert "HorizontalVisionCrop" not in source
