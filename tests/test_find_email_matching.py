"""app/outlook/find_email.py candidate matching/selection tests (Phase 8
Attempt-3 follow-up). Covers ONLY sender+subject matching/eligibility —
post-click verification, bbox validation, scrolling architecture,
click-once behavior, and provider retry behavior are untouched and
already covered by tests/test_find_open_email_worker.py.

Root cause under test: sender matching previously used bidirectional
substring containment ("yash" in "yash dhanraj" -> True), so a live run
against a real mailbox containing both a sender literally named "Yash"
and a DIFFERENT sender "Yash Dhanraj" clicked the wrong one. Matching
is now normalized EXACT equality for both sender and (when supplied)
subject — never substring/prefix, never fuzzy.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.outlook.find_email import FindOpenEmailSteps  # noqa: E402
from app.playbook.failure_reasons import LaunchFailureReason  # noqa: E402
from app.safety.abort_controller import AbortController  # noqa: E402
from app.vision.service import VisionService  # noqa: E402
from tests._capture_test_utils import real_capture_image_path, to_crop_relative_bbox  # noqa: E402

MODULE = "app.outlook.find_email"


def _steps(target_sender: str = "Yash", target_subject: str = "Mail for project") -> FindOpenEmailSteps:
    steps = FindOpenEmailSteps(
        AbortController(), VisionService(MagicMock(), fallback=None), "gemini-3.6-flash",
        target_sender=target_sender, target_subject=target_subject,
    )
    steps.result.ready_for_interaction = True
    return steps


def _candidate(sender: str, subject: str, bbox=(400.0, 100.0, 440.0, 480.0), confidence=0.9, date="Today",
                subject_truncated: bool = False):
    return {"sender": sender, "subject": subject, "subject_truncated": subject_truncated, "date_or_order": date,
            "row_bbox": to_crop_relative_bbox(list(bbox)), "confidence": confidence}


def _verify_call(subject_detected: str, sender_detected: str, subject_match: bool = True, sender_match: bool = True,
                  email_open: bool = True, body_visible: bool = True):
    """Post-open verification response. subject_match/sender_match are
    Vision's OWN (now non-authoritative) judgment — deliberately set
    independent of subject_detected/sender_detected in some tests below,
    to prove the deterministic Python-side recheck is what actually
    gates PASS/WRONG_EMAIL_OPENED, not these two fields."""
    return MagicMock(
        parsed_json={"email_open": email_open, "subject_detected": subject_detected, "sender_detected": sender_detected,
                     "subject_match": subject_match, "sender_match": sender_match, "body_visible": body_visible,
                     "confidence": 0.95, "reason": "ok"},
        raw_text="{}", model="gemini-3.6-flash", latency_ms=80.0, input_tokens=10, output_tokens=5,
    )


def _search_call(candidates: list[dict], target_visible: bool = True):
    return MagicMock(
        parsed_json={
            "outlook_visible": True, "message_list_visible": True, "target_visible": target_visible,
            "candidate_count": len(candidates), "candidates": candidates,
            "more_content_below": False, "reason": "ok",
        },
        raw_text="{}", model="gemini-3.6-flash", latency_ms=80.0, input_tokens=10, output_tokens=5,
    )


def _capture(width=1920, height=1080, filename="inbox.png"):
    return MagicMock(filename=filename, path=real_capture_image_path(width, height), width=width, height=height)


def _identity_refine_call(sender, subject, row_bbox, confidence=0.95):
    return MagicMock(
        parsed_json={"grounded_sender": sender, "grounded_subject": subject,
                     "row_bbox": to_crop_relative_bbox(list(row_bbox)), "confidence": confidence, "reason": "ok"},
        raw_text="{}", model="gemini-3.6-flash", latency_ms=80.0, input_tokens=10, output_tokens=5,
    )


def _run(steps: FindOpenEmailSteps, call, extra_calls=None):
    """Runs find_target_email() with every external dependency mocked.
    Returns (result_bool, mock_pyautogui) so callers can assert zero
    physical clicks on a rejected/ineligible outcome — find_target_email()
    itself never clicks (that's the separate click_target_email() method),
    so this also structurally proves matching alone gates the click.

    extra_calls, if given, is appended after `call` as a side_effect
    sequence — for scenarios (multiple same-sender rows, a provisional
    match, low confidence) that make the 2026-09-06 identity-row
    refinement eligible and need their own mocked response."""
    with patch(f"{MODULE}.get_foreground_window_title", return_value="Inbox - Outlook"), \
         patch(f"{MODULE}.capture_screen", return_value=_capture()), \
         patch(f"{MODULE}.scroll_message_list") as mock_scroll, \
         patch(f"{MODULE}.time.sleep"), \
         patch(f"{MODULE}.pyautogui") as mock_pyautogui:
        mock_pyautogui.FAILSAFE = True
        if extra_calls:
            steps.vision.primary.analyze_screen.side_effect = [call, *extra_calls]
        else:
            steps.vision.primary.analyze_screen.return_value = call
        result = steps.find_target_email()
    return result, mock_pyautogui, mock_scroll


# --- A: exact target among distractor "Yash"-prefixed senders ---

def test_only_exact_sender_and_subject_candidate_is_eligible_and_found():
    steps = _steps()
    candidates = [
        _candidate("Yash", "Testing the poc"),
        _candidate("Yash Dhanraj", "Important Update Regarding Current Work and Next Steps"),
        _candidate("Yash", "Mail for project"),
    ]
    result, mock_pyautogui, _ = _run(
        steps, _search_call(candidates),
        extra_calls=[_identity_refine_call("Yash", "Mail for project", (400.0, 100.0, 440.0, 480.0))],
    )
    assert result is True
    mock_pyautogui.click.assert_not_called()  # find_target_email() itself never clicks
    assert steps.result.email_candidate_count == 1
    log = steps.result.candidate_match_log
    assert len(log) == 3
    assert [e.eligible for e in log] == [False, False, True]
    assert log[0].rejection_reason == "subject_mismatch"   # "Yash" sender ok, wrong subject
    assert log[1].rejection_reason == "sender_mismatch"    # "Yash Dhanraj" != "Yash"
    assert log[2].eligible is True and log[2].rejection_reason is None


# --- B: sender matches, subject doesn't -> zero click ---

def test_sender_match_subject_mismatch_yields_zero_click():
    steps = _steps()
    candidates = [_candidate("Yash", "Testing the poc")]
    result, mock_pyautogui, _ = _run(steps, _search_call(candidates, target_visible=False))
    assert result is False
    assert steps.result.failure_reason == LaunchFailureReason.TARGET_EMAIL_NOT_FOUND
    mock_pyautogui.click.assert_not_called()
    assert steps.result.candidate_match_log[0].sender_match is True
    assert steps.result.candidate_match_log[0].subject_match is False
    assert steps.result.candidate_match_log[0].eligible is False


# --- C: subject matches, sender doesn't -> zero click ---

def test_subject_match_sender_mismatch_yields_zero_click():
    steps = _steps()
    candidates = [_candidate("Yash Dhanraj", "Mail for project")]
    result, mock_pyautogui, _ = _run(steps, _search_call(candidates, target_visible=False))
    assert result is False
    assert steps.result.failure_reason == LaunchFailureReason.TARGET_EMAIL_NOT_FOUND
    mock_pyautogui.click.assert_not_called()
    entry = steps.result.candidate_match_log[0]
    assert entry.sender_match is False
    assert entry.subject_match is True
    assert entry.eligible is False
    assert entry.rejection_reason == "sender_mismatch"


# --- D: multiple sender matches, exactly one sender+subject match wins ---

def test_multiple_sender_matches_exact_pair_wins():
    steps = _steps()
    candidates = [
        _candidate("Yash", "Testing the poc"),
        _candidate("Yash", "Another unrelated subject"),
        _candidate("Yash", "Mail for project"),
    ]
    result, mock_pyautogui, _ = _run(
        steps, _search_call(candidates),
        extra_calls=[_identity_refine_call("Yash", "Mail for project", (400.0, 100.0, 440.0, 480.0))],
    )
    assert result is True
    assert steps.result.email_candidate_count == 1
    mock_pyautogui.click.assert_not_called()


# --- E: target_subject provided, sender matches everywhere, none match subject -> no sender-only fallback ---

def test_no_sender_only_fallback_when_subject_provided_and_unmatched():
    steps = _steps()
    candidates = [
        _candidate("Yash", "Testing the poc"),
        _candidate("Yash", "Another unrelated subject"),
    ]
    result, mock_pyautogui, mock_scroll = _run(steps, _search_call(candidates, target_visible=False))
    assert result is False
    assert steps.result.failure_reason == LaunchFailureReason.TARGET_EMAIL_NOT_FOUND
    assert steps.result.email_candidate_count == 0
    mock_pyautogui.click.assert_not_called()
    assert mock_scroll.call_count > 0  # bounded scroll-and-reobserve, never a click on a sender-only match


# --- F: target_subject empty -> existing sender-only behavior unchanged ---

def test_empty_target_subject_keeps_sender_only_behavior():
    steps = _steps(target_subject="")
    candidates = [_candidate("Yash", "Whatever subject is on this one")]
    result, mock_pyautogui, _ = _run(steps, _search_call(candidates))
    assert result is True
    mock_pyautogui.click.assert_not_called()
    entry = steps.result.candidate_match_log[0]
    assert entry.sender_match is True
    assert entry.subject_match is None  # not applicable
    assert entry.eligible is True


# --- G: truncated/partially-visible subject is never guessed-complete -> zero click ---

def test_truncated_subject_is_not_treated_as_a_match():
    steps = _steps()
    candidates = [_candidate("Yash", "Mail for pr...")]
    result, mock_pyautogui, _ = _run(steps, _search_call(candidates, target_visible=False))
    assert result is False
    assert steps.result.failure_reason == LaunchFailureReason.TARGET_EMAIL_NOT_FOUND
    mock_pyautogui.click.assert_not_called()
    entry = steps.result.candidate_match_log[0]
    assert entry.sender_match is True
    assert entry.subject_match is False
    assert entry.eligible is False
    assert entry.rejection_reason == "subject_mismatch"


# --- H: post-click verification remains an independent, unchanged safety net ---

def test_post_click_verification_still_catches_mismatch_after_a_valid_match():
    """Even though matching is now strict, verify_email_opened() is a
    SEPARATE, untouched safety net — if a genuinely eligible candidate is
    clicked but the wrong email somehow ends up open (e.g. a stale click
    target after the list scrolled), verification must still catch it.
    Proves find_target_email()'s tightened matching did not remove or
    weaken this independent check."""
    steps = _steps()
    steps.result.ready_for_interaction = True

    candidates = [_candidate("Yash", "Mail for project")]
    result, _, _ = _run(steps, _search_call(candidates))
    assert result is True  # matching + grounding validation succeeded

    verify_call = MagicMock(
        parsed_json={"email_open": True, "subject_detected": "Something else entirely",
                     "sender_detected": "Someone else", "subject_match": False, "sender_match": False,
                     "body_visible": True, "confidence": 0.95, "reason": "mismatch"},
        raw_text="{}", model="gemini-3.6-flash", latency_ms=80.0, input_tokens=10, output_tokens=5,
    )
    with patch(f"{MODULE}.get_foreground_window_title", return_value="Inbox - Outlook"), \
         patch(f"{MODULE}.capture_screen", return_value=_capture()), \
         patch(f"{MODULE}.time.sleep"):
        steps.vision.primary.analyze_screen.return_value = verify_call
        assert steps.verify_email_opened() is False
    assert steps.result.failure_reason == LaunchFailureReason.WRONG_EMAIL_OPENED


# ==================================================================
# Two-stage subject identity: full-subject vs explicitly-truncated
# subject (Outlook row-width truncation). See _evaluate_candidate()'s
# docstring in app/outlook/find_email.py for the full policy. Letters
# below match the task's own A-H test list.
# ==================================================================

TRUNCATION_TARGET_SUBJECT = "Important Update Regarding Current Work and Next Steps"
TRUNCATION_TARGET_SENDER = "Yash Dhanraj"


def _run_find_click_verify(steps: FindOpenEmailSteps, search_call, verify_call, identity_call=None):
    """find_target_email() -> click_target_email() -> verify_email_opened(),
    all mocked, in one call — for the provisional-match tests, which need
    the FULL flow (a provisional match is only ever confirmed post-open).
    verify_call is supplied for every bounded verification attempt (up to
    MAX_EMAIL_OPEN_VERIFICATION_ATTEMPTS) — on a genuine mismatch, both
    attempts see the same (still-mismatched) detected text, exactly as a
    real unchanged-but-wrong open email would.

    identity_call: a provisional (truncated-subject) match always makes
    the 2026-09-06 identity-row refinement eligible — every caller of
    this helper passes one, inserted right after search_call."""
    with patch(f"{MODULE}.get_foreground_window_title", return_value="Inbox - Outlook"), \
         patch(f"{MODULE}.capture_screen", return_value=_capture()), \
         patch(f"{MODULE}.time.sleep"), \
         patch(f"{MODULE}.pyautogui") as mock_pyautogui:
        mock_pyautogui.FAILSAFE = True
        calls = [search_call]
        if identity_call is not None:
            calls.append(identity_call)
        calls.extend([verify_call, verify_call])
        steps.vision.primary.analyze_screen.side_effect = calls
        found = steps.find_target_email()
        if not found:
            return found, False, False, mock_pyautogui
        clicked = steps.click_target_email()
        verified = clicked and steps.verify_email_opened()
        return found, clicked, verified, mock_pyautogui


# --- A: unique truncated-prefix candidate -> provisional -> clicked once -> confirmed ---

def test_A_truncated_prefix_provisional_match_confirmed_after_open():
    steps = _steps(target_sender=TRUNCATION_TARGET_SENDER, target_subject=TRUNCATION_TARGET_SUBJECT)
    candidates = [_candidate(TRUNCATION_TARGET_SENDER, "Important Update Regardi...", subject_truncated=True)]
    search_call = _search_call(candidates)
    verify_call = _verify_call(TRUNCATION_TARGET_SUBJECT, TRUNCATION_TARGET_SENDER)
    identity_call = _identity_refine_call(
        TRUNCATION_TARGET_SENDER, "Important Update Regardi...", (400.0, 100.0, 440.0, 480.0),
    )

    found, clicked, verified, mock_pyautogui = _run_find_click_verify(steps, search_call, verify_call, identity_call)

    assert found is True
    assert steps.result.provisional_match is True
    assert steps.result.candidate_match_log[-1].match_type == "provisional"
    assert clicked is True
    assert mock_pyautogui.click.call_count == 1  # clicked exactly once
    assert verified is True
    assert steps.result.result == "PASS"


# --- B: same visible text but subject_truncated=False -> reject partial match ---

def test_B_untruncated_partial_subject_is_rejected():
    steps = _steps(target_sender=TRUNCATION_TARGET_SENDER, target_subject=TRUNCATION_TARGET_SUBJECT)
    candidates = [_candidate(TRUNCATION_TARGET_SENDER, "Important Update Regardi...", subject_truncated=False)]
    result, mock_pyautogui, _ = _run(steps, _search_call(candidates, target_visible=False))

    assert result is False
    assert steps.result.failure_reason == LaunchFailureReason.TARGET_EMAIL_NOT_FOUND
    mock_pyautogui.click.assert_not_called()
    entry = steps.result.candidate_match_log[0]
    assert entry.subject_truncated is False
    assert entry.eligible is False
    assert entry.match_type is None
    assert entry.rejection_reason == "subject_mismatch"


# --- C: two rows, both truncated, both compatible prefixes -> ambiguity, zero click ---

def test_C_multiple_provisional_matches_is_a_safe_stop():
    steps = _steps(target_sender=TRUNCATION_TARGET_SENDER, target_subject=TRUNCATION_TARGET_SUBJECT)
    candidates = [
        _candidate(TRUNCATION_TARGET_SENDER, "Important Update Regardi...", subject_truncated=True,
                   bbox=(200.0, 100.0, 240.0, 900.0)),
        _candidate(TRUNCATION_TARGET_SENDER, "Important Update...", subject_truncated=True,
                   bbox=(400.0, 100.0, 440.0, 900.0)),
    ]
    result, mock_pyautogui, _ = _run(steps, _search_call(candidates))

    assert result is False
    assert steps.result.failure_reason == LaunchFailureReason.MULTIPLE_TARGET_EMAILS_FOUND
    mock_pyautogui.click.assert_not_called()
    assert steps.result.email_candidate_count == 2


# --- D: provisional candidate clicked, opened full subject differs -> WRONG_EMAIL_OPENED ---

def test_D_provisional_match_rejected_after_open_on_full_subject_mismatch():
    steps = _steps(target_sender=TRUNCATION_TARGET_SENDER, target_subject=TRUNCATION_TARGET_SUBJECT)
    candidates = [_candidate(TRUNCATION_TARGET_SENDER, "Important Update Regardi...", subject_truncated=True)]
    search_call = _search_call(candidates)
    # Vision's OWN subject_match/sender_match say "matched" — but the
    # actual detected text differs from the target; the deterministic
    # Python recheck must be what decides, and must reject this.
    verify_call = _verify_call(
        "Important Update Regarding A Completely Different Topic", TRUNCATION_TARGET_SENDER,
        subject_match=True, sender_match=True,
    )
    identity_call = _identity_refine_call(
        TRUNCATION_TARGET_SENDER, "Important Update Regardi...", (400.0, 100.0, 440.0, 480.0),
    )

    found, clicked, verified, mock_pyautogui = _run_find_click_verify(steps, search_call, verify_call, identity_call)

    assert found is True
    assert clicked is True
    assert mock_pyautogui.click.call_count == 1  # clicked once — never re-clicked on mismatch
    assert verified is False
    assert steps.result.failure_reason == LaunchFailureReason.WRONG_EMAIL_OPENED
    assert steps.result.result == "FAIL"
    # No Reply/draft/Send code path is reachable from this stage at all —
    # find_email.py has no such methods; structurally impossible here.


# --- E: full subject visible and exact -> existing exact-match behavior unchanged ---

def test_E_full_subject_exact_match_unchanged():
    steps = _steps(target_sender=TRUNCATION_TARGET_SENDER, target_subject=TRUNCATION_TARGET_SUBJECT)
    candidates = [_candidate(TRUNCATION_TARGET_SENDER, TRUNCATION_TARGET_SUBJECT, subject_truncated=False)]
    result, mock_pyautogui, _ = _run(steps, _search_call(candidates))

    assert result is True
    assert steps.result.provisional_match is False
    assert steps.result.candidate_match_log[0].match_type == "exact"
    mock_pyautogui.click.assert_not_called()  # find_target_email() itself never clicks


# --- F: full subject visible but different -> reject as before ---

def test_F_full_subject_different_is_rejected():
    steps = _steps(target_sender=TRUNCATION_TARGET_SENDER, target_subject=TRUNCATION_TARGET_SUBJECT)
    candidates = [_candidate(TRUNCATION_TARGET_SENDER, "A totally unrelated subject line", subject_truncated=False)]
    result, mock_pyautogui, _ = _run(steps, _search_call(candidates, target_visible=False))

    assert result is False
    assert steps.result.failure_reason == LaunchFailureReason.TARGET_EMAIL_NOT_FOUND
    mock_pyautogui.click.assert_not_called()
    assert steps.result.candidate_match_log[0].match_type is None


# --- G: truncated visible subject is NOT a prefix of the target -> reject ---

def test_G_truncated_subject_not_a_prefix_is_rejected():
    steps = _steps(target_sender=TRUNCATION_TARGET_SENDER, target_subject=TRUNCATION_TARGET_SUBJECT)
    candidates = [_candidate(TRUNCATION_TARGET_SENDER, "Something else entirely...", subject_truncated=True)]
    result, mock_pyautogui, _ = _run(steps, _search_call(candidates, target_visible=False))

    assert result is False
    assert steps.result.failure_reason == LaunchFailureReason.TARGET_EMAIL_NOT_FOUND
    mock_pyautogui.click.assert_not_called()
    entry = steps.result.candidate_match_log[0]
    assert entry.subject_truncated is True
    assert entry.eligible is False
    assert entry.match_type is None
    assert entry.rejection_reason == "subject_mismatch"


# --- H: case/whitespace differences only -> follow existing normalization policy ---

def test_H_case_and_whitespace_differences_still_match():
    # Exact-subject case: extra internal whitespace + different case.
    steps_exact = _steps(target_sender=TRUNCATION_TARGET_SENDER, target_subject=TRUNCATION_TARGET_SUBJECT)
    candidates_exact = [_candidate(
        "  yash dhanraj  ", "important   update regarding current work and next steps",
        subject_truncated=False,
    )]
    result_exact, _, _ = _run(steps_exact, _search_call(candidates_exact))
    assert result_exact is True
    assert steps_exact.result.candidate_match_log[0].match_type == "exact"

    # Truncated-prefix case: same normalization tolerance applies to the
    # prefix comparison too.
    steps_prefix = _steps(target_sender=TRUNCATION_TARGET_SENDER, target_subject=TRUNCATION_TARGET_SUBJECT)
    candidates_prefix = [_candidate(
        "YASH DHANRAJ", "Important   Update  Regardi...", subject_truncated=True,
    )]
    result_prefix, _, _ = _run(
        steps_prefix, _search_call(candidates_prefix),
        extra_calls=[_identity_refine_call(
            "YASH DHANRAJ", "Important   Update  Regardi...", (400.0, 100.0, 440.0, 480.0),
        )],
    )
    assert result_prefix is True
    assert steps_prefix.result.candidate_match_log[0].match_type == "provisional"
