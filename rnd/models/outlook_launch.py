"""RND-009B Outlook launch models — Windows Search grounding, Outlook
launch verification, and the full traceable result record.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field, field_validator


class WindowsSearchGroundingResponse(BaseModel):
    search_visible: bool
    outlook_result_visible: bool
    result_label: str
    x: Optional[float] = None
    y: Optional[float] = None
    confidence: float
    reason: str

    @field_validator("confidence")
    @classmethod
    def confidence_in_range(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"confidence {v} is outside the valid range [0.0, 1.0]")
        return v


class OutlookLaunchVerificationResponse(BaseModel):
    """Readiness-hardened (post-Attempt-2 adjustment): outlook_visible
    alone is not enough to call Outlook launched — a splash/loading
    screen also makes outlook_visible true. splash_screen_visible and
    ready_for_interaction are separate, explicit signals so a loading
    screen can never be silently counted as ready."""

    application: str
    outlook_visible: bool
    splash_screen_visible: bool
    ready_for_interaction: bool
    detected_state: str
    confidence: float
    reason: str = ""

    @field_validator("confidence")
    @classmethod
    def confidence_in_range(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"confidence {v} is outside the valid range [0.0, 1.0]")
        return v


class CallMetrics(BaseModel):
    latency_ms: Optional[float] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    estimated_cost: Optional[float] = None


class OutlookReadinessAttempt(BaseModel):
    attempt_number: int  # 1 or 2 — max 2 per instruction
    delay_seconds: float
    timestamp: Optional[str] = None
    screenshot: Optional[str] = None

    outlook_visible: Optional[bool] = None
    splash_screen_visible: Optional[bool] = None
    ready_for_interaction: Optional[bool] = None
    detected_state: Optional[str] = None
    confidence: Optional[float] = None
    reason: Optional[str] = None

    metrics: CallMetrics = CallMetrics()
    schema_valid: bool = False
    error: Optional[str] = None


class RND009BResult(BaseModel):
    test_id: str = "RND009B-outlook-launch"

    windows_key_timestamp: Optional[str] = None
    search_query_typed_timestamp: Optional[str] = None
    search_capture_timestamp: Optional[str] = None
    search_screenshot: Optional[str] = None

    foreground_before_search_check: Optional[str] = None

    grounding: Optional[WindowsSearchGroundingResponse] = None
    grounding_metrics: CallMetrics = CallMetrics()
    vision_grounding_latency_ms: Optional[float] = None

    raw_x: Optional[float] = None
    raw_y: Optional[float] = None
    converted_x: Optional[int] = None
    converted_y: Optional[int] = None
    screen_width: Optional[int] = None
    screen_height: Optional[int] = None
    coordinate_in_screen_bounds: Optional[bool] = None

    human_target_approved: Optional[bool] = None

    foreground_before_click: Optional[str] = None
    outlook_click_timestamp: Optional[str] = None
    outlook_launch_click_executed: bool = False

    outlook_detected_timestamp: Optional[str] = None
    outlook_launch_duration_ms: Optional[float] = None
    foreground_after_launch: Optional[str] = None
    foreground_verified: Optional[bool] = None

    # Readiness hardening (post-Attempt-2 adjustment): splash/loading no
    # longer counts as OUTLOOK_VERIFIED. Up to 2 attempts, each recorded
    # separately — never collapsed into a single pass/fail bit — plus the
    # screenshot filename for each (a real gap found in the original
    # Attempt 2 result, where the verification screenshot's filename was
    # never persisted at all; fixed here).
    readiness_attempts: list[OutlookReadinessAttempt] = Field(default_factory=list)
    ready_for_interaction: Optional[bool] = None

    launch_verification: Optional[OutlookLaunchVerificationResponse] = None
    launch_verification_metrics: CallMetrics = CallMetrics()

    result: str = "PENDING"  # PENDING | PASS | FAIL | ABORTED | ERROR
    failure_reason: Optional[str] = None
    notes: str = ""

    mouse_click_count: int = 0
    keyboard_action_count: int = 0
    retries: int = 0
    human_interventions: int = 0
    safety_aborts: int = 0
    send_click_count: int = 0  # must remain 0 throughout RND-009B

    total_vision_calls: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_estimated_cost: float = 0.0
    total_latency_ms: float = 0.0
