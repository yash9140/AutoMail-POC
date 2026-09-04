"""Unit tests for RND-003's structured response schema and coordinate
bounds check. No network calls — pure schema/logic validation.
"""

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rnd.models.vision_result import (  # noqa: E402
    StructuredVisionResponse,
    validate_coordinates_in_bounds,
)

VALID_PAYLOAD = {
    "application": "Microsoft Outlook",
    "screen_state": "email_open",
    "screen_description": "An email is open in Microsoft Outlook.",
    "visible_controls": ["Reply", "Forward"],
    "recommended_action": {"action": "click", "target": "Reply", "x": 900, "y": 680},
    "confidence": 0.9,
    "reason": "Reply is visible and matches the goal.",
}


def test_valid_structured_response_parses():
    resp = StructuredVisionResponse.model_validate(VALID_PAYLOAD)
    assert resp.recommended_action.target == "Reply"
    assert resp.recommended_action.x == 900
    assert resp.recommended_action.y == 680


def test_missing_required_field_rejected():
    payload = dict(VALID_PAYLOAD)
    del payload["screen_state"]
    with pytest.raises(ValidationError):
        StructuredVisionResponse.model_validate(payload)


def test_missing_recommended_action_rejected():
    payload = dict(VALID_PAYLOAD)
    del payload["recommended_action"]
    with pytest.raises(ValidationError):
        StructuredVisionResponse.model_validate(payload)


def test_confidence_above_one_rejected():
    payload = {**VALID_PAYLOAD, "confidence": 1.5}
    with pytest.raises(ValidationError):
        StructuredVisionResponse.model_validate(payload)


def test_confidence_below_zero_rejected():
    payload = {**VALID_PAYLOAD, "confidence": -0.1}
    with pytest.raises(ValidationError):
        StructuredVisionResponse.model_validate(payload)


def test_confidence_boundary_values_accepted():
    assert StructuredVisionResponse.model_validate({**VALID_PAYLOAD, "confidence": 0.0}).confidence == 0.0
    assert StructuredVisionResponse.model_validate({**VALID_PAYLOAD, "confidence": 1.0}).confidence == 1.0


def test_coordinates_inside_bounds():
    assert validate_coordinates_in_bounds(900, 680, 1920, 1080) is True
    assert validate_coordinates_in_bounds(0, 0, 1920, 1080) is True


def test_coordinates_outside_bounds_rejected():
    assert validate_coordinates_in_bounds(2000, 680, 1920, 1080) is False
    assert validate_coordinates_in_bounds(900, -5, 1920, 1080) is False
    # Exactly at width/height is out of bounds — valid range is [0, dim-1]
    assert validate_coordinates_in_bounds(1920, 680, 1920, 1080) is False
    assert validate_coordinates_in_bounds(900, 1080, 1920, 1080) is False
