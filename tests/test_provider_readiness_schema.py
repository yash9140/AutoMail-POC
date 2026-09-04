"""Unit tests for the coordinate-free ReadinessVisionResponse schema used
by the multi-provider readiness stage (pre-RND-004). No network calls.
"""

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rnd.models.provider_readiness import ReadinessVisionResponse  # noqa: E402

VALID_PAYLOAD = {
    "application": "Microsoft Outlook",
    "screen_state": "email_open",
    "screen_description": "An email is open in Microsoft Outlook.",
    "relevant_visible_controls": ["Reply", "Forward"],
    "recommended_action": "click",
    "target": "Reply button",
    "confidence": 0.9,
    "reason": "Reply is visible and matches the goal.",
}


def test_valid_response_parses_without_coordinates():
    resp = ReadinessVisionResponse.model_validate(VALID_PAYLOAD)
    assert resp.target == "Reply button"
    assert not hasattr(resp, "x")
    assert not hasattr(resp, "y")


def test_confidence_is_optional():
    payload = dict(VALID_PAYLOAD)
    del payload["confidence"]
    resp = ReadinessVisionResponse.model_validate(payload)
    assert resp.confidence is None


def test_missing_required_field_rejected():
    payload = dict(VALID_PAYLOAD)
    del payload["screen_state"]
    with pytest.raises(ValidationError):
        ReadinessVisionResponse.model_validate(payload)


def test_missing_target_rejected():
    payload = dict(VALID_PAYLOAD)
    del payload["target"]
    with pytest.raises(ValidationError):
        ReadinessVisionResponse.model_validate(payload)


def test_confidence_above_one_rejected():
    payload = {**VALID_PAYLOAD, "confidence": 1.2}
    with pytest.raises(ValidationError):
        ReadinessVisionResponse.model_validate(payload)


def test_confidence_below_zero_rejected():
    payload = {**VALID_PAYLOAD, "confidence": -0.2}
    with pytest.raises(ValidationError):
        ReadinessVisionResponse.model_validate(payload)
