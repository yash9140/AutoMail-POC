"""RND-009D reply + draft models.

RND009DResult INHERITS RND009CResult (rather than the flat-copy pattern
used when RND009CResult was built over RND009BResult) — RND-009D is a
direct chained continuation of RND-009C's exact result shape plus new
fields for the reply/draft phases, and the chain is now deep enough
(4 stages) that a third full field-by-field duplication would be pure
noise. This is a deliberate, documented departure from the earlier
per-stage-own-model convention, not an oversight.

Reuses RND-007B's EmailUnderstandingResponse/ReplyGenerationResponse
and RND-006B's VerificationResponseV2 verbatim (same prompts, same
schemas) — only draft verification gets a new v2 schema (adds
reply_editor_open, which the original RND-007B response never exposed
as its own field).
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field, field_validator

from rnd.models.find_open_email import RND009CResult, StepMetrics
from rnd.models.grounding import GroundingResponse  # noqa: F401  (re-exported for convenience)
from rnd.models.outlook_launch import CallMetrics


class DraftVerificationResponseV2(BaseModel):
    reply_editor_open: bool
    semantic_match: bool
    detected_draft: str
    confidence: float
    reason: str = ""

    @field_validator("confidence")
    @classmethod
    def confidence_in_range(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"confidence {v} is outside the valid range [0.0, 1.0]")
        return v


class ReplyEditorVerificationAttempt(BaseModel):
    attempt_number: int  # 1 or 2 — max 2
    delay_seconds: float
    timestamp: Optional[str] = None
    screenshot: Optional[str] = None

    verified: Optional[bool] = None
    detected_state: Optional[str] = None
    confidence: Optional[float] = None
    reason: Optional[str] = None

    metrics: CallMetrics = CallMetrics()
    schema_valid: bool = False
    error: Optional[str] = None


class RND009DResult(RND009CResult):
    test_id: str = "RND009D-reply-draft"

    # --- Phase 5: email understanding ---
    email_understanding_summary: Optional[str] = None
    email_understanding_sender_intent: Optional[str] = None
    requires_reply: Optional[bool] = None
    email_understanding_requested_action: Optional[str] = None
    email_understanding_important_points: list[str] = Field(default_factory=list)
    email_understanding_confidence: Optional[float] = None
    email_understanding_metrics: CallMetrics = CallMetrics()

    # --- Phase 6-7: reply grounding + click ---
    reply_editor_already_open: Optional[bool] = None
    reply_grounding_target_raw: Optional[str] = None
    reply_raw_x: Optional[float] = None
    reply_raw_y: Optional[float] = None
    reply_converted_x: Optional[int] = None
    reply_converted_y: Optional[int] = None
    reply_coordinate_in_screen_bounds: Optional[bool] = None
    reply_grounding_metrics: CallMetrics = CallMetrics()

    foreground_before_reply_move: Optional[str] = None
    foreground_before_reply_click: Optional[str] = None
    reply_click_timestamp: Optional[str] = None
    reply_click_executed: bool = False

    # --- Phase 8: reply editor verification ---
    reply_editor_verification_attempts: list[ReplyEditorVerificationAttempt] = Field(default_factory=list)
    reply_editor_verified: Optional[bool] = None

    # --- Phase 9: draft generation ---
    draft_reply: Optional[str] = None
    draft_reasoning_summary: Optional[str] = None
    draft_generation_confidence: Optional[float] = None
    reply_generation_metrics: CallMetrics = CallMetrics()

    # --- Phase 10: draft quality gate (local, programmatic) ---
    draft_quality_passed: Optional[bool] = None
    draft_quality_notes: Optional[str] = None

    # --- Phase 11: controlled multiline typing ---
    typed_text: Optional[str] = None
    text_entry_executed: bool = False
    typing_result: str = "PENDING"  # PENDING | PASS | FAIL | ABORTED | ERROR
    typing_abort_stage: Optional[str] = None  # "before_write" | "before_enter"
    segment_index_aborted_at: Optional[int] = None
    segments_typed_before_abort: Optional[int] = None

    # --- Phase 12-13: post-typing stabilization ---
    foreground_before_draft_capture: Optional[str] = None
    post_typing_screenshot: Optional[str] = None

    # --- Phase 14: draft verification ---
    expected_draft: Optional[str] = None
    detected_draft: Optional[str] = None
    exact_match: Optional[bool] = None
    semantic_match: Optional[bool] = None
    draft_verification_reply_editor_open: Optional[bool] = None
    draft_verification_metrics: CallMetrics = CallMetrics()

    # --- Human final review (recorded, never gates DRAFT_READY, never
    # proceeds to Send even on yes) ---
    human_draft_confirmed: Optional[bool] = None

    # --- Step-level metrics (new steps beyond RND-009C's 4) ---
    understand_email_metrics: StepMetrics = StepMetrics()
    ground_reply_metrics: StepMetrics = StepMetrics()
    verify_reply_editor_metrics: StepMetrics = StepMetrics()
    generate_draft_metrics: StepMetrics = StepMetrics()
    verify_draft_metrics: StepMetrics = StepMetrics()
