"""RND-006B Attempt 2 — pure classification logic for the stabilized
verification retry rule. No AI, no network — deterministic rules over
already-obtained verified/human_verified booleans.

Click correctness and verification-subsystem correctness are always kept
as two separate outputs (click_result, classification) — never collapsed
into one number, per the RND-006B Attempt 2 instructions.
"""

from __future__ import annotations

from typing import Optional

AI_VERIFIED_FIRST_ATTEMPT = "AI_VERIFIED_FIRST_ATTEMPT"
UI_STABILIZATION_DELAY_REQUIRED = "UI_STABILIZATION_DELAY_REQUIRED"
VISION_VERIFICATION_FALSE_NEGATIVE = "VISION_VERIFICATION_FALSE_NEGATIVE"
CLICK_FAILED = "CLICK_FAILED"
ATTEMPT1_TECHNICAL_ERROR_ATTEMPT2_PASSED = "ATTEMPT1_TECHNICAL_ERROR_ATTEMPT2_PASSED"
ATTEMPT1_TECHNICAL_ERROR_ATTEMPT2_ALSO_FAILED_OR_ERRORED = "ATTEMPT1_TECHNICAL_ERROR_ATTEMPT2_ALSO_FAILED_OR_ERRORED"


def classify_verification_outcome(
    attempt1_verified: Optional[bool],
    attempt2_verified: Optional[bool],
    human_verified: bool,
    attempt1_had_error: bool = False,
) -> str:
    """Classifies why AI verification and human judgment agreed or
    disagreed. Does NOT decide click_result — that is human_verified
    alone (see compute_click_result).

    attempt1_had_error distinguishes a genuine model verdict of
    verified=False from attempt 1 never producing a verdict at all (e.g.
    a network error, a timeout, an invalid-schema response). Those are
    NOT evidence for the stabilization-delay hypothesis — a network
    failure recovering on retry says nothing about UI render timing —
    so they get their own distinct classification instead of being
    silently folded into UI_STABILIZATION_DELAY_REQUIRED.
    """
    if attempt1_verified:
        return AI_VERIFIED_FIRST_ATTEMPT
    if attempt1_had_error:
        if attempt2_verified:
            return ATTEMPT1_TECHNICAL_ERROR_ATTEMPT2_PASSED
        return ATTEMPT1_TECHNICAL_ERROR_ATTEMPT2_ALSO_FAILED_OR_ERRORED
    if attempt2_verified:
        return UI_STABILIZATION_DELAY_REQUIRED
    if human_verified:
        return VISION_VERIFICATION_FALSE_NEGATIVE
    return CLICK_FAILED


def compute_click_result(human_verified: bool) -> str:
    """Click correctness is decided by human confirmation alone —
    authoritative, never overridden by AI verification, per design.
    """
    return "PASS" if human_verified else "FAIL"
