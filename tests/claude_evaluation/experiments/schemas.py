"""Evaluation-ONLY Pydantic schemas for Experiments C and D.

These are NOT used by the runtime — app/vision/models.py is untouched.
Kept in tests/claude_evaluation/experiments/ specifically so nothing
under app/ ever imports them. Experiment B reuses the runtime's own
OutlookSearchGroundingResponse (same bbox convention, same field
names) with only a different prompt.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field, field_validator


class ComponentGroundingResponse(BaseModel):
    """Experiment C — ask Claude to independently locate three visible
    landmarks (icon, label text, sublabel text) instead of guessing the
    boundary of an inferred, invisible clickable container. Python (see
    experiments/strategies.py) derives a "visual cluster" bbox as the
    union of whichever of the three were confidently located — never
    trusted as a click target outside this evaluation."""

    target_visible: bool = False
    outlook_icon_bbox: Optional[list[float]] = None
    outlook_label_bbox: Optional[list[float]] = None
    app_sublabel_bbox: Optional[list[float]] = None
    confidence: float = 0.0
    reason: str = ""

    @field_validator("confidence")
    @classmethod
    def confidence_in_range(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"confidence {v} is outside the valid range [0.0, 1.0]")
        return v


class CandidateItem(BaseModel):
    visible_label: str = ""
    visible_sublabel: str = ""
    target_type: str = "other"
    bbox: Optional[list[float]] = None
    confidence: float = 0.0


class CandidateListResponse(BaseModel):
    """Experiment D — mirrors the architecture app/outlook/find_email.py
    already uses successfully for target-email selection: Vision reports
    EVERY candidate it sees; deterministic Python code (never Vision)
    picks the one unique match against the playbook-supplied criteria."""

    candidates: list[CandidateItem] = Field(default_factory=list)
    reason: str = ""


class PointGroundingResponse(BaseModel):
    """Experiments P1/P2/P3 (2026-09-03) — after bbox-prediction
    strategies A-D all failed to reliably bound the FULL clickable row,
    this tests a narrower question: can Claude reliably localize a
    single VISIBLE POINT (icon center / label-text center / any point
    on visible cluster content) that happens to fall inside the actual
    row, without ever being asked to infer the row's own (invisible)
    boundary at all. point = [y, x], 0-1000 normalized — same axis
    convention as every bbox in this project, just two values instead
    of four."""

    target_visible: bool = False
    target_type: str = "other"
    visible_label: str = ""
    point: Optional[list[float]] = None  # [y, x], 0-1000 normalized
    confidence: float = 0.0
    reason: str = ""

    @field_validator("confidence")
    @classmethod
    def confidence_in_range(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"confidence {v} is outside the valid range [0.0, 1.0]")
        return v
