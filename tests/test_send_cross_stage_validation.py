"""Cross-stage consistency safety gate for SEND_GROUNDING (2026-09-06
follow-up to the two-stage architecture in tests/test_send_two_stage_
grounding.py).

The two-stage architecture's own static production-path regression
(benchmarks/claude/experiments/run_static_production_path_regression.py,
5 Claude-only chains against screenshots/raw/screen_20260906_192232_725.png)
found Stage 2 spatially correct in 4/5 trials — the miss (Trial 3)
landed on a DIFFERENT nearby control ~65-70px above the real Send
button while Stage 2 still semantically reported "Send". Since a wrong
bbox that is nonetheless labeled "Send" is exactly the failure mode the
two-stage architecture on its own cannot catch, this file covers the
NEW cross-stage consistency gate (app.safety.validators.
validate_send_candidate_against_action_bar) and its bounded, same-crop
refinement path (app.outlook.send._refine_send_grounding /
MAX_SEND_GROUNDING_REFINEMENTS=1).

Chosen invariant (explicit product decision, not a default): STRICT
center-containment — the Stage-2 bbox's own center must lie inside
Stage-1's action-bar bbox. This is the stricter of two invariants
considered (the other being "bbox intersects the action bar at all");
strict containment never produces a wrong-actionable result, at the
cost of one additional safe-stop found in the saved 5-trial data (see
test_F_saved_trials_1_2_4_accepted_5_rejected_under_strict_containment
below) that a looser intersection-based invariant would have accepted.
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.outlook.send import SendFlowSteps  # noqa: E402
from app.playbook.failure_reasons import LaunchFailureReason  # noqa: E402
from app.safety.abort_controller import AbortController  # noqa: E402
from app.safety.validators import validate_send_candidate_against_action_bar  # noqa: E402
from app.vision.service import VisionService  # noqa: E402
from tests._capture_test_utils import real_capture_image_path  # noqa: E402

SEND_MODULE = "app.outlook.send"

STATIC_REGRESSION_RESULTS_PATH = (
    Path(__file__).resolve().parents[1] / "benchmarks" / "claude" / "experiments"
    / "send_grounding_results" / "static_production_path_regression.json"
)
FULL_W, FULL_H = 1920, 1080

# Stage-1 action-bar bbox, crop-relative to the reading-pane crop (same
# fixture used throughout tests/test_send_two_stage_grounding.py).
DEFAULT_ACTION_BAR_BBOX = (900.0, 350.0, 980.0, 550.0)
# Send bbox, crop-relative to the dynamic crop, verified (via the REAL
# production crop/remap math) to remap to a full-screen point centered
# inside DEFAULT_ACTION_BAR_BBOX's own full-screen bbox — the
# "consistent" candidate used across tests/test_send_two_stage_
# grounding.py's own fixtures.
CONSISTENT_SEND_BBOX = (619.0, 385.0, 837.0, 616.0)
# Deliberately far from the action bar's own region within the SAME
# dynamic crop — the "inconsistent" candidate for this file's own tests.
INCONSISTENT_SEND_BBOX = (20.0, 20.0, 60.0, 60.0)


def _real_send_flow_steps() -> SendFlowSteps:
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


def _capture(width=1920, height=1080, filename="cross_stage.png"):
    return MagicMock(filename=filename, path=real_capture_image_path(width, height, name=filename), width=width, height=height)


def _composer_localization_call(action_bar_bbox=DEFAULT_ACTION_BAR_BBOX, confidence=0.95):
    return MagicMock(
        parsed_json={
            "composer_visible": True, "action_bar_visible": True,
            "action_bar_bbox": list(action_bar_bbox), "composer_bbox": None,
            "confidence": confidence, "reason": "ok",
        },
        raw_text="{}", model="test-model", latency_ms=50.0, input_tokens=10, output_tokens=5,
    )


def _send_search_call(bbox=CONSISTENT_SEND_BBOX, send_visible=True, identity="Send", confidence=0.9):
    return MagicMock(
        parsed_json={"outlook_visible": True, "send_visible": send_visible, "control_identity": identity,
                     "control_type": "button", "bbox": list(bbox) if bbox is not None else None,
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


# --- A-D: pure-function invariant tests against validate_send_candidate_against_action_bar ---

def test_A_send_center_inside_action_bar_accepted():
    action_bar = [900.0, 400.0, 1000.0, 600.0]
    send_bbox = [930.0, 480.0, 970.0, 520.0]  # center (950, 500) — inside action_bar
    result = validate_send_candidate_against_action_bar(action_bar, send_bbox)
    assert result.accepted is True
    assert result.center_inside_action_bar is True


def test_B_send_bbox_above_action_bar_rejected():
    action_bar = [900.0, 400.0, 1000.0, 600.0]
    send_bbox = [700.0, 480.0, 750.0, 520.0]  # center y=725, well above action_bar's y_min=900
    result = validate_send_candidate_against_action_bar(action_bar, send_bbox)
    assert result.accepted is False
    assert result.center_inside_action_bar is False


def test_C_send_bbox_below_action_bar_rejected():
    action_bar = [900.0, 400.0, 1000.0, 600.0]
    send_bbox = [1050.0, 480.0, 1090.0, 520.0]  # center y=1070, below action_bar's y_max=1000
    result = validate_send_candidate_against_action_bar(action_bar, send_bbox)
    assert result.accepted is False
    assert result.center_inside_action_bar is False


def test_D_send_bbox_horizontally_outside_action_bar_rejected():
    action_bar = [900.0, 400.0, 1000.0, 600.0]
    send_bbox = [930.0, 650.0, 970.0, 700.0]  # center x=675, right of action_bar's x_max=600
    result = validate_send_candidate_against_action_bar(action_bar, send_bbox)
    assert result.accepted is False
    assert result.center_inside_action_bar is False


# --- E/F: replay the validator against the SAVED 5-trial static
# production-path regression data (task item 10 — analyze existing
# trials FIRST, before any runtime refinement behavior was added). ---

def _load_saved_trials():
    if not STATIC_REGRESSION_RESULTS_PATH.exists():
        return None
    return json.loads(STATIC_REGRESSION_RESULTS_PATH.read_text(encoding="utf-8"))["trials"]


def test_E_trial_3_saved_regression_rejected():
    trials = _load_saved_trials()
    if trials is None:
        return  # research artifact not present in this environment — nothing to replay
    trial3 = next(t for t in trials if t["trial"] == 3)
    action_bar_px = trial3["stage1_bbox_full_px"]
    y_min, x_min, y_max, x_max = trial3["remapped_bbox_full_screen_norm"]
    send_px = [y_min / 1000 * FULL_H, x_min / 1000 * FULL_W, y_max / 1000 * FULL_H, x_max / 1000 * FULL_W]
    result = validate_send_candidate_against_action_bar(action_bar_px, send_px)
    assert result.accepted is False


def test_F_saved_trials_1_2_4_accepted_5_rejected_under_strict_containment():
    """Documents the ACTUAL measured split under the chosen (strict
    center-containment) invariant — NOT the same as "every ground-truth-
    correct trial is accepted": Trial 5 is a genuinely correct Send
    location (per real-button-bounds ground truth) that strict
    containment still safe-stops, because Stage 1's own action-bar bbox
    on that trial was itself slightly too narrow (its left edge cut off
    part of the real Send button). This is the documented, deliberate
    trade-off of choosing the stricter invariant: zero wrong-actionable
    results, at the cost of this one extra safe-stop."""
    trials = _load_saved_trials()
    if trials is None:
        return
    expected_accepted = {1: True, 2: True, 4: True, 5: False}
    for trial_num, expected in expected_accepted.items():
        trial = next(t for t in trials if t["trial"] == trial_num)
        action_bar_px = trial["stage1_bbox_full_px"]
        y_min, x_min, y_max, x_max = trial["remapped_bbox_full_screen_norm"]
        send_px = [y_min / 1000 * FULL_H, x_min / 1000 * FULL_W, y_max / 1000 * FULL_H, x_max / 1000 * FULL_W]
        result = validate_send_candidate_against_action_bar(action_bar_px, send_px)
        assert result.accepted is expected, f"trial {trial_num}: expected accepted={expected}, got {result.accepted}"


# --- G/H/I: cross-stage failure (both initial AND refined candidates
# inconsistent) -> zero mouse movement, zero click, no Gemini fallback ---

def test_G_H_cross_stage_failure_both_attempts_zero_mouse_movement_and_click():
    steps = _ready_steps()
    steps.vision.primary.analyze_screen.side_effect = [
        _composer_localization_call(),
        _send_search_call(bbox=INCONSISTENT_SEND_BBOX),  # initial — rejected
        _send_search_call(bbox=INCONSISTENT_SEND_BBOX),  # refinement — still rejected
    ]
    ok, mock_pyautogui = _run_ground_and_click(steps)
    assert ok is False
    assert steps.result.failure_reason == LaunchFailureReason.SEND_GROUNDING_INVALID
    mock_pyautogui.moveTo.assert_not_called()
    mock_pyautogui.click.assert_not_called()
    assert steps.result.send_click_count == 0
    assert steps.result.send_click_executed is False


def test_I_cross_stage_failure_never_triggers_gemini_fallback():
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

    primary.analyze_screen.side_effect = [
        _composer_localization_call(),
        _send_search_call(bbox=INCONSISTENT_SEND_BBOX),
        _send_search_call(bbox=INCONSISTENT_SEND_BBOX),
    ]
    ok, _ = _run_ground_and_click(steps)
    assert ok is False
    fallback.analyze_screen.assert_not_called()


# --- J/K: exactly one refinement, using the IDENTICAL dynamic crop path ---

def test_J_K_one_refinement_maximum_using_identical_dynamic_crop():
    steps = _ready_steps()
    steps.vision.primary.analyze_screen.side_effect = [
        _composer_localization_call(),
        _send_search_call(bbox=INCONSISTENT_SEND_BBOX),  # initial — rejected
        _send_search_call(bbox=INCONSISTENT_SEND_BBOX),  # refinement — still rejected
    ]
    ok, _ = _run_ground_and_click(steps)
    assert ok is False
    # Exactly 3 Vision calls total: Stage 1 + Stage-2 initial + ONE
    # refinement. No second refinement, no loop.
    assert steps.vision.primary.analyze_screen.call_count == 3
    assert steps.result.send_grounding_refinement_attempted is True

    calls = steps.vision.primary.analyze_screen.call_args_list
    stage2_initial_path = calls[1].args[0]
    stage2_refine_path = calls[2].args[0]
    assert stage2_initial_path == stage2_refine_path  # SAME dynamic crop image, no recapture/re-crop


# --- L: successful refinement -> existing validation continues (identity + bbox + click) ---

def test_L_successful_refinement_leads_to_exactly_one_click():
    steps = _ready_steps()
    steps.vision.primary.analyze_screen.side_effect = [
        _composer_localization_call(),
        _send_search_call(bbox=INCONSISTENT_SEND_BBOX),  # initial — cross-stage rejected
        _send_search_call(bbox=CONSISTENT_SEND_BBOX),  # refinement — now consistent
    ]
    ok, mock_pyautogui = _run_ground_and_click(steps)
    assert ok is True
    assert steps.result.send_grounding_refinement_attempted is True
    assert steps.result.send_cross_stage_validation_accepted is True
    mock_pyautogui.click.assert_called_once_with()
    assert steps.result.send_click_count == 1


# --- M/N: failed refinement -> safe stop, no physical click during refinement ---

def test_M_N_failed_refinement_safe_stops_with_zero_click():
    steps = _ready_steps()
    steps.vision.primary.analyze_screen.side_effect = [
        _composer_localization_call(),
        _send_search_call(bbox=INCONSISTENT_SEND_BBOX),
        _send_search_call(bbox=INCONSISTENT_SEND_BBOX),
    ]
    ok, mock_pyautogui = _run_ground_and_click(steps)
    assert ok is False
    assert steps.result.failure_reason == LaunchFailureReason.SEND_GROUNDING_INVALID
    assert "refinement" in steps.result.notes.lower()
    mock_pyautogui.moveTo.assert_not_called()
    mock_pyautogui.click.assert_not_called()
    assert steps.result.send_click_count == 0


def test_refinement_declined_when_refined_identity_is_not_send():
    """A refined candidate that is geometrically fine but semantically
    NOT Send (e.g. the dropdown) must still be rejected — cross-stage
    consistency never overrides the existing semantic identity check."""
    steps = _ready_steps()
    steps.vision.primary.analyze_screen.side_effect = [
        _composer_localization_call(),
        _send_search_call(bbox=INCONSISTENT_SEND_BBOX),
        _send_search_call(bbox=CONSISTENT_SEND_BBOX, identity="Send dropdown"),
    ]
    ok, mock_pyautogui = _run_ground_and_click(steps)
    assert ok is False
    mock_pyautogui.click.assert_not_called()


def test_refinement_technical_failure_is_a_safe_stop_not_a_crash():
    steps = _ready_steps()
    steps.vision.primary.analyze_screen.side_effect = [
        _composer_localization_call(),
        _send_search_call(bbox=INCONSISTENT_SEND_BBOX),
        MagicMock(parsed_json=None, raw_text="not json", model="test-model", latency_ms=10.0, input_tokens=5, output_tokens=1),
    ]
    ok, mock_pyautogui = _run_ground_and_click(steps)
    assert ok is False
    assert steps.result.result == "ERROR"
    mock_pyautogui.click.assert_not_called()


# --- O: successful final result (no refinement needed) -> exactly one Send click ---

def test_O_successful_initial_result_exactly_one_click():
    steps = _ready_steps()
    steps.vision.primary.analyze_screen.side_effect = [
        _composer_localization_call(), _send_search_call(bbox=CONSISTENT_SEND_BBOX),
    ]
    ok, mock_pyautogui = _run_ground_and_click(steps)
    assert ok is True
    mock_pyautogui.click.assert_called_once_with()
    assert steps.result.send_click_count == 1
    assert steps.result.send_grounding_refinement_attempted is False


# --- P: verification retry after a successful (possibly refined) click -> still exactly one Send click ---

def test_P_verification_retry_after_refined_success_still_one_click():
    steps = _ready_steps()
    steps.vision.primary.analyze_screen.side_effect = [
        _composer_localization_call(),
        _send_search_call(bbox=INCONSISTENT_SEND_BBOX),
        _send_search_call(bbox=CONSISTENT_SEND_BBOX),
    ]
    ok, mock_pyautogui = _run_ground_and_click(steps)
    assert ok is True
    assert steps.result.send_click_count == 1

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
         patch(f"{SEND_MODULE}.pyautogui") as mock_pyautogui2:
        mock_pyautogui2.FAILSAFE = True
        assert steps.verify_sent() is True
        mock_pyautogui2.click.assert_not_called()
    assert steps.result.send_click_count == 1
