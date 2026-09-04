"""RND-006B safe click execution models.

Click allowlist is deliberately its own dict (not a re-export of RND-006A's
ALLOWED_MOVE_TARGETS) so RND-006A's move-only allowlist can never be
silently widened by a change made for clicking — each stage owns its own
explicit list. The two currently happen to have the same three keys; that
is a coincidence of scope, not a shared source of truth.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, field_validator

# The ONLY targets this stage may click. Anything else — including Send,
# Reply All, Forward, Delete, Archive, or an unrecognized name — is
# rejected before any capture, API call, or mouse action happens.
CLICK_ALLOWED_TARGETS: dict[str, str] = {
    "email_row": "the existing email row in the inbox message list",
    "reply": "the Reply button",
    "reply_editor": "the reply text-entry editor",
}

# Named explicitly (not just "whatever isn't in the allowlist") so the
# rejection reason can say why, and so a reviewer can see at a glance
# that these were deliberately considered and excluded, not overlooked.
EXPLICITLY_BLOCKED_TARGETS = {"send", "reply_all", "forward", "delete", "archive"}

EXPECTED_STATE_AFTER_CLICK: dict[str, str] = {
    "email_row": "email_open",
    "reply": "reply_editor_open",
    "reply_editor": "reply_editor_focused",  # not reliably state-verifiable by screenshot alone — see docs
}

# RND-006B Attempt 2: tighter, verification-only expected-state statements
# (state_verification_v2) — describes only what the screen should show,
# never asks about a next action. Kept separate from
# EXPECTED_STATE_AFTER_CLICK above so Attempt 1's exact prompt input is
# preserved unchanged for traceability.
EXPECTED_VERIFICATION_STATEMENT_V2: dict[str, str] = {
    "email_row": "An email is open and its message content is visible in the reading pane.",
    "reply": "The reply composer/editor is visibly open.",
    "reply_editor": "The reply editor has focus and is ready for text entry.",
}


def is_click_target_allowed(target: str) -> bool:
    return target in CLICK_ALLOWED_TARGETS


class VerificationResponse(BaseModel):
    verified: bool
    detected_state: str
    confidence: float
    reason: str

    @field_validator("confidence")
    @classmethod
    def confidence_in_range(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"confidence {v} is outside the valid range [0.0, 1.0]")
        return v


class VerificationResponseV2(BaseModel):
    """state_verification_v2 — adds visual_evidence over VerificationResponse."""

    verified: bool
    detected_state: str
    confidence: float
    visual_evidence: str
    reason: str

    @field_validator("confidence")
    @classmethod
    def confidence_in_range(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"confidence {v} is outside the valid range [0.0, 1.0]")
        return v


class RND006BClickResult(BaseModel):
    test_id: str
    target: str

    pre_screenshot: Optional[str] = None
    post_screenshot: Optional[str] = None

    raw_x: Optional[float] = None
    raw_y: Optional[float] = None
    converted_x: Optional[int] = None
    converted_y: Optional[int] = None

    foreground_before: Optional[str] = None
    foreground_before_click: Optional[str] = None

    approval_received: bool = False
    movement_executed: bool = False
    click_executed: bool = False

    expected_state: Optional[str] = None
    detected_state: Optional[str] = None
    verification_result: Optional[bool] = None
    verification_confidence: Optional[float] = None
    human_verification: Optional[bool] = None

    grounding_latency_ms: Optional[float] = None
    verification_latency_ms: Optional[float] = None

    vision_calls: int = 0
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    estimated_cost: Optional[float] = None

    result: str = "PENDING"  # PENDING | PASS | FAIL | ABORTED | ERROR
    failure_reason: Optional[str] = None
    notes: str = ""


class VerificationAttempt(BaseModel):
    """One stabilization-delay + screenshot + verification-call cycle
    (RND-006B Attempt 2). Never involves a click — only a wait and a
    fresh look.
    """

    attempt_number: int
    delay_seconds: float
    screenshot_timestamp: str
    screenshot_filename: str
    time_since_click_ms: Optional[float] = None

    verified: Optional[bool] = None
    detected_state: Optional[str] = None
    confidence: Optional[float] = None
    visual_evidence: Optional[str] = None
    reason: Optional[str] = None

    latency_ms: Optional[float] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    estimated_cost: Optional[float] = None

    schema_valid: bool = False
    error: Optional[str] = None


class RND006BAttempt2Result(BaseModel):
    """RND-006B Attempt 2 — stabilized verification. Click correctness and
    verification-subsystem correctness are recorded as two independent
    metrics (click_result vs verification_classification), per instruction
    — never collapsed into one number.
    """

    test_id: str
    target: str
    attempt_label: str = "RND-006B Attempt 2"

    pre_screenshot: Optional[str] = None

    raw_x: Optional[float] = None
    raw_y: Optional[float] = None
    converted_x: Optional[int] = None
    converted_y: Optional[int] = None

    foreground_before: Optional[str] = None
    foreground_before_click: Optional[str] = None

    approval_received: bool = False
    movement_executed: bool = False
    click_executed: bool = False
    click_timestamp: Optional[str] = None

    expected_state: Optional[str] = None

    verification_attempt_1: Optional[VerificationAttempt] = None
    verification_attempt_2: Optional[VerificationAttempt] = None

    ai_verification_final: Optional[bool] = None  # True if either attempt verified=True
    human_verification: Optional[bool] = None

    click_result: str = "PENDING"  # PENDING | PASS | FAIL | ABORTED | ERROR
    verification_classification: Optional[str] = None
    # one of: AI_VERIFIED_FIRST_ATTEMPT | UI_STABILIZATION_DELAY_REQUIRED |
    #         VISION_VERIFICATION_FALSE_NEGATIVE | CLICK_FAILED | None (not yet determined)

    grounding_latency_ms: Optional[float] = None
    vision_calls: int = 0
    total_input_tokens: Optional[int] = None
    total_output_tokens: Optional[int] = None
    total_estimated_cost: Optional[float] = None

    failure_reason: Optional[str] = None
    notes: str = ""
