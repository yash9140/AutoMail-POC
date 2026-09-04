"""Multi-provider readiness stage models (pre-RND-004).

Deliberately separate from rnd/models/vision_result.py (RND-003's Gemini
result schema) — that schema includes x/y grounding coordinates; this one
does not, since coordinate grounding is out of scope until RND-005. This
keeps RND-003's Gemini results untouched and independently reproducible.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field, field_validator


class ReadinessVisionResponse(BaseModel):
    """Schema the model's JSON output must satisfy — no coordinates required."""

    application: str
    screen_state: str
    screen_description: str
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


class ProviderReadinessResult(BaseModel):
    experiment_id: str = "PROVIDER-READINESS"
    test_id: str
    timestamp: str

    provider: str
    model: str
    prompt_version: str

    image_filename: str
    image_width: int
    image_height: int

    goal: str

    application: Optional[str] = None
    screen_state: Optional[str] = None
    screen_description: Optional[str] = None
    relevant_visible_controls: Optional[list[str]] = None
    recommended_action: Optional[str] = None
    target: Optional[str] = None
    confidence: Optional[float] = None
    reason: Optional[str] = None

    latency_ms: Optional[float] = None

    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None

    estimated_cost: Optional[float] = None
    cost_note: Optional[str] = None

    schema_valid: bool
    request_success: bool
    attempt_count: int

    raw_response_reference: Optional[str] = None
    error: Optional[str] = None
