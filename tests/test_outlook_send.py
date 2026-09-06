"""app/outlook/send.py unit tests (Phase 7 — Send once + verify sent
state). Covers preconditions, Send grounding/click, the hard max-one
guard, and observation-only sent verification. All external calls
(pyautogui, Vision provider, screen capture, foreground) are mocked —
no live Outlook, ever.
"""

import inspect
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.settings import MAX_SEND_VERIFICATION_ATTEMPTS, SEND_CLICK_MAX  # noqa: E402
from app.outlook.draft import ReplyDraftResult  # noqa: E402
from app.outlook.send import SendFlowSteps, SendResult, SendSteps  # noqa: E402
from app.playbook.failure_reasons import LaunchFailureReason  # noqa: E402
from app.safety.abort_controller import AbortController  # noqa: E402
from app.vision.providers.base import VisionProviderError  # noqa: E402
from app.vision.service import VisionService  # noqa: E402
from tests._capture_test_utils import real_capture_image_path  # noqa: E402

MODULE = "app.outlook.send"

# A plausible, crop-relative (relative to the reading-pane crop,
# 1248x1080 at 1920x1080) action-bar bbox — remaps to a full-screen
# region that in turn derives a valid, non-degenerate Stage-2 crop.
# Exact numeric meaning doesn't matter for tests that only exercise
# Stage-2's OWN validation (invalid bbox / out-of-range / low confidence
# / wrong identity) — those keep working through any valid Stage-1
# localization, since the crop transform is linear and preserves
# validity/invalidity either way (same reasoning as the analogous Reply/
# email crop-relative test fixtures elsewhere in this suite).
DEFAULT_ACTION_BAR_BBOX = (900.0, 350.0, 980.0, 550.0)


def _steps(send_approval_granted: bool = True) -> SendFlowSteps:
    return SendFlowSteps(AbortController(), VisionService(MagicMock(), fallback=None), "gemini-3.6-flash", send_approval_granted)


def _ready_steps(send_approval_granted: bool = True) -> SendFlowSteps:
    """Every Phase 1-6 precondition already satisfied — DRAFT_READY,
    approval granted, zero Send clicks — the state Phase 7 always starts
    from."""
    steps = _steps(send_approval_granted)
    steps.result.text_entry_executed = True
    steps.result.draft_verified_at = "2026-01-01T00:00:00"
    steps.result.semantic_match = True
    steps.result.draft_verification_reply_editor_open = True
    steps.result.draft_reply = "Sure, will do."
    return steps


def _capture(width=1920, height=1080, filename="send.png"):
    return MagicMock(filename=filename, path=real_capture_image_path(width, height, name=filename), width=width, height=height)


def _composer_localization_call(action_bar_bbox=DEFAULT_ACTION_BAR_BBOX, confidence=0.95,
                                 composer_visible=True, action_bar_visible=True):
    """Stage 1 (SEND_COMPOSER_LOCALIZATION) mock response — action_bar_bbox
    is crop-relative to the reading-pane crop, per production's actual
    contract (see app/outlook/send.py::_locate_send_composer_action_bar)."""
    return MagicMock(
        parsed_json={
            "composer_visible": composer_visible, "action_bar_visible": action_bar_visible,
            "action_bar_bbox": list(action_bar_bbox) if action_bar_bbox is not None else None,
            "composer_bbox": None, "confidence": confidence, "reason": "ok",
        },
        raw_text="{}", model="gemini-3.6-flash", latency_ms=50.0, input_tokens=10, output_tokens=5,
    )


def _send_search_call(send_visible=True, identity="Send", control_type="button",
                       bbox=(619.0, 385.0, 837.0, 616.0), confidence=0.9):
    return MagicMock(
        parsed_json={"outlook_visible": True, "send_visible": send_visible, "control_identity": identity,
                     "control_type": control_type, "bbox": list(bbox) if bbox is not None else None,
                     "confidence": confidence, "reason": "ok"},
        raw_text="{}", model="gemini-3.6-flash", latency_ms=80.0, input_tokens=10, output_tokens=5,
    )


def _sent_verify_call(verified: bool, detected_state="Inbox view, no compose box"):
    return MagicMock(
        parsed_json={"verified": verified, "detected_state": detected_state, "confidence": 0.9,
                     "visual_evidence": "no compose box visible", "reason": "ok"},
        raw_text="{}", model="gemini-3.6-flash", latency_ms=80.0, input_tokens=10, output_tokens=5,
    )


# --- Preconditions (B, C) ---

def test_precondition_fails_when_send_not_approved():
    steps = _ready_steps(send_approval_granted=False)
    assert steps.validate_send_preconditions() is False
    assert steps.result.failure_reason == LaunchFailureReason.SEND_NOT_APPROVED
    assert steps.result.send_click_count == 0


def test_precondition_fails_when_draft_not_verified():
    steps = _steps(send_approval_granted=True)  # draft-verified fields never set
    with patch(f"{MODULE}.get_foreground_window_title", return_value="Outlook"):
        assert steps.validate_send_preconditions() is False
    assert steps.result.failure_reason == LaunchFailureReason.SEND_PRECONDITION_FAILED
    assert steps.result.send_click_count == 0


def test_precondition_passes_when_everything_satisfied():
    steps = _ready_steps()
    with patch(f"{MODULE}.get_foreground_window_title", return_value="Outlook"):
        assert steps.validate_send_preconditions() is True
    assert steps.result.send_click_count == 0


# --- Grounding validation (D, E, F, G) ---

def test_invalid_bbox_zero_click():
    steps = _ready_steps()
    steps.vision.primary.analyze_screen.side_effect = [_composer_localization_call(), _send_search_call(bbox=None)]
    with patch(f"{MODULE}.get_foreground_window_title", return_value="Outlook"), \
         patch(f"{MODULE}.capture_screen", return_value=_capture()), \
         patch(f"{MODULE}.pyautogui") as mock_pyautogui:
        mock_pyautogui.FAILSAFE = True
        assert steps.ground_and_click_send() is False
        mock_pyautogui.click.assert_not_called()
    assert steps.result.failure_reason == LaunchFailureReason.SEND_GROUNDING_INVALID
    assert steps.result.send_click_count == 0


def test_out_of_range_bbox_zero_click():
    steps = _ready_steps()
    # A huge, unambiguous excess (crop-relative) — guaranteed to remain
    # outside the valid 0-1000 range in full-screen space regardless of
    # the dynamic crop's own (much smaller) size, unlike a merely
    # slightly-out-of-range value which a small crop's linear remap
    # could bring back inside range.
    steps.vision.primary.analyze_screen.side_effect = [
        _composer_localization_call(), _send_search_call(bbox=(400.0, 800.0, 440.0, 100000.0)),
    ]
    with patch(f"{MODULE}.get_foreground_window_title", return_value="Outlook"), \
         patch(f"{MODULE}.capture_screen", return_value=_capture()), \
         patch(f"{MODULE}.pyautogui") as mock_pyautogui:
        mock_pyautogui.FAILSAFE = True
        assert steps.ground_and_click_send() is False
        mock_pyautogui.click.assert_not_called()
    assert steps.result.failure_reason == LaunchFailureReason.SEND_GROUNDING_INVALID
    assert steps.result.send_click_count == 0


def test_low_confidence_zero_click():
    steps = _ready_steps()
    steps.vision.primary.analyze_screen.side_effect = [
        _composer_localization_call(), _send_search_call(confidence=0.1),
    ]
    with patch(f"{MODULE}.get_foreground_window_title", return_value="Outlook"), \
         patch(f"{MODULE}.capture_screen", return_value=_capture()), \
         patch(f"{MODULE}.pyautogui") as mock_pyautogui:
        mock_pyautogui.FAILSAFE = True
        assert steps.ground_and_click_send() is False
        mock_pyautogui.click.assert_not_called()
    assert steps.result.failure_reason == LaunchFailureReason.SEND_GROUNDING_INVALID
    assert steps.result.send_click_count == 0


def test_wrong_semantic_control_zero_click():
    steps = _ready_steps()
    steps.vision.primary.analyze_screen.side_effect = [
        _composer_localization_call(), _send_search_call(identity="Schedule Send"),
    ]
    with patch(f"{MODULE}.get_foreground_window_title", return_value="Outlook"), \
         patch(f"{MODULE}.capture_screen", return_value=_capture()), \
         patch(f"{MODULE}.pyautogui") as mock_pyautogui:
        mock_pyautogui.FAILSAFE = True
        assert steps.ground_and_click_send() is False
        mock_pyautogui.click.assert_not_called()
    assert steps.result.failure_reason == LaunchFailureReason.SEND_GROUNDING_INVALID
    assert steps.result.send_click_count == 0


# --- Foreground / abort safety around the click (H, I) ---

def test_foreground_lost_before_move_zero_click():
    steps = _ready_steps()
    steps.result.send_converted_x = 850
    steps.result.send_converted_y = 420
    with patch(f"{MODULE}.get_foreground_window_title", return_value="Visual Studio Code"), \
         patch(f"{MODULE}.pyautogui") as mock_pyautogui:
        mock_pyautogui.FAILSAFE = True
        assert steps._click_send() is False
        mock_pyautogui.moveTo.assert_not_called()
        mock_pyautogui.click.assert_not_called()
    assert steps.result.failure_reason == LaunchFailureReason.OUTLOOK_FOREGROUND_LOST
    assert steps.result.send_click_count == 0


def test_foreground_lost_between_move_and_click_zero_click():
    steps = _ready_steps()
    steps.result.send_converted_x = 850
    steps.result.send_converted_y = 420
    titles = iter(["Outlook", "Visual Studio Code"])  # ok before move, drift before click
    with patch(f"{MODULE}.get_foreground_window_title", side_effect=lambda: next(titles)), \
         patch(f"{MODULE}.pyautogui") as mock_pyautogui:
        mock_pyautogui.FAILSAFE = True
        assert steps._click_send() is False
        mock_pyautogui.moveTo.assert_called_once()
        mock_pyautogui.click.assert_not_called()
    assert steps.result.failure_reason == LaunchFailureReason.OUTLOOK_FOREGROUND_LOST
    assert steps.result.send_click_count == 0


# --- Provider retry during grounding (J) ---

def test_provider_transient_error_during_grounding_retries_then_succeeds():
    steps = _ready_steps()
    # Transient failure on Stage 1 (SEND_COMPOSER_LOCALIZATION)'s first
    # attempt, retried once (PROVIDER_RETRY_COUNT=1) then succeeds;
    # Stage 2 (SEND_GROUNDING) succeeds on its own first attempt.
    steps.vision.primary.analyze_screen.side_effect = [
        VisionProviderError("timeout"), _composer_localization_call(), _send_search_call(),
    ]
    with patch(f"{MODULE}.get_foreground_window_title", return_value="Outlook"), \
         patch(f"{MODULE}.capture_screen", return_value=_capture()), \
         patch(f"{MODULE}.pyautogui") as mock_pyautogui:
        mock_pyautogui.FAILSAFE = True
        assert steps.ground_and_click_send() is True
        assert mock_pyautogui.click.call_count == 1
    assert steps.result.provider_retries == 1
    assert steps.result.send_click_count == 1


# --- Full success path (A) ---

def test_full_send_flow_one_click_then_verified():
    steps = _ready_steps()
    steps.vision.primary.analyze_screen.side_effect = [
        _composer_localization_call(), _send_search_call(), _sent_verify_call(True),
    ]
    with patch(f"{MODULE}.get_foreground_window_title", return_value="Outlook"):
        assert steps.validate_send_preconditions() is True
        with patch(f"{MODULE}.capture_screen", return_value=_capture()), \
             patch(f"{MODULE}.pyautogui") as mock_pyautogui:
            mock_pyautogui.FAILSAFE = True
            assert steps.ground_and_click_send() is True
            mock_pyautogui.click.assert_called_once()
        assert steps.result.send_click_count == 1
        assert steps.result.send_click_executed is True

        with patch(f"{MODULE}.time.sleep"), patch(f"{MODULE}.capture_screen", return_value=_capture()):
            assert steps.verify_sent() is True
    assert steps.result.sent_verified is True
    assert steps.result.result == "PASS"
    assert steps.result.send_click_count == 1


# --- Sent-verification retry (K, L, M, N) ---

def test_verification_uncertain_then_succeeds_click_count_stays_one():
    steps = _ready_steps()
    steps.result.send_click_executed = True
    steps.result.send_click_count = 1
    steps.vision.primary.analyze_screen.side_effect = [_sent_verify_call(False), _sent_verify_call(True)]
    captures = [_capture(filename="s1.png"), _capture(filename="s2.png")]
    with patch(f"{MODULE}.time.sleep"), patch(f"{MODULE}.capture_screen", side_effect=captures), \
         patch(f"{MODULE}.pyautogui") as mock_pyautogui:
        mock_pyautogui.FAILSAFE = True
        assert steps.verify_sent() is True
        mock_pyautogui.click.assert_not_called()
        mock_pyautogui.moveTo.assert_not_called()
    assert steps.result.send_click_count == 1
    assert steps.result.sent_verified is True
    assert len(steps.result.send_verification_attempts) == 2


def test_verification_exhausts_click_count_stays_one_no_resend():
    steps = _ready_steps()
    steps.result.send_click_executed = True
    steps.result.send_click_count = 1
    steps.vision.primary.analyze_screen.side_effect = [_sent_verify_call(False), _sent_verify_call(False)]
    captures = [_capture(filename="s1.png"), _capture(filename="s2.png")]
    with patch(f"{MODULE}.time.sleep"), patch(f"{MODULE}.capture_screen", side_effect=captures), \
         patch(f"{MODULE}.pyautogui") as mock_pyautogui:
        mock_pyautogui.FAILSAFE = True
        assert steps.verify_sent() is False
        mock_pyautogui.click.assert_not_called()
        mock_pyautogui.moveTo.assert_not_called()
    assert steps.result.send_click_count == 1
    assert steps.result.sent_verified is False
    assert steps.result.failure_reason == LaunchFailureReason.SEND_VERIFICATION_UNCERTAIN
    assert len(steps.result.send_verification_attempts) == MAX_SEND_VERIFICATION_ATTEMPTS


def test_provider_failure_during_sent_verification_no_resend():
    steps = _ready_steps()
    steps.result.send_click_executed = True
    steps.result.send_click_count = 1
    steps.vision.primary.analyze_screen.side_effect = [VisionProviderError("timeout"), VisionProviderError("timeout")]
    with patch(f"{MODULE}.time.sleep"), patch(f"{MODULE}.capture_screen", return_value=_capture()), \
         patch(f"{MODULE}.pyautogui") as mock_pyautogui:
        mock_pyautogui.FAILSAFE = True
        assert steps.verify_sent() is False
        mock_pyautogui.click.assert_not_called()
    assert steps.result.send_click_count == 1
    assert steps.result.failure_reason == LaunchFailureReason.TECHNICAL_PROVIDER_ERROR


def test_verification_retries_never_call_click_move_write_press():
    steps = _ready_steps()
    steps.result.send_click_executed = True
    steps.result.send_click_count = 1
    steps.vision.primary.analyze_screen.side_effect = [_sent_verify_call(False), _sent_verify_call(True)]
    captures = [_capture(filename="s1.png"), _capture(filename="s2.png")]
    with patch(f"{MODULE}.time.sleep"), patch(f"{MODULE}.capture_screen", side_effect=captures), \
         patch(f"{MODULE}.pyautogui") as mock_pyautogui:
        mock_pyautogui.FAILSAFE = True
        assert steps.verify_sent() is True
    mock_pyautogui.click.assert_not_called()
    mock_pyautogui.moveTo.assert_not_called()
    mock_pyautogui.write.assert_not_called()
    mock_pyautogui.press.assert_not_called()


def test_verify_sent_raises_if_send_never_clicked():
    steps = _ready_steps()
    try:
        steps.verify_sent()
        assert False, "expected RuntimeError"
    except RuntimeError:
        pass


# --- Hard max-one guard (O) ---

def test_click_send_refuses_second_call():
    steps = _ready_steps()
    steps.result.send_converted_x = 850
    steps.result.send_converted_y = 420
    with patch(f"{MODULE}.get_foreground_window_title", return_value="Outlook"), \
         patch(f"{MODULE}.pyautogui") as mock_pyautogui:
        mock_pyautogui.FAILSAFE = True
        assert steps._click_send() is True
        assert steps.result.send_click_count == 1
        assert steps._click_send() is False
        assert steps.result.send_click_count == 1
    assert mock_pyautogui.click.call_count == 1
    assert SEND_CLICK_MAX == 1


def test_precondition_refuses_when_click_count_already_nonzero():
    steps = _ready_steps()
    steps.result.send_click_count = 1
    with patch(f"{MODULE}.get_foreground_window_title", return_value="Outlook"):
        assert steps.validate_send_preconditions() is False
    assert steps.result.failure_reason == LaunchFailureReason.SEND_PRECONDITION_FAILED


# --- Metrics propagation (Q) ---

def test_send_result_inherits_all_phase1_6_fields():
    assert issubclass(SendResult, ReplyDraftResult)
    for field in (
        "draft_generation_ms", "typing_segment_count", "draft_verification_attempts",
        "reply_click_count", "email_sections", "total_vision_calls", "total_estimated_cost",
    ):
        assert field in SendResult.model_fields


# --- Structural / source safety (P, R, and the structural regression block) ---

def test_no_send_shortcut_source_exists():
    import app.outlook.send as send_mod

    source = inspect.getsource(send_mod)
    normalized = source.replace('"', "'").lower()
    assert "hotkey('ctrl', 'enter')" not in normalized
    assert "hotkey('alt', 's')" not in normalized
    assert "doubleclick" not in normalized


def test_only_one_physical_send_click_path():
    import app.outlook.send as send_mod

    source = inspect.getsource(send_mod)
    assert source.count("pyautogui.click()") == 1  # only inside _click_send

    verify_sent_source = inspect.getsource(send_mod.SendSteps.verify_sent)
    assert "_click_send" not in verify_sent_source
    assert "pyautogui.click" not in verify_sent_source
    assert "pyautogui.moveTo" not in verify_sent_source
    assert "pyautogui.write" not in verify_sent_source
    assert "pyautogui.press" not in verify_sent_source


def test_no_historical_or_fixed_coordinates_used():
    import app.outlook.send as send_mod

    source = inspect.getsource(send_mod)
    assert "rnd.experiments" not in source
    assert "screenshots/annotated" not in source
    assert (
        "self.result.send_converted_x, self.result.send_converted_y = "
        "validation.converted_x, validation.converted_y" in source
    )
