"""Unit test for RND-005B's CoordinateConventionResponse schema. No network.
"""

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rnd.models.grounding import CoordinateConventionResponse  # noqa: E402

VALID_PAYLOAD = {
    "target": "Reply",
    "x": 477.0,
    "y": 631.0,
    "coordinate_system": "native image pixels",
    "coordinate_range_x": "0 to 1920",
    "coordinate_range_y": "0 to 1080",
    "confidence": 0.95,
    "reason": "Located the Reply button at the bottom of the email message pane.",
}


def test_valid_response_parses():
    resp = CoordinateConventionResponse.model_validate(VALID_PAYLOAD)
    assert resp.coordinate_system == "native image pixels"
    assert resp.x == 477.0


def test_missing_coordinate_system_rejected():
    payload = dict(VALID_PAYLOAD)
    del payload["coordinate_system"]
    with pytest.raises(ValidationError):
        CoordinateConventionResponse.model_validate(payload)


def test_confidence_out_of_range_rejected():
    payload = {**VALID_PAYLOAD, "confidence": 1.5}
    with pytest.raises(ValidationError):
        CoordinateConventionResponse.model_validate(payload)
