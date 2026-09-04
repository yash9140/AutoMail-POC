"""Unit tests for the RND-004 local evaluator (rnd/metrics/evaluator.py).
Pure logic, no network, no AI.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rnd.metrics.evaluator import (  # noqa: E402
    control_detected,
    evaluate_case,
    normalize_action,
    normalize_state,
)


def test_normalize_state_inbox():
    assert normalize_state("Inbox view with the message list visible, no individual message selected") == "inbox"


def test_normalize_state_email_open():
    assert normalize_state("Email viewing pane open with an email selected") == "email_open"


def test_normalize_state_reply_editor():
    assert normalize_state("Reply editor is open, compose pane visible below the email") == "reply_editor_open"


def test_normalize_state_dialog():
    assert normalize_state("A notification popup is shown over the inbox") == "dialog_or_overlay"


def test_normalize_state_unknown_when_no_keywords_match():
    assert normalize_state("Something indeterminate is on screen") == "unknown"


def test_normalize_state_known_ambiguity_inbox_wins_over_email_open_keywords():
    # Documented limitation: real Outlook layouts often show the message
    # list alongside the reading pane, so a description mentioning both
    # inbox-style and email-open-style keywords resolves to "inbox" by
    # rule order (checked first) — a real precision trade-off, not a bug
    # to silently fix, since the real Gemini responses seen so far (RND-003,
    # RND-004) keep screen_state free of this ambiguity in practice.
    assert normalize_state("Message list and inbox folder visible alongside the reading pane") == "inbox"


def test_normalize_action_click_reply():
    assert normalize_action("click", "Reply button") == "click_reply"


def test_normalize_action_click_send():
    assert normalize_action("click", "Send button") == "click_send"


def test_normalize_action_focus_reply_editor():
    assert normalize_action("focus", "reply text editor") == "focus_reply_editor"


def test_normalize_action_select_existing_email():
    assert normalize_action("select", "existing email in the list") == "select_existing_email"


def test_normalize_action_other_when_unrecognized():
    assert normalize_action("scroll", "the page") == "other"


def test_normalize_action_compose_describing_click_reply_purpose_is_not_focus_editor():
    # Regression test for a real RND-004 evaluator bug: models routinely
    # phrase the click_reply recommendation as "click Reply to compose a
    # reply" — "compose" here describes the *purpose*, not an already-open
    # editor. The original heuristic treated "compose" as a focus_reply_editor
    # signal, which flipped two genuinely-correct Gemini answers
    # (OUTLOOK-003, OUTLOOK-008) into false WRONG_NEXT_ACTION failures.
    assert normalize_action(
        "Click the Reply button to compose a reply to the currently opened email.", "Reply button"
    ) == "click_reply"
    assert normalize_action(
        "Click the Reply button in the reading pane to compose a response to the opened email.",
        "Reply button in email pane",
    ) == "click_reply"


def test_control_detected_reply():
    assert control_detected("Reply", ["Reply button (reading pane)", "Forward button"]) is True


def test_control_detected_absent():
    assert control_detected("Send", ["Reply button", "Forward button"]) is False


def test_control_detected_email_row():
    assert control_detected("email_row", ["An email row from Test Innomick showing subject text"]) is True


def test_control_detected_reply_all_not_confused_with_plain_reply():
    # Regression test for a real RND-004 evaluator bug: "Reply All" was
    # collapsing to the same generic "reply" keyword as plain "Reply",
    # so any response merely mentioning "Reply" (without ever saying
    # "Reply All") registered as having detected — and therefore
    # hallucinated, when Reply All was supposed to be absent — "Reply All".
    assert control_detected("Reply All", ["Reply button", "Forward button", "Reply icon (email header)"]) is False
    assert control_detected("Reply All", ["Reply All button", "Forward button"]) is True


def test_evaluate_case_all_correct():
    result = evaluate_case(
        expected_application="Microsoft Outlook",
        expected_state="email_open",
        expected_action="click_reply",
        expected_relevant_controls=["Reply"],
        predicted_application="Microsoft Outlook",
        predicted_state="Email viewing pane open with an email selected",
        predicted_relevant_controls=["Reply button", "Forward button"],
        predicted_action="click",
        predicted_target="Reply button",
    )
    assert result["application_correct"] is True
    assert result["state_correct"] is True
    assert result["controls_correct"] is True
    assert result["action_correct"] is True
    assert result["failure_types"] == []


def test_evaluate_case_wrong_state_and_action():
    result = evaluate_case(
        expected_application="Microsoft Outlook",
        expected_state="reply_editor_open",
        expected_action="click_send",
        expected_relevant_controls=["Send"],
        predicted_application="Microsoft Outlook",
        predicted_state="Inbox view, message list visible",
        predicted_relevant_controls=["Inbox list"],
        predicted_action="click",
        predicted_target="an email row",
    )
    assert result["state_correct"] is False
    assert result["controls_correct"] is False
    assert result["action_correct"] is False
    assert "STATE_CLASSIFICATION_ERROR" in result["failure_types"]
    assert "CONTROL_MISSED" in result["failure_types"]
    assert "WRONG_NEXT_ACTION" in result["failure_types"]


def test_evaluate_case_no_ground_truth_for_controls_yields_none_not_false():
    result = evaluate_case(
        expected_application="Microsoft Outlook",
        expected_state="inbox",
        expected_action=None,
        expected_relevant_controls=None,
        predicted_application="Microsoft Outlook",
        predicted_state="Inbox view with message list",
        predicted_relevant_controls=["Inbox list"],
        predicted_action="click",
        predicted_target="an email",
    )
    assert result["controls_correct"] is None
    assert result["action_correct"] is None
    assert "CONTROL_MISSED" not in result["failure_types"]
    assert "WRONG_NEXT_ACTION" not in result["failure_types"]


def test_evaluate_case_failed_prediction_yields_none_not_false():
    result = evaluate_case(
        expected_application="Microsoft Outlook",
        expected_state="inbox",
        expected_action="select_existing_email",
        expected_relevant_controls=["email_row"],
        predicted_application=None,
        predicted_state=None,
        predicted_relevant_controls=None,
        predicted_action=None,
        predicted_target=None,
    )
    assert result["application_correct"] is None
    assert result["state_correct"] is None
    assert result["controls_correct"] is None
    assert result["action_correct"] is None
    assert result["failure_types"] == []


def test_evaluate_case_hallucination_detected_against_known_absent_control():
    result = evaluate_case(
        expected_application="Microsoft Outlook",
        expected_state="email_open",
        expected_action="click_reply",
        expected_relevant_controls=["Reply"],
        predicted_application="Microsoft Outlook",
        predicted_state="Email viewing pane open with an email selected",
        predicted_relevant_controls=["Reply button", "Reply All button"],
        predicted_action="click",
        predicted_target="Reply button",
        known_absent_controls=["Reply All"],
    )
    assert "CONTROL_HALLUCINATED" in result["failure_types"]
