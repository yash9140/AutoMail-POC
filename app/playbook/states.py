"""Final POC playbook state vocabulary and transition rules.

This is the single source of truth for which state transitions are
valid. The engine (engine.py) never hardcodes a transition rule itself
— it always consults VALID_TRANSITIONS / the dedicated SENDING guard
defined here, so the safety-critical rule ("SENDING is only reachable
from WAITING_FOR_SEND_APPROVAL, and only after explicit approval") has
exactly one place it could be weakened, making any future change to it
easy to spot in review.

Reconciled from the RND-009A/B/C/D state list (READY..COMPLETED/FAILED/
ABORTED) for the final POC:
  - OUTLOOK_VERIFIED -> OUTLOOK_READY (reached only after maximize
    enforcement passes, from Phase 2 onward — no separate MAXIMIZING
    state; maximize-enforcement is a documented sub-step of reaching
    this one state, not a new externally-visible state).
  - READING_EMAIL, FINDING_REPLY added (bounded scroll/accumulation
    live inside these, from Phase 3/4/5).
  - REPLYING -> REPLY_EDITOR_OPEN.
  - TYPING_DRAFT, VERIFYING_DRAFT added (previously implicit within the
    GENERATING_DRAFT -> DRAFT_READY gap).
  - FAILED -> FAILED_SAFE, split from a NEW PROVIDER_ERROR terminal —
    a technical/network/timeout/auth/rate-limit failure is never
    conflated with a semantic/grounding/verification/content failure.
"""

from __future__ import annotations

from enum import Enum


class PlaybookState(str, Enum):
    READY = "READY"
    LAUNCHING_OUTLOOK = "LAUNCHING_OUTLOOK"
    OUTLOOK_READY = "OUTLOOK_READY"
    FINDING_EMAIL = "FINDING_EMAIL"
    EMAIL_OPENED = "EMAIL_OPENED"
    READING_EMAIL = "READING_EMAIL"
    FINDING_REPLY = "FINDING_REPLY"
    REPLY_EDITOR_OPEN = "REPLY_EDITOR_OPEN"
    GENERATING_DRAFT = "GENERATING_DRAFT"
    TYPING_DRAFT = "TYPING_DRAFT"
    VERIFYING_DRAFT = "VERIFYING_DRAFT"
    DRAFT_READY = "DRAFT_READY"
    WAITING_FOR_SEND_APPROVAL = "WAITING_FOR_SEND_APPROVAL"
    SENDING = "SENDING"
    VERIFYING_SEND = "VERIFYING_SEND"
    COMPLETED = "COMPLETED"
    FAILED_SAFE = "FAILED_SAFE"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    ABORTED = "ABORTED"


# The normal, linear happy-path order. Each state may advance ONLY to the
# next state in this list (plus a terminal state from anywhere
# non-terminal, handled separately in the engine) — no skipping ahead,
# no going back.
LINEAR_ORDER: list[PlaybookState] = [
    PlaybookState.READY,
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
    PlaybookState.WAITING_FOR_SEND_APPROVAL,
    PlaybookState.SENDING,
    PlaybookState.VERIFYING_SEND,
    PlaybookState.COMPLETED,
]

VALID_TRANSITIONS: dict[PlaybookState, set[PlaybookState]] = {
    LINEAR_ORDER[i]: {LINEAR_ORDER[i + 1]} for i in range(len(LINEAR_ORDER) - 1)
}

TERMINAL_STATES: set[PlaybookState] = {
    PlaybookState.COMPLETED,
    PlaybookState.FAILED_SAFE,
    PlaybookState.PROVIDER_ERROR,
    PlaybookState.ABORTED,
}

# The one state in this whole machine with an extra, non-negotiable guard:
# reachable ONLY from WAITING_FOR_SEND_APPROVAL, and even then only if the
# engine's send_approved flag has been explicitly set. Unweakened from the
# RND-009A design.
SEND_STATE = PlaybookState.SENDING
SEND_REQUIRED_PREDECESSOR = PlaybookState.WAITING_FOR_SEND_APPROVAL
