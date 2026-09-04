"""Final POC playbook engine.

Enforces explicit, one-step-at-a-time state transitions. No Vision or
PyAutoGUI call happens anywhere in this file — it is pure state-model
logic. The engine is the single gate every automation worker must pass
through before advancing state, which is what makes the SENDING guard
meaningful: a worker cannot simply set a state variable directly, it
must ask the engine, and the engine enforces the rule from states.py.

fail(reason) routes to one of two terminal states — FAILED_SAFE
(semantic/grounding/verification/content/ambiguity failures) or
PROVIDER_ERROR (technical/network/timeout/auth/rate-limit failures) —
via app/fallback/classifications.py, so a provider outage is never
recorded the same way as a genuine "target not found."
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from app.fallback.classifications import classify_terminal_state
from app.playbook.models import PlaybookStep
from app.playbook.states import (
    SEND_REQUIRED_PREDECESSOR,
    SEND_STATE,
    TERMINAL_STATES,
    VALID_TRANSITIONS,
    PlaybookState,
)


class InvalidTransitionError(Exception):
    """Raised when a requested state transition is not permitted."""


class PlaybookEngine:
    def __init__(self) -> None:
        self.current_state: PlaybookState = PlaybookState.READY
        self.current_step: Optional[PlaybookStep] = None
        self.steps: list[PlaybookStep] = []
        self.send_approved: bool = False
        self.abort_requested: bool = False

    def start(self) -> PlaybookStep:
        """Advances READY -> LAUNCHING_OUTLOOK."""
        return self.advance(PlaybookState.LAUNCHING_OUTLOOK)

    def approve_send(self) -> None:
        """Records the explicit approval SENDING requires. Setting this
        flag alone does not move any state — advance() must still be
        called, and only from WAITING_FOR_SEND_APPROVAL."""
        self.send_approved = True

    def advance(self, target_state: PlaybookState) -> PlaybookStep:
        if self.abort_requested:
            raise InvalidTransitionError("Cannot advance: abort has been requested.")
        if self.current_state in TERMINAL_STATES:
            raise InvalidTransitionError(f"Cannot advance: current state {self.current_state} is terminal.")

        if target_state == SEND_STATE:
            if self.current_state != SEND_REQUIRED_PREDECESSOR:
                raise InvalidTransitionError(
                    f"SENDING is only reachable from {SEND_REQUIRED_PREDECESSOR}, "
                    f"not from {self.current_state}."
                )
            if not self.send_approved:
                raise InvalidTransitionError("SENDING requires explicit send approval (approve_send() first).")
        elif target_state in TERMINAL_STATES and target_state != PlaybookState.COMPLETED:
            pass  # any non-terminal state may transition directly to a failure/abort terminal
        else:
            allowed = VALID_TRANSITIONS.get(self.current_state, set())
            if target_state not in allowed:
                raise InvalidTransitionError(f"Invalid transition: {self.current_state} -> {target_state}.")

        now = datetime.now().isoformat()
        if self.current_step is not None and self.current_step.status == "IN_PROGRESS":
            self.current_step.status = "COMPLETED"
            self.current_step.completed_at = now

        step = PlaybookStep(
            step_id=len(self.steps) + 1,
            name=target_state.value,
            expected_state=target_state,
            status="IN_PROGRESS",
            started_at=now,
        )
        self.steps.append(step)
        self.current_step = step
        self.current_state = target_state
        return step

    def fail(self, reason: str) -> PlaybookStep:
        """Any non-terminal state may fail — bypasses the transition table
        by design, mirroring 'any state may transition to a terminal
        state'. Routes to FAILED_SAFE or PROVIDER_ERROR based on `reason`
        (see app/fallback/classifications.py) rather than a single
        generic FAILED, so technical outages are never conflated with
        semantic/grounding/verification/content failures."""
        terminal_state = classify_terminal_state(reason)
        now = datetime.now().isoformat()
        if self.current_step is not None and self.current_step.status == "IN_PROGRESS":
            self.current_step.status = "FAILED"
            self.current_step.failure_reason = reason
            self.current_step.completed_at = now
        self.current_state = terminal_state
        return self.current_step

    def abort(self) -> PlaybookStep:
        """Any non-terminal state may abort. Sets abort_requested so no
        further advance() call can succeed, even if something later tries."""
        self.abort_requested = True
        now = datetime.now().isoformat()
        if self.current_step is not None and self.current_step.status == "IN_PROGRESS":
            self.current_step.status = "ABORTED"
            self.current_step.completed_at = now
        self.current_state = PlaybookState.ABORTED
        return self.current_step

    def reset(self) -> None:
        self.__init__()  # noqa: PLC0203 — deliberate full-state reset
