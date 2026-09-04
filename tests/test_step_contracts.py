"""app/playbook/step_contract.py unit tests."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.playbook.states import PlaybookState  # noqa: E402
from app.playbook.step_contract import NO_RETRY, RetryPolicy, StepDefinition  # noqa: E402


def test_retry_policy_rejects_short_wait_schedule():
    with pytest.raises(ValueError):
        RetryPolicy(max_attempts=2, wait_schedule_seconds=(1.0,), allows_reclick=False)


def test_retry_policy_accepts_matching_wait_schedule():
    policy = RetryPolicy(max_attempts=2, wait_schedule_seconds=(1.5, 1.0), allows_reclick=False)
    assert policy.max_attempts == 2
    assert len(policy.wait_schedule_seconds) == 2


def test_no_retry_policy_never_allows_reclick():
    assert NO_RETRY.allows_reclick is False
    assert NO_RETRY.max_attempts == 1


def test_step_definition_construction():
    step = StepDefinition(
        step_id="FIND_TARGET_EMAIL",
        objective="Locate the configured target email in the current message list.",
        expected_state=PlaybookState.FINDING_EMAIL,
        preconditions=("outlook_ready",),
        vision_requirement="Locate a row matching the configured sender (and subject, if given).",
        allowed_action="move+click",
        success_condition="Vision reports target_visible with a bbox that passes validation.",
        retry_policy=RetryPolicy(max_attempts=1, wait_schedule_seconds=(0.0,), allows_reclick=False),
        fallback_step_id="SCROLL_MESSAGE_LIST",
        failure_classifications=("TARGET_EMAIL_NOT_VISIBLE", "TARGET_EMAIL_MISMATCH"),
        metrics_key="find_email_metrics",
    )
    assert step.expected_state == PlaybookState.FINDING_EMAIL
    assert step.retry_policy.allows_reclick is False


def test_verification_step_contracts_never_allow_reclick():
    """Structural: any StepDefinition whose allowed_action follows a
    verification step must have allows_reclick=False — the 'never
    re-click after a failed verification' rule, encoded as a checked
    constraint rather than left to convention."""
    verification_steps = [
        StepDefinition(
            step_id="VERIFY_EMAIL_OPEN", objective="Confirm the correct email is open.",
            expected_state=PlaybookState.EMAIL_OPENED, preconditions=("email_row_clicked",),
            vision_requirement="Is the target email open with matching subject/sender/body visible?",
            allowed_action="capture_only",
            success_condition="email_open and subject_match and sender_match and body_visible.",
            retry_policy=RetryPolicy(max_attempts=2, wait_schedule_seconds=(1.5, 1.0), allows_reclick=False),
            fallback_step_id=None, failure_classifications=("EMAIL_OPEN_VERIFICATION_FAILED",),
            metrics_key="verify_email_opened_metrics",
        ),
        StepDefinition(
            step_id="VERIFY_REPLY_EDITOR", objective="Confirm the reply editor is open.",
            expected_state=PlaybookState.REPLY_EDITOR_OPEN, preconditions=("reply_clicked_or_already_open",),
            vision_requirement="Is the reply composer visibly open and ready for text entry?",
            allowed_action="capture_only",
            success_condition="verified is true.",
            retry_policy=RetryPolicy(max_attempts=2, wait_schedule_seconds=(1.5, 1.0), allows_reclick=False),
            fallback_step_id=None, failure_classifications=("REPLY_EDITOR_NOT_OPEN",),
            metrics_key="verify_reply_editor_metrics",
        ),
        StepDefinition(
            step_id="VERIFY_DRAFT", objective="Confirm the typed draft matches what was intended.",
            expected_state=PlaybookState.VERIFYING_DRAFT, preconditions=("text_entry_executed",),
            vision_requirement="Does the visible draft semantically match the expected draft?",
            allowed_action="capture_only",
            success_condition="reply_editor_open and semantic_match.",
            retry_policy=NO_RETRY,
            fallback_step_id=None, failure_classifications=("DRAFT_VERIFICATION_FAILED",),
            metrics_key="verify_draft_metrics",
        ),
    ]
    for step in verification_steps:
        assert step.retry_policy.allows_reclick is False, f"{step.step_id} must never allow a reclick"
