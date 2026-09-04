"""app/fallback/classifications.py unit tests."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.fallback.classifications import classify_terminal_state  # noqa: E402
from app.playbook.failure_reasons import LaunchFailureReason  # noqa: E402
from app.playbook.states import PlaybookState  # noqa: E402


def test_technical_provider_error_routes_to_provider_error():
    assert classify_terminal_state(LaunchFailureReason.TECHNICAL_PROVIDER_ERROR) == PlaybookState.PROVIDER_ERROR


def test_semantic_failure_routes_to_failed_safe():
    assert classify_terminal_state(LaunchFailureReason.TARGET_EMAIL_MISMATCH) == PlaybookState.FAILED_SAFE
    assert classify_terminal_state(LaunchFailureReason.EMAIL_GROUNDING_SIDEBAR_REJECTED) == PlaybookState.FAILED_SAFE
    assert classify_terminal_state(LaunchFailureReason.CONTENT_NOT_FULLY_READ) == PlaybookState.FAILED_SAFE


def test_none_reason_routes_to_failed_safe():
    assert classify_terminal_state(None) == PlaybookState.FAILED_SAFE


def test_unrecognized_reason_defaults_to_failed_safe():
    assert classify_terminal_state("SOME_UNKNOWN_REASON") == PlaybookState.FAILED_SAFE
