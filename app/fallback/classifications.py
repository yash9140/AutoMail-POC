"""Maps a failure_reason string to which terminal PlaybookState it routes
to — PROVIDER_ERROR for technical/network/timeout/auth/rate-limit
failures, FAILED_SAFE for everything else (semantic/grounding/
verification/content/ambiguity failures). Kept as one small lookup so
the routing rule has exactly one place it could be weakened, mirroring
the SENDING-guard discipline in app/playbook/engine.py.
"""

from __future__ import annotations

from typing import Optional

from app.playbook.failure_reasons import PROVIDER_ERROR_REASONS
from app.playbook.states import PlaybookState


def classify_terminal_state(failure_reason: Optional[str]) -> PlaybookState:
    if failure_reason in PROVIDER_ERROR_REASONS:
        return PlaybookState.PROVIDER_ERROR
    return PlaybookState.FAILED_SAFE
