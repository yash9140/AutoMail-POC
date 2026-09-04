"""app/playbook/context.py::PlaybookContext unit tests."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.playbook.context import PlaybookContext  # noqa: E402
from app.playbook.states import PlaybookState  # noqa: E402


def test_target_sender_required():
    with pytest.raises(ValueError):
        PlaybookContext(session_id="s1", target_sender="")


def test_target_sender_required_rejects_whitespace_only():
    with pytest.raises(ValueError):
        PlaybookContext(session_id="s1", target_sender="   ")


def test_target_subject_optional_defaults_to_none():
    ctx = PlaybookContext(session_id="s1", target_sender="Yash")
    assert ctx.target_subject is None
    assert ctx.target_sender == "Yash"


def test_target_subject_can_be_provided():
    ctx = PlaybookContext(session_id="s1", target_sender="Yash", target_subject="Testing the poc")
    assert ctx.target_subject == "Testing the poc"


def test_fresh_context_has_no_stale_state():
    ctx1 = PlaybookContext(session_id="s1", target_sender="Yash")
    ctx1.email_found = True
    ctx1.message_list_scroll_count = 3

    ctx2 = PlaybookContext(session_id="s2", target_sender="Someone Else")
    assert ctx2.email_found is False
    assert ctx2.message_list_scroll_count == 0
    assert ctx2.current_state == PlaybookState.READY


def test_default_fields():
    ctx = PlaybookContext(session_id="s1", target_sender="Yash")
    assert ctx.candidate_count == 0
    assert ctx.candidates_ambiguous is False
    assert ctx.email_sections == []
    assert ctx.send_executed is False
    assert ctx.send_verified is False
    assert ctx.abort_requested is False
