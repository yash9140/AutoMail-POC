"""Pre-click screenshot-freshness guard integration tests (2026-09-06).

Live evidence proved click-point math and physical execution were both
correct (EMAIL_MOUSE_POSITION_CONFIRMED matches=True) yet the wrong
email opened — the grounding screenshot had gone stale (~16s, across
three sequential Vision calls) by the time the physical click executed.
This guard adds a fast, fully local (no Vision call) pixel-difference
comparison of the message-list ROI immediately before any physical
action; see app/safety/screen_freshness.py for the deterministic
comparison itself (unit-tested separately in
tests/test_safety_screen_freshness.py) and app/outlook/find_email.py::
_ensure_target_row_still_fresh_or_reground() for the integration.

This file controls changed=True/False deterministically by patching
check_message_list_roi_freshness() at the app.outlook.find_email
import site — the same "mock at the boundary" pattern already used for
every Vision call in this test suite — rather than needing real,
distinguishable screenshots for every control-flow scenario.

No real mouse/keyboard/network/provider call happens anywhere in this
file, and no live Outlook run is performed.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.outlook.find_email import FindOpenEmailSteps  # noqa: E402
from app.playbook.failure_reasons import LaunchFailureReason  # noqa: E402
from app.safety.abort_controller import AbortController  # noqa: E402
from app.safety.screen_freshness import FreshnessCheckResult  # noqa: E402
from app.vision.service import VisionService  # noqa: E402
from tests._capture_test_utils import real_capture_image_path, to_crop_relative_bbox  # noqa: E402

MODULE = "app.outlook.find_email"
SCROLL_MODULE = "app.automation.scrolling"

ROW_BBOX = (378.0, 258.0, 456.0, 548.0)


def _steps(target_sender="Yash Dhanraj", target_subject="Quick Question Regarding Tomorrow") -> FindOpenEmailSteps:
    steps = FindOpenEmailSteps(
        AbortController(), VisionService(MagicMock(), fallback=None), "test-model",
        target_sender=target_sender, target_subject=target_subject,
    )
    steps.result.ready_for_interaction = True
    return steps


def _candidate(subject="Quick Question Regarding Tomorrow", subject_truncated=False, row_bbox=ROW_BBOX,
               confidence=0.95, date_or_order="Today"):
    return {"sender": "Yash Dhanraj", "subject": subject, "subject_truncated": subject_truncated,
            "date_or_order": date_or_order, "row_bbox": to_crop_relative_bbox(list(row_bbox)), "confidence": confidence}


def _search_call(candidates):
    return MagicMock(
        parsed_json={
            "outlook_visible": True, "message_list_visible": True, "target_visible": True,
            "candidate_count": len(candidates), "candidates": candidates,
            "more_content_below": False, "reason": "ok",
        },
        raw_text="{}", model="test-model", latency_ms=100.0, input_tokens=10, output_tokens=5,
    )


def _capture(width=1920, height=1080, filename="inbox.png"):
    return MagicMock(filename=filename, path=real_capture_image_path(width, height, name=filename), width=width, height=height)


def _fresh(changed: bool, score: float = 0.0) -> FreshnessCheckResult:
    return FreshnessCheckResult(changed=changed, difference_score=score, threshold=6.0, roi=[326, 300, 1056, 500])


def _found_ready_steps():
    """A steps object that has already run find_target_email() to
    completion (search + geometry validated + click point computed),
    ready for click_target_email() to be exercised in isolation."""
    steps = _steps()
    steps.vision.primary.analyze_screen.return_value = _search_call([_candidate()])
    with patch(f"{MODULE}.get_foreground_window_title", return_value="Mail - Outlook"), \
         patch(f"{MODULE}.capture_screen", return_value=_capture()), \
         patch(f"{MODULE}.time.sleep"), \
         patch(f"{SCROLL_MODULE}.pyautogui"):
        ok = steps.find_target_email()
    assert ok is True
    return steps


# --- A / H: unchanged ROI -> click proceeds with original coordinates, no extra Vision call ---

def test_A_unchanged_roi_click_proceeds_with_original_coordinates():
    steps = _found_ready_steps()
    original_x, original_y = steps.result.email_converted_x, steps.result.email_converted_y
    calls_before = steps.vision.primary.analyze_screen.call_count

    with patch(f"{MODULE}.pyautogui") as mock_pyautogui, \
         patch(f"{MODULE}.get_foreground_window_title", return_value="Mail - Outlook"), \
         patch(f"{MODULE}.capture_screen", return_value=_capture()), \
         patch(f"{MODULE}.check_message_list_roi_freshness", return_value=_fresh(changed=False)):
        mock_pyautogui.FAILSAFE = True
        assert steps.click_target_email() is True
        mock_pyautogui.moveTo.assert_called_once_with(original_x, original_y, duration=mock_pyautogui.moveTo.call_args.kwargs.get("duration"))
        mock_pyautogui.click.assert_called_once_with()

    assert steps.vision.primary.analyze_screen.call_count == calls_before  # H: zero extra Vision calls


# --- F: changed ROI -> exactly one bounded fresh re-ground, then click ---

def test_F_changed_roi_triggers_exactly_one_bounded_reground_then_clicks():
    steps = _found_ready_steps()
    stale_x, stale_y = steps.result.email_converted_x, steps.result.email_converted_y

    reground_search = _search_call([_candidate(row_bbox=ROW_BBOX)])
    steps.vision.primary.analyze_screen.side_effect = [reground_search]

    with patch(f"{MODULE}.pyautogui") as mock_pyautogui, \
         patch(f"{MODULE}.get_foreground_window_title", return_value="Mail - Outlook"), \
         patch(f"{MODULE}.capture_screen", return_value=_capture(filename="fresh_b.png")), \
         patch(f"{MODULE}.check_message_list_roi_freshness") as mock_freshness:
        mock_freshness.side_effect = [_fresh(changed=True, score=40.0), _fresh(changed=False)]
        mock_pyautogui.FAILSAFE = True
        assert steps.click_target_email() is True
        mock_pyautogui.click.assert_called_once_with()

    assert steps.result.stale_reground_attempted is True
    assert steps.result.target_email_grounding_screenshot_path == real_capture_image_path(1920, 1080, name="fresh_b.png")
    # Coordinates are still valid/consistent (re-derived from the same
    # bbox in this scenario) — the important proof is that a re-ground
    # cycle actually ran (see test_I for the "uses the fresh path" proof).
    assert steps.result.email_converted_x is not None


# --- G: screen changes AGAIN after the bounded re-ground -> safe stop, zero click ---

def test_G_second_freshness_failure_after_reground_is_a_safe_stop():
    steps = _found_ready_steps()
    reground_search = _search_call([_candidate(row_bbox=ROW_BBOX)])
    steps.vision.primary.analyze_screen.side_effect = [reground_search]

    with patch(f"{MODULE}.pyautogui") as mock_pyautogui, \
         patch(f"{MODULE}.get_foreground_window_title", return_value="Mail - Outlook"), \
         patch(f"{MODULE}.capture_screen", return_value=_capture(filename="fresh_b.png")), \
         patch(f"{MODULE}.check_message_list_roi_freshness") as mock_freshness:
        mock_freshness.side_effect = [_fresh(changed=True, score=40.0), _fresh(changed=True, score=35.0)]
        mock_pyautogui.FAILSAFE = True
        assert steps.click_target_email() is False
        mock_pyautogui.moveTo.assert_not_called()
        mock_pyautogui.click.assert_not_called()

    assert steps.result.failure_reason == LaunchFailureReason.TARGET_EMAIL_SCREEN_CHANGED_BEFORE_CLICK
    # Exactly 2 Vision calls total: the original search (inside
    # _found_ready_steps()) + the ONE bounded re-ground search — never a
    # second re-ground.
    assert steps.vision.primary.analyze_screen.call_count == 2


# --- I: the bounded re-ground uses the FRESH screenshot path, not the original ---

def test_I_reground_search_call_uses_the_fresh_screenshot_path():
    steps = _found_ready_steps()
    reground_search = _search_call([_candidate(row_bbox=ROW_BBOX)])
    steps.vision.primary.analyze_screen.side_effect = [reground_search]

    with patch(f"{MODULE}.pyautogui") as mock_pyautogui, \
         patch(f"{MODULE}.get_foreground_window_title", return_value="Mail - Outlook"), \
         patch(f"{MODULE}.capture_screen", return_value=_capture(filename="fresh_b.png")), \
         patch(f"{MODULE}.check_message_list_roi_freshness") as mock_freshness:
        mock_freshness.side_effect = [_fresh(changed=True, score=40.0), _fresh(changed=False)]
        mock_pyautogui.FAILSAFE = True
        steps.click_target_email()

    # call_args_list[0] is the ORIGINAL search (inside _found_ready_steps());
    # call_args_list[1] is the bounded re-ground search this test proves.
    # Production sends the message-list CROP built from the capture, not
    # the raw screenshot itself — its filename still derives from the
    # FRESH capture's own name (see create_message_list_crop()'s naming),
    # proving the re-ground used the fresh screenshot, not the original.
    call_args = steps.vision.primary.analyze_screen.call_args_list[1]
    assert call_args.args[0].name == "fresh_b_message_list_crop.png"


# --- C: changed ROI with a DECLINING re-ground (no candidate found in
# the fresh screenshot) -> zero click, stale coordinates never used ---

def test_C_reground_finds_nothing_zero_click_using_stale_coordinates():
    steps = _found_ready_steps()
    # The fresh screenshot no longer shows any matching candidate at all
    # (e.g. the target row scrolled out of view).
    empty_search = MagicMock(
        parsed_json={"outlook_visible": True, "message_list_visible": True, "target_visible": False,
                     "candidate_count": 0, "candidates": [], "more_content_below": False, "reason": "ok"},
        raw_text="{}", model="test-model", latency_ms=100.0, input_tokens=10, output_tokens=5,
    )
    steps.vision.primary.analyze_screen.side_effect = [empty_search]

    with patch(f"{MODULE}.pyautogui") as mock_pyautogui, \
         patch(f"{MODULE}.get_foreground_window_title", return_value="Mail - Outlook"), \
         patch(f"{MODULE}.capture_screen", return_value=_capture(filename="fresh_b.png")), \
         patch(f"{MODULE}.check_message_list_roi_freshness", return_value=_fresh(changed=True, score=40.0)):
        mock_pyautogui.FAILSAFE = True
        assert steps.click_target_email() is False
        mock_pyautogui.moveTo.assert_not_called()
        mock_pyautogui.click.assert_not_called()

    assert steps.result.failure_reason == LaunchFailureReason.TARGET_EMAIL_NOT_FOUND


# --- J: provider-neutral implementation ---

def test_J_no_provider_name_branch_in_freshness_logic():
    import inspect

    from app.outlook import find_email as fe_module

    source = inspect.getsource(fe_module.FindOpenEmailSteps._ensure_target_row_still_fresh_or_reground)
    source += inspect.getsource(fe_module.FindOpenEmailSteps._compute_preclick_roi_pixels)
    for literal in ('"gemini"', "'gemini'", '"claude"', "'claude'", '"anthropic"', "'anthropic'"):
        assert literal not in source


# --- K: physical click remains max once across every scenario ---

def test_K_click_max_once_when_unchanged():
    steps = _found_ready_steps()
    with patch(f"{MODULE}.pyautogui") as mock_pyautogui, \
         patch(f"{MODULE}.get_foreground_window_title", return_value="Mail - Outlook"), \
         patch(f"{MODULE}.capture_screen", return_value=_capture()), \
         patch(f"{MODULE}.check_message_list_roi_freshness", return_value=_fresh(changed=False)):
        mock_pyautogui.FAILSAFE = True
        assert steps.click_target_email() is True
        assert mock_pyautogui.click.call_count == 1
    assert steps.result.email_click_count == 1


def test_K_click_max_once_after_successful_reground():
    steps = _found_ready_steps()
    reground_search = _search_call([_candidate(row_bbox=ROW_BBOX)])
    steps.vision.primary.analyze_screen.side_effect = [reground_search]
    with patch(f"{MODULE}.pyautogui") as mock_pyautogui, \
         patch(f"{MODULE}.get_foreground_window_title", return_value="Mail - Outlook"), \
         patch(f"{MODULE}.capture_screen", return_value=_capture(filename="fresh_b.png")), \
         patch(f"{MODULE}.check_message_list_roi_freshness") as mock_freshness:
        mock_freshness.side_effect = [_fresh(changed=True, score=40.0), _fresh(changed=False)]
        mock_pyautogui.FAILSAFE = True
        assert steps.click_target_email() is True
        assert mock_pyautogui.click.call_count == 1
    assert steps.result.email_click_count == 1
