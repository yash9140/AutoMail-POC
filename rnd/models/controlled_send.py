"""RND-008 controlled Send + Send verification models.

Send is the first externally-visible, irreversible action in this POC.
Every metric that could disagree is kept separate, never collapsed —
per instruction: send_click_result, ai_send_verification, and
human_send_verification are three independent fields, and the final
classification (SEND_CONFIRMED / SEND_SUCCEEDED_AI_FALSE_NEGATIVE /
SEND_VERIFIED_AFTER_STABILIZATION / SEND_NOT_CONFIRMED) is derived
from their combination, not a re-labeling of any single one of them.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field, field_validator


class CallMetrics(BaseModel):
    latency_ms: Optional[float] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    estimated_cost: Optional[float] = None


class SendVerificationResponse(BaseModel):
    verified_sent: bool
    detected_state: str
    visual_evidence: list[str] = Field(default_factory=list)
    confidence: float
    reason: str

    @field_validator("confidence")
    @classmethod
    def confidence_in_range(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"confidence {v} is outside the valid range [0.0, 1.0]")
        return v


class PostSendVerificationAttempt(BaseModel):
    attempt_number: int  # 1 or 2 — max 2 per instruction
    delay_seconds: float
    timestamp: Optional[str] = None
    screenshot: Optional[str] = None

    verified_sent: Optional[bool] = None
    detected_state: Optional[str] = None
    visual_evidence: list[str] = Field(default_factory=list)
    confidence: Optional[float] = None
    reason: Optional[str] = None

    metrics: CallMetrics = Field(default_factory=CallMetrics)
    schema_valid: bool = False
    error: Optional[str] = None


class RND008Result(BaseModel):
    test_id: str = "RND008-send"

    # Test email context (carried forward from RND-007B's approved
    # understanding + draft — not re-derived from scratch)
    email_sender: Optional[str] = None
    email_subject: Optional[str] = None
    email_body_summary: Optional[str] = None
    approved_draft: Optional[str] = None
    human_email_approved: Optional[bool] = None

    foreground_before_capture: Optional[str] = None

    # Draft verification gate (must pass human approval regardless of
    # vision_match — vision failing does not block a human PASS, and
    # vision passing does not substitute for human approval)
    pre_send_screenshot: Optional[str] = None
    expected_draft: Optional[str] = None
    detected_draft: Optional[str] = None
    vision_match: Optional[bool] = None
    draft_verification_metrics: CallMetrics = Field(default_factory=CallMetrics)
    human_draft_confirmed: Optional[bool] = None

    # Send grounding
    grounding_screenshot: Optional[str] = None
    foreground_before_grounding: Optional[str] = None
    screen_width: Optional[int] = None
    screen_height: Optional[int] = None
    grounding_target_raw: Optional[str] = None  # the target string Gemini returned
    raw_x: Optional[float] = None
    raw_y: Optional[float] = None
    converted_x: Optional[int] = None
    converted_y: Optional[int] = None
    grounding_confidence: Optional[float] = None
    grounding_reason: Optional[str] = None
    ground_truth_bbox: Optional[str] = None  # human-readable, e.g. "(800,1022)-(902,1060)"
    coordinate_inside_bbox: Optional[bool] = None
    coordinate_in_screen_bounds: Optional[bool] = None
    grounding_metrics: CallMetrics = Field(default_factory=CallMetrics)

    # Final, immediate pre-click approval
    human_send_approved: Optional[bool] = None
    approval_timestamp: Optional[str] = None

    # Send execution
    foreground_before_move: Optional[str] = None
    foreground_before_click: Optional[str] = None
    send_click_executed: bool = False
    send_click_timestamp: Optional[str] = None
    send_click_result: str = "PENDING"  # PENDING | PASS | ABORTED | ERROR

    # Post-send verification (max 2 attempts, never triggers a re-click)
    post_send_attempts: list[PostSendVerificationAttempt] = Field(default_factory=list)
    ai_send_verification: Optional[bool] = None  # True if any attempt reported verified_sent True
    ai_send_verification_classification: Optional[str] = None
    # one of: AI_VERIFIED_FIRST_ATTEMPT | AI_VERIFIED_AFTER_STABILIZATION |
    #         AI_NOT_VERIFIED | NOT_ATTEMPTED

    # Human verification — authoritative
    human_send_verification: Optional[bool] = None

    # Combined, never-collapsed final classification
    final_classification: Optional[str] = None
    # one of: SEND_CONFIRMED | SEND_SUCCEEDED_AI_FALSE_NEGATIVE |
    #         SEND_VERIFIED_AFTER_STABILIZATION | SEND_NOT_CONFIRMED

    result: str = "PENDING"  # PENDING | PASS | FAIL | ABORTED | ERROR
    failure_reason: Optional[str] = None
    abort_stage: Optional[str] = None
    send_was_executed_at_abort: Optional[bool] = None
    notes: str = ""

    total_vision_calls: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_estimated_cost: float = 0.0
    total_latency_ms: float = 0.0
