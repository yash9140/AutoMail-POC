"""RND-005 UI grounding models.

Separate from every earlier stage's schema — this is the first prompt
that asks Gemini for pixel coordinates in isolation, with no screen
understanding or action-reasoning framing mixed in (unlike RND-003's
combined prompt).
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field, field_validator

from rnd.models.dataset_manifest import BoundingBox


class GroundingResponse(BaseModel):
    target: str
    x: int
    y: int
    confidence: float
    reason: str

    @field_validator("confidence")
    @classmethod
    def confidence_in_range(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"confidence {v} is outside the valid range [0.0, 1.0]")
        return v


class CoordinateConventionResponse(BaseModel):
    """RND-005B: same as GroundingResponse plus the model's own declared
    coordinate system — x/y are float, not int, since a self-described
    convention might not be integer-only (unlikely, but not assumed).
    """

    target: str
    x: float
    y: float
    coordinate_system: str
    coordinate_range_x: str
    coordinate_range_y: str
    confidence: float
    reason: str

    @field_validator("confidence")
    @classmethod
    def confidence_in_range(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"confidence {v} is outside the valid range [0.0, 1.0]")
        return v


class RND005CaseResult(BaseModel):
    test_id: str
    target: str
    provider: str = "gemini"
    model: str
    prompt_version: str

    image_filename: str
    image_width: int
    image_height: int

    bbox: BoundingBox
    bbox_width: int
    bbox_height: int

    predicted_x: Optional[int] = None
    predicted_y: Optional[int] = None
    coordinate_in_bounds: Optional[bool] = None
    coordinate_inside_target: Optional[bool] = None
    grounding_result: str  # "PASS" | "FAIL" | "ERROR"

    distance_to_bbox_px: Optional[float] = None
    distance_to_bbox_center_px: Optional[float] = None

    confidence: Optional[float] = None
    reason: Optional[str] = None

    latency_ms: Optional[float] = None
    attempt_count: int

    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    estimated_cost: Optional[float] = None
    cost_note: Optional[str] = None

    schema_valid: bool
    request_success: bool
    high_confidence_failure: bool = False

    raw_response_reference: Optional[str] = None
    error: Optional[str] = None
    notes: str = ""
