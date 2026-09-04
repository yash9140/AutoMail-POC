"""Generic bounded N-attempt verification loop.

Extracts the shared shape of the "wait → fresh screenshot → Vision call
→ check pass/fail → on failure, wait again → re-check; NEVER a second
physical action" pattern used throughout this project (readiness
verification, email-open verification, reply-editor verification, and
— from Phase 7 — send verification).

Phase 1 scope note: the three already-hardened, already-tested verify
loops ported this phase (app/outlook/launch.py::verify_outlook_readiness,
app/outlook/find_email.py::verify_email_opened,
app/outlook/reply.py::verify_reply_editor) keep their exact original
method bodies rather than being rewritten to call this helper — a
mechanical rewrite of already-proven retry logic carries real regression
risk for no behavior change, which "no new behavior" in this phase
argues against. This helper exists so every NEW bounded-retry loop added
in later phases (message-list scroll-and-reground in Phase 3, Send
verification in Phase 7) is built on one shared, independently tested
implementation instead of copy-pasting a fourth near-identical loop.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Optional, TypeVar

T = TypeVar("T")


@dataclass
class VerificationOutcome:
    succeeded: bool
    attempts: int
    last_result: Optional[T] = None
    last_error: Optional[str] = None
    aborted: bool = False


def bounded_verify(
    attempt_fn: Callable[[int], T],
    success_predicate: Callable[[T], bool],
    max_attempts: int,
    wait_schedule_seconds: tuple[float, ...],
    check_abort: Optional[Callable[[], bool]] = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> VerificationOutcome:
    """Runs up to max_attempts of: wait (per wait_schedule_seconds[i]) →
    check_abort() (if given) → attempt_fn(attempt_number) → check
    success_predicate(result). Stops at the first success. Never retries
    via a second physical action — attempt_fn must be read-only (a
    capture + Vision call), never a click.

    len(wait_schedule_seconds) must be >= max_attempts; wait_schedule_seconds[i]
    is the wait BEFORE attempt i+1 (e.g. a longer initial wait, shorter
    retry waits, matching the existing project convention).
    """
    if len(wait_schedule_seconds) < max_attempts:
        raise ValueError("wait_schedule_seconds must have at least max_attempts entries")

    last_result: Optional[T] = None
    for attempt_number in range(1, max_attempts + 1):
        if check_abort is not None and check_abort():
            return VerificationOutcome(succeeded=False, attempts=attempt_number - 1, aborted=True)

        sleep_fn(wait_schedule_seconds[attempt_number - 1])

        if check_abort is not None and check_abort():
            return VerificationOutcome(succeeded=False, attempts=attempt_number - 1, aborted=True)

        try:
            result = attempt_fn(attempt_number)
        except Exception as exc:  # noqa: BLE001 — normalized into the outcome, never re-raised here
            return VerificationOutcome(
                succeeded=False, attempts=attempt_number, last_error=str(exc),
            )

        last_result = result
        if success_predicate(result):
            return VerificationOutcome(succeeded=True, attempts=attempt_number, last_result=result)

    return VerificationOutcome(succeeded=False, attempts=max_attempts, last_result=last_result)
