"""RND-007A controlled text entry models.

Typing correctness (did the characters land where intended) and
verification correctness (could Vision/human confirm the right text
appeared) are recorded as separate fields, never collapsed — same
philosophy as RND-006B's click_result vs verification_classification
split.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, field_validator

FIXED_TEST_TEXT = "This is a controlled POC test reply."

# Explicitly documented here as NEVER sent by this stage — grepped for in
# tests/test_text_entry.py to confirm the module source never calls them.
BLOCKED_KEY_ACTIONS = ["enter", "ctrl+enter", "alt+s"]


class TextEntryVerificationResponse(BaseModel):
    verified: bool
    detected_text: str
    confidence: float
    reason: str

    @field_validator("confidence")
    @classmethod
    def confidence_in_range(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"confidence {v} is outside the valid range [0.0, 1.0]")
        return v


class RND007ACaseResult(BaseModel):
    test_id: str = "RND007A-reply_editor"
    target: str = "reply_editor"

    expected_text: str = FIXED_TEST_TEXT
    typed_text: Optional[str] = None

    pre_screenshot: Optional[str] = None
    post_screenshot: Optional[str] = None

    foreground_before: Optional[str] = None
    approval_received: bool = False

    text_entry_executed: bool = False
    typing_result: str = "PENDING"  # PENDING | PASS | FAIL | ABORTED | ERROR

    detected_text: Optional[str] = None
    exact_match: Optional[bool] = None
    vision_verification: Optional[bool] = None
    verification_confidence: Optional[float] = None
    verification_reason: Optional[str] = None

    human_verification: Optional[bool] = None
    result: str = "PENDING"  # overall — PENDING | PASS | FAIL | ABORTED | ERROR

    verification_latency_ms: Optional[float] = None
    vision_calls: int = 0
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    estimated_cost: Optional[float] = None

    failure_reason: Optional[str] = None
    notes: str = ""
