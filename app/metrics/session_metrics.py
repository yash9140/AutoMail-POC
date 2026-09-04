"""RND-009A session-level metrics skeleton.

Fields only — nothing populates these yet except the UI/controller
wiring built in this stage (start_time on Start, safety_aborts on
Abort). Real Vision/PyAutoGUI/Send instrumentation is wired in later
stages, reusing the same accumulate-every-call discipline established
in RND-007B/RND-008 (no call site left invisible from the totals).
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class SessionMetrics(BaseModel):
    session_id: Optional[str] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    current_step: Optional[str] = None
    completed_steps: int = 0
    failed_step: Optional[str] = None

    vision_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost: float = 0.0
    total_latency_ms: float = 0.0  # sum of Vision call durations only
    # Pre-RND-009D hardening: TOTAL E2E WALL-CLOCK TIME, kept strictly
    # separate from total_latency_ms above — includes waits, Windows UI
    # interaction, mouse/keyboard execution, and stabilization delays, not
    # just the Vision API calls. Populated from the worker's own
    # steps.result.total_elapsed_ms via metrics_update, not computed here.
    total_elapsed_ms: Optional[float] = None

    retries: int = 0
    human_interventions: int = 0
    safety_aborts: int = 0

    # Send is the one action in this whole project that must never exceed
    # one per session. This counter exists now, at zero, specifically so a
    # future stage has somewhere to enforce that ceiling — not because
    # anything in RND-009A increments it (nothing does; Send is not wired
    # up yet, per this stage's explicit scope).
    send_click_count: int = 0
