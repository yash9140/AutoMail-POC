"""Final POC playbook engine/state-machine tests. Pure state-model logic
— no UI, no Vision, no PyAutoGUI, no real Outlook launch anywhere here.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.metrics.session_metrics import SessionMetrics  # noqa: E402
from app.playbook.engine import InvalidTransitionError, PlaybookEngine  # noqa: E402
from app.playbook.failure_reasons import LaunchFailureReason  # noqa: E402
from app.playbook.states import PlaybookState  # noqa: E402
from app.safety.abort_controller import AbortController  # noqa: E402

FULL_PATH_TO_DRAFT_READY = (
    PlaybookState.LAUNCHING_OUTLOOK,
    PlaybookState.OUTLOOK_READY,
    PlaybookState.FINDING_EMAIL,
    PlaybookState.EMAIL_OPENED,
    PlaybookState.READING_EMAIL,
    PlaybookState.FINDING_REPLY,
    PlaybookState.REPLY_EDITOR_OPEN,
    PlaybookState.GENERATING_DRAFT,
    PlaybookState.TYPING_DRAFT,
    PlaybookState.VERIFYING_DRAFT,
    PlaybookState.DRAFT_READY,
)


def test_complete_state_path_ready_to_draft_ready():
    engine = PlaybookEngine()
    for state in FULL_PATH_TO_DRAFT_READY:
        step = engine.advance(state)
        assert step.expected_state == state
    assert engine.current_state == PlaybookState.DRAFT_READY

    # DRAFT_READY must not be able to jump straight to SENDING —
    # WAITING_FOR_SEND_APPROVAL is required in between, and even then
    # only with explicit approve_send().
    with pytest.raises(InvalidTransitionError):
        engine.advance(PlaybookState.SENDING)
    assert engine.current_state == PlaybookState.DRAFT_READY


def test_complete_state_path_ready_to_email_opened():
    engine = PlaybookEngine()
    for state in (
        PlaybookState.LAUNCHING_OUTLOOK,
        PlaybookState.OUTLOOK_READY,
        PlaybookState.FINDING_EMAIL,
        PlaybookState.EMAIL_OPENED,
    ):
        step = engine.advance(state)
        assert step.expected_state == state
    assert engine.current_state == PlaybookState.EMAIL_OPENED


def test_complete_state_path_draft_ready_to_completed_with_send_approval():
    engine = PlaybookEngine()
    for state in FULL_PATH_TO_DRAFT_READY:
        engine.advance(state)
    engine.advance(PlaybookState.WAITING_FOR_SEND_APPROVAL)
    engine.approve_send()
    for state in (PlaybookState.SENDING, PlaybookState.VERIFYING_SEND, PlaybookState.COMPLETED):
        step = engine.advance(state)
        assert step.expected_state == state
    assert engine.current_state == PlaybookState.COMPLETED


def test_playbook_starts_in_ready():
    engine = PlaybookEngine()
    assert engine.current_state == PlaybookState.READY
    assert engine.current_step is None
    assert engine.send_approved is False
    assert engine.abort_requested is False


def test_start_moves_ready_to_launching_outlook():
    engine = PlaybookEngine()
    step = engine.start()
    assert engine.current_state == PlaybookState.LAUNCHING_OUTLOOK
    assert step.expected_state == PlaybookState.LAUNCHING_OUTLOOK
    assert step.status == "IN_PROGRESS"
    assert step.started_at is not None


def test_invalid_transition_rejected():
    engine = PlaybookEngine()
    # READY may only advance to LAUNCHING_OUTLOOK, never straight to EMAIL_OPENED
    with pytest.raises(InvalidTransitionError):
        engine.advance(PlaybookState.EMAIL_OPENED)
    assert engine.current_state == PlaybookState.READY  # unchanged on rejection


def test_ready_to_sending_rejected():
    engine = PlaybookEngine()
    with pytest.raises(InvalidTransitionError):
        engine.advance(PlaybookState.SENDING)
    assert engine.current_state == PlaybookState.READY


def test_draft_ready_to_sending_rejected():
    engine = PlaybookEngine()
    for state in FULL_PATH_TO_DRAFT_READY:
        engine.advance(state)
    assert engine.current_state == PlaybookState.DRAFT_READY

    with pytest.raises(InvalidTransitionError):
        engine.advance(PlaybookState.SENDING)  # skips WAITING_FOR_SEND_APPROVAL entirely
    assert engine.current_state == PlaybookState.DRAFT_READY


def test_sending_requires_correct_predecessor_and_explicit_approval():
    engine = PlaybookEngine()
    for state in (*FULL_PATH_TO_DRAFT_READY, PlaybookState.WAITING_FOR_SEND_APPROVAL):
        engine.advance(state)
    assert engine.current_state == PlaybookState.WAITING_FOR_SEND_APPROVAL

    # Correct predecessor, but no explicit approval yet — still rejected.
    with pytest.raises(InvalidTransitionError):
        engine.advance(PlaybookState.SENDING)
    assert engine.current_state == PlaybookState.WAITING_FOR_SEND_APPROVAL

    # Only after approve_send() does the SAME transition succeed.
    engine.approve_send()
    step = engine.advance(PlaybookState.SENDING)
    assert engine.current_state == PlaybookState.SENDING
    assert step.expected_state == PlaybookState.SENDING


def test_sending_rejected_even_with_approval_from_wrong_predecessor():
    engine = PlaybookEngine()
    engine.advance(PlaybookState.LAUNCHING_OUTLOOK)
    engine.approve_send()  # approval alone is not enough without the right predecessor state
    with pytest.raises(InvalidTransitionError):
        engine.advance(PlaybookState.SENDING)
    assert engine.current_state == PlaybookState.LAUNCHING_OUTLOOK


def test_abort_moves_to_aborted():
    engine = PlaybookEngine()
    engine.start()
    step = engine.abort()
    assert engine.current_state == PlaybookState.ABORTED
    assert engine.abort_requested is True
    assert step.status == "ABORTED"


def test_state_cannot_advance_after_abort():
    engine = PlaybookEngine()
    engine.start()
    engine.abort()
    with pytest.raises(InvalidTransitionError):
        engine.advance(PlaybookState.OUTLOOK_READY)
    assert engine.current_state == PlaybookState.ABORTED


def test_fail_with_semantic_reason_routes_to_failed_safe():
    engine = PlaybookEngine()
    engine.start()
    step = engine.fail(LaunchFailureReason.TARGET_EMAIL_MISMATCH)
    assert engine.current_state == PlaybookState.FAILED_SAFE
    assert step.status == "FAILED"
    assert step.failure_reason == LaunchFailureReason.TARGET_EMAIL_MISMATCH


def test_fail_with_provider_reason_routes_to_provider_error():
    engine = PlaybookEngine()
    engine.start()
    step = engine.fail(LaunchFailureReason.TECHNICAL_PROVIDER_ERROR)
    assert engine.current_state == PlaybookState.PROVIDER_ERROR
    assert step.status == "FAILED"
    assert step.failure_reason == LaunchFailureReason.TECHNICAL_PROVIDER_ERROR


def test_fail_with_unrecognized_reason_defaults_to_failed_safe():
    engine = PlaybookEngine()
    engine.start()
    engine.fail("some ad-hoc reason string")
    assert engine.current_state == PlaybookState.FAILED_SAFE


def test_cannot_advance_from_terminal_state():
    engine = PlaybookEngine()
    engine.start()
    engine.fail(LaunchFailureReason.TARGET_EMAIL_MISMATCH)
    with pytest.raises(InvalidTransitionError):
        engine.advance(PlaybookState.OUTLOOK_READY)


def test_reset_returns_engine_to_fresh_ready_state():
    engine = PlaybookEngine()
    engine.start()
    engine.approve_send()
    engine.reset()
    assert engine.current_state == PlaybookState.READY
    assert engine.current_step is None
    assert engine.send_approved is False
    assert engine.abort_requested is False


def test_send_click_count_starts_at_zero():
    metrics = SessionMetrics()
    assert metrics.send_click_count == 0


def test_session_metrics_defaults():
    metrics = SessionMetrics()
    assert metrics.completed_steps == 0
    assert metrics.vision_calls == 0
    assert metrics.retries == 0
    assert metrics.human_interventions == 0
    assert metrics.safety_aborts == 0


def test_abort_controller_default_and_request():
    controller = AbortController()
    assert controller.is_abort_requested() is False
    controller.request_abort()
    assert controller.is_abort_requested() is True
    controller.reset()
    assert controller.is_abort_requested() is False
