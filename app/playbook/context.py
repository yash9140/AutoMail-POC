"""Session-scoped playbook context.

Instantiated fresh once per run (never a module-level singleton — a
stale value left over from a previous run must never leak into a new
one). Phase 1 scope: declared now as the target-config carrier
(target_sender required, target_subject optional — see
docs/poc/05_CONFIGURATION.md) and future home for cross-step state;
not yet threaded through app/outlook/*.py, which happens when
app/playbook/playbook.py (the unified orchestrator) is built in Phase 5.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from app.metrics.session_metrics import SessionMetrics
from app.playbook.states import PlaybookState
from app.vision.models import EmailSection


@dataclass
class PlaybookContext:
    session_id: str
    target_sender: str  # required — Start Automation is disabled without this
    target_subject: Optional[str] = None  # optional

    current_state: PlaybookState = PlaybookState.READY

    outlook_ready: bool = False

    email_found: bool = False
    candidate_count: int = 0
    candidates_ambiguous: bool = False
    email_open: bool = False

    email_sections: list[EmailSection] = field(default_factory=list)
    email_understanding: Optional[dict] = None

    reply_editor_open: bool = False
    draft_reply: Optional[str] = None
    draft_verified: bool = False

    send_executed: bool = False
    send_verified: bool = False

    message_list_scroll_count: int = 0
    email_body_scroll_count: int = 0
    reply_search_scroll_count: int = 0

    abort_requested: bool = False

    metrics: SessionMetrics = field(default_factory=SessionMetrics)

    def __post_init__(self) -> None:
        if not self.target_sender or not self.target_sender.strip():
            raise ValueError("PlaybookContext.target_sender is required and must be non-empty.")
