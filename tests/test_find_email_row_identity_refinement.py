"""Candidate-identity <-> row-bbox association regression tests
(2026-09-06, same-day follow-up to the deterministic click-X safe zone).

Live evidence: Claude's TARGET_EMAIL_SEARCH correctly ACCEPTED the
candidate identity (sender='Yash Dhanraj', subject='Quick Question
Regarding...', subject_truncated=True) — sender_ok=True, subject_ok=True
per _evaluate_candidate() — and its row_bbox passed every geometry
check (message-list boundary, width/height plausibility) after one
bounded geometry refinement. The deterministic safe-zone click policy
computed a correct, working click point and physically executed it.
Yet the email that opened was 'Yash Dhanraj / Demo of live run' — a
DIFFERENT email from the same sender. Vision had bound the accepted
candidate's sender+subject to the WRONG row's bbox; nothing before this
fix ever independently verified that the bbox visually belonged to the
same row as the candidate's own reported subject text — a purely
geometric check cannot detect this, since the wrong-row bbox was a
perfectly plausible row shape on its own.

Fix: when same-sender ambiguity, a provisional (truncated-subject)
match, or low confidence make that risk real, ONE bounded, same-
screenshot TARGET_EMAIL_ROW_IDENTITY_REFINE call independently re-
grounds the accepted sender+subject to its own exact row BEFORE
geometry validation / the physical click — see app/outlook/find_email.py
::_refine_row_identity().

No real mouse/keyboard/network/provider call happens anywhere in this
file, and no live Outlook run is performed.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.outlook.find_email import FindOpenEmailSteps  # noqa: E402
from app.playbook.failure_reasons import LaunchFailureReason  # noqa: E402
from app.safety.abort_controller import AbortController  # noqa: E402
from app.vision.providers.base import RateLimitError  # noqa: E402
from app.vision.service import VisionService  # noqa: E402
from tests._capture_test_utils import real_capture_image_path, to_crop_relative_bbox  # noqa: E402

MODULE = "app.outlook.find_email"
SCROLL_MODULE = "app.automation.scrolling"

# The two real rows from the live evidence.
DEMO_ROW_BBOX = (300.0, 200.0, 340.0, 480.0)
QUICK_QUESTION_ROW_BBOX = (378.0, 258.0, 456.0, 548.0)


def _steps(target_sender="Yash Dhanraj", target_subject="Quick Question Regarding Tomorrow") -> FindOpenEmailSteps:
    steps = FindOpenEmailSteps(
        AbortController(), VisionService(MagicMock(), fallback=None), "test-model",
        target_sender=target_sender, target_subject=target_subject,
    )
    steps.result.ready_for_interaction = True
    return steps


def _candidate(sender="Yash Dhanraj", subject="Quick Question Regarding...", subject_truncated=True,
               row_bbox=QUICK_QUESTION_ROW_BBOX, confidence=0.95, date_or_order="Today"):
    return {"sender": sender, "subject": subject, "subject_truncated": subject_truncated,
            "date_or_order": date_or_order, "row_bbox": to_crop_relative_bbox(list(row_bbox)), "confidence": confidence}


def _search_call(candidates, target_visible=True):
    return MagicMock(
        parsed_json={
            "outlook_visible": True, "message_list_visible": True, "target_visible": target_visible,
            "candidate_count": len(candidates), "candidates": candidates,
            "more_content_below": False, "reason": "ok",
        },
        raw_text="{}", model="test-model", latency_ms=100.0, input_tokens=10, output_tokens=5,
    )


def _identity_refine_call(grounded_sender="Yash Dhanraj", grounded_subject="Quick Question Regarding Tomorrow",
                           row_bbox=QUICK_QUESTION_ROW_BBOX, confidence=0.92):
    return MagicMock(
        parsed_json={
            "grounded_sender": grounded_sender, "grounded_subject": grounded_subject,
            "row_bbox": to_crop_relative_bbox(list(row_bbox)) if row_bbox is not None else None,
            "confidence": confidence, "reason": "ok",
        },
        raw_text="{}", model="test-model", latency_ms=80.0, input_tokens=10, output_tokens=5,
    )


def _capture(width=1920, height=1080, filename="inbox.png"):
    return MagicMock(filename=filename, path=real_capture_image_path(width, height, name=filename), width=width, height=height)


def _run_find(steps, patches=None):
    patches = patches or {}
    with patch(f"{MODULE}.get_foreground_window_title", return_value=patches.get("title", "Mail - Outlook")), \
         patch(f"{MODULE}.capture_screen", return_value=patches.get("capture", _capture())), \
         patch(f"{MODULE}.time.sleep"), \
         patch(f"{SCROLL_MODULE}.pyautogui") as mock_scroll_pg:
        mock_scroll_pg.FAILSAFE = True
        result = steps.find_target_email()
    return result, mock_scroll_pg


# --- A: two visible same-sender rows -> bbox must end up on the
# Quick Question row, not the Demo row ---

def test_A_bbox_corrected_to_the_matching_subject_row():
    steps = _steps()
    candidates = [
        _candidate(subject="Demo of live run", subject_truncated=False, row_bbox=DEMO_ROW_BBOX,
                   date_or_order="Fri 1:15 PM"),
        # Live bug reproduction: the accepted candidate's own reported
        # bbox is (mistakenly) the WRONG row's — identity refinement
        # must correct it.
        _candidate(subject="Quick Question Regarding...", subject_truncated=True, row_bbox=DEMO_ROW_BBOX),
    ]
    steps.vision.primary.analyze_screen.side_effect = [
        _search_call(candidates),
        _identity_refine_call(row_bbox=QUICK_QUESTION_ROW_BBOX),
    ]
    ok, _ = _run_find(steps)
    assert ok is True
    assert steps.result.row_identity_refinement_succeeded is True
    assert steps.result.email_grounding_bbox_raw == pytest.approx(list(QUICK_QUESTION_ROW_BBOX))
    assert steps.result.email_grounding_bbox_raw != list(DEMO_ROW_BBOX)


# --- B: same sender, but identity refinement grounds a DIFFERENT
# subject -> rejected ---

def test_B_wrong_subject_grounded_by_refinement_is_rejected():
    steps = _steps()
    candidates = [
        _candidate(subject="Demo of live run", subject_truncated=False, row_bbox=DEMO_ROW_BBOX),
        _candidate(subject="Quick Question Regarding...", subject_truncated=True, row_bbox=QUICK_QUESTION_ROW_BBOX),
    ]
    steps.vision.primary.analyze_screen.side_effect = [
        _search_call(candidates),
        # Refinement grounds the SAME sender but a DIFFERENT subject —
        # must never be silently accepted.
        _identity_refine_call(grounded_subject="Demo of live run", row_bbox=DEMO_ROW_BBOX),
    ]
    ok, _ = _run_find(steps)
    assert ok is False
    assert steps.result.failure_reason == LaunchFailureReason.EMAIL_ROW_IDENTITY_UNCONFIRMED
    assert steps.result.email_converted_x is None


# --- C: exact sender + truncated target subject -> accepted provisionally ---

def test_C_truncated_subject_still_accepted_provisionally_after_refinement():
    steps = _steps()
    candidates = [_candidate(subject="Quick Question Regarding...", subject_truncated=True,
                              row_bbox=QUICK_QUESTION_ROW_BBOX)]
    steps.vision.primary.analyze_screen.side_effect = [
        _search_call(candidates),
        _identity_refine_call(row_bbox=QUICK_QUESTION_ROW_BBOX),
    ]
    ok, _ = _run_find(steps)
    assert ok is True
    assert steps.result.provisional_match is True
    assert steps.result.row_identity_refinement_succeeded is True


# --- D: same sender appears multiple times -> sender alone cannot
# determine the bbox; only sender+subject grounding is trusted ---

def test_D_multiple_same_sender_rows_always_trigger_refinement():
    steps = _steps(target_subject="")  # sender-only mode
    candidates = [
        _candidate(subject="Demo of live run", subject_truncated=False, row_bbox=DEMO_ROW_BBOX,
                   date_or_order="2026-09-01"),
        _candidate(subject="Quick Question Regarding Tomorrow", subject_truncated=False,
                   row_bbox=QUICK_QUESTION_ROW_BBOX, date_or_order="2026-09-02"),
    ]
    steps.vision.primary.analyze_screen.side_effect = [
        _search_call(candidates),
        _identity_refine_call(grounded_subject="Quick Question Regarding Tomorrow", row_bbox=QUICK_QUESTION_ROW_BBOX),
    ]
    ok, _ = _run_find(steps)
    # Sender-only mode resolves the newer of two exact (sender-only)
    # matches deterministically by date — but identity refinement still
    # ran (two same-sender rows were visible) before trusting either bbox.
    assert ok is True
    assert steps.result.row_identity_refinement_attempted is True


# --- E: identity refinement bounded to max one call ---

def test_E_identity_refinement_called_at_most_once():
    steps = _steps()
    candidates = [
        _candidate(subject="Demo of live run", subject_truncated=False, row_bbox=DEMO_ROW_BBOX),
        _candidate(subject="Quick Question Regarding...", subject_truncated=True, row_bbox=QUICK_QUESTION_ROW_BBOX),
    ]
    steps.vision.primary.analyze_screen.side_effect = [
        _search_call(candidates),
        _identity_refine_call(row_bbox=QUICK_QUESTION_ROW_BBOX),
    ]
    _run_find(steps)
    identity_calls = [
        c for c in steps.vision.primary.analyze_screen.call_args_list
    ]
    # Exactly 2 Vision calls total for this run: the search + ONE identity refinement.
    assert len(identity_calls) == 2


# --- F: identity refinement uses the SAME screenshot as the search ---

def test_F_identity_refinement_reuses_the_same_screenshot():
    steps = _steps()
    candidates = [_candidate(subject="Quick Question Regarding...", subject_truncated=True,
                              row_bbox=QUICK_QUESTION_ROW_BBOX)]
    steps.vision.primary.analyze_screen.side_effect = [
        _search_call(candidates),
        _identity_refine_call(row_bbox=QUICK_QUESTION_ROW_BBOX),
    ]
    with patch(f"{MODULE}.get_foreground_window_title", return_value="Mail - Outlook"), \
         patch(f"{MODULE}.capture_screen", return_value=_capture(filename="same_shot.png")) as mock_capture, \
         patch(f"{MODULE}.time.sleep"), \
         patch(f"{SCROLL_MODULE}.pyautogui"):
        steps.find_target_email()
    assert mock_capture.call_count == 1
    calls = steps.vision.primary.analyze_screen.call_args_list
    # Both calls now receive the message-list CROP built from the
    # capture (never the raw screenshot) — the crop is cached per source
    # screenshot path, so "same screenshot reused" now proves out as
    # "the identical crop object/path was reused for both calls".
    assert calls[0].args[0] == calls[1].args[0]
    assert calls[0].args[0].name == "same_shot_message_list_crop.png"


# --- G: no physical action before identity refinement completes ---

def test_G_no_physical_action_before_identity_refinement_completes():
    steps = _steps()
    candidates = [_candidate(subject="Quick Question Regarding...", subject_truncated=True,
                              row_bbox=QUICK_QUESTION_ROW_BBOX)]
    steps.vision.primary.analyze_screen.side_effect = [
        _search_call(candidates),
        _identity_refine_call(row_bbox=QUICK_QUESTION_ROW_BBOX),
    ]
    with patch(f"{MODULE}.get_foreground_window_title", return_value="Mail - Outlook"), \
         patch(f"{MODULE}.capture_screen", return_value=_capture()), \
         patch(f"{MODULE}.time.sleep"), \
         patch(f"{SCROLL_MODULE}.pyautogui"), \
         patch(f"{MODULE}.pyautogui") as mock_pyautogui:
        mock_pyautogui.FAILSAFE = True
        ok = steps.find_target_email()
        assert ok is True
        mock_pyautogui.moveTo.assert_not_called()
        mock_pyautogui.click.assert_not_called()


# --- H: identity refinement failure (declines / no bbox) -> zero click ---

def test_H_identity_refinement_declines_zero_click():
    steps = _steps()
    candidates = [
        _candidate(subject="Demo of live run", subject_truncated=False, row_bbox=DEMO_ROW_BBOX),
        _candidate(subject="Quick Question Regarding...", subject_truncated=True, row_bbox=QUICK_QUESTION_ROW_BBOX),
    ]
    steps.vision.primary.analyze_screen.side_effect = [
        _search_call(candidates),
        _identity_refine_call(grounded_sender="", grounded_subject="", row_bbox=None, confidence=0.3),
    ]
    ok, _ = _run_find(steps)
    assert ok is False
    assert steps.result.failure_reason == LaunchFailureReason.EMAIL_ROW_IDENTITY_UNCONFIRMED
    assert steps.result.email_converted_x is None

    with patch(f"{MODULE}.pyautogui") as mock_pyautogui, \
         patch(f"{MODULE}.get_foreground_window_title", return_value="Mail - Outlook"):
        mock_pyautogui.FAILSAFE = True
        # email_converted_x/y were never set — nothing to click.
        assert steps.result.email_converted_x is None


# --- I: successful identity refinement -> deterministic safe X +
# refined row-center Y -> exactly one click ---

def test_I_successful_refinement_yields_safe_x_and_row_y_one_click():
    steps = _steps()
    candidates = [
        _candidate(subject="Demo of live run", subject_truncated=False, row_bbox=DEMO_ROW_BBOX),
        _candidate(subject="Quick Question Regarding...", subject_truncated=True, row_bbox=DEMO_ROW_BBOX),
    ]
    steps.vision.primary.analyze_screen.side_effect = [
        _search_call(candidates),
        _identity_refine_call(row_bbox=QUICK_QUESTION_ROW_BBOX),
    ]
    ok, _ = _run_find(steps)
    assert ok is True

    y_min, x_min, y_max, x_max = QUICK_QUESTION_ROW_BBOX
    assert steps.result.email_converted_y == round(((y_min + y_max) / 2) / 1000 * 1080)

    with patch(f"{MODULE}.pyautogui") as mock_pyautogui, \
         patch(f"{MODULE}.get_foreground_window_title", return_value="Mail - Outlook"), \
         patch(f"{MODULE}.capture_screen", return_value=_capture()):
        mock_pyautogui.FAILSAFE = True
        assert steps.click_target_email() is True
        mock_pyautogui.click.assert_called_once_with()
        mock_pyautogui.moveTo.assert_called_once()
    assert steps.result.email_click_count == 1


# --- J: wrong email opened post-click (identity refinement not
# triggered/insufficient) -> safe stop, no second click ---

def test_J_wrong_email_opened_post_click_is_a_safe_stop_no_second_click():
    steps = _steps()
    steps.result.email_click_count = 1  # simulating click_target_email() already ran
    wrong_email = MagicMock(
        parsed_json={"email_open": True, "subject_detected": "Demo of live run", "sender_detected": "Yash Dhanraj",
                     "subject_match": False, "sender_match": True, "body_visible": True, "confidence": 0.95,
                     "reason": "wrong email"},
        raw_text="{}", model="test-model", latency_ms=50.0, input_tokens=5, output_tokens=5,
    )
    steps.vision.primary.analyze_screen.return_value = wrong_email
    with patch(f"{MODULE}.time.sleep"), patch(f"{MODULE}.capture_screen", return_value=_capture()), \
         patch(f"{MODULE}.pyautogui") as mock_pyautogui:
        mock_pyautogui.FAILSAFE = True
        assert steps.verify_email_opened() is False
    assert steps.result.failure_reason == LaunchFailureReason.WRONG_EMAIL_OPENED
    assert steps.result.email_click_count == 1
    mock_pyautogui.click.assert_not_called()
    mock_pyautogui.moveTo.assert_not_called()


# --- K: provider-neutral implementation ---

def test_K_no_provider_name_branch_in_identity_refinement_logic():
    import inspect

    from app.outlook import find_email as fe_module

    source = inspect.getsource(fe_module.FindOpenEmailSteps._refine_row_identity)
    source += inspect.getsource(fe_module.FindOpenEmailSteps.find_target_email)
    for literal in ('"gemini"', "'gemini'", '"claude"', "'claude'", '"anthropic"', "'anthropic'"):
        assert literal not in source


def test_K_technical_failure_during_identity_refinement_uses_existing_policy():
    steps = _steps()
    candidates = [
        _candidate(subject="Demo of live run", subject_truncated=False, row_bbox=DEMO_ROW_BBOX),
        _candidate(subject="Quick Question Regarding...", subject_truncated=True, row_bbox=QUICK_QUESTION_ROW_BBOX),
    ]
    steps.vision.primary.analyze_screen.side_effect = [
        _search_call(candidates), RateLimitError("HTTP 429"), RateLimitError("HTTP 429"),
    ]
    ok, _ = _run_find(steps)
    assert ok is False
    assert steps.result.result == "ERROR"
    assert steps.result.failure_reason == LaunchFailureReason.TECHNICAL_PROVIDER_ERROR
    assert steps.result.email_converted_x is None
