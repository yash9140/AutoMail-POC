"""app/vision/verification.py::bounded_verify() unit tests."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.vision.verification import bounded_verify  # noqa: E402


def test_succeeds_on_first_attempt():
    calls = []

    def attempt(n):
        calls.append(n)
        return "ready"

    outcome = bounded_verify(
        attempt_fn=attempt, success_predicate=lambda r: r == "ready",
        max_attempts=2, wait_schedule_seconds=(1.0, 1.0), sleep_fn=lambda s: None,
    )
    assert outcome.succeeded is True
    assert outcome.attempts == 1
    assert calls == [1]


def test_retries_once_before_succeeding():
    results = iter(["not_ready", "ready"])

    outcome = bounded_verify(
        attempt_fn=lambda n: next(results), success_predicate=lambda r: r == "ready",
        max_attempts=2, wait_schedule_seconds=(1.5, 1.0), sleep_fn=lambda s: None,
    )
    assert outcome.succeeded is True
    assert outcome.attempts == 2


def test_exhausts_bounded_attempts_without_a_third_try():
    call_count = 0

    def attempt(n):
        nonlocal call_count
        call_count += 1
        return "not_ready"

    outcome = bounded_verify(
        attempt_fn=attempt, success_predicate=lambda r: r == "ready",
        max_attempts=2, wait_schedule_seconds=(1.5, 1.0), sleep_fn=lambda s: None,
    )
    assert outcome.succeeded is False
    assert outcome.attempts == 2
    assert call_count == 2  # never a third attempt


def test_abort_checked_before_and_after_each_wait():
    # check_abort() is called twice per attempt (before AND after the
    # wait, mirroring the existing double-check pattern already used by
    # e.g. verify_outlook_readiness()). Letting the first two calls
    # (attempt 1's own before/after-wait checks) pass allows attempt 1
    # to run; the third call (attempt 2's before-wait check) aborts
    # before a second attempt ever executes.
    abort_after = {"n": 0}

    def check_abort():
        abort_after["n"] += 1
        return abort_after["n"] > 2

    calls = []
    outcome = bounded_verify(
        attempt_fn=lambda n: calls.append(n), success_predicate=lambda r: False,
        max_attempts=3, wait_schedule_seconds=(1.0, 1.0, 1.0), check_abort=check_abort, sleep_fn=lambda s: None,
    )
    assert outcome.aborted is True
    assert len(calls) == 1  # exactly attempt 1 ran before abort stopped further attempts


def test_wait_schedule_shorter_than_max_attempts_raises():
    with pytest.raises(ValueError):
        bounded_verify(
            attempt_fn=lambda n: None, success_predicate=lambda r: True,
            max_attempts=3, wait_schedule_seconds=(1.0, 1.0), sleep_fn=lambda s: None,
        )


def test_exception_in_attempt_fn_is_captured_not_raised():
    def attempt(n):
        raise RuntimeError("provider exploded")

    outcome = bounded_verify(
        attempt_fn=attempt, success_predicate=lambda r: True,
        max_attempts=1, wait_schedule_seconds=(1.0,), sleep_fn=lambda s: None,
    )
    assert outcome.succeeded is False
    assert "provider exploded" in outcome.last_error
