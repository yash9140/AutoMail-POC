"""RND-009C find + open email models.

Reuses the RND-009B Vision response schemas verbatim for the launch +
readiness phases (WindowsSearchGroundingResponse,
OutlookLaunchVerificationResponse, OutlookReadinessAttempt, CallMetrics)
since RND-009C runs that exact same logic first — only the new
FIND_EMAIL / VERIFY_EMAIL_OPENED phases get new schemas here.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field, field_validator

from rnd.models.outlook_launch import CallMetrics, OutlookReadinessAttempt


class EmailGroundingResponse(BaseModel):
    """Vision's role is strictly 'find the email row matching this exact
    target' — never 'which email is interesting.' target_subject/
    target_sender are supplied BY the playbook into the prompt; Vision
    only reports whether what it sees matches them."""

    target_visible: bool
    matched_subject: str = ""
    matched_sender: str = ""
    box_2d: Optional[list[float]] = None  # [y_min, x_min, y_max, x_max], 0-1000 normalized
    x: Optional[float] = None
    y: Optional[float] = None
    confidence: float
    reason: str = ""

    @field_validator("confidence")
    @classmethod
    def confidence_in_range(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"confidence {v} is outside the valid range [0.0, 1.0]")
        return v


class EmailOpenVerificationResponse(BaseModel):
    email_open: bool
    subject_detected: str = ""
    sender_detected: str = ""
    subject_match: bool
    sender_match: bool
    body_visible: bool
    confidence: float
    reason: str = ""

    @field_validator("confidence")
    @classmethod
    def confidence_in_range(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"confidence {v} is outside the valid range [0.0, 1.0]")
        return v


class EmailOpenVerificationAttempt(BaseModel):
    attempt_number: int  # 1 or 2 — max 2, mirrors the readiness-attempt pattern
    delay_seconds: float
    timestamp: Optional[str] = None
    screenshot: Optional[str] = None

    email_open: Optional[bool] = None
    subject_detected: Optional[str] = None
    sender_detected: Optional[str] = None
    subject_match: Optional[bool] = None
    sender_match: Optional[bool] = None
    body_visible: Optional[bool] = None
    confidence: Optional[float] = None
    reason: Optional[str] = None

    metrics: CallMetrics = CallMetrics()
    schema_valid: bool = False
    error: Optional[str] = None


class StepMetrics(BaseModel):
    """Aggregate metrics for one named playbook step — kept separate per
    step (never merged into one grand total alone) so latency/cost can be
    attributed to LAUNCH_OUTLOOK vs VERIFY_OUTLOOK_READY vs FIND_EMAIL vs
    VERIFY_EMAIL_OPENED."""

    vision_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost: float = 0.0
    latency_ms: float = 0.0


class RND009CResult(BaseModel):
    test_id: str = "RND009C-find-open-email"

    # Playbook-decided target — Gemini never chooses this.
    target_subject: str = ""
    target_sender: str = ""

    session_start: Optional[str] = None
    session_end: Optional[str] = None

    # --- Phase 1: Windows Search -> Outlook launch (reuses RND-009B logic) ---
    windows_key_timestamp: Optional[str] = None
    search_query_typed_timestamp: Optional[str] = None
    search_screenshot: Optional[str] = None
    foreground_before_search_check: Optional[str] = None

    outlook_grounding_raw_x: Optional[float] = None
    outlook_grounding_raw_y: Optional[float] = None
    outlook_converted_x: Optional[int] = None
    outlook_converted_y: Optional[int] = None
    outlook_click_timestamp: Optional[str] = None
    outlook_launch_click_executed: bool = False
    outlook_launch_duration_ms: Optional[float] = None
    foreground_after_launch: Optional[str] = None
    foreground_verified: Optional[bool] = None

    # --- Phase 2: readiness (splash vs ready) ---
    readiness_attempts: list[OutlookReadinessAttempt] = Field(default_factory=list)
    ready_for_interaction: Optional[bool] = None

    # --- Phase 3: find target email ---
    inbox_screenshot: Optional[str] = None
    foreground_before_email_grounding: Optional[str] = None
    email_grounding: Optional[EmailGroundingResponse] = None
    email_grounding_metrics: CallMetrics = CallMetrics()
    email_raw_x: Optional[float] = None
    email_raw_y: Optional[float] = None
    email_converted_x: Optional[int] = None
    email_converted_y: Optional[int] = None
    email_coordinate_in_screen_bounds: Optional[bool] = None

    foreground_before_email_move: Optional[str] = None
    foreground_before_email_click: Optional[str] = None
    email_open_click_timestamp: Optional[str] = None
    email_open_click_executed: bool = False

    # --- Phase 4: verify correct email opened ---
    email_open_verification_attempts: list[EmailOpenVerificationAttempt] = Field(default_factory=list)
    email_open_verification: Optional[EmailOpenVerificationResponse] = None

    # --- Step-level metrics (kept separate, never merged into one total alone) ---
    launch_outlook_metrics: StepMetrics = StepMetrics()
    verify_outlook_ready_metrics: StepMetrics = StepMetrics()
    find_email_metrics: StepMetrics = StepMetrics()
    verify_email_opened_metrics: StepMetrics = StepMetrics()

    result: str = "PENDING"  # PENDING | PASS | FAIL | ABORTED | ERROR
    failure_reason: Optional[str] = None
    notes: str = ""

    mouse_click_count: int = 0
    keyboard_action_count: int = 0
    retries: int = 0
    human_interventions: int = 0
    safety_aborts: int = 0
    send_click_count: int = 0  # must remain 0 throughout RND-009C

    total_vision_calls: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_estimated_cost: float = 0.0
    total_latency_ms: float = 0.0
    total_elapsed_ms: Optional[float] = None
