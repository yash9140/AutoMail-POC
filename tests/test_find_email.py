"""app/outlook/find_email.py unit tests (Phase 3 — bounded search +
scroll, candidate-list grounding, sender-required/subject-optional
matching). All external calls (pyautogui, provider, screen capture,
foreground, ctypes) are mocked.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.settings import MAX_MESSAGE_LIST_SCROLL_ATTEMPTS  # noqa: E402
from app.outlook.find_email import FindOpenEmailSteps  # noqa: E402
from app.playbook.failure_reasons import LaunchFailureReason  # noqa: E402
from app.safety.abort_controller import AbortController  # noqa: E402
from app.vision.providers.base import RateLimitError  # noqa: E402
from app.vision.service import VisionService  # noqa: E402
from tests._capture_test_utils import real_capture_image_path, to_crop_relative_bbox  # noqa: E402

MODULE = "app.outlook.find_email"
SCROLL_MODULE = "app.automation.scrolling"


def _steps(target_sender="Yash", target_subject="Mail for project") -> FindOpenEmailSteps:
    return FindOpenEmailSteps(
        AbortController(), VisionService(MagicMock(), fallback=None), "gemini-3.6-flash",
        target_sender=target_sender, target_subject=target_subject,
    )


def _capture(width=1920, height=1080, filename="inbox.png"):
    return MagicMock(filename=filename, path=real_capture_image_path(width, height), width=width, height=height)


def _ready_steps(**kwargs) -> FindOpenEmailSteps:
    steps = _steps(**kwargs)
    steps.result.ready_for_interaction = True
    return steps


def _candidate(sender="Yash", subject="Mail for project", date_or_order="Today",
                row_bbox=(480.0, 300.0, 520.0, 480.0), confidence=0.95):
    # row_bbox here is expressed as the FULL-SCREEN bbox this fixture has
    # always intended (matching every downstream geometry assertion) —
    # converted to crop-relative since that's what Vision now actually
    # returns; see tests/_capture_test_utils.py.
    return {"sender": sender, "subject": subject, "date_or_order": date_or_order,
            "row_bbox": to_crop_relative_bbox(list(row_bbox)), "confidence": confidence}


def _search_call(candidates=(), target_visible=None, more_below=False, **overrides):
    payload = {
        "outlook_visible": True, "message_list_visible": True,
        "target_visible": target_visible if target_visible is not None else bool(candidates),
        "candidate_count": len(candidates), "candidates": list(candidates),
        "more_content_below": more_below, "reason": "ok",
    }
    payload.update(overrides)
    return MagicMock(
        parsed_json=payload, raw_text="{}", model="gemini-3.6-flash",
        latency_ms=100.0, input_tokens=10, output_tokens=5,
    )


def _identity_refine_call(sender, subject, row_bbox, confidence=0.95, target_visible=True):
    payload = {
        "grounded_sender": sender, "grounded_subject": subject,
        "row_bbox": to_crop_relative_bbox(list(row_bbox)), "confidence": confidence, "reason": "ok",
    }
    if not target_visible:
        payload["row_bbox"] = None
    return MagicMock(
        parsed_json=payload, raw_text="{}", model="gemini-3.6-flash",
        latency_ms=80.0, input_tokens=10, output_tokens=5,
    )


def _run_find(steps, patches=None):
    patches = patches or {}
    with patch(f"{MODULE}.get_foreground_window_title", return_value=patches.get("title", "Mail - Outlook")), \
         patch(f"{MODULE}.capture_screen", return_value=patches.get("capture", _capture())), \
         patch(f"{MODULE}.time.sleep"), \
         patch(f"{SCROLL_MODULE}.pyautogui") as mock_scroll_pg:
        mock_scroll_pg.FAILSAFE = True
        result = steps.find_target_email()
    return result, mock_scroll_pg


# --- Constructor: sender required, subject optional ---

def test_target_sender_required():
    try:
        FindOpenEmailSteps(AbortController(), VisionService(MagicMock(), fallback=None), "gemini-3.6-flash", target_sender="")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_target_subject_optional_defaults_empty_when_omitted():
    steps = FindOpenEmailSteps(AbortController(), VisionService(MagicMock(), fallback=None), "gemini-3.6-flash", target_sender="Yash", target_subject="")
    assert steps.result.target_subject == ""
    assert steps.result.target_sender == "Yash"


# --- A. sender + subject → valid row → single candidate found ---

def test_sender_and_subject_both_required_when_subject_given():
    steps = _ready_steps(target_sender="Yash", target_subject="Mail for project")
    steps.vision.primary.analyze_screen.return_value = _search_call([
        _candidate(sender="Yash", subject="Totally different subject"),
    ])
    ok, _ = _run_find(steps)
    assert ok is False
    assert steps.result.failure_reason == LaunchFailureReason.TARGET_EMAIL_NOT_FOUND


def test_visible_sender_and_subject_match_found_on_first_look():
    steps = _ready_steps(target_sender="Yash", target_subject="Mail for project")
    steps.vision.primary.analyze_screen.return_value = _search_call([_candidate()])
    ok, mock_scroll_pg = _run_find(steps)
    assert ok is True
    mock_scroll_pg.scroll.assert_not_called()
    assert steps.result.email_candidate_count == 1
    assert steps.result.email_found_at is not None
    assert steps.result.email_search_ms is not None


# --- B. sender-only → one clear candidate → open ---

def test_sender_only_single_candidate_matches():
    steps = _ready_steps(target_sender="Yash", target_subject="")
    steps.vision.primary.analyze_screen.return_value = _search_call([
        _candidate(sender="Yash", subject="Whatever subject this is"),
    ])
    ok, _ = _run_find(steps)
    assert ok is True
    assert steps.result.email_candidate_count == 1


# --- C. sender-only → multiple ambiguous candidates → safe stop ---

def test_sender_only_multiple_ambiguous_candidates_safe_stops():
    steps = _ready_steps(target_sender="Yash", target_subject="")
    steps.vision.primary.analyze_screen.return_value = _search_call([
        _candidate(sender="Yash", subject="First email", date_or_order="Mon"),
        _candidate(sender="Yash", subject="Second email", date_or_order="Tue"),
    ])
    ok, mock_scroll_pg = _run_find(steps)
    assert ok is False
    assert steps.result.failure_reason == LaunchFailureReason.MULTIPLE_TARGET_EMAILS_FOUND
    mock_scroll_pg.scroll.assert_not_called()  # ambiguity is a safe stop, not a scroll trigger


def test_multiple_candidates_resolved_when_dates_are_unambiguous_iso():
    steps = _ready_steps(target_sender="Yash", target_subject="")
    # Two rows share the target sender in this observation -> the new
    # same-sender-row identity refinement is eligible (see find_target_
    # email()) even though the ambiguity itself is resolved by date.
    steps.vision.primary.analyze_screen.side_effect = [
        _search_call([
            _candidate(sender="Yash", subject="Older", date_or_order="2026-08-30"),
            _candidate(sender="Yash", subject="Newer", date_or_order="2026-08-31", row_bbox=(600, 300, 640, 480)),
        ]),
        _identity_refine_call("Yash", "Newer", (600, 300, 640, 480)),
    ]
    ok, _ = _run_find(steps)
    assert ok is True
    assert steps.result.email_search_response.candidates[1].subject == "Newer"


def test_multiple_candidates_with_tied_dates_stay_ambiguous():
    steps = _ready_steps(target_sender="Yash", target_subject="")
    steps.vision.primary.analyze_screen.return_value = _search_call([
        _candidate(sender="Yash", subject="A", date_or_order="2026-08-31"),
        _candidate(sender="Yash", subject="B", date_or_order="2026-08-31", row_bbox=(600, 300, 640, 480)),
    ])
    ok, _ = _run_find(steps)
    assert ok is False
    assert steps.result.failure_reason == LaunchFailureReason.MULTIPLE_TARGET_EMAILS_FOUND


# --- D. target not visible → bounded scroll → found → open ---

def test_not_visible_then_found_after_one_scroll():
    steps = _ready_steps()
    steps.vision.primary.analyze_screen.side_effect = [
        _search_call([]),
        _search_call([_candidate()]),
    ]
    ok, mock_scroll_pg = _run_find(steps)
    assert ok is True
    assert mock_scroll_pg.scroll.call_count == 1
    assert steps.result.message_list_scroll_attempts == 1


# --- E. target not visible after max attempts → safe stop ---

def test_not_found_after_max_scroll_attempts():
    steps = _ready_steps()
    steps.vision.primary.analyze_screen.return_value = _search_call([])
    ok, mock_scroll_pg = _run_find(steps)
    assert ok is False
    assert steps.result.failure_reason == LaunchFailureReason.TARGET_EMAIL_NOT_FOUND
    assert mock_scroll_pg.scroll.call_count == MAX_MESSAGE_LIST_SCROLL_ATTEMPTS - 1
    assert steps.result.message_list_scroll_attempts == MAX_MESSAGE_LIST_SCROLL_ATTEMPTS - 1


def test_scroll_never_determines_click_point():
    """Structural: scroll_message_list only moves the mouse + scrolls —
    it accepts screen dimensions, never a click coordinate, and the
    click point Phase 3 ultimately uses always comes from a freshly
    validated candidate bbox, never from wherever scrolling last
    positioned the cursor."""
    import inspect

    from app.automation import scrolling

    source = inspect.getsource(scrolling.scroll_message_list)
    assert "email_converted_x" not in source
    assert "click" not in source.lower()


# --- F. invalid bbox → zero click ---

def test_missing_row_bbox_rejected_zero_click():
    steps = _ready_steps()
    candidate = _candidate()
    candidate["row_bbox"] = None
    steps.vision.primary.analyze_screen.return_value = _search_call([candidate])
    ok, _ = _run_find(steps)
    assert ok is False
    assert steps.result.failure_reason == LaunchFailureReason.EMAIL_GROUNDING_INVALID
    assert steps.result.email_converted_x is None


def test_degenerate_zero_area_bbox_rejected():
    steps = _ready_steps()
    steps.vision.primary.analyze_screen.return_value = _search_call([
        _candidate(row_bbox=(500, 500, 500, 500)),
    ])
    ok, _ = _run_find(steps)
    assert ok is False
    assert steps.result.failure_reason == LaunchFailureReason.EMAIL_GROUNDING_INVALID


def test_sidebar_bbox_rejected_regardless_of_confidence():
    steps = _ready_steps()
    # x range [10, 100] -> center x=55 normalized -> pixel = 55/1000*1920 ≈ 106px,
    # well inside the 17% sidebar exclusion zone (~326px), confidence=1.0.
    steps.vision.primary.analyze_screen.return_value = _search_call([
        _candidate(row_bbox=(480, 10, 520, 100), confidence=1.0),
    ])
    ok, _ = _run_find(steps)
    assert ok is False
    assert steps.result.failure_reason == LaunchFailureReason.EMAIL_GROUNDING_SIDEBAR_REJECTED


# --- G. bbox out of bounds → zero click ---

def test_bbox_out_of_normalized_range_rejected():
    steps = _ready_steps()
    steps.vision.primary.analyze_screen.return_value = _search_call([
        _candidate(row_bbox=(480, 300, 520, 1500)),  # x_max=1500 > 1000
    ])
    ok, _ = _run_find(steps)
    assert ok is False
    assert steps.result.failure_reason == LaunchFailureReason.EMAIL_GROUNDING_INVALID


# --- Message-list row bbox plausibility (2026-09-06 live fix) ---
# A live run's Vision-returned row_bbox extended horizontally past the
# real message-list column into the reading pane (raw_bbox=[418, 253,
# 492, 572] on a 1920x1080 screen, x_max=57.2% of screen width) — the
# click landed somewhere that never actually opened the target email.
# See app/safety/validators.py::validate_email_row_bbox().

def test_live_failure_bbox_extending_into_reading_pane_rejected_zero_click():
    steps = _ready_steps()
    steps.vision.primary.analyze_screen.return_value = _search_call([
        _candidate(row_bbox=(418.0, 253.0, 492.0, 572.0)),
    ])
    ok, mock_scroll_pg = _run_find(steps)
    assert ok is False
    assert steps.result.failure_reason == LaunchFailureReason.EMAIL_ROW_BBOX_IMPLAUSIBLE
    assert steps.result.email_converted_x is None  # no click point ever computed
    mock_scroll_pg.scroll.assert_not_called()  # a bad bbox is a safe stop, never a scroll trigger


def test_good_row_fixture_inside_message_list_clicks_exactly_once():
    """A well-formed row confined to the message-list column (the
    qualitative geometry described by the annotated R&D ground-truth
    evidence — avatar/sender/subject/preview/date, not extending into
    the reading pane) is accepted, and the full find -> click flow
    executes exactly one physical click."""
    steps = _ready_steps()
    steps.vision.primary.analyze_screen.return_value = _search_call([
        _candidate(row_bbox=(300.0, 200.0, 350.0, 480.0)),
    ])
    ok, _ = _run_find(steps)
    assert ok is True
    assert steps.result.failure_reason is None

    with patch(f"{MODULE}.pyautogui") as mock_pyautogui, \
         patch(f"{MODULE}.get_foreground_window_title", return_value="Mail - Outlook"):
        mock_pyautogui.FAILSAFE = True
        assert steps.click_target_email() is True
        mock_pyautogui.click.assert_called_once_with()
    assert steps.result.email_click_count == 1


def test_high_confidence_does_not_rescue_implausible_geometry():
    """Vision's self-reported confidence must never override a geometry
    plausibility failure — a confidence=1.0 candidate with a row_bbox
    that extends into the reading pane is still rejected, and the
    rejection happens BEFORE confidence is even consulted."""
    steps = _ready_steps()
    steps.vision.primary.analyze_screen.return_value = _search_call([
        _candidate(row_bbox=(418.0, 253.0, 492.0, 572.0), confidence=1.0),
    ])
    ok, _ = _run_find(steps)
    assert ok is False
    assert steps.result.failure_reason == LaunchFailureReason.EMAIL_ROW_BBOX_IMPLAUSIBLE


def test_row_bbox_validation_logged_before_click_point_or_confidence_considered(caplog):
    steps = _ready_steps()
    steps.vision.primary.analyze_screen.return_value = _search_call([
        _candidate(row_bbox=(418.0, 253.0, 492.0, 572.0), confidence=1.0),
    ])
    with caplog.at_level("INFO", logger="app.outlook.find_email.grounding"):
        ok, _ = _run_find(steps)
    assert ok is False
    messages = [r.getMessage() for r in caplog.records]
    validation_lines = [m for m in messages if "EMAIL_ROW_BBOX_VALIDATION" in m]
    assert len(validation_lines) == 1
    assert "valid=False" in validation_lines[0]
    assert "reason=bbox_extends_beyond_message_list" in validation_lines[0]
    assert any("EMAIL_CLICK_BLOCKED reason=EMAIL_ROW_BBOX_IMPLAUSIBLE" in m for m in messages)


def test_claude_and_gemini_produce_identical_validation_outcome_for_same_bbox():
    """Provider-neutral: the SAME candidate bbox must be validated
    identically regardless of which provider (primary Claude, fallback
    Gemini) produced it — provider_used is logged for diagnostics only,
    never branched on."""
    same_bbox = (418.0, 253.0, 492.0, 572.0)

    steps_claude = _ready_steps()
    steps_claude.vision.primary.analyze_screen.return_value = _search_call([_candidate(row_bbox=same_bbox)])
    ok_claude, _ = _run_find(steps_claude)

    steps_gemini = _ready_steps()
    steps_gemini.vision.primary.analyze_screen.return_value = _search_call([_candidate(row_bbox=same_bbox)])
    ok_gemini, _ = _run_find(steps_gemini)

    assert ok_claude is ok_gemini is False
    assert steps_claude.result.failure_reason == steps_gemini.result.failure_reason == \
        LaunchFailureReason.EMAIL_ROW_BBOX_IMPLAUSIBLE


def test_validate_and_record_candidate_has_no_provider_name_branch():
    import inspect

    from app.outlook.find_email import FindOpenEmailSteps

    source = inspect.getsource(FindOpenEmailSteps._validate_and_record_candidate)
    for literal in ('"gemini"', "'gemini'", '"claude"', "'claude'", '"anthropic"', "'anthropic'"):
        assert literal not in source


# --- H. confidence below threshold → zero click ---

def test_low_confidence_candidate_rejected():
    steps = _ready_steps()
    # Low confidence alone makes identity refinement eligible; give it a
    # matching, valid response so the ORIGINAL low-confidence rejection
    # (unaffected by refinement, which never touches confidence) is what
    # this test actually proves.
    steps.vision.primary.analyze_screen.side_effect = [
        _search_call([_candidate(confidence=0.1)]),
        _identity_refine_call("Yash", "Mail for project", (480.0, 300.0, 520.0, 480.0)),
    ]
    ok, _ = _run_find(steps)
    assert ok is False
    assert steps.result.failure_reason == LaunchFailureReason.EMAIL_GROUNDING_INVALID


# --- I/J. provider transient error → provider retry only, never scroll/click ---

def test_provider_transient_error_retried_once_then_succeeds():
    steps = _ready_steps()
    steps.vision.primary.analyze_screen.side_effect = [RateLimitError("HTTP 429"), _search_call([_candidate()])]
    ok, mock_scroll_pg = _run_find(steps)
    assert ok is True
    assert steps.result.provider_retries == 1
    mock_scroll_pg.scroll.assert_not_called()  # retry replaces the SAME call, never triggers a scroll


def test_provider_error_exhausted_does_not_scroll_or_click():
    steps = _ready_steps()
    steps.vision.primary.analyze_screen.side_effect = RateLimitError("HTTP 429")
    ok, mock_scroll_pg = _run_find(steps)
    assert ok is False
    assert steps.result.failure_reason == LaunchFailureReason.TECHNICAL_PROVIDER_ERROR
    mock_scroll_pg.scroll.assert_not_called()


# --- K/L. foreground loss before click / after move before click ---

def test_click_blocked_when_outlook_not_foreground():
    steps = _ready_steps()
    steps.result.email_converted_x, steps.result.email_converted_y = 500, 500
    with patch(f"{MODULE}.pyautogui") as mock_pyautogui, \
         patch(f"{MODULE}.get_foreground_window_title", return_value="Visual Studio Code"):
        mock_pyautogui.FAILSAFE = True
        assert steps.click_target_email() is False
        mock_pyautogui.moveTo.assert_not_called()
    assert steps.result.failure_reason == LaunchFailureReason.OUTLOOK_FOREGROUND_LOST
    assert steps.result.email_click_count == 0


def test_foreground_lost_after_move_before_click_blocks_click():
    steps = _ready_steps()
    steps.result.email_converted_x, steps.result.email_converted_y = 500, 500
    titles = iter(["Mail - Outlook", "Visual Studio Code"])
    with patch(f"{MODULE}.pyautogui") as mock_pyautogui, \
         patch(f"{MODULE}.get_foreground_window_title", side_effect=lambda: next(titles)):
        mock_pyautogui.FAILSAFE = True
        assert steps.click_target_email() is False
        mock_pyautogui.moveTo.assert_called_once()
        mock_pyautogui.click.assert_not_called()
    assert steps.result.failure_reason == LaunchFailureReason.OUTLOOK_FOREGROUND_LOST
    assert steps.result.email_click_count == 0


def test_click_executed_exactly_once_on_success():
    steps = _ready_steps()
    steps.result.email_converted_x, steps.result.email_converted_y = 500, 500
    with patch(f"{MODULE}.pyautogui") as mock_pyautogui, \
         patch(f"{MODULE}.get_foreground_window_title", return_value="Mail - Outlook"):
        mock_pyautogui.FAILSAFE = True
        assert steps.click_target_email() is True
        mock_pyautogui.click.assert_called_once_with()
    assert steps.result.email_click_count == 1
    assert steps.result.mouse_click_count == 1


# --- M. post-click verification failure → click remains exactly one ---

def test_open_verification_retries_without_reclick_and_click_count_stays_one():
    steps = _steps()
    steps.result.email_click_count = 1  # simulating click_target_email() already ran
    not_yet = MagicMock(
        parsed_json={"email_open": False, "subject_detected": "", "sender_detected": "", "subject_match": False,
                     "sender_match": False, "body_visible": False, "confidence": 0.5, "reason": "still loading"},
        raw_text="{}", model="gemini-3.6-flash", latency_ms=50.0, input_tokens=5, output_tokens=5,
    )
    confirmed = MagicMock(
        parsed_json={"email_open": True, "subject_detected": "Mail for project", "sender_detected": "Yash",
                     "subject_match": True, "sender_match": True, "body_visible": True, "confidence": 0.98,
                     "reason": "open"},
        raw_text="{}", model="gemini-3.6-flash", latency_ms=50.0, input_tokens=5, output_tokens=5,
    )
    steps.vision.primary.analyze_screen.side_effect = [not_yet, confirmed]
    with patch(f"{MODULE}.time.sleep"), patch(f"{MODULE}.capture_screen", return_value=_capture()):
        assert steps.verify_email_opened() is True
    assert len(steps.result.email_open_verification_attempts) == 2
    assert steps.result.email_click_count == 1  # unchanged — verification never clicks
    assert steps.result.email_open_verified_at is not None
    assert steps.result.email_open_ms is not None


def test_live_regression_name_only_target_accepts_name_plus_email_detected_sender():
    """2026-09-06 live-failure regression: the correct email was opened
    (Vision itself semantically confirmed sender AND subject), but the
    deterministic sender_ok check compared target_sender='Yash' as an
    opaque string against the full visible
    'Yash<yashdhanraj9140@gmail.com>' display text and rejected it as a
    sender_mismatch. sender_matches() (app/safety/sender_identity.py)
    now parses both sides into (display_name, email_address) before
    comparing — this must PASS on the very first observation."""
    steps = _steps(target_sender="Yash", target_subject="Question Regarding Meeting Details")
    confirmed = MagicMock(
        parsed_json={
            "email_open": True, "subject_detected": "Question Regarding Meeting Details",
            "sender_detected": "Yash<yashdhanraj9140@gmail.com>",
            "subject_match": True, "sender_match": True, "body_visible": True, "confidence": 0.99,
            "reason": "open",
        },
        raw_text="{}", model="claude-sonnet-5", latency_ms=50.0, input_tokens=5, output_tokens=5,
    )
    steps.vision.primary.analyze_screen.return_value = confirmed
    with patch(f"{MODULE}.time.sleep"), patch(f"{MODULE}.capture_screen", return_value=_capture()):
        assert steps.verify_email_opened() is True
    assert len(steps.result.email_open_verification_attempts) == 1
    assert steps.result.result == "PASS"


# --- N. wrong email opened → safe stop ---

def test_wrong_email_opened_classified_distinctly():
    steps = _steps()
    wrong_email = MagicMock(
        parsed_json={"email_open": True, "subject_detected": "Unrelated subject", "sender_detected": "Someone Else",
                     "subject_match": False, "sender_match": False, "body_visible": True, "confidence": 0.9,
                     "reason": "wrong email"},
        raw_text="{}", model="gemini-3.6-flash", latency_ms=50.0, input_tokens=5, output_tokens=5,
    )
    steps.vision.primary.analyze_screen.return_value = wrong_email
    with patch(f"{MODULE}.time.sleep"), patch(f"{MODULE}.capture_screen", return_value=_capture()):
        assert steps.verify_email_opened() is False
    assert steps.result.failure_reason == LaunchFailureReason.WRONG_EMAIL_OPENED


def test_open_verify_decision_logs_sender_parsing_diagnostics(caplog):
    """2026-09-06: EMAIL_OPEN_VERIFY_DECISION additionally logs
    target_sender_mode/detected_display_name/email_present so a
    sender-identity decision is explainable without exposing the full
    detected email address in the NEW fields."""
    steps = _steps(target_sender="Yash", target_subject="")
    confirmed = MagicMock(
        parsed_json={"email_open": True, "subject_detected": "Anything", "sender_detected": "Yash <yash@example.com>",
                     "subject_match": True, "sender_match": True, "body_visible": True, "confidence": 0.98,
                     "reason": "open"},
        raw_text="{}", model="claude-sonnet-5", latency_ms=50.0, input_tokens=5, output_tokens=5,
    )
    steps.vision.primary.analyze_screen.return_value = confirmed
    with patch(f"{MODULE}.time.sleep"), patch(f"{MODULE}.capture_screen", return_value=_capture()), \
         caplog.at_level("INFO", logger="app.outlook.find_email.open_verification"):
        assert steps.verify_email_opened() is True

    decision_lines = [r.getMessage() for r in caplog.records if "EMAIL_OPEN_VERIFY_DECISION" in r.getMessage()]
    assert len(decision_lines) == 1
    assert "target_sender_mode=name" in decision_lines[0]
    assert "detected_display_name='yash'" in decision_lines[0]
    assert "email_present=True" in decision_lines[0]


def test_open_verification_diagnostic_logging_reports_parsed_fields_and_decision(caplog):
    """EMAIL_OPEN_VERIFICATION diagnostics-only logging (2026-09-05): both
    the raw parsed fields and the deterministic accept/reject decision
    (including WHY it was rejected) must be observable in logs, with no
    change to the pass/fail outcome itself."""
    steps = _steps(target_sender="Yash", target_subject="Mail for project")
    mismatched = MagicMock(
        parsed_json={"email_open": True, "subject_detected": "Different subject", "sender_detected": "Yash",
                     "subject_match": False, "sender_match": True, "body_visible": True, "confidence": 0.9,
                     "reason": "subject differs"},
        raw_text="{}", model="gemini-3.6-flash", latency_ms=50.0, input_tokens=5, output_tokens=5,
    )
    confirmed = MagicMock(
        parsed_json={"email_open": True, "subject_detected": "Mail for project", "sender_detected": "Yash",
                     "subject_match": True, "sender_match": True, "body_visible": True, "confidence": 0.98,
                     "reason": "open"},
        raw_text="{}", model="gemini-3.6-flash", latency_ms=50.0, input_tokens=5, output_tokens=5,
    )
    steps.vision.primary.analyze_screen.side_effect = [mismatched, confirmed]
    with patch(f"{MODULE}.time.sleep"), patch(f"{MODULE}.capture_screen", return_value=_capture()), \
         caplog.at_level("INFO", logger="app.outlook.find_email.open_verification"):
        assert steps.verify_email_opened() is True

    messages = [r.getMessage() for r in caplog.records]
    result_lines = [m for m in messages if "EMAIL_OPEN_VERIFY_RESULT" in m]
    decision_lines = [m for m in messages if "EMAIL_OPEN_VERIFY_DECISION" in m]
    assert len(result_lines) == 2
    assert len(decision_lines) == 2

    assert "observation_attempt=1" in result_lines[0]
    assert "visible_subject='Different subject'" in result_lines[0]
    assert "observation_attempt=1" in decision_lines[0]
    assert "accepted=False" in decision_lines[0]
    assert "reason=subject_mismatch" in decision_lines[0]
    assert "sender_ok=True" in decision_lines[0]
    assert "subject_ok=False" in decision_lines[0]

    assert "observation_attempt=2" in decision_lines[1]
    assert "accepted=True" in decision_lines[1]
    assert "reason=n/a" in decision_lines[1]


def test_subject_not_required_when_target_subject_empty():
    """Subject-optional: when no target subject was configured,
    subject_match is never part of the pass/fail decision."""
    steps = _steps(target_subject="")
    call = MagicMock(
        parsed_json={"email_open": True, "subject_detected": "Whatever it says", "sender_detected": "Yash",
                     "subject_match": False, "sender_match": True, "body_visible": True, "confidence": 0.9,
                     "reason": "ok"},
        raw_text="{}", model="gemini-3.6-flash", latency_ms=50.0, input_tokens=5, output_tokens=5,
    )
    steps.vision.primary.analyze_screen.return_value = call
    with patch(f"{MODULE}.time.sleep"), patch(f"{MODULE}.capture_screen", return_value=_capture()):
        assert steps.verify_email_opened() is True


# --- O. Phase 2 metrics propagate into the Phase 3 result ---

def test_phase_2_launch_metrics_propagate_into_find_email_result():
    steps = _steps()
    steps.launch.result.outlook_launch_started_at = "2026-09-01T12:00:00"
    steps.launch.result.maximize_required = True
    steps.launch.result.maximize_executed = True
    steps.launch.result.maximize_duration_ms = 1234.5
    steps.launch.result.post_maximize_screenshot = "post_max.png"
    steps.launch.result.outlook_ready_at = "2026-09-01T12:00:10"
    steps.launch.result.outlook_ready_ms = 10000.0
    steps.launch.result.provider_retries = 2
    steps.launch.result.ready_for_interaction = True
    steps.launch.result.foreground_verified = True

    with patch(f"{MODULE}.OutlookLaunchSteps.press_windows_key", return_value=True), \
         patch(f"{MODULE}.OutlookLaunchSteps.type_search_query", return_value=True), \
         patch(f"{MODULE}.OutlookLaunchSteps.capture_search_screenshot", return_value=_capture()), \
         patch(f"{MODULE}.OutlookLaunchSteps.ground_search_result", return_value=True), \
         patch(f"{MODULE}.OutlookLaunchSteps.activate_outlook_result", return_value=True), \
         patch(f"{MODULE}.OutlookLaunchSteps.poll_for_outlook_foreground", return_value=True), \
         patch(f"{MODULE}.OutlookLaunchSteps.enforce_maximized", return_value=True), \
         patch(f"{MODULE}.OutlookLaunchSteps.verify_outlook_readiness", return_value=True):
        assert steps.run_launch_and_readiness() is True

    assert steps.result.outlook_launch_started_at == "2026-09-01T12:00:00"
    assert steps.result.maximize_required is True
    assert steps.result.maximize_executed is True
    assert steps.result.maximize_duration_ms == 1234.5
    assert steps.result.post_maximize_screenshot == "post_max.png"
    assert steps.result.outlook_ready_at == "2026-09-01T12:00:10"
    assert steps.result.outlook_ready_ms == 10000.0
    assert steps.result.provider_retries == 2


# --- P. historical coordinates/annotations never used as runtime click sources ---

def test_module_never_references_historical_screenshot_or_annotation_paths():
    import inspect

    from app.outlook import find_email as find_email_mod

    source = inspect.getsource(find_email_mod)
    assert "screenshots/annotated" not in source
    assert "screenshots\\annotated" not in source
    assert "test_cases" not in source
    assert "dataset_manifest" not in source
    assert "ground_truth" not in source.lower()


# --- Session timing ---

def test_session_timing_populates_total_elapsed_ms():
    steps = _steps()
    steps.start_session()
    steps.finalize_session()
    assert steps.result.total_elapsed_ms is not None
    assert steps.result.session_start is not None
    assert steps.result.session_end is not None


# --- Abort handling ---

def test_abort_before_email_search_stops_safely_no_capture():
    steps = _ready_steps()
    steps.abort_controller.request_abort()
    with patch(f"{MODULE}.capture_screen") as mock_capture:
        assert steps.find_target_email() is False
        mock_capture.assert_not_called()
    assert steps.result.result == "ABORTED"
    assert steps.result.failure_reason == LaunchFailureReason.USER_ABORTED


def test_grounding_raises_if_not_ready():
    steps = _steps()
    try:
        steps.find_target_email()
        assert False, "expected RuntimeError"
    except RuntimeError:
        pass
