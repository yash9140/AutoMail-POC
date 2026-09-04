"""RND-004 screen-understanding models.

Separate from rnd/models/vision_result.py (RND-003, has x/y) and
rnd/models/provider_readiness.py (readiness stage, has screen_description)
— this stage's prompt (screen_understanding_v2) asks for neither
coordinates nor a free-form description, only the fields actually scored:
application, screen_state, relevant_visible_controls, recommended_action,
target, confidence, reason.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field, field_validator

FAILURE_TYPES = {
    "APP_RECOGNITION_ERROR",
    "STATE_CLASSIFICATION_ERROR",
    "CONTROL_MISSED",
    "CONTROL_HALLUCINATED",
    "WRONG_NEXT_ACTION",
    "INVALID_SCHEMA",
    "TIMEOUT",
    "PROVIDER_ERROR",
    "OTHER",
}


class ScreenUnderstandingResponse(BaseModel):
    application: str
    screen_state: str
    relevant_visible_controls: list[str] = Field(default_factory=list)
    recommended_action: str
    target: str
    confidence: Optional[float] = None
    reason: str

    @field_validator("confidence")
    @classmethod
    def confidence_in_range(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and not (0.0 <= v <= 1.0):
            raise ValueError(f"confidence {v} is outside the valid range [0.0, 1.0]")
        return v


class RND004CaseResult(BaseModel):
    test_id: str
    provider: str
    model: str
    prompt_version: str

    expected_application: str
    predicted_application: Optional[str] = None
    application_correct: Optional[bool] = None

    expected_state: str
    predicted_state: Optional[str] = None
    normalized_predicted_state: Optional[str] = None
    state_correct: Optional[bool] = None

    expected_relevant_controls: Optional[list[str]] = None
    predicted_relevant_controls: Optional[list[str]] = None
    controls_correct: Optional[bool] = None

    expected_action: Optional[str] = None
    predicted_action: Optional[str] = None
    predicted_target: Optional[str] = None
    normalized_predicted_action: Optional[str] = None
    action_correct: Optional[bool] = None

    confidence: Optional[float] = None

    latency_ms: Optional[float] = None
    attempt_count: int

    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    estimated_cost: Optional[float] = None

    schema_valid: bool
    request_success: bool
    failure_types: list[str] = Field(default_factory=list)
    error: Optional[str] = None
    notes: str = ""

    raw_response_reference: Optional[str] = None
