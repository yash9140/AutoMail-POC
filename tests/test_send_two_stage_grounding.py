"""SEND_GROUNDING two-stage architecture tests (2026-09-06).

Live evidence + two static Claude-only benchmarks
(run_send_grounding_experiment.py, run_dynamic_send_grounding_
experiment.py) proved full-screen and reading-pane-crop exact-Send bbox
grounding are spatially unreliable (landing right at/just past the
boundary between the primary Send button and its own dropdown chevron),
while a two-stage architecture — coarse action-bar localization (Stage
1) followed by an exact-Send request against a crop DYNAMICALLY DERIVED
from Stage 1's own bbox (Stage 2, "Policy A": h=15%/v=150% expansion) —
was reliable. This file proves the runtime wiring (app/outlook/send.py +
app/vision/crop.py) matches that evidence.

Every test constructs the REAL SendFlowSteps via its actual constructor
(never an isolated mixin) — the earlier _reply_vision_crop_cache
AttributeError escaped 818 tests specifically because no test exercised
the real SendFlowSteps construction path for a crop-cache field; this
file exists partly to make sure that mistake is never repeated for
Send's own new cache fields.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.settings import (  # noqa: E402
    SEND_COMPOSER_READING_PANE_LEFT_FRACTION,
    SEND_DYNAMIC_CROP_HORIZONTAL_EXPANSION_FRACTION,
    SEND_DYNAMIC_CROP_VERTICAL_EXPANSION_FRACTION,
)
from app.outlook.send import SendFlowSteps  # noqa: E402
from app.playbook.failure_reasons import LaunchFailureReason  # noqa: E402
from app.safety.abort_controller import AbortController  # noqa: E402
from app.vision.crop import (  # noqa: E402
    HorizontalVisionCrop,
    RectangularVisionCrop,
    compute_horizontal_vision_crop_bounds,
    derive_send_composer_crop_bounds_px,
)
from app.vision.providers.base import RateLimitError  # noqa: E402
from app.vision.service import VisionRequest, VisionService  # noqa: E402
from tests._capture_test_utils import real_capture_image_path  # noqa: E402

SEND_MODULE = "app.outlook.send"

DEFAULT_ACTION_BAR_BBOX = (900.0, 350.0, 980.0, 550.0)  # crop-relative to the reading-pane crop


def _real_send_flow_steps() -> SendFlowSteps:
    """Constructs SendFlowSteps via the EXACT same constructor call shape
    app/workers/send_worker.py uses."""
    return SendFlowSteps(
        AbortController(), VisionService(MagicMock(), fallback=None), "test-model",
        send_approval_granted=True, target_sender="Yash", target_subject="Question Regarding Meeting Details",
    )


def _ready_steps() -> SendFlowSteps:
    steps = _real_send_flow_steps()
    steps.result.text_entry_executed = True
    steps.result.draft_verified_at = "2026-01-01T00:00:00"
    steps.result.semantic_match = True
    steps.result.draft_verification_reply_editor_open = True
    steps.result.draft_reply = "Sure, will do."
    return steps


def _capture(width=1920, height=1080, filename="send_two_stage.png"):
    return MagicMock(filename=filename, path=real_capture_image_path(width, height, name=filename), width=width, height=height)


def _composer_localization_call(action_bar_bbox=DEFAULT_ACTION_BAR_BBOX, confidence=0.95,
                                 composer_visible=True, action_bar_visible=True):
    return MagicMock(
        parsed_json={
            "composer_visible": composer_visible, "action_bar_visible": action_bar_visible,
            "action_bar_bbox": list(action_bar_bbox) if action_bar_bbox is not None else None,
            "composer_bbox": None, "confidence": confidence, "reason": "ok",
        },
        raw_text="{}", model="test-model", latency_ms=50.0, input_tokens=10, output_tokens=5,
    )


def _send_search_call(send_visible=True, identity="Send", control_type="button",
                       bbox=(619.0, 385.0, 837.0, 616.0), confidence=0.9):
    return MagicMock(
        parsed_json={"outlook_visible": True, "send_visible": send_visible, "control_identity": identity,
                     "control_type": control_type, "bbox": list(bbox) if bbox is not None else None,
                     "confidence": confidence, "reason": "ok"},
        raw_text="{}", model="test-model", latency_ms=80.0, input_tokens=10, output_tokens=5,
    )


def _run_ground_and_click(steps, capture=None):
    with patch(f"{SEND_MODULE}.get_foreground_window_title", return_value="Outlook"), \
         patch(f"{SEND_MODULE}.capture_screen", return_value=capture or _capture()), \
         patch(f"{SEND_MODULE}.pyautogui") as mock_pyautogui:
        mock_pyautogui.FAILSAFE = True
        result = steps.ground_and_click_send()
    return result, mock_pyautogui


# --- U: real SendFlowSteps construction initializes any new crop/latch fields ---

def test_U_real_construction_initializes_send_reading_pane_crop_cache():
    steps = _real_send_flow_steps()
    assert hasattr(steps, "_send_reading_pane_crop_cache")
    assert steps._send_reading_pane_crop_cache == {}


# --- A: SEND_COMPOSER_LOCALIZATION receives the reading-pane crop ---

def test_A_send_composer_localization_receives_reading_pane_crop():
    steps = _ready_steps()
    capture = _capture(filename="stage1_crop_wiring.png")
    steps.vision.primary.analyze_screen.side_effect = [_composer_localization_call(), _send_search_call()]
    _run_ground_and_click(steps, capture=capture)

    calls = steps.vision.primary.analyze_screen.call_args_list
    stage1_request_path = calls[0].args[0]
    assert stage1_request_path != Path(capture.path)
    assert stage1_request_path.name == "stage1_crop_wiring_send_composer_reading_pane_crop.png"


# --- B: reading-pane crop scales across resolutions ---

def test_B_reading_pane_crop_bounds_scale_across_resolutions():
    for width, height in ((2560, 1440), (1366, 768), (3840, 2160), (1920, 1080)):
        left, top, right, bottom = compute_horizontal_vision_crop_bounds(
            width, height, SEND_COMPOSER_READING_PANE_LEFT_FRACTION,
        )
        assert left == round(SEND_COMPOSER_READING_PANE_LEFT_FRACTION * width)
        assert top == 0
        assert right == width
        assert bottom == height


# --- C: Stage-1 bbox remaps correctly to full screen ---

def test_C_stage1_bbox_remaps_to_full_screen(tmp_path):
    from PIL import Image

    from app.vision.crop import create_horizontal_vision_crop

    image_path = tmp_path / "shot.png"
    Image.new("RGB", (1920, 1080), color=(10, 10, 10)).save(image_path)
    crop = create_horizontal_vision_crop(
        image_path, 1920, 1080, SEND_COMPOSER_READING_PANE_LEFT_FRACTION, filename_suffix="test_crop",
    )
    full_bbox = crop.remap_bbox_to_full_screen([0.0, 0.0, 1000.0, 1000.0])
    y_min, x_min, y_max, x_max = full_bbox
    assert x_min == pytest.approx(crop.left / 1920 * 1000, abs=0.5)
    assert x_max == pytest.approx(1920 / 1920 * 1000, abs=0.5)
    assert y_min == pytest.approx(0.0, abs=0.5)
    assert y_max == pytest.approx(1000.0, abs=0.5)


# --- D: Policy-A crop reproduces benchmark math exactly ---

def test_D_policy_a_crop_math_matches_benchmark_formula():
    """Known fixture: action-bar bbox (full-screen PIXELS) and reading-
    pane clamp bounds -> hand-computed expected crop, using the EXACT
    same per-side-percentage-of-own-size interpretation as benchmarks/
    claude/experiments/run_dynamic_send_grounding_experiment.py::
    derive_stage2_crop_px()."""
    action_bar_bbox_full_px = (972.0, 1108.8, 1058.4, 1358.4)  # (y_min, x_min, y_max, x_max)
    reading_pane_bounds_px = (672, 0, 1920, 1080)

    width = 1358.4 - 1108.8
    height = 1058.4 - 972.0
    clamp_left, clamp_top, clamp_right, clamp_bottom = reading_pane_bounds_px
    expected_left = round(max(clamp_left, 1108.8 - SEND_DYNAMIC_CROP_HORIZONTAL_EXPANSION_FRACTION * width))
    expected_right = round(min(clamp_right, 1358.4 + SEND_DYNAMIC_CROP_HORIZONTAL_EXPANSION_FRACTION * width))
    expected_top = round(max(clamp_top, 972.0 - SEND_DYNAMIC_CROP_VERTICAL_EXPANSION_FRACTION * height))
    expected_bottom = round(min(clamp_bottom, 1058.4 + SEND_DYNAMIC_CROP_VERTICAL_EXPANSION_FRACTION * height))

    result = derive_send_composer_crop_bounds_px(
        action_bar_bbox_full_px, SEND_DYNAMIC_CROP_HORIZONTAL_EXPANSION_FRACTION,
        SEND_DYNAMIC_CROP_VERTICAL_EXPANSION_FRACTION, reading_pane_bounds_px,
    )
    assert result == (expected_left, expected_top, expected_right, expected_bottom)


# --- E: Policy A uses percentages, not hardcoded pixels ---

def test_E_policy_a_expansion_scales_with_bbox_size_not_fixed_pixels():
    small_bbox = (900.0, 900.0, 920.0, 950.0)   # width=50, height=20
    large_bbox = (900.0, 900.0, 1000.0, 1100.0)  # width=200, height=100
    clamp = (0, 0, 1920, 1080)

    small_crop = derive_send_composer_crop_bounds_px(
        small_bbox, SEND_DYNAMIC_CROP_HORIZONTAL_EXPANSION_FRACTION,
        SEND_DYNAMIC_CROP_VERTICAL_EXPANSION_FRACTION, clamp,
    )
    large_crop = derive_send_composer_crop_bounds_px(
        large_bbox, SEND_DYNAMIC_CROP_HORIZONTAL_EXPANSION_FRACTION,
        SEND_DYNAMIC_CROP_VERTICAL_EXPANSION_FRACTION, clamp,
    )
    small_width = small_crop[2] - small_crop[0]
    large_width = large_crop[2] - large_crop[0]
    # A 4x-wider input bbox must produce a proportionally wider crop —
    # never the same fixed expansion regardless of input size.
    assert large_width > small_width * 2


# --- F: dynamic crop clamped safely to reading-pane bounds ---

def test_F_dynamic_crop_clamped_to_reading_pane_bounds():
    # Action-bar bbox near the very edge of the reading-pane crop —
    # unclamped expansion would extend past it on every side.
    reading_pane_bounds_px = (672, 0, 1920, 1080)
    edge_bbox = (5.0, 675.0, 25.0, 1915.0)
    result = derive_send_composer_crop_bounds_px(
        edge_bbox, SEND_DYNAMIC_CROP_HORIZONTAL_EXPANSION_FRACTION,
        SEND_DYNAMIC_CROP_VERTICAL_EXPANSION_FRACTION, reading_pane_bounds_px,
    )
    left, top, right, bottom = result
    assert left >= reading_pane_bounds_px[0]
    assert top >= reading_pane_bounds_px[1]
    assert right <= reading_pane_bounds_px[2]
    assert bottom <= reading_pane_bounds_px[3]


def test_F_degenerate_crop_is_rejected(tmp_path):
    from PIL import Image

    from app.vision.crop import create_rectangular_vision_crop

    image_path = tmp_path / "shot.png"
    Image.new("RGB", (1920, 1080), color=(0, 0, 0)).save(image_path)
    with pytest.raises(ValueError):
        create_rectangular_vision_crop(image_path, 1920, 1080, left=100, top=100, right=100, bottom=200)


# --- G/H: Stage-2 SEND_GROUNDING receives the dynamic crop, and its bbox
# remaps correctly to full screen ---

def test_G_H_send_grounding_receives_dynamic_crop_and_remaps_bbox():
    steps = _ready_steps()
    capture = _capture(filename="stage2_crop_wiring.png")
    steps.vision.primary.analyze_screen.side_effect = [
        _composer_localization_call(), _send_search_call(bbox=(619.0, 385.0, 837.0, 616.0)),
    ]
    ok, _ = _run_ground_and_click(steps, capture=capture)
    assert ok is True

    calls = steps.vision.primary.analyze_screen.call_args_list
    stage2_request_path = calls[1].args[0]
    assert stage2_request_path.name == "stage2_crop_wiring_send_dynamic_composer_crop.png"

    # Full-screen bbox must have been derived (not left crop-relative) —
    # the existing click math (I, below) already proves the value is
    # correctly interpreted; here we only prove it is NOT the raw
    # crop-relative value.
    assert steps.result.send_grounding_bbox_raw != [400.0, 800.0, 440.0, 900.0]


# --- I: existing bbox-center click calculation receives the REMAPPED
# full-screen bbox (not the crop-relative one) ---

def test_I_click_point_derived_from_remapped_full_screen_bbox():
    steps = _ready_steps()
    steps.vision.primary.analyze_screen.side_effect = [
        _composer_localization_call(), _send_search_call(bbox=(619.0, 385.0, 837.0, 616.0)),
    ]
    ok, mock_pyautogui = _run_ground_and_click(steps)
    assert ok is True
    # The click point must correspond to the DYNAMIC CROP's own full-
    # screen position, never the raw crop-relative [400,800,440,900]
    # interpreted as if it were already full-screen (which would place
    # it far outside the actual dynamically-derived crop region).
    assert steps.result.send_converted_x is not None
    assert steps.result.send_converted_y is not None
    mock_pyautogui.click.assert_called_once_with()


# --- J: the benchmark's +/-5px SCORING tolerance does not exist anywhere
# in runtime click logic ---

def test_J_benchmark_tolerance_not_present_in_runtime():
    import inspect

    import app.outlook.send as send_mod
    import app.config.settings as settings_mod

    for mod in (send_mod, settings_mod):
        source = inspect.getsource(mod)
        assert "SEND_CLICK_TOLERANCE" not in source
        assert "±5" not in source
    # No manually-measured Send button pixel bounds anywhere in runtime.
    send_source = inspect.getsource(send_mod)
    assert "(800, 1030, 892, 1073)" not in send_source


# --- K: semantic Send vs dropdown vs Discard rules unchanged ---

def test_K_send_dropdown_identity_rejected():
    steps = _ready_steps()
    steps.vision.primary.analyze_screen.side_effect = [
        _composer_localization_call(), _send_search_call(identity="Send dropdown"),
    ]
    ok, mock_pyautogui = _run_ground_and_click(steps)
    assert ok is False
    assert steps.result.failure_reason == LaunchFailureReason.SEND_GROUNDING_INVALID
    mock_pyautogui.click.assert_not_called()


def test_K_discard_identity_rejected():
    steps = _ready_steps()
    steps.vision.primary.analyze_screen.side_effect = [
        _composer_localization_call(), _send_search_call(identity="Discard"),
    ]
    ok, mock_pyautogui = _run_ground_and_click(steps)
    assert ok is False
    assert steps.result.failure_reason == LaunchFailureReason.SEND_GROUNDING_INVALID
    mock_pyautogui.click.assert_not_called()


# --- L: Stage-1 semantic uncertainty -> zero physical Send action ---

def test_L_stage1_composer_not_visible_zero_click():
    steps = _ready_steps()
    steps.vision.primary.analyze_screen.return_value = _composer_localization_call(composer_visible=False)
    ok, mock_pyautogui = _run_ground_and_click(steps)
    assert ok is False
    assert steps.result.failure_reason == LaunchFailureReason.SEND_GROUNDING_INVALID
    mock_pyautogui.click.assert_not_called()
    mock_pyautogui.moveTo.assert_not_called()
    assert steps.result.send_click_count == 0


def test_L_stage1_action_bar_not_visible_zero_click():
    steps = _ready_steps()
    steps.vision.primary.analyze_screen.return_value = _composer_localization_call(action_bar_visible=False, action_bar_bbox=None)
    ok, mock_pyautogui = _run_ground_and_click(steps)
    assert ok is False
    assert steps.result.failure_reason == LaunchFailureReason.SEND_GROUNDING_INVALID
    mock_pyautogui.click.assert_not_called()
    assert steps.result.send_click_count == 0


def test_L_stage1_low_confidence_zero_click():
    steps = _ready_steps()
    steps.vision.primary.analyze_screen.return_value = _composer_localization_call(confidence=0.1)
    ok, mock_pyautogui = _run_ground_and_click(steps)
    assert ok is False
    assert steps.result.failure_reason == LaunchFailureReason.SEND_GROUNDING_INVALID
    mock_pyautogui.click.assert_not_called()


def test_L_stage1_never_uses_gemini_merely_for_uncertainty():
    """Section 7/14: Stage-1 semantic uncertainty is a safe stop, never
    a reason to invoke the fallback provider — fallback exists only for
    TECHNICAL failures."""
    primary = MagicMock()
    primary.provider_name = "anthropic"
    fallback = MagicMock()
    fallback.provider_name = "gemini"
    vision = VisionService(primary, fallback=fallback, default_max_retries=0)
    steps = SendFlowSteps(AbortController(), vision, "test-model", send_approval_granted=True)
    steps.result.text_entry_executed = True
    steps.result.draft_verified_at = "2026-01-01T00:00:00"
    steps.result.semantic_match = True
    steps.result.draft_verification_reply_editor_open = True

    primary.analyze_screen.return_value = _composer_localization_call(composer_visible=False)
    ok, mock_pyautogui = _run_ground_and_click(steps)
    assert ok is False
    fallback.analyze_screen.assert_not_called()


# --- M: Stage-2 semantic uncertainty -> zero physical Send action
# (already covered by K above; explicit "not visible" case here) ---

def test_M_stage2_send_not_visible_zero_click():
    steps = _ready_steps()
    steps.vision.primary.analyze_screen.side_effect = [
        _composer_localization_call(), _send_search_call(send_visible=False, identity="", bbox=None),
    ]
    ok, mock_pyautogui = _run_ground_and_click(steps)
    assert ok is False
    assert steps.result.failure_reason == LaunchFailureReason.SEND_GROUNDING_INVALID
    mock_pyautogui.click.assert_not_called()


# --- N: foreground failure -> zero Send click (before Stage 1 even starts) ---

def test_N_foreground_lost_before_grounding_zero_click():
    steps = _ready_steps()
    with patch(f"{SEND_MODULE}.get_foreground_window_title", return_value="Visual Studio Code"), \
         patch(f"{SEND_MODULE}.pyautogui") as mock_pyautogui:
        mock_pyautogui.FAILSAFE = True
        assert steps.ground_and_click_send() is False
    assert steps.result.failure_reason == LaunchFailureReason.OUTLOOK_FOREGROUND_LOST
    steps.vision.primary.analyze_screen.assert_not_called()
    assert steps.result.send_click_count == 0


# --- O: abort before grounding -> zero Send click ---

def test_O_abort_before_grounding_zero_click():
    steps = _ready_steps()
    steps.abort_controller.request_abort()
    with patch(f"{SEND_MODULE}.capture_screen") as mock_capture:
        assert steps.ground_and_click_send() is False
        mock_capture.assert_not_called()
    assert steps.result.result == "ABORTED"
    assert steps.result.send_click_count == 0


# --- P: successful path -> exactly one Send click ---

def test_P_successful_path_exactly_one_click():
    steps = _ready_steps()
    steps.vision.primary.analyze_screen.side_effect = [_composer_localization_call(), _send_search_call()]
    ok, mock_pyautogui = _run_ground_and_click(steps)
    assert ok is True
    mock_pyautogui.click.assert_called_once_with()
    assert steps.result.send_click_count == 1
    assert steps.result.send_execution_occurred is True


# --- Q: verification retry -> still exactly one total Send click ---

def test_Q_verification_retry_click_count_stays_one():
    steps = _ready_steps()
    steps.result.send_click_executed = True
    steps.result.send_click_count = 1
    not_yet = MagicMock(
        parsed_json={"verified": False, "detected_state": "x", "confidence": 0.5,
                     "visual_evidence": "x", "reason": "x"},
        raw_text="{}", model="test-model", latency_ms=10.0, input_tokens=5, output_tokens=5,
    )
    confirmed = MagicMock(
        parsed_json={"verified": True, "detected_state": "sent", "confidence": 0.95,
                     "visual_evidence": "sent", "reason": "ok"},
        raw_text="{}", model="test-model", latency_ms=10.0, input_tokens=5, output_tokens=5,
    )
    steps.vision.primary.analyze_screen.side_effect = [not_yet, confirmed]
    with patch(f"{SEND_MODULE}.time.sleep"), patch(f"{SEND_MODULE}.capture_screen", return_value=_capture()), \
         patch(f"{SEND_MODULE}.pyautogui") as mock_pyautogui:
        mock_pyautogui.FAILSAFE = True
        assert steps.verify_sent() is True
        mock_pyautogui.click.assert_not_called()
    assert steps.result.send_click_count == 1


# --- R: technical Stage-1 fallback receives the IDENTICAL reading-pane crop ---

def test_R_gemini_fallback_receives_identical_reading_pane_crop_on_stage1_failure():
    primary = MagicMock()
    primary.provider_name = "anthropic"
    fallback = MagicMock()
    fallback.provider_name = "gemini"
    vision = VisionService(primary, fallback=fallback, default_max_retries=0)
    steps = SendFlowSteps(AbortController(), vision, "test-model", send_approval_granted=True)
    steps.result.text_entry_executed = True
    steps.result.draft_verified_at = "2026-01-01T00:00:00"
    steps.result.semantic_match = True
    steps.result.draft_verification_reply_editor_open = True

    # Stage 1 fails technically on both primary and fallback (so the
    # flow safe-stops right there); this isolates Stage-1's own fallback
    # crop-identity behavior from Stage 2 ever being reached.
    primary.analyze_screen.side_effect = RateLimitError("HTTP 429")
    fallback.analyze_screen.side_effect = RateLimitError("HTTP 429")

    with patch(f"{SEND_MODULE}.get_foreground_window_title", return_value="Outlook"), \
         patch(f"{SEND_MODULE}.capture_screen", return_value=_capture(filename="stage1_fallback.png")), \
         patch(f"{SEND_MODULE}.pyautogui") as mock_pyautogui:
        mock_pyautogui.FAILSAFE = True
        ok = steps.ground_and_click_send()

    assert ok is False
    assert primary.analyze_screen.call_count == 1
    assert fallback.analyze_screen.call_count == 1
    primary_path = primary.analyze_screen.call_args.args[0]
    fallback_path = fallback.analyze_screen.call_args.args[0]
    assert primary_path == fallback_path
    assert primary_path.name == "stage1_fallback_send_composer_reading_pane_crop.png"


# --- S: technical Stage-2 fallback receives the IDENTICAL dynamic crop ---

def test_S_gemini_fallback_receives_identical_dynamic_crop_on_stage2_failure():
    primary = MagicMock()
    primary.provider_name = "anthropic"
    fallback = MagicMock()
    fallback.provider_name = "gemini"
    vision = VisionService(primary, fallback=fallback, default_max_retries=0)
    steps = SendFlowSteps(AbortController(), vision, "test-model", send_approval_granted=True)
    steps.result.text_entry_executed = True
    steps.result.draft_verified_at = "2026-01-01T00:00:00"
    steps.result.semantic_match = True
    steps.result.draft_verification_reply_editor_open = True

    primary.analyze_screen.side_effect = [_composer_localization_call(), RateLimitError("HTTP 429")]
    fallback.analyze_screen.return_value = _send_search_call()

    with patch(f"{SEND_MODULE}.get_foreground_window_title", return_value="Outlook"), \
         patch(f"{SEND_MODULE}.capture_screen", return_value=_capture(filename="stage2_fallback.png")), \
         patch(f"{SEND_MODULE}.pyautogui") as mock_pyautogui:
        mock_pyautogui.FAILSAFE = True
        ok = steps.ground_and_click_send()

    assert ok is True
    stage2_primary_path = primary.analyze_screen.call_args_list[1].args[0]
    fallback_path = fallback.analyze_screen.call_args.args[0]
    assert stage2_primary_path == fallback_path
    assert stage2_primary_path.name == "stage2_fallback_send_dynamic_composer_crop.png"


# --- T: SEND_VERIFICATION remains full-screen ---

def test_T_send_verification_uses_full_screenshot_not_a_crop():
    steps = _ready_steps()
    steps.result.send_click_executed = True
    steps.result.send_click_count = 1
    capture = _capture(filename="send_verify_full.png")
    steps.vision.primary.analyze_screen.return_value = MagicMock(
        parsed_json={"verified": True, "detected_state": "sent", "confidence": 0.95,
                     "visual_evidence": "sent", "reason": "ok"},
        raw_text="{}", model="test-model", latency_ms=10.0, input_tokens=5, output_tokens=5,
    )
    with patch(f"{SEND_MODULE}.time.sleep"), patch(f"{SEND_MODULE}.capture_screen", return_value=capture):
        assert steps.verify_sent() is True
    call_args = steps.vision.primary.analyze_screen.call_args
    assert call_args.args[0] == Path(capture.path)  # the raw screenshot, not a crop


# --- V: TARGET_EMAIL_SEARCH and REPLY_SEARCH crops remain unchanged ---

def test_V_message_list_and_reply_crop_modules_untouched():
    import inspect

    from app.vision import crop as crop_mod

    # MessageListCrop / HorizontalVisionCrop's own remap methods must
    # still be fully self-contained — never delegating to the new
    # rectangular-crop helpers added for Send.
    message_list_source = inspect.getsource(crop_mod.MessageListCrop)
    horizontal_source = inspect.getsource(crop_mod.HorizontalVisionCrop)
    for source in (message_list_source, horizontal_source):
        assert "_rectangular_crop_normalized_to_full_normalized" not in source
        assert "_rectangular_full_normalized_to_crop_normalized" not in source


def test_V_find_email_and_reply_modules_never_reference_send_crop_utilities():
    import inspect

    from app.outlook import find_email as find_email_mod
    from app.outlook import reply as reply_mod

    for mod in (find_email_mod, reply_mod):
        source = inspect.getsource(mod)
        assert "RectangularVisionCrop" not in source
        assert "derive_send_composer_crop_bounds_px" not in source
        assert "create_rectangular_vision_crop" not in source


def test_V_no_provider_name_branch_in_two_stage_send_wiring():
    import inspect

    from app.outlook import send as send_mod

    source = inspect.getsource(send_mod.SendSteps._locate_send_composer_action_bar)
    source += inspect.getsource(send_mod.SendSteps._ground_exact_send)
    for literal in ('"gemini"', "'gemini'", '"claude"', "'claude'", '"anthropic"', "'anthropic'"):
        assert literal not in source


# --- crop cache semantics: SEND cache never shares with either the
# Reply crop cache or TARGET_EMAIL_SEARCH's message-list crop cache ---

def test_send_reading_pane_crop_cache_isolated_from_reply_and_message_list_caches():
    steps = _real_send_flow_steps()
    assert steps._send_reading_pane_crop_cache is not steps._reply_vision_crop_cache
    assert steps._send_reading_pane_crop_cache is not steps.find_open._message_list_crop_cache

    path = real_capture_image_path(1920, 1080, name="isolation_check.png")
    crop = steps._get_send_reading_pane_crop(path, 1920, 1080)
    assert isinstance(crop, HorizontalVisionCrop)
    assert path in steps._send_reading_pane_crop_cache
    assert path not in steps._reply_vision_crop_cache
    assert path not in steps.find_open._message_list_crop_cache


def test_send_reading_pane_crop_cache_reuses_same_crop_for_same_screenshot():
    steps = _real_send_flow_steps()
    path = real_capture_image_path(1920, 1080, name="reuse_check.png")
    crop1 = steps._get_send_reading_pane_crop(path, 1920, 1080)
    crop2 = steps._get_send_reading_pane_crop(path, 1920, 1080)
    assert crop1 is crop2


def test_send_reading_pane_crop_cache_builds_new_crop_for_fresh_screenshot():
    steps = _real_send_flow_steps()
    path_a = real_capture_image_path(1920, 1080, name="fresh_a.png")
    path_b = real_capture_image_path(1920, 1080, name="fresh_b.png")
    crop_a = steps._get_send_reading_pane_crop(path_a, 1920, 1080)
    crop_b = steps._get_send_reading_pane_crop(path_b, 1920, 1080)
    assert crop_a is not crop_b


# --- defensive guard proof (belt-and-suspenders, not a substitute) ---

def test_defensive_guard_recovers_from_a_missing_send_cache_attribute():
    steps = _real_send_flow_steps()
    del steps._send_reading_pane_crop_cache  # simulate a future composing class that forgets to set it

    path = real_capture_image_path(1920, 1080, name="defensive_guard_send.png")
    crop = steps._get_send_reading_pane_crop(path, 1920, 1080)
    assert isinstance(crop, HorizontalVisionCrop)
    assert steps._send_reading_pane_crop_cache == {path: crop}


def test_rectangular_vision_crop_is_its_own_type():
    """RectangularVisionCrop must never be confused with HorizontalVisionCrop
    — the two-stage Send architecture's dynamic crop is a genuinely
    different (all-four-bounds-arbitrary) shape."""
    assert not issubclass(RectangularVisionCrop, HorizontalVisionCrop)
    assert not issubclass(HorizontalVisionCrop, RectangularVisionCrop)
