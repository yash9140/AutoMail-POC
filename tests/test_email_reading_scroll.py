"""Email-reading scroll-DISTANCE fix tests (2026-09-06 follow-up to a
live long-email failure).

A live long-email run safe-stopped correctly as CONTENT_NOT_FULLY_READ
(unresolved_after_no_progress) after 4 sections — the no-progress
detector worked exactly as designed. The human supervisor observed each
physical scroll moving the email only a very small amount; offline
replay against the run's own saved screenshots (benchmarks/
email_reading/measure_scroll_displacement.py) confirmed it: the OLD
EMAIL_BODY_SCROLL_AMOUNT=-6 produced only ~6-7px of real content
movement out of a ~702px reading pane (under 1%).

This file proves the NEW physical-scroll-distance wiring in
app/outlook/read_email.py (EmailUnderstandingSteps.
_scroll_email_body_with_safety_checks, using app.automation.scrolling.
scroll_email_reading_body + app.config.settings.EMAIL_READING_SCROLL_
AMOUNT/_BOOSTED) without touching content-completion semantics,
current_message_continues_below/end_of_message_visible/conversation_
history_visible_below, overlap stripping, or the no-progress safe stop
— all of which are exercised here as unchanged regression checks, not
just the new scroll mechanics.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.settings import (  # noqa: E402
    EMAIL_BODY_SCROLL_ANCHOR_X_FRACTION,
    EMAIL_BODY_SCROLL_ANCHOR_Y_FRACTION,
    EMAIL_READING_SCROLL_AMOUNT,
    EMAIL_READING_SCROLL_AMOUNT_BOOSTED,
)
from app.outlook.draft import ReplyDraftSteps  # noqa: E402
from app.outlook.send import SendFlowSteps  # noqa: E402
from app.playbook.failure_reasons import LaunchFailureReason  # noqa: E402
from app.safety.abort_controller import AbortController  # noqa: E402
from app.vision.service import VisionService  # noqa: E402
from rnd.models.find_open_email import EmailOpenVerificationResponse  # noqa: E402
from tests._capture_test_utils import real_capture_image_path  # noqa: E402

READ_MODULE = "app.outlook.read_email"
SCROLL_MODULE = "app.automation.scrolling"


def _steps() -> ReplyDraftSteps:
    return ReplyDraftSteps(AbortController(), VisionService(MagicMock(), fallback=None), "test-model")


def _capture(width=1920, height=1080, filename="scroll_test.png"):
    return MagicMock(filename=filename, path=real_capture_image_path(width, height, name=filename), width=width, height=height)


def _mark_email_open(steps: ReplyDraftSteps) -> None:
    steps.result.email_open_verification = EmailOpenVerificationResponse(
        email_open=True, subject_detected="Mail for project", sender_detected="Yash",
        subject_match=True, sender_match=True, body_visible=True, confidence=0.98, reason="open",
    )


def _section_call(content="text", more_below=False, end_of_message_visible=None,
                   conversation_history_visible_below=False, no_new_content=False,
                   overlap_text="", confidence=0.9, **overrides):
    if end_of_message_visible is None:
        end_of_message_visible = not more_below
    payload = {
        "extracted_visible_content": content, "overlap_text": overlap_text,
        "important_points": [], "requested_actions": [], "names_entities": [], "dates": [], "commitments": [],
        "more_content_below": more_below, "end_of_message_visible": end_of_message_visible,
        "conversation_history_visible_below": conversation_history_visible_below,
        "no_new_content": no_new_content,
        "reply_expectation": "OPTIONAL_REPLY", "requires_user_decision": False, "sender_intent": "check in",
        "requested_action_summary": "", "confidence": confidence, "reason": "ok",
    }
    payload.update(overrides)
    return MagicMock(parsed_json=payload, raw_text="{}", model="test-model", latency_ms=50.0, input_tokens=5, output_tokens=5)


def _run(steps, foreground="Outlook"):
    with patch(f"{READ_MODULE}.get_foreground_window_title", return_value=foreground), \
         patch(f"{READ_MODULE}.capture_screen", return_value=_capture()) as mock_capture, \
         patch(f"{READ_MODULE}.time.sleep"), \
         patch(f"{SCROLL_MODULE}.pyautogui") as mock_pg:
        mock_pg.FAILSAFE = True
        result = steps.understand_email()
    return result, mock_pg, mock_capture


# --- A: scrolling function uses the intended larger bounded amount ---

def test_A_scroll_uses_new_larger_bounded_amount_not_old_tiny_one():
    steps = _steps()
    _mark_email_open(steps)
    steps.vision.primary.analyze_screen.side_effect = [
        _section_call(content="A ", more_below=True),
        _section_call(content="B", more_below=False),
        _section_call(),  # holistic assessment
    ]
    ok, mock_pg, _ = _run(steps)
    assert ok is True
    mock_pg.scroll.assert_called_once_with(EMAIL_READING_SCROLL_AMOUNT)
    assert abs(EMAIL_READING_SCROLL_AMOUNT) > abs(-6) * 5  # meaningfully larger than the old, measured-too-small amount


def test_A_boosted_amount_is_strictly_larger_than_normal():
    assert abs(EMAIL_READING_SCROLL_AMOUNT_BOOSTED) > abs(EMAIL_READING_SCROLL_AMOUNT)


# --- B: foreground failure -> zero scroll ---

def test_B_foreground_lost_before_scroll_zero_scroll():
    steps = _steps()
    _mark_email_open(steps)
    steps.vision.primary.analyze_screen.side_effect = [
        _section_call(content="A ", more_below=True),
        _section_call(content="B", more_below=False),
    ]
    with patch(f"{READ_MODULE}.get_foreground_window_title", side_effect=["Outlook", "Notepad"]), \
         patch(f"{READ_MODULE}.capture_screen", return_value=_capture()), \
         patch(f"{READ_MODULE}.time.sleep"), \
         patch(f"{SCROLL_MODULE}.pyautogui") as mock_pg:
        mock_pg.FAILSAFE = True
        ok = steps.understand_email()
    assert ok is False
    assert steps.result.failure_reason == LaunchFailureReason.OUTLOOK_FOREGROUND_LOST
    mock_pg.scroll.assert_not_called()
    mock_pg.moveTo.assert_not_called()


# --- C: abort -> zero scroll ---

def test_C_abort_before_scroll_zero_scroll():
    steps = _steps()
    _mark_email_open(steps)
    steps.vision.primary.analyze_screen.return_value = _section_call(content="A ", more_below=True)

    real_check_abort = steps.check_abort
    calls = {"n": 0}

    def _check_abort(stage):
        calls["n"] += 1
        if stage == "before_email_body_scroll":
            steps.abort_controller.request_abort()
        return real_check_abort(stage)

    with patch.object(steps, "check_abort", side_effect=_check_abort), \
         patch(f"{READ_MODULE}.get_foreground_window_title", return_value="Outlook"), \
         patch(f"{READ_MODULE}.capture_screen", return_value=_capture()), \
         patch(f"{SCROLL_MODULE}.pyautogui") as mock_pg:
        mock_pg.FAILSAFE = True
        ok = steps.understand_email()
    assert ok is False
    mock_pg.scroll.assert_not_called()


# --- D: cursor positioned in the intended reading-pane safe region ---

def test_D_cursor_positioned_at_reading_pane_anchor():
    steps = _steps()
    _mark_email_open(steps)
    steps.vision.primary.analyze_screen.side_effect = [
        _section_call(content="A ", more_below=True),
        _section_call(content="B", more_below=False),
        _section_call(),
    ]
    ok, mock_pg, _ = _run(steps)
    assert ok is True
    expected_x = round(1920 * EMAIL_BODY_SCROLL_ANCHOR_X_FRACTION)
    expected_y = round(1080 * EMAIL_BODY_SCROLL_ANCHOR_Y_FRACTION)
    mock_pg.moveTo.assert_called_once_with(expected_x, expected_y)


# --- E: one physical scroll per Vision-reading iteration maximum ---

def test_E_exactly_one_scroll_between_each_of_four_sections():
    steps = _steps()
    _mark_email_open(steps)
    steps.vision.primary.analyze_screen.side_effect = [
        _section_call(content="A ", more_below=True),
        _section_call(content="B ", more_below=True),
        _section_call(content="C ", more_below=True),
        _section_call(content="D", more_below=False),
        _section_call(),
    ]
    ok, mock_pg, _ = _run(steps)
    assert ok is True
    assert steps.result.sections_seen == 4
    assert mock_pg.scroll.call_count == 3  # one scroll between each of the 4 sections' reads
    assert steps.result.email_body_scroll_attempts == 3


# --- F: fresh screenshot required after each scroll ---

def test_F_extra_capture_taken_after_each_physical_scroll():
    steps = _steps()
    _mark_email_open(steps)
    steps.vision.primary.analyze_screen.side_effect = [
        _section_call(content="A ", more_below=True),
        _section_call(content="B", more_below=False),
        _section_call(),
    ]
    ok, _, mock_capture = _run(steps)
    assert ok is True
    # 2 top-of-loop captures (section 1, section 2) + 1 post-scroll
    # movement-guard capture (after the one scroll between them) = 3.
    assert mock_capture.call_count == 3


# --- G: no-progress detector remains unchanged ---

def test_G_no_progress_detector_still_safe_stops_exactly_as_before():
    steps = _steps()
    _mark_email_open(steps)
    steps.vision.primary.analyze_screen.side_effect = [
        _section_call(content="Section 1 content.", more_below=True),
        _section_call(content="", more_below=True, no_new_content=True),
    ]
    ok, mock_pg, _ = _run(steps)
    assert ok is False
    assert steps.result.failure_reason == LaunchFailureReason.CONTENT_NOT_FULLY_READ
    assert steps.result.content_complete is False
    assert "no new target-message content" in steps.result.notes


# --- H: current completion rule remains unchanged ---

def test_H_more_below_false_but_no_end_evidence_does_not_complete():
    """not more_content_below ALONE must still never be enough — content_
    complete additionally requires end_of_message_visible OR
    conversation_history_visible_below (the original live-bug fix this
    task must not weaken)."""
    steps = _steps()
    _mark_email_open(steps)
    steps.vision.primary.analyze_screen.return_value = _section_call(
        content="looks done", more_below=False, end_of_message_visible=False,
        conversation_history_visible_below=False,
    )
    with patch(f"{READ_MODULE}.get_foreground_window_title", return_value="Outlook"), \
         patch(f"{READ_MODULE}.capture_screen", return_value=_capture()), \
         patch(f"{READ_MODULE}.time.sleep"), \
         patch(f"{SCROLL_MODULE}.pyautogui") as mock_pg:
        mock_pg.FAILSAFE = True
        ok = steps.understand_email()
    assert ok is False
    assert steps.result.content_complete is False
    assert steps.result.failure_reason == LaunchFailureReason.CONTENT_NOT_FULLY_READ


def test_H_more_below_false_with_end_of_message_visible_completes():
    steps = _steps()
    _mark_email_open(steps)
    steps.vision.primary.analyze_screen.side_effect = [
        _section_call(content="done", more_below=False, end_of_message_visible=True),
        _section_call(),
    ]
    ok, _, _ = _run(steps)
    assert ok is True
    assert steps.result.content_complete is True


# --- I: conversation-history completion remains unchanged ---

def test_I_conversation_history_visible_below_completes_without_end_of_message_visible():
    steps = _steps()
    _mark_email_open(steps)
    steps.vision.primary.analyze_screen.side_effect = [
        _section_call(
            content="short complete message", more_below=False, end_of_message_visible=False,
            conversation_history_visible_below=True,
        ),
        _section_call(),
    ]
    ok, _, _ = _run(steps)
    assert ok is True
    assert steps.result.content_complete is True


# --- J: movement-check does not itself declare content complete ---

def test_J_zero_measured_movement_never_blocks_or_completes_reading():
    """capture_screen returns the SAME mock capture every call in this
    test harness, so the movement guard's difference_score is always
    0.0 (identical-path shortcut) — i.e. the movement guard ALWAYS
    reports "not sufficient" here. This must never itself finish the
    read or safe-stop it; only Vision's own more_content_below/end_of_
    message_visible/conversation_history_visible_below decide that."""
    steps = _steps()
    _mark_email_open(steps)
    steps.vision.primary.analyze_screen.side_effect = [
        _section_call(content="A ", more_below=True),
        _section_call(content="B", more_below=False, end_of_message_visible=True),
        _section_call(),
    ]
    ok, _, _ = _run(steps)
    assert ok is True
    assert steps.result.content_complete is True
    assert steps.result.sections_seen == 2
    # The boost-pending flag SHOULD have been set (movement was "not
    # sufficient" every time in this harness) — proving the movement
    # guard reacted internally — while completion still proceeded
    # normally, unaffected by it.
    assert steps._email_body_scroll_boost_pending is True


def test_J_second_scroll_uses_boosted_amount_after_first_insufficient_movement():
    steps = _steps()
    _mark_email_open(steps)
    steps.vision.primary.analyze_screen.side_effect = [
        _section_call(content="A ", more_below=True),
        _section_call(content="B ", more_below=True),
        _section_call(content="C", more_below=False),
        _section_call(),
    ]
    ok, mock_pg, _ = _run(steps)
    assert ok is True
    assert mock_pg.scroll.call_count == 2
    calls = mock_pg.scroll.call_args_list
    assert calls[0].args == (EMAIL_READING_SCROLL_AMOUNT,)
    assert calls[1].args == (EMAIL_READING_SCROLL_AMOUNT_BOOSTED,)


# --- K: long-email flow can accumulate multiple overlapping sections ---

def test_K_five_section_long_email_accumulates_all_sections():
    steps = _steps()
    _mark_email_open(steps)
    steps.vision.primary.analyze_screen.side_effect = [
        _section_call(content="Sec1. ", important_points=["a"], more_below=True),
        _section_call(content="Sec2. ", important_points=["b"], more_below=True),
        _section_call(content="Sec3. ", important_points=["c"], more_below=True),
        _section_call(content="Sec4. ", important_points=["d"], more_below=True),
        _section_call(content="Sec5.", important_points=["e"], more_below=False),
        _section_call(),
    ]
    ok, _, _ = _run(steps)
    assert ok is True
    assert steps.result.sections_seen == 5
    assert len(steps.result.email_sections) == 5
    for point in ("a", "b", "c", "d", "e"):
        assert point in steps.result.email_understanding_important_points


# --- L: no content is duplicated after overlap stripping ---

def test_L_overlap_stripping_prevents_duplicated_text_in_combined_output():
    steps = _steps()
    _mark_email_open(steps)
    steps.vision.primary.analyze_screen.side_effect = [
        _section_call(content="Hello team, the deadline is Friday ", more_below=True),
        _section_call(
            content="the deadline is Friday and the budget is $500.",
            overlap_text="the deadline is Friday ", more_below=False,
        ),
        _section_call(),
    ]
    ok, _, _ = _run(steps)
    assert ok is True
    combined = steps.result.email_understanding_summary
    assert combined.count("the deadline is Friday") == 1
    assert combined == "Hello team, the deadline is Friday and the budget is $500."


# --- M: no scroll occurs once content_complete=True ---

def test_M_no_scroll_after_content_complete_reached():
    steps = _steps()
    _mark_email_open(steps)
    steps.vision.primary.analyze_screen.side_effect = [
        _section_call(content="A ", more_below=True),
        _section_call(content="B", more_below=False, end_of_message_visible=True),
        _section_call(),
    ]
    ok, mock_pg, _ = _run(steps)
    assert ok is True
    assert steps.result.content_complete is True
    # Exactly one scroll happened (between section 1 and 2) — none after
    # section 2, which is where completion was reached.
    assert mock_pg.scroll.call_count == 1


# --- real-constructor initialization of the new boost-pending flag
# (same "must be set via the ACTUAL constructor path" lesson as every
# other per-instance field added to these classes this session) ---

def test_real_reply_draft_steps_construction_initializes_boost_pending_flag():
    steps = ReplyDraftSteps(AbortController(), VisionService(MagicMock(), fallback=None), "test-model")
    assert hasattr(steps, "_email_body_scroll_boost_pending")
    assert steps._email_body_scroll_boost_pending is False


def test_real_send_flow_steps_construction_initializes_boost_pending_flag():
    steps = SendFlowSteps(
        AbortController(), VisionService(MagicMock(), fallback=None), "test-model", send_approval_granted=True,
    )
    assert hasattr(steps, "_email_body_scroll_boost_pending")
    assert steps._email_body_scroll_boost_pending is False
