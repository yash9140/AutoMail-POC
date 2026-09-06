"""Bounded, SAME-screenshot row-bbox refinement regression tests
(2026-09-06 live fix, follow-up to validate_email_row_bbox()).

Live evidence: Claude correctly identified the target candidate
(sender='Yash Dhanraj', visible_subject='Quick Question Regarding...',
subject_truncated=True) but returned an implausible row_bbox
([385.0, 255.0, 460.0, 575.0] — x_max=57.5% of screen width, well past
MESSAGE_LIST_RIGHT_MAX_X_FRACTION). The existing plausibility check
correctly rejected it with zero click (EMAIL_ROW_BBOX_IMPLAUSIBLE) —
that safety behavior is preserved unchanged. This adds exactly ONE
bounded, same-screenshot refinement request (identity already
confirmed, geometry-only) before giving up, per MAX_EMAIL_ROW_BBOX_
REFINEMENTS = 1 — never a loop, never a second attempt, never a reason
to ask the fallback provider to "vote" on unsafe geometry.

No real mouse/keyboard/network/provider call happens anywhere in this
file, and no live Outlook run is performed.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.settings import MAX_MESSAGE_LIST_SCROLL_ATTEMPTS  # noqa: E402
from app.outlook.find_email import FindOpenEmailSteps  # noqa: E402
from app.playbook.failure_reasons import LaunchFailureReason  # noqa: E402
from app.safety.abort_controller import AbortController  # noqa: E402
from app.vision.models import EmailCandidate  # noqa: E402
from app.vision.service import VisionService  # noqa: E402
from tests._capture_test_utils import real_capture_image_path, to_crop_relative_bbox  # noqa: E402

MODULE = "app.outlook.find_email"

# The exact live-failure geometry from this task's report.
LIVE_BAD_BBOX = [385.0, 255.0, 460.0, 575.0]
# A row comfortably inside the message-list column (qualitative R&D
# ground-truth shape — never hardcoded historical pixels).
GOOD_BBOX = [300.0, 200.0, 350.0, 480.0]
SCREENSHOT_PATH = real_capture_image_path(1920, 1080)


def _steps(target_sender: str = "Yash Dhanraj", target_subject: str = "") -> FindOpenEmailSteps:
    return FindOpenEmailSteps(
        AbortController(), VisionService(MagicMock(), fallback=None), "test-model",
        target_sender=target_sender, target_subject=target_subject,
    )


def _candidate(row_bbox=LIVE_BAD_BBOX, sender="Yash Dhanraj", subject="Quick Question Regarding...",
               subject_truncated=True, confidence=0.95, date_or_order="Today") -> EmailCandidate:
    return EmailCandidate(
        sender=sender, subject=subject, subject_truncated=subject_truncated,
        date_or_order=date_or_order, row_bbox=row_bbox, confidence=confidence,
    )


def _refine_call(target_visible=True, row_bbox=GOOD_BBOX, confidence=0.9, reason="ok"):
    # row_bbox here is the FULL-SCREEN bbox this fixture intends (matching
    # every downstream geometry assertion) — converted to crop-relative
    # since that's what the (mocked) refinement call actually returns now.
    return MagicMock(
        parsed_json={
            "target_visible": target_visible,
            "row_bbox": to_crop_relative_bbox(list(row_bbox)) if row_bbox is not None else None,
            "confidence": confidence, "reason": reason,
        },
        raw_text="{}", model="test-model", latency_ms=80.0, input_tokens=10, output_tokens=5,
    )


def _search_call(candidates, target_visible=None, more_below=False):
    payload = {
        "outlook_visible": True, "message_list_visible": True,
        "target_visible": target_visible if target_visible is not None else bool(candidates),
        "candidate_count": len(candidates), "candidates": list(candidates),
        "more_content_below": more_below, "reason": "ok",
    }
    return MagicMock(
        parsed_json=payload, raw_text="{}", model="test-model", latency_ms=100.0, input_tokens=10, output_tokens=5,
    )


def _candidate_dict(row_bbox=LIVE_BAD_BBOX, sender="Yash Dhanraj", subject="Quick Question Regarding...",
                     subject_truncated=True, confidence=0.95, date_or_order="Today"):
    return {"sender": sender, "subject": subject, "subject_truncated": subject_truncated,
            "date_or_order": date_or_order, "row_bbox": to_crop_relative_bbox(list(row_bbox)), "confidence": confidence}


def _capture(width=1920, height=1080, filename="inbox.png"):
    return MagicMock(filename=filename, path=real_capture_image_path(width, height, name=filename), width=width, height=height)


def _run_find(steps, patches=None):
    patches = patches or {}
    with patch(f"{MODULE}.get_foreground_window_title", return_value=patches.get("title", "Mail - Outlook")), \
         patch(f"{MODULE}.capture_screen", return_value=patches.get("capture", _capture())), \
         patch(f"{MODULE}.time.sleep"), \
         patch("app.automation.scrolling.pyautogui") as mock_scroll_pg:
        mock_scroll_pg.FAILSAFE = True
        result = steps.find_target_email()
    return result, mock_scroll_pg


# --- A: original bbox valid -> no refinement call -> click once ---

def test_A_valid_original_bbox_never_triggers_refinement():
    steps = _steps()
    candidate = _candidate(row_bbox=GOOD_BBOX)
    assert steps._validate_and_record_candidate(candidate, 1920, 1080, SCREENSHOT_PATH) is True
    steps.vision.primary.analyze_screen.assert_not_called()
    assert steps.result.row_bbox_refinement_attempted is False
    assert steps.result.failure_reason is None


# --- B: original bbox extends into reading pane -> refinement requested once ---

def test_B_geometry_rejection_triggers_exactly_one_refinement_call():
    steps = _steps()
    candidate = _candidate(row_bbox=LIVE_BAD_BBOX)
    steps.vision.primary.analyze_screen.return_value = _refine_call(target_visible=False, row_bbox=None)
    steps._validate_and_record_candidate(candidate, 1920, 1080, SCREENSHOT_PATH)
    assert steps.vision.primary.analyze_screen.call_count == 1
    assert steps.result.row_bbox_refinement_attempted is True


# --- C: refined bbox valid -> click once ---

def test_C_refined_bbox_valid_allows_click():
    steps = _steps()
    candidate = _candidate(row_bbox=LIVE_BAD_BBOX)
    steps.vision.primary.analyze_screen.return_value = _refine_call(target_visible=True, row_bbox=GOOD_BBOX)
    assert steps._validate_and_record_candidate(candidate, 1920, 1080, SCREENSHOT_PATH) is True
    assert steps.result.failure_reason is None
    assert steps.result.row_bbox_refinement_succeeded is True
    assert steps.result.email_converted_x is not None
    assert steps.result.email_grounding_bbox_raw == pytest.approx(GOOD_BBOX)


# --- D: refined bbox still invalid -> safe stop, zero click ---

def test_D_refined_bbox_still_invalid_safe_stops_zero_click():
    steps = _steps()
    candidate = _candidate(row_bbox=LIVE_BAD_BBOX)
    still_bad = [385.0, 300.0, 460.0, 700.0]  # x_max=700 -> 70%, still past the boundary
    steps.vision.primary.analyze_screen.return_value = _refine_call(target_visible=True, row_bbox=still_bad)
    assert steps._validate_and_record_candidate(candidate, 1920, 1080, SCREENSHOT_PATH) is False
    assert steps.result.failure_reason == LaunchFailureReason.EMAIL_ROW_BBOX_IMPLAUSIBLE
    assert steps.result.row_bbox_refinement_succeeded is False
    assert steps.result.email_converted_x is None


# --- E: current live example — exact reported geometry, rescued by refinement ---

def test_E_live_example_geometry_rescued_by_one_refinement_click_once():
    steps = _steps()
    candidate = _candidate(row_bbox=[385.0, 255.0, 460.0, 575.0])  # exact live-reported raw_bbox
    steps.vision.primary.analyze_screen.return_value = _refine_call(target_visible=True, row_bbox=GOOD_BBOX)
    assert steps._validate_and_record_candidate(candidate, 1920, 1080, SCREENSHOT_PATH) is True
    assert steps.result.failure_reason is None

    with patch(f"{MODULE}.pyautogui") as mock_pyautogui, \
         patch(f"{MODULE}.get_foreground_window_title", return_value="Mail - Outlook"):
        mock_pyautogui.FAILSAFE = True
        assert steps.click_target_email() is True
        mock_pyautogui.click.assert_called_once_with()
    assert steps.result.email_click_count == 1


# --- F: same screenshot path/object used for initial search AND refinement ---

def test_F_refinement_reuses_the_exact_same_screenshot_no_new_capture():
    steps = _steps(target_sender="Yash", target_subject="")
    steps.result.ready_for_interaction = True
    candidates = [_candidate_dict(sender="Yash", row_bbox=LIVE_BAD_BBOX)]
    steps.vision.primary.analyze_screen.side_effect = [
        _search_call(candidates),
        _refine_call(target_visible=True, row_bbox=GOOD_BBOX),
    ]
    with patch(f"{MODULE}.get_foreground_window_title", return_value="Mail - Outlook"), \
         patch(f"{MODULE}.capture_screen", return_value=_capture(filename="same_screenshot.png")) as mock_capture, \
         patch(f"{MODULE}.time.sleep"), \
         patch("app.automation.scrolling.pyautogui"):
        ok = steps.find_target_email()
    assert ok is True
    assert mock_capture.call_count == 1  # exactly one screenshot for the whole search+refine sequence
    calls = steps.vision.primary.analyze_screen.call_args_list
    assert len(calls) == 2
    initial_path = calls[0].args[0]
    refine_path = calls[1].args[0]
    # Both calls now receive the message-list CROP built from the capture
    # (never the raw screenshot) — the crop is cached per source
    # screenshot path, so "same screenshot reused" now proves out as
    # "the identical crop object/path was reused for both calls".
    assert initial_path == refine_path
    assert initial_path.name == "same_screenshot_message_list_crop.png"


# --- G: multiple candidate ambiguity -> no refinement ---

def test_G_multiple_ambiguous_candidates_never_triggers_refinement():
    steps = _steps(target_sender="Yash", target_subject="")
    steps.result.ready_for_interaction = True
    candidates = [
        _candidate_dict(sender="Yash", subject="First", date_or_order="Mon", row_bbox=LIVE_BAD_BBOX),
        _candidate_dict(sender="Yash", subject="Second", date_or_order="Tue", row_bbox=LIVE_BAD_BBOX),
    ]
    steps.vision.primary.analyze_screen.return_value = _search_call(candidates)
    ok, _ = _run_find(steps)
    assert ok is False
    assert steps.result.failure_reason == LaunchFailureReason.MULTIPLE_TARGET_EMAILS_FOUND
    assert steps.vision.primary.analyze_screen.call_count == 1  # only the search call — no refinement
    assert steps.result.row_bbox_refinement_attempted is False


# --- H: wrong sender -> no refinement ---

def test_H_sender_mismatch_never_triggers_refinement():
    steps = _steps(target_sender="Yash Dhanraj", target_subject="")
    steps.result.ready_for_interaction = True
    candidates = [_candidate_dict(sender="Someone Else", row_bbox=LIVE_BAD_BBOX)]
    steps.vision.primary.analyze_screen.return_value = _search_call(candidates, target_visible=False)
    ok, _ = _run_find(steps)
    assert ok is False
    assert steps.result.failure_reason == LaunchFailureReason.TARGET_EMAIL_NOT_FOUND
    # Bounded search-and-scroll (existing, unrelated behavior) retries the
    # SEARCH call up to the scroll limit since no candidate ever matches —
    # the real proof refinement never engaged is that it was never
    # attempted, since _validate_and_record_candidate() (where refinement
    # lives) is never reached without an accepted candidate.
    assert steps.vision.primary.analyze_screen.call_count == MAX_MESSAGE_LIST_SCROLL_ATTEMPTS
    assert steps.result.row_bbox_refinement_attempted is False


# --- I: wrong subject -> no refinement ---

def test_I_subject_mismatch_never_triggers_refinement():
    steps = _steps(target_sender="Yash Dhanraj", target_subject="Mail for project")
    steps.result.ready_for_interaction = True
    candidates = [_candidate_dict(sender="Yash Dhanraj", subject="Totally unrelated", subject_truncated=False,
                                  row_bbox=LIVE_BAD_BBOX)]
    steps.vision.primary.analyze_screen.return_value = _search_call(candidates, target_visible=False)
    ok, _ = _run_find(steps)
    assert ok is False
    assert steps.result.failure_reason == LaunchFailureReason.TARGET_EMAIL_NOT_FOUND
    assert steps.vision.primary.analyze_screen.call_count == MAX_MESSAGE_LIST_SCROLL_ATTEMPTS
    assert steps.result.row_bbox_refinement_attempted is False


# --- J: foreground lost before refinement action -> zero click ---
# (foreground is checked before find_target_email() ever calls the search
# vision request at all — refinement is never reached once it's already
# lost; this proves the existing foreground-loss safe stop is unaffected.)

def test_J_foreground_lost_before_search_blocks_everything_zero_click():
    steps = _steps()
    steps.result.ready_for_interaction = True
    with patch(f"{MODULE}.get_foreground_window_title", return_value="Visual Studio Code"), \
         patch(f"{MODULE}.capture_screen", return_value=_capture()):
        ok = steps.find_target_email()
    assert ok is False
    assert steps.result.failure_reason == LaunchFailureReason.OUTLOOK_FOREGROUND_LOST
    steps.vision.primary.analyze_screen.assert_not_called()


# --- K: abort requested -> zero click ---

def test_K_abort_requested_before_search_blocks_everything_zero_click():
    steps = _steps()
    steps.result.ready_for_interaction = True
    steps.abort_controller.request_abort()
    ok = steps.find_target_email()
    assert ok is False
    assert steps.result.failure_reason == LaunchFailureReason.USER_ABORTED
    steps.vision.primary.analyze_screen.assert_not_called()


# --- L: refinement technical timeout -> common VisionService technical
# policy applies, no physical action before a valid result ---

def test_L_refinement_technical_failure_uses_existing_technical_policy():
    from app.vision.providers.base import RateLimitError

    steps = _steps()
    candidate = _candidate(row_bbox=LIVE_BAD_BBOX)
    steps.vision.primary.analyze_screen.side_effect = RateLimitError("HTTP 429")
    assert steps._validate_and_record_candidate(candidate, 1920, 1080, SCREENSHOT_PATH) is False
    assert steps.result.result == "ERROR"
    assert steps.result.failure_reason == LaunchFailureReason.TECHNICAL_PROVIDER_ERROR
    assert steps.result.email_converted_x is None


# --- M: refined schema-valid but unsafe bbox -> no provider voting, safe stop ---

def test_M_refined_unsafe_bbox_never_escalates_to_a_second_provider():
    steps = _steps()
    candidate = _candidate(row_bbox=LIVE_BAD_BBOX)
    steps.vision.primary.analyze_screen.return_value = _refine_call(target_visible=True, row_bbox=[385.0, 300.0, 460.0, 700.0])
    assert steps._validate_and_record_candidate(candidate, 1920, 1080, SCREENSHOT_PATH) is False
    assert steps.result.failure_reason == LaunchFailureReason.EMAIL_ROW_BBOX_IMPLAUSIBLE
    # Exactly one Vision call for refinement — never a second, and the
    # VisionService here has no fallback configured at all (fallback=None).
    assert steps.vision.primary.analyze_screen.call_count == 1
    assert steps.vision.fallback is None


# --- N: confidence=1.0 cannot override invalid geometry (original or refined) ---

def test_N_high_confidence_refined_bbox_still_validated_by_geometry():
    steps = _steps()
    candidate = _candidate(row_bbox=LIVE_BAD_BBOX, confidence=1.0)
    steps.vision.primary.analyze_screen.return_value = _refine_call(
        target_visible=True, row_bbox=[385.0, 300.0, 460.0, 700.0], confidence=1.0,
    )
    assert steps._validate_and_record_candidate(candidate, 1920, 1080, SCREENSHOT_PATH) is False
    assert steps.result.failure_reason == LaunchFailureReason.EMAIL_ROW_BBOX_IMPLAUSIBLE


# --- O: provider-neutral structural test ---

def test_O_no_provider_name_branch_anywhere_in_refinement_logic():
    import inspect

    from app.outlook import find_email as fe_module

    source = inspect.getsource(fe_module.FindOpenEmailSteps._refine_row_bbox)
    source += inspect.getsource(fe_module.FindOpenEmailSteps._validate_and_record_candidate)
    source += inspect.getsource(fe_module.FindOpenEmailSteps._check_row_bbox_geometry)
    for literal in ('"gemini"', "'gemini'", '"claude"', "'claude'", '"anthropic"', "'anthropic'"):
        assert literal not in source


def test_O_claude_and_gemini_identical_outcome_for_same_refined_bbox():
    """Provider-neutral: whichever provider answers the refinement call,
    the SAME shared validator produces the SAME outcome for the SAME
    refined bbox — provider_used is a diagnostic label only."""
    for provider_label in ("anthropic", "gemini"):
        steps = _steps()
        candidate = _candidate(row_bbox=LIVE_BAD_BBOX)
        refine_response = _refine_call(target_visible=True, row_bbox=GOOD_BBOX)
        refine_response.model = provider_label
        steps.vision.primary.analyze_screen.return_value = refine_response
        assert steps._validate_and_record_candidate(candidate, 1920, 1080, SCREENSHOT_PATH) is True, provider_label
        assert steps.result.failure_reason is None, provider_label


# --- P: PyAutoGUI click max once (even across a refinement) ---

def test_P_click_max_once_even_after_refinement():
    steps = _steps()
    candidate = _candidate(row_bbox=LIVE_BAD_BBOX)
    steps.vision.primary.analyze_screen.return_value = _refine_call(target_visible=True, row_bbox=GOOD_BBOX)
    assert steps._validate_and_record_candidate(candidate, 1920, 1080, SCREENSHOT_PATH) is True

    with patch(f"{MODULE}.pyautogui") as mock_pyautogui, \
         patch(f"{MODULE}.get_foreground_window_title", return_value="Mail - Outlook"):
        mock_pyautogui.FAILSAFE = True
        assert steps.click_target_email() is True
        assert mock_pyautogui.click.call_count == 1


# --- Q: email-open verification failure never causes a re-click ---

def test_Q_open_verification_failure_after_refined_click_never_reclicks():
    steps = _steps()
    steps.result.email_click_count = 1  # simulating click_target_email() already ran, post-refinement
    not_yet = MagicMock(
        parsed_json={"email_open": False, "subject_detected": "", "sender_detected": "", "subject_match": False,
                     "sender_match": False, "body_visible": False, "confidence": 0.5, "reason": "still loading"},
        raw_text="{}", model="test-model", latency_ms=50.0, input_tokens=5, output_tokens=5,
    )
    confirmed = MagicMock(
        parsed_json={"email_open": True, "subject_detected": "Quick Question Regarding Project",
                     "sender_detected": "Yash Dhanraj", "subject_match": True, "sender_match": True,
                     "body_visible": True, "confidence": 0.98, "reason": "open"},
        raw_text="{}", model="test-model", latency_ms=50.0, input_tokens=5, output_tokens=5,
    )
    steps.result.target_sender = "Yash Dhanraj"
    steps.result.target_subject = "Quick Question Regarding Project"
    steps.vision.primary.analyze_screen.side_effect = [not_yet, confirmed]
    with patch(f"{MODULE}.time.sleep"), patch(f"{MODULE}.capture_screen", return_value=_capture()):
        assert steps.verify_email_opened() is True
    assert steps.result.email_click_count == 1  # unchanged — verification never clicks


# --- E2: refinement declining (target no longer visible) safe-stops too ---

def test_refinement_target_not_visible_safe_stops():
    steps = _steps()
    candidate = _candidate(row_bbox=LIVE_BAD_BBOX)
    steps.vision.primary.analyze_screen.return_value = _refine_call(target_visible=False, row_bbox=GOOD_BBOX)
    assert steps._validate_and_record_candidate(candidate, 1920, 1080, SCREENSHOT_PATH) is False
    assert steps.result.failure_reason == LaunchFailureReason.EMAIL_ROW_BBOX_IMPLAUSIBLE
    assert steps.result.email_converted_x is None


def test_malformed_bbox_length_never_triggers_refinement():
    """A structurally malformed row_bbox (wrong length / None) is caught
    BEFORE validate_email_row_bbox() is even called — refinement is only
    ever eligible for a well-formed-but-implausible bbox."""
    steps = _steps()
    candidate = _candidate(row_bbox=[1.0, 2.0, 3.0])
    assert steps._validate_and_record_candidate(candidate, 1920, 1080, SCREENSHOT_PATH) is False
    assert steps.result.failure_reason == LaunchFailureReason.EMAIL_GROUNDING_INVALID
    steps.vision.primary.analyze_screen.assert_not_called()
    assert steps.result.row_bbox_refinement_attempted is False
