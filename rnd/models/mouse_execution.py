"""RND-006 safe mouse execution models.

Move-only stage: no click support exists anywhere in this module or the
executor that uses it. Send is not a valid target — see
ALLOWED_MOVE_TARGETS below, which is the single source of truth for what
this stage is permitted to move toward.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

# Single source of truth for allowed move targets — deliberately excludes
# "send" (destructive/externally-visible, out of scope for RND-006A) and
# anything else not explicitly reviewed. The human-readable description is
# what gets embedded in the grounding prompt sent to Gemini.
ALLOWED_MOVE_TARGETS: dict[str, str] = {
    "email_row": "the existing email row in the inbox message list",
    "reply": "the Reply button",
    "reply_editor": "the reply text-entry editor",
}


def is_target_allowed(target: str) -> bool:
    return target in ALLOWED_MOVE_TARGETS


class RND006MoveResult(BaseModel):
    test_id: str
    target: str

    raw_x: Optional[float] = None
    raw_y: Optional[float] = None
    converted_x: Optional[int] = None
    converted_y: Optional[int] = None

    screen_width: Optional[int] = None
    screen_height: Optional[int] = None

    foreground_window: Optional[str] = None
    foreground_check_passed: Optional[bool] = None

    coordinate_valid: Optional[bool] = None
    movement_executed: bool = False
    human_verified: Optional[bool] = None
    result: str = "PENDING"  # PENDING | PASS | FAIL | ABORTED | ERROR

    latency_ms: Optional[float] = None
    vision_calls: int = 0
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    estimated_cost: Optional[float] = None

    screenshot_filename: Optional[str] = None
    raw_response_reference: Optional[str] = None
    notes: str = ""
