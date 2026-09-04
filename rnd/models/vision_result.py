"""RND-003: normalized Vision AI result models.

Two schemas:

- `StructuredVisionResponse` — what we ask the model to return, and what
  we validate the model's raw JSON output against, before trusting it.
- `VisionResult` — the full, provider-independent experiment record,
  combining the validated model output with request metadata (timing,
  usage, cost, success/error). This is what gets written to
  results/raw/rnd003_first_provider_result.json.

No accuracy/grounding judgment happens here — RND-003 only validates that
a response is well-formed and records it. Comparing predicted coordinates
against ground-truth bounding boxes is RND-004/RND-005's job.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field, field_validator


class RecommendedAction(BaseModel):
    action: str
    target: str
    x: int
    y: int


class StructuredVisionResponse(BaseModel):
    """Schema the Vision model's JSON output must satisfy to be considered valid."""

    application: str
    screen_state: str
    screen_description: str
    visible_controls: list[str] = Field(default_factory=list)
    recommended_action: RecommendedAction
    confidence: float
    reason: str

    @field_validator("confidence")
    @classmethod
    def confidence_in_range(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"confidence {v} is outside the valid range [0.0, 1.0]")
        return v


def validate_coordinates_in_bounds(x: int, y: int, image_width: int, image_height: int) -> bool:
    """RND-003 coordinate check: is the predicted point inside the image at all.

    This is NOT a grounding-accuracy check (i.e. not "is it inside the
    Reply button") — that comparison belongs to RND-004/RND-005. This only
    catches a model returning coordinates that are structurally nonsensical
    (negative, or beyond the screenshot's own dimensions).
    """
    return 0 <= x <= image_width - 1 and 0 <= y <= image_height - 1


class VisionResult(BaseModel):
    experiment_id: str
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
    visible_controls: Optional[list[str]] = None

    action: Optional[str] = None
    target: Optional[str] = None

    x: Optional[int] = None
    y: Optional[int] = None

    confidence: Optional[float] = None
    reason: Optional[str] = None

    latency_ms: Optional[float] = None

    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None

    estimated_cost: Optional[float] = None
    cost_note: Optional[str] = None

    schema_valid: bool
    coordinates_in_bounds: Optional[bool] = None
    request_success: bool
    attempt_count: int

    raw_response_reference: Optional[str] = None
    error: Optional[str] = None
