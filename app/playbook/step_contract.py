"""Per-step contract representation.

Every app/outlook/*.py step function is preceded by its own module-level
STEP_DEFINITION constant describing its objective, preconditions, the
Vision question it asks (if any), the one physical action it's allowed
to take, its retry policy, and its failure classifications — a
structural, inspectable encoding of "the playbook decides what happens
next" rather than that decision being implicit in code control flow
alone.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.playbook.states import PlaybookState


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int
    wait_schedule_seconds: tuple[float, ...]
    allows_reclick: bool  # must be False for every verification-retry step

    def __post_init__(self) -> None:
        if len(self.wait_schedule_seconds) < self.max_attempts:
            raise ValueError("wait_schedule_seconds must have at least max_attempts entries")


# The single, no-retry policy for steps that perform exactly one physical
# action with no bounded-retry loop of their own (e.g. a one-shot click).
NO_RETRY = RetryPolicy(max_attempts=1, wait_schedule_seconds=(0.0,), allows_reclick=False)


@dataclass(frozen=True)
class StepDefinition:
    step_id: str
    objective: str
    expected_state: PlaybookState
    preconditions: tuple[str, ...]
    vision_requirement: str | None  # None => no Vision call in this step
    allowed_action: str  # "none" | "move+click" | "scroll" | "type" | "capture_only"
    success_condition: str
    retry_policy: RetryPolicy
    fallback_step_id: str | None
    failure_classifications: tuple[str, ...]
    metrics_key: str
