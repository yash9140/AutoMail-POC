"""RND-007B contextual reply generation + draft entry models.

Five stages are kept as separate metrics, never collapsed into one score,
per instruction: (A) email understanding, (B) reply generation quality,
(C) text-entry execution, (D) vision draft verification, (E) human
verification.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field, field_validator


class EmailUnderstandingResponse(BaseModel):
    email_summary: str
    sender_intent: str
    requires_reply: bool
    requested_action: str
    important_points: list[str] = Field(default_factory=list)
    confidence: float

    @field_validator("confidence")
    @classmethod
    def confidence_in_range(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"confidence {v} is outside the valid range [0.0, 1.0]")
        return v


class ReplyGenerationResponse(BaseModel):
    draft_reply: str
    reasoning_summary: str
    confidence: float

    @field_validator("confidence")
    @classmethod
    def confidence_in_range(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"confidence {v} is outside the valid range [0.0, 1.0]")
        return v


class DraftVerificationResponse(BaseModel):
    semantic_match: bool
    detected_draft: str
    confidence: float
    reason: str

    @field_validator("confidence")
    @classmethod
    def confidence_in_range(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"confidence {v} is outside the valid range [0.0, 1.0]")
        return v


class ReplyQualityScores(BaseModel):
    """Human-scored, one PASS/FAIL per criterion — never averaged into a
    single number.
    """

    relevant: Optional[bool] = None
    accurate: Optional[bool] = None
    no_hallucination: Optional[bool] = None
    professional: Optional[bool] = None
    concise: Optional[bool] = None
    complete: Optional[bool] = None


class CallMetrics(BaseModel):
    latency_ms: Optional[float] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    estimated_cost: Optional[float] = None


class RND007BResult(BaseModel):
    test_id: str = "RND007B-reply"

    # Privacy / test email
    email_subject: Optional[str] = None
    email_body_summary_human: Optional[str] = None
    privacy_confirmed: bool = False

    pre_screenshot: Optional[str] = None
    foreground_before: Optional[str] = None

    # Stage A — email understanding
    email_understanding: Optional[EmailUnderstandingResponse] = None
    email_understanding_metrics: CallMetrics = Field(default_factory=CallMetrics)
    human_understanding_approved: Optional[bool] = None

    # Stage B — reply generation
    reply_generation: Optional[ReplyGenerationResponse] = None
    reply_generation_metrics: CallMetrics = Field(default_factory=CallMetrics)
    human_draft_approved: Optional[bool] = None

    # Reply-editor preparation
    reply_editor_state_before_typing: Optional[str] = None  # "already_open" | "clicked_reply"
    reply_editor_state_check_metrics: Optional[CallMetrics] = None
    grounding_metrics: Optional[CallMetrics] = None

    # Stage C — text entry
    typed_text: Optional[str] = None
    text_entry_executed: bool = False
    typing_result: str = "PENDING"  # PENDING | PASS | FAIL | ABORTED | ERROR

    # Mid-action foreground protection (RND-007B pre-RND-008 hardening):
    # populated only when typing_result == "ABORTED" with
    # failure_reason == "FOREGROUND_CHANGED_DURING_TYPING".
    segments_typed_before_abort: Optional[int] = None
    segment_index_aborted_at: Optional[int] = None
    abort_stage: Optional[str] = None  # "before_write" | "before_enter"

    # Stage D — vision draft verification
    post_screenshot: Optional[str] = None
    expected_draft: Optional[str] = None
    detected_draft: Optional[str] = None
    exact_match: Optional[bool] = None
    semantic_match: Optional[bool] = None
    vision_verification: Optional[bool] = None  # alias for semantic_match, kept explicit per spec field name
    draft_verification_metrics: CallMetrics = Field(default_factory=CallMetrics)

    # Stage E — human verification
    human_verification: Optional[bool] = None

    # Reply quality (human-scored)
    reply_quality: ReplyQualityScores = Field(default_factory=ReplyQualityScores)

    result: str = "PENDING"  # overall — PENDING | PASS | FAIL | ABORTED | ERROR
    failure_reason: Optional[str] = None
    notes: str = ""

    total_vision_calls: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_estimated_cost: float = 0.0
    total_latency_ms: float = 0.0
