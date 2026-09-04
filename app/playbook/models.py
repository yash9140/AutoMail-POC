"""RND-009A playbook step model."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from app.playbook.states import PlaybookState


class PlaybookStep(BaseModel):
    step_id: int
    name: str
    expected_state: PlaybookState
    status: str = "IN_PROGRESS"  # IN_PROGRESS | COMPLETED | FAILED | ABORTED
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    failure_reason: Optional[str] = None
