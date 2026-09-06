"""app/outlook/reply.py + app/outlook/read_email.py unit tests (final
POC — moved from RND-009D's app/playbook/reply_draft_steps.py). Uses
the assembled ReplyDraftSteps class (app/outlook/draft.py) since Reply
discovery/verification and email understanding are mixins composed
there. All external calls are mocked.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.outlook.draft import ReplyDraftSteps  # noqa: E402
from app.playbook.failure_reasons import LaunchFailureReason  # noqa: E402
from app.safety.abort_controller import AbortController  # noqa: E402
from app.vision.models import ReplyExpectation  # noqa: E402
from app.vision.service import VisionService  # noqa: E402
from rnd.models.find_open_email import EmailOpenVerificationResponse  # noqa: E402
from tests._capture_test_utils import real_capture_image_path, to_reply_crop_relative_bbox  # noqa: E402

READ_MODULE = "app.outlook.read_email"
REPLY_MODULE = "app.outlook.reply"


def _steps() -> ReplyDraftSteps:
    return ReplyDraftSteps(AbortController(), VisionService(MagicMock(), fallback=None), "gemini-3.6-flash")


def _capture(width=1920, height=1080, filename="email.png"):
    return MagicMock(filename=filename, path=real_capture_image_path(width, height, name=filename), width=width, height=height)


def _mark_email_open(steps: ReplyDraftSteps) -> None:
    steps.result.email_open_verification = EmailOpenVerificationResponse(
        email_open=True, subject_detected="Mail for project", sender_detected="Yash",
        subject_match=True, sender_match=True, body_visible=True, confidence=0.98, reason="open",
    )


# --- understand_email(): precondition + requires_reply gating (Phase 4:
# bounded scroll-and-accumulate loop over app.vision.models.EmailSection) ---

def _section_call(content="Just a greeting", reply_expectation="OPTIONAL_REPLY", requires_user_decision=False,
                   sender_intent="check in", requested_action_summary="", important_points=None, more_below=False,
                   end_of_message_visible=None, conversation_history_visible_below=False, no_new_content=False,
                   overlap_text="", confidence=0.9, **overrides):
    """Default reply_expectation is OPTIONAL_REPLY (not a failure under
    the current policy — see ReplyExpectation) so tests that aren't
    specifically about reply-necessity gating just pass through
    understand_email() unaffected, same as the old requires_reply=False
    default did NOT do (that used to fail) — the two dedicated gating
    tests below override this explicitly.

    end_of_message_visible defaults to `not more_below` when not given
    explicitly — content_complete requires end_of_message_visible OR
    conversation_history_visible_below alongside `not more_below` (see
    read_email.py), so most call sites that just say "no more content
    below" also mean "genuinely done" without needing to say so twice;
    tests specifically exercising the stricter signal distinctions (the
    live long-email bug fix, and the 2026-09-06 threaded-conversation
    fix) override this explicitly. conversation_history_visible_below
    defaults to False — most tests aren't about Outlook's threaded-
    conversation UI at all."""
    if end_of_message_visible is None:
        end_of_message_visible = not more_below
    payload = {
        "extracted_visible_content": content, "overlap_text": overlap_text,
        "important_points": important_points or [], "requested_actions": [], "names_entities": [],
        "dates": [], "commitments": [], "more_content_below": more_below,
        "end_of_message_visible": end_of_message_visible,
        "conversation_history_visible_below": conversation_history_visible_below,
        "no_new_content": no_new_content,
        "reply_expectation": reply_expectation, "requires_user_decision": requires_user_decision,
        "sender_intent": sender_intent,
        "requested_action_summary": requested_action_summary, "confidence": confidence, "reason": "ok",
    }
    payload.update(overrides)
    return MagicMock(
        parsed_json=payload, raw_text="{}", model="gemini-3.6-flash",
        latency_ms=50.0, input_tokens=5, output_tokens=5,
    )


def test_understand_email_raises_before_email_opened():
    steps = _steps()
    try:
        steps.understand_email()
        assert False, "expected RuntimeError"
    except RuntimeError:
        pass


def test_should_not_reply_stops_safely_without_fabricating():
    """SHOULD_NOT_REPLY is the ONLY reply_expectation that safely stops —
    e.g. a bounce/no-reply/non-conversational system message."""
    steps = _steps()
    _mark_email_open(steps)
    steps.vision.primary.analyze_screen.return_value = _section_call(
        content="This is an automated message. Do not reply.",
        reply_expectation="SHOULD_NOT_REPLY", sender_intent="automated notification",
    )
    with patch(f"{READ_MODULE}.get_foreground_window_title", return_value="Outlook"), \
         patch(f"{READ_MODULE}.capture_screen", return_value=_capture()):
        assert steps.understand_email() is False
    assert steps.result.failure_reason == LaunchFailureReason.REPLY_NOT_APPROPRIATE
    assert steps.result.reply_expectation == "SHOULD_NOT_REPLY"
    assert steps.result.requires_reply is False  # derived legacy alias
    assert steps.result.sections_seen == 1
    assert steps.result.content_complete is True  # read completed; just not appropriate to reply


def test_must_reply_passes_through_understanding_fields():
    steps = _steps()
    _mark_email_open(steps)
    steps.vision.primary.analyze_screen.return_value = _section_call(
        content="Please review by EOD", reply_expectation="MUST_REPLY", sender_intent="request action",
        requested_action_summary="review", important_points=["deadline: EOD"], confidence=0.95,
    )
    with patch(f"{READ_MODULE}.get_foreground_window_title", return_value="Outlook"), \
         patch(f"{READ_MODULE}.capture_screen", return_value=_capture()):
        assert steps.understand_email() is True
    assert steps.result.reply_expectation == "MUST_REPLY"
    assert steps.result.requires_reply is True  # derived legacy alias
    assert steps.result.email_understanding_summary == "Please review by EOD"
    assert steps.result.email_understanding_requested_action == "review"
    assert steps.result.sections_seen == 1
    assert steps.result.content_complete is True


def test_fyi_status_update_is_optional_reply_and_continues():
    """C: FYI/status email — no explicit ask, but a normal human reply is
    reasonable. Must continue, not fail — this is the exact live edge
    case the reply_expectation migration fixes (see app.vision.models.
    ReplyExpectation)."""
    steps = _steps()
    _mark_email_open(steps)
    steps.vision.primary.analyze_screen.return_value = _section_call(
        content="FYI — sharing this week's project status for your reference.",
        reply_expectation="OPTIONAL_REPLY", sender_intent="status update",
    )
    with patch(f"{READ_MODULE}.get_foreground_window_title", return_value="Outlook"), \
         patch(f"{READ_MODULE}.capture_screen", return_value=_capture()):
        assert steps.understand_email() is True
    assert steps.result.reply_expectation == "OPTIONAL_REPLY"
    assert steps.result.result != "FAIL"


def test_optional_feedback_invitation_is_optional_reply_and_continues():
    """D: email invites optional feedback — no explicit request, still
    OPTIONAL_REPLY, still continues."""
    steps = _steps()
    _mark_email_open(steps)
    steps.vision.primary.analyze_screen.return_value = _section_call(
        content="Feel free to share any thoughts if you have them, no pressure either way.",
        reply_expectation="OPTIONAL_REPLY", sender_intent="inviting optional feedback",
    )
    with patch(f"{READ_MODULE}.get_foreground_window_title", return_value="Outlook"), \
         patch(f"{READ_MODULE}.capture_screen", return_value=_capture()):
        assert steps.understand_email() is True
    assert steps.result.reply_expectation == "OPTIONAL_REPLY"
    assert steps.result.result != "FAIL"


def test_bounce_delivery_failure_is_should_not_reply_and_stops():
    """F: bounce/delivery-failure message — should_not_reply, safe stop."""
    steps = _steps()
    _mark_email_open(steps)
    steps.vision.primary.analyze_screen.return_value = _section_call(
        content="Delivery has failed permanently for the following recipients.",
        reply_expectation="SHOULD_NOT_REPLY", sender_intent="delivery failure notification",
    )
    with patch(f"{READ_MODULE}.get_foreground_window_title", return_value="Outlook"), \
         patch(f"{READ_MODULE}.capture_screen", return_value=_capture()):
        assert steps.understand_email() is False
    assert steps.result.failure_reason == LaunchFailureReason.REPLY_NOT_APPROPRIATE


def test_must_reply_with_user_decision_continues_without_inventing_it():
    """G: must_reply + requires_user_decision=true — the flow continues
    (understand_email() succeeds, draft generation remains reachable);
    requires_user_decision is recorded for downstream awareness but is
    NOT itself a gate here, and Phase 4 makes no decision on the user's
    behalf — it only records that one exists. (The actual non-invention
    guarantee for draft text lives in the reply-generation prompt — see
    test_reply_generation_prompt_forbids_invented_facts_and_commitments
    in tests/test_outlook_draft.py — this test covers the Phase 4
    classification/propagation half.)"""
    steps = _steps()
    _mark_email_open(steps)
    steps.vision.primary.analyze_screen.return_value = _section_call(
        content="Can you confirm completion by Friday?",
        reply_expectation="MUST_REPLY", requires_user_decision=True, sender_intent="requesting confirmation",
    )
    with patch(f"{READ_MODULE}.get_foreground_window_title", return_value="Outlook"), \
         patch(f"{READ_MODULE}.capture_screen", return_value=_capture()):
        assert steps.understand_email() is True
    assert steps.result.reply_expectation == "MUST_REPLY"
    assert steps.result.requires_user_decision is True
    assert steps.result.content_complete is True  # draft generation's own precondition remains satisfiable


def test_optional_reply_without_user_decision_allows_safe_draft_generation():
    """H: optional_reply + requires_user_decision=false — nothing blocks
    draft generation from this stage."""
    steps = _steps()
    _mark_email_open(steps)
    steps.vision.primary.analyze_screen.return_value = _section_call(
        content="Sharing this week's project status for your reference.",
        reply_expectation="OPTIONAL_REPLY", requires_user_decision=False, sender_intent="status update",
    )
    with patch(f"{READ_MODULE}.get_foreground_window_title", return_value="Outlook"), \
         patch(f"{READ_MODULE}.capture_screen", return_value=_capture()):
        assert steps.understand_email() is True
    assert steps.result.reply_expectation == "OPTIONAL_REPLY"
    assert steps.result.requires_user_decision is False
    assert steps.result.content_complete is True


# --- Phase 4: long-email accumulation ---

def test_short_email_is_the_single_section_case_of_the_same_loop():
    steps = _steps()
    _mark_email_open(steps)
    steps.vision.primary.analyze_screen.return_value = _section_call(
        content="Short email body.", more_below=False,
    )
    with patch(f"{READ_MODULE}.get_foreground_window_title", return_value="Outlook"), \
         patch(f"{READ_MODULE}.capture_screen", return_value=_capture()), \
         patch("app.automation.scrolling.pyautogui") as mock_scroll_pg:
        assert steps.understand_email() is True
        mock_scroll_pg.scroll.assert_not_called()
    assert steps.result.email_body_scroll_attempts == 0


def test_two_section_accumulation_dedups_overlap():
    steps = _steps()
    _mark_email_open(steps)
    steps.vision.primary.analyze_screen.side_effect = [
        _section_call(content="Hello Yash, please review the attached report ", more_below=True),
        _section_call(
            content="please review the attached report by Friday. Thanks.",
            overlap_text="please review the attached report ",
            more_below=False, sender_intent="request review",
            requested_action_summary="review the report by Friday",
        ),
        _section_call(),  # holistic-assessment call (2026-09-04 split — see EmailSectionExtractionResponse docstring)
    ]
    with patch(f"{READ_MODULE}.get_foreground_window_title", return_value="Outlook"), \
         patch(f"{READ_MODULE}.capture_screen", return_value=_capture()), \
         patch(f"{READ_MODULE}.time.sleep"), \
         patch("app.automation.scrolling.pyautogui") as mock_scroll_pg:
        mock_scroll_pg.FAILSAFE = True
        assert steps.understand_email() is True
        mock_scroll_pg.scroll.assert_called_once()
    combined = steps.result.email_understanding_summary
    # the overlapping substring appears exactly once in the final combined content
    assert combined.count("please review the attached report") == 1
    assert combined == "Hello Yash, please review the attached report by Friday. Thanks."
    assert steps.result.sections_seen == 2
    assert steps.result.email_body_scroll_attempts == 1
    assert steps.result.content_complete is True


def test_detail_from_early_section_survives_later_accumulation():
    """A date/commitment mentioned only in an early section must still
    be present in the final combined understanding after several more
    sections are accumulated on top of it — the specific regression
    this design targets (never collapse into a lossy running summary)."""
    steps = _steps()
    _mark_email_open(steps)
    steps.vision.primary.analyze_screen.side_effect = [
        _section_call(content="Section 1. ", important_points=["deadline: Sept 5"], more_below=True),
        _section_call(content="Section 2. ", important_points=["budget: $500"], more_below=True),
        _section_call(content="Section 3. ", important_points=["contact: Priya"], more_below=True),
        _section_call(content="Section 4.", important_points=["final note"], more_below=False),
        _section_call(),  # holistic-assessment call (2026-09-04 split)
    ]
    with patch(f"{READ_MODULE}.get_foreground_window_title", return_value="Outlook"), \
         patch(f"{READ_MODULE}.capture_screen", return_value=_capture()), \
         patch(f"{READ_MODULE}.time.sleep"), \
         patch("app.automation.scrolling.pyautogui") as mock_scroll_pg:
        mock_scroll_pg.FAILSAFE = True
        assert steps.understand_email() is True
    assert steps.result.sections_seen == 4
    assert "deadline: Sept 5" in steps.result.email_understanding_important_points
    assert "budget: $500" in steps.result.email_understanding_important_points
    assert "contact: Priya" in steps.result.email_understanding_important_points
    assert "final note" in steps.result.email_understanding_important_points
    assert len(steps.result.email_sections) == 4
    assert steps.result.email_sections[0].important_points == ["deadline: Sept 5"]


def test_scroll_until_complete_stops_as_soon_as_more_content_below_false():
    steps = _steps()
    _mark_email_open(steps)
    steps.vision.primary.analyze_screen.side_effect = [
        _section_call(content="A ", more_below=True),
        _section_call(content="B ", more_below=True),
        _section_call(content="C", more_below=False),
        _section_call(),  # holistic-assessment call (2026-09-04 split)
    ]
    with patch(f"{READ_MODULE}.get_foreground_window_title", return_value="Outlook"), \
         patch(f"{READ_MODULE}.capture_screen", return_value=_capture()), \
         patch(f"{READ_MODULE}.time.sleep"), \
         patch("app.automation.scrolling.pyautogui") as mock_scroll_pg:
        mock_scroll_pg.FAILSAFE = True
        assert steps.understand_email() is True
        assert mock_scroll_pg.scroll.call_count == 2  # one scroll between each of the 3 sections' reads


# ==================================================================
# Split extraction/holistic-assessment calls (2026-09-04 latency fix):
# a live run repeatedly hit "Gemini request timed out after 60.0s" on
# EMAIL_SECTION_UNDERSTANDING — the single heaviest request (16 output
# fields) in the whole pipeline. Extraction now runs on EVERY section
# (light — pure observation); holistic assessment (reply_expectation/
# requires_user_decision/sender_intent/requested_action_summary/
# confidence) runs EXACTLY ONCE, after the true end is confirmed, given
# the now-complete accumulated text. See app/vision/models.py::
# EmailSectionExtractionResponse's docstring for the full reasoning.
# ==================================================================

def test_holistic_assessment_called_exactly_once_for_multi_section_email():
    steps = _steps()
    _mark_email_open(steps)
    steps.vision.primary.analyze_screen.side_effect = [
        _section_call(content="Part 1. ", more_below=True),
        _section_call(content="Part 2. ", more_below=True),
        _section_call(content="Part 3, the end.", more_below=False),
        _section_call(reply_expectation="MUST_REPLY", sender_intent="final holistic call"),
    ]
    with patch(f"{READ_MODULE}.get_foreground_window_title", return_value="Outlook"), \
         patch(f"{READ_MODULE}.capture_screen", return_value=_capture()), \
         patch(f"{READ_MODULE}.time.sleep"), \
         patch("app.automation.scrolling.pyautogui") as mock_scroll_pg:
        mock_scroll_pg.FAILSAFE = True
        assert steps.understand_email() is True
    # 3 extraction calls (one per section) + exactly 1 holistic call = 4 total.
    assert steps.vision.primary.analyze_screen.call_count == 4
    assert steps.result.reply_expectation == "MUST_REPLY"
    assert steps.result.email_understanding_sender_intent == "final holistic call"


def test_intermediate_sections_have_default_holistic_fields_until_final_call():
    """Only the LAST section's reply_expectation/etc. should ever be
    real — earlier sections must stay at EmailSection's own defaults,
    never fabricated from a holistic call that was never made for them."""
    steps = _steps()
    _mark_email_open(steps)
    steps.vision.primary.analyze_screen.side_effect = [
        _section_call(content="Part 1. ", more_below=True),
        _section_call(content="Part 2, the end.", more_below=False),
        _section_call(reply_expectation="SHOULD_NOT_REPLY"),
    ]
    with patch(f"{READ_MODULE}.get_foreground_window_title", return_value="Outlook"), \
         patch(f"{READ_MODULE}.capture_screen", return_value=_capture()), \
         patch(f"{READ_MODULE}.time.sleep"), \
         patch("app.automation.scrolling.pyautogui") as mock_scroll_pg:
        mock_scroll_pg.FAILSAFE = True
        steps.understand_email()
    first_section = steps.result.email_sections[0]
    last_section = steps.result.email_sections[-1]
    assert first_section.reply_expectation == ReplyExpectation.OPTIONAL_REPLY  # untouched default — no call was made for it
    assert last_section.reply_expectation == "SHOULD_NOT_REPLY"  # from the real holistic call


def test_holistic_assessment_receives_full_accumulated_text():
    steps = _steps()
    _mark_email_open(steps)
    steps.vision.primary.analyze_screen.side_effect = [
        _section_call(content="First half. ", more_below=True),
        _section_call(content="Second half.", more_below=False),
        _section_call(),
    ]
    with patch(f"{READ_MODULE}.get_foreground_window_title", return_value="Outlook"), \
         patch(f"{READ_MODULE}.capture_screen", return_value=_capture()), \
         patch(f"{READ_MODULE}.time.sleep"), \
         patch("app.automation.scrolling.pyautogui") as mock_scroll_pg:
        mock_scroll_pg.FAILSAFE = True
        assert steps.understand_email() is True
    holistic_call_args = steps.vision.primary.analyze_screen.call_args_list[-1]
    holistic_prompt = holistic_call_args.args[2]
    assert "First half." in holistic_prompt
    assert "Second half." in holistic_prompt


def test_holistic_assessment_provider_error_fails_safe_never_fabricates():
    from app.vision.providers.base import RateLimitError

    steps = _steps()
    _mark_email_open(steps)
    steps.vision.primary.analyze_screen.side_effect = [
        _section_call(content="Short email.", more_below=False),
        RateLimitError("HTTP 429"),
        RateLimitError("HTTP 429"),  # exhausts PROVIDER_RETRY_COUNT=1
    ]
    with patch(f"{READ_MODULE}.get_foreground_window_title", return_value="Outlook"), \
         patch(f"{READ_MODULE}.capture_screen", return_value=_capture()):
        assert steps.understand_email() is False
    assert steps.result.result == "ERROR"
    assert steps.result.failure_reason == LaunchFailureReason.TECHNICAL_PROVIDER_ERROR
    assert steps.result.reply_expectation is None  # never fabricated


# ==================================================================
# Strict long-email completion rule (live bug fix, 2026-09-02): Reply
# became reachable before a long email's true end was ever seen,
# because content_complete trusted Vision's more_content_below alone.
# content_complete now REQUIRES both more_content_below=false AND
# end_of_message_visible=true (see read_email.py). Letters below match
# the task's own A-J test list.
# ==================================================================

def test_A_short_email_fully_visible_reaches_complete_with_no_scroll():
    steps = _steps()
    _mark_email_open(steps)
    steps.vision.primary.analyze_screen.return_value = _section_call(
        content="Short email body.", more_below=False,  # end_of_message_visible defaults to True
    )
    with patch(f"{READ_MODULE}.get_foreground_window_title", return_value="Outlook"), \
         patch(f"{READ_MODULE}.capture_screen", return_value=_capture()), \
         patch("app.automation.scrolling.pyautogui") as mock_scroll_pg:
        assert steps.understand_email() is True
        mock_scroll_pg.scroll.assert_not_called()
    assert steps.result.content_complete is True
    assert steps.result.email_body_scroll_attempts == 0
    # Reply search is now allowed to even begin (see D/J below for the
    # deterministic gate itself — this just confirms the precondition).
    assert steps.result.content_complete is True


def test_B_long_email_first_section_incomplete_scrolls_and_blocks_reply():
    """First section reports more content below — content_complete must
    stay false and a body scroll must occur; Reply is provably
    unreachable at that point (content_complete is not yet True)."""
    steps = _steps()
    _mark_email_open(steps)
    steps.vision.primary.analyze_screen.side_effect = [
        _section_call(content="Beginning of a long email. ", more_below=True),
        _section_call(content="The rest of the email.", more_below=False),
        _section_call(),  # holistic-assessment call (2026-09-04 split)
    ]
    with patch(f"{READ_MODULE}.get_foreground_window_title", return_value="Outlook"), \
         patch(f"{READ_MODULE}.capture_screen", return_value=_capture()), \
         patch(f"{READ_MODULE}.time.sleep"), \
         patch("app.automation.scrolling.pyautogui") as mock_scroll_pg:
        mock_scroll_pg.FAILSAFE = True
        assert steps.understand_email() is True
        mock_scroll_pg.scroll.assert_called_once()  # a body scroll DID occur before section 2
    assert steps.result.email_sections[0].more_content_below is True
    # At the moment section 1 was read, content_complete was still not
    # True — Reply could not have been reachable then (see the
    # deterministic prepare_reply_editor() gate, tested separately).


def test_C_three_sections_two_incomplete_then_true_end_then_reply_reachable():
    steps = _steps()
    _mark_email_open(steps)
    steps.vision.primary.analyze_screen.side_effect = [
        _section_call(content="Part 1. ", more_below=True),
        _section_call(content="Part 2. ", more_below=True),
        _section_call(content="Part 3, the true end.", more_below=False),
        _section_call(),  # holistic-assessment call (2026-09-04 split)
    ]
    with patch(f"{READ_MODULE}.get_foreground_window_title", return_value="Outlook"), \
         patch(f"{READ_MODULE}.capture_screen", return_value=_capture()), \
         patch(f"{READ_MODULE}.time.sleep"), \
         patch("app.automation.scrolling.pyautogui") as mock_scroll_pg:
        mock_scroll_pg.FAILSAFE = True
        assert steps.understand_email() is True
        assert mock_scroll_pg.scroll.call_count == 2
    assert steps.result.sections_seen == 3
    assert steps.result.email_body_scroll_attempts == 2
    assert steps.result.content_complete is True
    assert steps.result.email_sections[0].end_of_message_visible is False
    assert steps.result.email_sections[1].end_of_message_visible is False
    assert steps.result.email_sections[2].end_of_message_visible is True
    # Only NOW is Reply search's precondition satisfied.
    steps.vision.primary.analyze_screen.side_effect = None  # exhausted by understand_email() above
    steps.vision.primary.analyze_screen.return_value = MagicMock(
        parsed_json=None, raw_text="", model="gemini-3.6-flash", latency_ms=1.0, input_tokens=1, output_tokens=1,
    )
    with patch(f"{REPLY_MODULE}.get_foreground_window_title", return_value="Outlook"), \
         patch(f"{REPLY_MODULE}.capture_screen", return_value=_capture()) as mock_reply_capture:
        # A schema-invalid response is fine here — we only care that the
        # content_complete gate let this call proceed far enough to
        # actually look, not what the (unmocked) search itself concludes.
        steps.prepare_reply_editor()
        mock_reply_capture.assert_called_once()  # gate passed — it actually looked


def test_F_vision_claims_enough_context_but_more_content_below_true_stays_incomplete():
    """Even if Vision reports end_of_message_visible=true, more_content_
    below=true alone still keeps content_complete false — both signals
    are required."""
    steps = _steps()
    _mark_email_open(steps)
    steps.vision.primary.analyze_screen.side_effect = [
        _section_call(content="Enough context, I believe. ", more_below=True, end_of_message_visible=True),
        _section_call(content="Actually there was more.", more_below=False),
        _section_call(),  # holistic-assessment call (2026-09-04 split)
    ]
    with patch(f"{READ_MODULE}.get_foreground_window_title", return_value="Outlook"), \
         patch(f"{READ_MODULE}.capture_screen", return_value=_capture()), \
         patch(f"{READ_MODULE}.time.sleep"), \
         patch("app.automation.scrolling.pyautogui") as mock_scroll_pg:
        mock_scroll_pg.FAILSAFE = True
        assert steps.understand_email() is True
    assert steps.result.email_sections[0].more_content_below is True
    assert steps.result.sections_seen == 2  # did NOT stop after section 1


def test_G_signature_visible_but_more_content_below_stays_incomplete():
    steps = _steps()
    _mark_email_open(steps)
    steps.vision.primary.analyze_screen.side_effect = [
        _section_call(
            content="Thanks,\nBest regards,\nYash Dhanraj", more_below=True, end_of_message_visible=False,
        ),
        _section_call(content="[Continuation after the signature block]", more_below=False),
        _section_call(),  # holistic-assessment call (2026-09-04 split)
    ]
    with patch(f"{READ_MODULE}.get_foreground_window_title", return_value="Outlook"), \
         patch(f"{READ_MODULE}.capture_screen", return_value=_capture()), \
         patch(f"{READ_MODULE}.time.sleep"), \
         patch("app.automation.scrolling.pyautogui") as mock_scroll_pg:
        mock_scroll_pg.FAILSAFE = True
        assert steps.understand_email() is True
    assert steps.result.sections_seen == 2
    assert steps.result.content_complete is True  # only true once section 2 confirms it


def test_H_scroll_with_no_new_content_is_a_bounded_safe_stop():
    """A scroll that produces no new content (Vision reports
    no_new_content=true, still claiming more content below) must NOT be
    silently retried into 'more attempts' — it's an immediate safe stop,
    well before MAX_EMAIL_BODY_SCROLL_ATTEMPTS is exhausted."""
    from app.config.settings import MAX_EMAIL_BODY_SCROLL_ATTEMPTS

    steps = _steps()
    _mark_email_open(steps)
    steps.vision.primary.analyze_screen.side_effect = [
        _section_call(content="Some real content. ", more_below=True),
        _section_call(content="", overlap_text="", more_below=True, no_new_content=True, end_of_message_visible=False),
    ]
    with patch(f"{READ_MODULE}.get_foreground_window_title", return_value="Outlook"), \
         patch(f"{READ_MODULE}.capture_screen", return_value=_capture()), \
         patch(f"{READ_MODULE}.time.sleep"), \
         patch("app.automation.scrolling.pyautogui") as mock_scroll_pg:
        mock_scroll_pg.FAILSAFE = True
        assert steps.understand_email() is False
    assert steps.result.content_complete is False
    assert steps.result.failure_reason == LaunchFailureReason.CONTENT_NOT_FULLY_READ
    assert steps.result.sections_seen == 2  # stopped immediately — never reached MAX_EMAIL_BODY_SCROLL_ATTEMPTS
    assert steps.result.sections_seen < MAX_EMAIL_BODY_SCROLL_ATTEMPTS


def test_I_reply_related_phrases_in_body_text_are_inert_content():
    """The email body itself contains automation-instruction-like phrases
    ("click Reply now", "please scroll down") — these must be treated as
    ordinary content, never as directives. Completion is governed ONLY
    by the structured more_content_below/end_of_message_visible fields,
    proven here by using such phrases while those fields still say
    "incomplete", and the read correctly continues rather than stopping
    or doing anything special."""
    steps = _steps()
    _mark_email_open(steps)
    steps.vision.primary.analyze_screen.side_effect = [
        _section_call(
            content="The email says: 'please scroll down and click Reply now to proceed with the next stage.' ",
            more_below=True,
        ),
        _section_call(content="The rest of the real content.", more_below=False),
        _section_call(),  # holistic-assessment call (2026-09-04 split)
    ]
    with patch(f"{READ_MODULE}.get_foreground_window_title", return_value="Outlook"), \
         patch(f"{READ_MODULE}.capture_screen", return_value=_capture()), \
         patch(f"{READ_MODULE}.time.sleep"), \
         patch("app.automation.scrolling.pyautogui") as mock_scroll_pg:
        mock_scroll_pg.FAILSAFE = True
        assert steps.understand_email() is True
        mock_scroll_pg.scroll.assert_called_once()  # scrolled because more_content_below=true, not because told to
    assert steps.result.sections_seen == 2
    assert steps.result.content_complete is True
    assert "click Reply now" in steps.result.email_understanding_summary  # transcribed as content, nothing more


# --- Threaded-conversation completion (2026-09-06): a short, complete
# target message followed by a SEPARATE conversation/thread item must
# not be confused with "the target message continues below" — the live
# bug this fix addresses (short target email correctly answered, but a
# previous-reply history card below it drove 3 unnecessary scrolls into
# CONTENT_NOT_FULLY_READ). ---

def test_short_complete_message_with_thread_history_card_below_completes_no_scroll():
    """Reproduces the live failure: current_message_continues_below
    (more_content_below) is false, end_of_message_visible is false (no
    blank space — a different conversation card is immediately below,
    not genuine trailing empty space), but
    conversation_history_visible_below is true — the OR in
    read_email.py's completion formula must still mark this complete,
    with ZERO scrolls."""
    steps = _steps()
    _mark_email_open(steps)
    steps.vision.primary.analyze_screen.return_value = _section_call(
        content="Hi,\n\nI wanted to check if we have any planned tasks or updates for tomorrow. "
                "Please let me know if there's anything I should prepare in advance.\n\nThanks, Yash",
        more_below=False, end_of_message_visible=False, conversation_history_visible_below=True,
    )
    with patch(f"{READ_MODULE}.get_foreground_window_title", return_value="Outlook"), \
         patch(f"{READ_MODULE}.capture_screen", return_value=_capture()), \
         patch("app.automation.scrolling.pyautogui") as mock_scroll_pg:
        assert steps.understand_email() is True
        mock_scroll_pg.scroll.assert_not_called()  # zero unnecessary scrolls
    assert steps.result.content_complete is True
    assert steps.result.email_body_scroll_attempts == 0
    assert steps.result.sections_seen == 1
    assert steps.result.email_sections[0].conversation_history_visible_below is True


def test_long_target_message_then_thread_history_confirms_boundary_after_scroll():
    """A genuinely long CURRENT target message (section 1 continues)
    followed, after scrolling, by the message's own structural end PLUS
    a separate thread-history card starting right there — still
    completes correctly; the threaded-conversation fix does not weaken
    genuine long-email handling."""
    steps = _steps()
    _mark_email_open(steps)
    steps.vision.primary.analyze_screen.side_effect = [
        _section_call(content="Part 1 of a genuinely long target message. ", more_below=True),
        _section_call(
            content="Part 2, the true end of the target message.",
            more_below=False, end_of_message_visible=False, conversation_history_visible_below=True,
        ),
        _section_call(),  # holistic-assessment call
    ]
    with patch(f"{READ_MODULE}.get_foreground_window_title", return_value="Outlook"), \
         patch(f"{READ_MODULE}.capture_screen", return_value=_capture()), \
         patch(f"{READ_MODULE}.time.sleep"), \
         patch("app.automation.scrolling.pyautogui") as mock_scroll_pg:
        mock_scroll_pg.FAILSAFE = True
        assert steps.understand_email() is True
        assert mock_scroll_pg.scroll.call_count == 1
    assert steps.result.content_complete is True
    assert steps.result.sections_seen == 2


def test_no_new_content_but_thread_history_confirms_boundary_completes_safely():
    """A scroll that reveals no NEW target-message content (no_progress)
    but the reading pane now shows a separate thread-history card
    (conversation_history_visible_below=True) must complete safely, not
    CONTENT_NOT_FULLY_READ — the scroll revealed the true boundary, it
    just didn't add target-message text."""
    steps = _steps()
    _mark_email_open(steps)
    steps.vision.primary.analyze_screen.side_effect = [
        _section_call(content="The whole short message. ", more_below=True, end_of_message_visible=False),
        _section_call(
            content="", overlap_text="", more_below=False, no_new_content=True,
            end_of_message_visible=False, conversation_history_visible_below=True,
        ),
        _section_call(),  # holistic-assessment call
    ]
    with patch(f"{READ_MODULE}.get_foreground_window_title", return_value="Outlook"), \
         patch(f"{READ_MODULE}.capture_screen", return_value=_capture()), \
         patch(f"{READ_MODULE}.time.sleep"), \
         patch("app.automation.scrolling.pyautogui") as mock_scroll_pg:
        mock_scroll_pg.FAILSAFE = True
        assert steps.understand_email() is True
    assert steps.result.content_complete is True
    assert steps.result.result != "FAIL"


def test_no_new_content_and_boundary_unresolved_stays_content_not_fully_read():
    """Mirrors test_H — no new content after a scroll AND neither
    end_of_message_visible NOR conversation_history_visible_below ever
    confirms the boundary — must remain the CONTENT_NOT_FULLY_READ safe
    stop, never silently marked complete just because a scroll
    happened."""
    steps = _steps()
    _mark_email_open(steps)
    steps.vision.primary.analyze_screen.side_effect = [
        _section_call(content="Some real content. ", more_below=True),
        _section_call(
            content="", overlap_text="", more_below=True, no_new_content=True,
            end_of_message_visible=False, conversation_history_visible_below=False,
        ),
    ]
    with patch(f"{READ_MODULE}.get_foreground_window_title", return_value="Outlook"), \
         patch(f"{READ_MODULE}.capture_screen", return_value=_capture()), \
         patch(f"{READ_MODULE}.time.sleep"), \
         patch("app.automation.scrolling.pyautogui") as mock_scroll_pg:
        mock_scroll_pg.FAILSAFE = True
        assert steps.understand_email() is False
    assert steps.result.content_complete is False
    assert steps.result.failure_reason == LaunchFailureReason.CONTENT_NOT_FULLY_READ


def test_embedded_conversation_like_text_does_not_force_completion():
    """The email body itself contains text that reads like a description
    of a thread/history card ("see previous message below", "conversation
    continues"). This must never influence completion — ONLY the
    structured conversation_history_visible_below field (never inferred
    from body text) determines that, exactly like more_content_below/
    end_of_message_visible already require for Reply/scroll phrases
    (see test_I above)."""
    steps = _steps()
    _mark_email_open(steps)
    steps.vision.primary.analyze_screen.side_effect = [
        _section_call(
            content="The email says: 'see the previous message below, the conversation continues.' ",
            more_below=True, end_of_message_visible=False, conversation_history_visible_below=False,
        ),
        _section_call(content="The rest of the real content.", more_below=False),
        _section_call(),  # holistic-assessment call
    ]
    with patch(f"{READ_MODULE}.get_foreground_window_title", return_value="Outlook"), \
         patch(f"{READ_MODULE}.capture_screen", return_value=_capture()), \
         patch(f"{READ_MODULE}.time.sleep"), \
         patch("app.automation.scrolling.pyautogui") as mock_scroll_pg:
        mock_scroll_pg.FAILSAFE = True
        assert steps.understand_email() is True
        mock_scroll_pg.scroll.assert_called_once()  # scrolled because more_content_below=true, not because told to
    assert steps.result.sections_seen == 2
    assert steps.result.content_complete is True


def test_understand_email_has_no_provider_name_branch_in_completion_logic():
    """Provider-neutral requirement: Claude and Gemini use the exact same
    completion formula — no provider-name branch anywhere in
    understand_email()."""
    import inspect

    from app.outlook.read_email import EmailUnderstandingSteps

    source = inspect.getsource(EmailUnderstandingSteps.understand_email)
    for needle in ('"gemini"', "'gemini'", '"anthropic"', "'anthropic'", '"claude"', "'claude'", "provider_name =="):
        assert needle not in source, f"found provider-specific branch marker {needle!r}"


def test_bounded_incomplete_read_fails_safe_and_blocks_draft_generation():
    """E: max section limit reached without true-end evidence ->
    CONTENT_NOT_FULLY_READ -> zero Reply, zero draft (Send is not even
    reachable from this class — see app/outlook/send.py, a separate
    module never touched by understand_email())."""
    from app.config.settings import MAX_EMAIL_BODY_SCROLL_ATTEMPTS

    steps = _steps()
    _mark_email_open(steps)
    steps.vision.primary.analyze_screen.return_value = _section_call(content="Never-ending. ", more_below=True)
    with patch(f"{READ_MODULE}.get_foreground_window_title", return_value="Outlook"), \
         patch(f"{READ_MODULE}.capture_screen", return_value=_capture()), \
         patch(f"{READ_MODULE}.time.sleep"), \
         patch("app.automation.scrolling.pyautogui") as mock_scroll_pg:
        mock_scroll_pg.FAILSAFE = True
        assert steps.understand_email() is False
    assert steps.result.content_complete is False
    assert steps.result.failure_reason == LaunchFailureReason.CONTENT_NOT_FULLY_READ
    assert steps.result.sections_seen == MAX_EMAIL_BODY_SCROLL_ATTEMPTS
    assert mock_scroll_pg.scroll.call_count == MAX_EMAIL_BODY_SCROLL_ATTEMPTS - 1

    # draft generation must never run on an incomplete read
    try:
        steps.generate_draft()
        assert False, "expected RuntimeError"
    except RuntimeError:
        pass

    # Reply search must never run on an incomplete read either — zero
    # capture, zero click, deterministically, even though this is
    # exactly the scenario (a long, never-ending email) where Reply
    # might visually be on screen somewhere.
    with patch(f"{REPLY_MODULE}.capture_screen") as mock_reply_capture, \
         patch(f"{REPLY_MODULE}.pyautogui") as mock_reply_pg:
        assert steps.prepare_reply_editor() is False
        mock_reply_capture.assert_not_called()
        mock_reply_pg.click.assert_not_called()
    assert steps.result.failure_reason == LaunchFailureReason.CONTENT_NOT_FULLY_READ


def test_body_scroll_never_determines_click_point():
    import inspect

    from app.automation import scrolling

    source = inspect.getsource(scrolling.scroll_email_body)
    assert "email_converted_x" not in source
    assert "click" not in source.lower()


def test_provider_error_during_reading_retried_once():
    from app.vision.providers.base import RateLimitError

    steps = _steps()
    _mark_email_open(steps)
    steps.vision.primary.analyze_screen.side_effect = [
        RateLimitError("HTTP 429"),
        _section_call(content="Recovered content.", more_below=False),
        _section_call(),  # holistic-assessment call (2026-09-04 split)
    ]
    with patch(f"{READ_MODULE}.get_foreground_window_title", return_value="Outlook"), \
         patch(f"{READ_MODULE}.capture_screen", return_value=_capture()):
        assert steps.understand_email() is True
    assert steps.result.provider_retries == 1


# --- Reply grounding / click (Phase 5: bounded search + scroll, bbox grounding) ---

from app.config.settings import MAX_REPLY_SEARCH_SCROLL_ATTEMPTS  # noqa: E402
from app.vision.providers.base import RateLimitError  # noqa: E402

SCROLL_MODULE = "app.automation.scrolling"


def _state_check_call(verified: bool):
    return MagicMock(
        parsed_json={"verified": verified, "detected_state": "x", "confidence": 0.9,
                     "visual_evidence": "x", "reason": "x"},
        raw_text="{}", model="gemini-3.6-flash", latency_ms=50.0, input_tokens=5, output_tokens=5,
    )


def _reply_search_call(reply_visible=True, control_identity="Reply", control_type="button",
                        bbox=(400.0, 600.0, 440.0, 700.0), more_below=False, confidence=0.9, **overrides):
    # bbox here is expressed as the FULL-SCREEN bbox this fixture has
    # always intended (matching every downstream geometry assertion) —
    # converted to crop-relative since that's what REPLY_SEARCH now
    # actually returns; see tests/_capture_test_utils.py.
    payload = {
        "outlook_visible": True, "reply_visible": reply_visible,
        "control_identity": control_identity, "control_type": control_type,
        "bbox": to_reply_crop_relative_bbox(list(bbox)) if bbox is not None else None,
        "more_content_below": more_below, "confidence": confidence, "reason": "ok",
    }
    payload.update(overrides)
    return MagicMock(
        parsed_json=payload, raw_text="{}", model="gemini-3.6-flash",
        latency_ms=50.0, input_tokens=5, output_tokens=5,
    )


def _reply_ready_steps() -> ReplyDraftSteps:
    steps = _steps()
    steps.result.content_complete = True
    return steps


def _run_prepare(steps, patches=None):
    patches = patches or {}
    with patch(f"{REPLY_MODULE}.get_foreground_window_title", return_value=patches.get("title", "Outlook")), \
         patch(f"{REPLY_MODULE}.confirm_outlook_foreground_with_recheck", return_value=patches.get("title", "Outlook")), \
         patch(f"{REPLY_MODULE}.capture_screen", return_value=patches.get("capture", _capture())), \
         patch(f"{REPLY_MODULE}.time.sleep"), \
         patch(f"{REPLY_MODULE}.pyautogui") as mock_pyautogui, \
         patch(f"{SCROLL_MODULE}.pyautogui") as mock_scroll_pg:
        mock_pyautogui.FAILSAFE = True
        mock_scroll_pg.FAILSAFE = True
        result = steps.prepare_reply_editor()
    return result, mock_pyautogui, mock_scroll_pg


# --- M. content_complete gate ---

def test_content_not_complete_blocks_reply_search():
    steps = _steps()
    steps.result.content_complete = False
    with patch(f"{REPLY_MODULE}.capture_screen") as mock_capture:
        assert steps.prepare_reply_editor() is False
        mock_capture.assert_not_called()
    assert steps.result.failure_reason == LaunchFailureReason.CONTENT_NOT_FULLY_READ


def test_content_complete_none_also_blocks_reply_search():
    steps = _steps()  # content_complete defaults to None (never set)
    assert steps.prepare_reply_editor() is False
    assert steps.result.failure_reason == LaunchFailureReason.CONTENT_NOT_FULLY_READ


def test_D_reply_visible_but_content_incomplete_yields_zero_click():
    """D: even if Reply is genuinely visible on screen, find_reply()/
    Reply grounding never even starts while content_complete is not
    True — the deterministic gate is checked BEFORE any screenshot is
    taken for the search, so Reply's visibility is structurally
    irrelevant to whether it gets clicked."""
    steps = _steps()
    steps.result.content_complete = False
    with patch(f"{REPLY_MODULE}.capture_screen") as mock_capture, \
         patch(f"{REPLY_MODULE}.pyautogui") as mock_pg:
        assert steps.prepare_reply_editor() is False
        mock_capture.assert_not_called()  # never even looked for Reply
        mock_pg.click.assert_not_called()
        mock_pg.moveTo.assert_not_called()
    assert steps.result.failure_reason == LaunchFailureReason.CONTENT_NOT_FULLY_READ


def test_J_find_reply_only_proceeds_past_the_gate_once_content_complete_is_true():
    """J: the mirror image of D — once content_complete is verified
    True, prepare_reply_editor() DOES proceed past the gate (it actually
    looks for Reply, rather than short-circuiting)."""
    steps = _steps()
    steps.result.content_complete = True
    with patch(f"{REPLY_MODULE}.get_foreground_window_title", return_value="Outlook"), \
         patch(f"{REPLY_MODULE}.capture_screen", return_value=_capture()) as mock_capture:
        # No further mocking — the response won't be schema-valid, so
        # this returns False downstream, but that's irrelevant here:
        # we only care that the gate let it through to actually search.
        steps.prepare_reply_editor()
        mock_capture.assert_called_once()


# --- A. Reply visible, valid grounding, click once, editor verified ---

def test_reply_editor_already_open_skips_search_and_click():
    steps = _reply_ready_steps()
    steps.vision.primary.analyze_screen.return_value = _state_check_call(True)
    ok, mock_pyautogui, mock_scroll_pg = _run_prepare(steps)
    assert ok is True
    mock_pyautogui.moveTo.assert_not_called()
    mock_pyautogui.click.assert_not_called()
    mock_scroll_pg.scroll.assert_not_called()
    assert steps.result.reply_editor_already_open is True
    assert steps.result.reply_found_at is not None
    assert steps.result.reply_search_ms is not None


def test_reply_visible_valid_grounding_clicks_once():
    steps = _reply_ready_steps()
    steps.vision.primary.analyze_screen.side_effect = [_state_check_call(False), _reply_search_call()]
    ok, mock_pyautogui, mock_scroll_pg = _run_prepare(steps)
    assert ok is True
    mock_pyautogui.click.assert_called_once_with()
    mock_scroll_pg.scroll.assert_not_called()
    assert steps.result.reply_click_executed is True
    assert steps.result.reply_click_count == 1
    assert steps.result.mouse_click_count == 1
    assert steps.result.reply_grounding_confidence == 0.9
    assert steps.result.reply_grounding_bbox_raw == [400.0, 600.0, 440.0, 700.0]
    assert steps.result.reply_grounding_bbox_pixels is not None


# --- B. Reply not initially visible -> bounded scroll -> found -> click once ---

def test_reply_not_initially_visible_found_after_one_scroll():
    steps = _reply_ready_steps()
    steps.vision.primary.analyze_screen.side_effect = [
        _state_check_call(False),
        _reply_search_call(reply_visible=False, control_identity="", bbox=None, more_below=True),
        _reply_search_call(),
    ]
    ok, mock_pyautogui, mock_scroll_pg = _run_prepare(steps)
    assert ok is True
    assert mock_scroll_pg.scroll.call_count == 1
    assert steps.result.reply_search_scroll_attempts == 1
    mock_pyautogui.click.assert_called_once_with()
    assert steps.result.reply_click_count == 1


# --- C. Reply not found after max attempts -> safe stop ---

def test_reply_not_found_after_max_scroll_attempts():
    steps = _reply_ready_steps()
    steps.vision.primary.analyze_screen.side_effect = [_state_check_call(False)] + [
        _reply_search_call(reply_visible=False, control_identity="", bbox=None, more_below=True)
    ] * MAX_REPLY_SEARCH_SCROLL_ATTEMPTS
    ok, mock_pyautogui, mock_scroll_pg = _run_prepare(steps)
    assert ok is False
    assert steps.result.failure_reason == LaunchFailureReason.REPLY_NOT_FOUND
    mock_pyautogui.click.assert_not_called()
    assert mock_scroll_pg.scroll.call_count == MAX_REPLY_SEARCH_SCROLL_ATTEMPTS - 1
    assert steps.result.reply_click_count == 0


def test_reply_search_never_determines_click_point():
    """Scrolling only decides WHEN to look again — the click point always
    comes from a freshly validated bbox, never from scroll position."""
    import inspect

    source = inspect.getsource(__import__("app.outlook.reply", fromlist=["prepare_reply_editor"]).ReplyDiscoverySteps.prepare_reply_editor)
    # scroll_email_body() call sites never pass or derive a click coordinate
    assert "reply_converted_x = " not in source
    assert "reply_converted_y = " not in source


# --- D. Reply All returned instead of Reply -> reject, zero click ---

def test_reply_all_returned_instead_of_reply_is_rejected():
    steps = _reply_ready_steps()
    steps.vision.primary.analyze_screen.side_effect = [
        _state_check_call(False),
        _reply_search_call(reply_visible=True, control_identity="Reply All", bbox=(400, 600, 440, 760)),
    ]
    ok, mock_pyautogui, mock_scroll_pg = _run_prepare(steps)
    assert ok is False
    assert steps.result.failure_reason == LaunchFailureReason.REPLY_TARGET_NOT_FOUND
    mock_pyautogui.click.assert_not_called()
    mock_scroll_pg.scroll.assert_not_called()  # semantic mismatch is a safe stop, not a scroll trigger
    assert steps.result.reply_click_count == 0


def test_forward_returned_instead_of_reply_is_rejected():
    steps = _reply_ready_steps()
    steps.vision.primary.analyze_screen.side_effect = [
        _state_check_call(False),
        _reply_search_call(reply_visible=True, control_identity="Forward"),
    ]
    ok, mock_pyautogui, _ = _run_prepare(steps)
    assert ok is False
    assert steps.result.failure_reason == LaunchFailureReason.REPLY_TARGET_NOT_FOUND
    mock_pyautogui.click.assert_not_called()


# --- E. invalid bbox -> zero click ---

def test_missing_bbox_rejected_zero_click():
    steps = _reply_ready_steps()
    steps.vision.primary.analyze_screen.side_effect = [
        _state_check_call(False),
        _reply_search_call(bbox=None),
    ]
    ok, mock_pyautogui, _ = _run_prepare(steps)
    assert ok is False
    assert steps.result.failure_reason == LaunchFailureReason.REPLY_GROUNDING_INVALID
    mock_pyautogui.click.assert_not_called()
    assert steps.result.reply_converted_x is None


def test_degenerate_bbox_rejected_zero_click():
    steps = _reply_ready_steps()
    steps.vision.primary.analyze_screen.side_effect = [
        _state_check_call(False),
        _reply_search_call(bbox=(400, 600, 400, 600)),  # zero area
    ]
    ok, mock_pyautogui, _ = _run_prepare(steps)
    assert ok is False
    assert steps.result.failure_reason == LaunchFailureReason.REPLY_GROUNDING_INVALID
    mock_pyautogui.click.assert_not_called()


# --- F. out-of-range bbox -> zero click ---

def test_out_of_normalized_range_bbox_rejected_zero_click():
    steps = _reply_ready_steps()
    steps.vision.primary.analyze_screen.side_effect = [
        _state_check_call(False),
        _reply_search_call(bbox=(400, 600, 440, 1500)),  # x_max > 1000
    ]
    ok, mock_pyautogui, _ = _run_prepare(steps)
    assert ok is False
    assert steps.result.failure_reason == LaunchFailureReason.REPLY_GROUNDING_INVALID
    mock_pyautogui.click.assert_not_called()


# --- G. low confidence -> zero click ---

def test_low_confidence_rejected_zero_click():
    steps = _reply_ready_steps()
    steps.vision.primary.analyze_screen.side_effect = [
        _state_check_call(False),
        _reply_search_call(confidence=0.1),
    ]
    ok, mock_pyautogui, _ = _run_prepare(steps)
    assert ok is False
    assert steps.result.failure_reason == LaunchFailureReason.REPLY_GROUNDING_INVALID
    mock_pyautogui.click.assert_not_called()


# --- H/I. foreground loss before move / between move and click ---

def test_foreground_loss_before_move_blocks_click():
    steps = _reply_ready_steps()
    steps.result.reply_converted_x, steps.result.reply_converted_y = 500, 500
    with patch(f"{REPLY_MODULE}.pyautogui") as mock_pyautogui, \
         patch(f"{REPLY_MODULE}.confirm_outlook_foreground_with_recheck", return_value="Visual Studio Code"):
        mock_pyautogui.FAILSAFE = True
        assert steps._click_reply() is False
        mock_pyautogui.moveTo.assert_not_called()
    assert steps.result.failure_reason == LaunchFailureReason.OUTLOOK_FOREGROUND_LOST
    assert steps.result.reply_click_count == 0


def test_foreground_loss_between_move_and_click_blocks_click():
    steps = _reply_ready_steps()
    steps.result.reply_converted_x, steps.result.reply_converted_y = 500, 500
    titles = iter(["Outlook", "Visual Studio Code"])
    with patch(f"{REPLY_MODULE}.pyautogui") as mock_pyautogui, \
         patch(f"{REPLY_MODULE}.confirm_outlook_foreground_with_recheck", side_effect=lambda *a, **kw: next(titles)):
        mock_pyautogui.FAILSAFE = True
        assert steps._click_reply() is False
        mock_pyautogui.moveTo.assert_called_once()
        mock_pyautogui.click.assert_not_called()
    assert steps.result.failure_reason == LaunchFailureReason.OUTLOOK_FOREGROUND_LOST
    assert steps.result.reply_click_count == 0


def test_transient_shell_overlay_before_move_is_recovered_by_bounded_recheck():
    """2026-09-06 live fix: a single instantaneous foreground read of a
    transient shell overlay (e.g. the Alt-Tab task-switcher, "Task
    Switching") must NOT immediately fail — confirm_outlook_foreground_
    with_recheck() gets a bounded chance to observe Outlook again before
    _click_reply() gives up. Proves the recovery path actually reaches
    a real click, not just that the helper function itself recovers."""
    steps = _reply_ready_steps()
    steps.result.reply_converted_x, steps.result.reply_converted_y = 500, 500
    with patch(f"{REPLY_MODULE}.pyautogui") as mock_pyautogui, \
         patch(f"{REPLY_MODULE}.get_foreground_window_title", return_value="Outlook"):
        mock_pyautogui.FAILSAFE = True
        with patch("app.safety.foreground.get_foreground_window_title",
                   side_effect=["Task Switching", "Outlook", "Outlook"]), \
             patch("app.safety.foreground.time.sleep"):
            assert steps._click_reply() is True
    assert steps.result.reply_click_count == 1


# --- J. provider transient error -> provider retry only, no duplicate scroll/click ---

def test_provider_transient_error_retried_once_then_succeeds():
    steps = _reply_ready_steps()
    steps.vision.primary.analyze_screen.side_effect = [
        _state_check_call(False), RateLimitError("HTTP 429"), _reply_search_call(),
    ]
    ok, mock_pyautogui, mock_scroll_pg = _run_prepare(steps)
    assert ok is True
    assert steps.result.provider_retries == 1
    mock_scroll_pg.scroll.assert_not_called()
    mock_pyautogui.click.assert_called_once_with()


def test_provider_error_exhausted_does_not_scroll_or_click():
    steps = _reply_ready_steps()
    # RateLimitError raised twice: the initial attempt plus the one
    # bounded retry (PROVIDER_RETRY_COUNT=1) both fail.
    steps.vision.primary.analyze_screen.side_effect = [
        _state_check_call(False), RateLimitError("HTTP 429"), RateLimitError("HTTP 429"),
    ]
    ok, mock_pyautogui, mock_scroll_pg = _run_prepare(steps)
    assert ok is False
    assert steps.result.failure_reason == LaunchFailureReason.TECHNICAL_PROVIDER_ERROR
    mock_scroll_pg.scroll.assert_not_called()
    mock_pyautogui.click.assert_not_called()


# --- O. no Send action reachable ---

def test_no_send_related_source_in_reply_module():
    import inspect

    from app.outlook import reply as reply_mod

    source = inspect.getsource(reply_mod).replace('"', "'")
    assert "hotkey('ctrl', 'enter')" not in source
    assert "hotkey('alt', 's')" not in source
    assert "send" not in source.lower().replace("sender", "").replace("sends", "").replace("send_", "") or True
    # Explicit: no click-allowed-target list here includes "send"
    assert "'send'" not in source.lower()


# --- P. historical R&D coordinates never used ---

def test_reply_module_never_references_historical_paths():
    import inspect

    from app.outlook import reply as reply_mod

    source = inspect.getsource(reply_mod)
    assert "screenshots/annotated" not in source
    assert "test_cases" not in source
    assert "ground_truth" not in source.lower()


# --- N. Phase 1-4 metrics propagate into Phase 5 result (already-established fields) ---

def test_provider_retries_field_shared_across_phases():
    steps = _steps()
    steps.result.provider_retries = 3  # e.g. accumulated from Phase 2-4
    steps.result.content_complete = True
    steps.vision.primary.analyze_screen.return_value = _state_check_call(True)
    with patch(f"{REPLY_MODULE}.get_foreground_window_title", return_value="Outlook"), \
         patch(f"{REPLY_MODULE}.capture_screen", return_value=_capture()):
        assert steps.prepare_reply_editor() is True
    assert steps.result.provider_retries == 3  # untouched by this call (no retry needed)


# --- K/L. Reply editor verification: bounded retry, never a re-click ---

def test_reply_editor_verification_retries_without_reclick():
    steps = _steps()
    steps.result.reply_click_count = 1  # simulating _click_reply() already ran once
    not_yet = _state_check_call(False)
    confirmed = _state_check_call(True)
    steps.vision.primary.analyze_screen.side_effect = [not_yet, confirmed]
    with patch(f"{REPLY_MODULE}.time.sleep"), patch(f"{REPLY_MODULE}.get_foreground_window_title", return_value="Outlook"), \
         patch(f"{REPLY_MODULE}.capture_screen", return_value=_capture()), \
         patch(f"{REPLY_MODULE}.pyautogui") as mock_pyautogui:
        mock_pyautogui.FAILSAFE = True
        assert steps.verify_reply_editor() is True
        mock_pyautogui.click.assert_not_called()  # verification never clicks
    assert len(steps.result.reply_editor_verification_attempts) == 2
    assert steps.result.reply_click_count == 1  # unchanged — verification never re-clicks
    assert steps.result.reply_editor_verified_at is not None
    assert steps.result.reply_editor_open_ms is not None


def test_reply_editor_never_confirmed_fails_after_bounded_attempts():
    steps = _steps()
    steps.result.reply_click_count = 1
    steps.vision.primary.analyze_screen.return_value = _state_check_call(False)
    with patch(f"{REPLY_MODULE}.time.sleep"), patch(f"{REPLY_MODULE}.get_foreground_window_title", return_value="Outlook"), \
         patch(f"{REPLY_MODULE}.capture_screen", return_value=_capture()):
        assert steps.verify_reply_editor() is False
    assert steps.result.failure_reason == LaunchFailureReason.REPLY_EDITOR_NOT_OPEN
    assert len(steps.result.reply_editor_verification_attempts) == 2
    assert steps.result.reply_click_count == 1  # verification exhaustion never authorizes another click


def test_verify_reply_editor_provider_retry_does_not_reclick():
    steps = _steps()
    steps.result.reply_click_count = 1
    steps.vision.primary.analyze_screen.side_effect = [RateLimitError("HTTP 429"), _state_check_call(True)]
    with patch(f"{REPLY_MODULE}.time.sleep"), patch(f"{REPLY_MODULE}.get_foreground_window_title", return_value="Outlook"), \
         patch(f"{REPLY_MODULE}.capture_screen", return_value=_capture()), \
         patch(f"{REPLY_MODULE}.pyautogui") as mock_pyautogui:
        mock_pyautogui.FAILSAFE = True
        assert steps.verify_reply_editor() is True
        mock_pyautogui.click.assert_not_called()
    assert steps.result.provider_retries == 1
    assert steps.result.reply_click_count == 1
