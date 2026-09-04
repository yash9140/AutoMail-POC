"""Unit tests for GeminiProvider's error normalization and API-key handling.

No real network calls are made — the google-genai client's
`models.generate_content` is mocked. Real API integration is exercised
separately via rnd/experiments/rnd003_first_vision_call.py --execute,
only after explicit human approval.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from google.genai import errors as genai_errors

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rnd.providers.base import (  # noqa: E402
    AuthenticationError,
    InvalidModelError,
    MissingAPIKeyError,
    RateLimitError,
    VisionProviderError,
)
from rnd.providers.gemini_provider import GeminiProvider  # noqa: E402

DUMMY_IMAGE_BYTES = b"\x89PNG\r\n\x1a\n" + b"0" * 32  # not a real PNG, fine since generate_content is mocked


def _client_error(code: int, message: str = "mocked error") -> genai_errors.ClientError:
    return genai_errors.ClientError(code=code, response_json={"error": {"message": message}})


def test_missing_api_key_raises_before_any_network_call():
    with pytest.raises(MissingAPIKeyError):
        GeminiProvider(api_key="", model="gemini-2.5-flash")


def test_401_normalized_to_authentication_error(tmp_path):
    image_path = tmp_path / "shot.png"
    image_path.write_bytes(DUMMY_IMAGE_BYTES)

    provider = GeminiProvider(api_key="fake-key", model="gemini-2.5-flash")
    with patch.object(provider._client.models, "generate_content", side_effect=_client_error(401)):
        with pytest.raises(AuthenticationError):
            provider.analyze_screen(image_path, "goal", "prompt")


def test_403_normalized_to_authentication_error(tmp_path):
    image_path = tmp_path / "shot.png"
    image_path.write_bytes(DUMMY_IMAGE_BYTES)

    provider = GeminiProvider(api_key="fake-key", model="gemini-2.5-flash")
    with patch.object(provider._client.models, "generate_content", side_effect=_client_error(403)):
        with pytest.raises(AuthenticationError):
            provider.analyze_screen(image_path, "goal", "prompt")


def test_404_normalized_to_invalid_model_error(tmp_path):
    image_path = tmp_path / "shot.png"
    image_path.write_bytes(DUMMY_IMAGE_BYTES)

    provider = GeminiProvider(api_key="fake-key", model="not-a-real-model")
    with patch.object(provider._client.models, "generate_content", side_effect=_client_error(404)):
        with pytest.raises(InvalidModelError):
            provider.analyze_screen(image_path, "goal", "prompt")


def test_429_normalized_to_rate_limit_error(tmp_path):
    image_path = tmp_path / "shot.png"
    image_path.write_bytes(DUMMY_IMAGE_BYTES)

    provider = GeminiProvider(api_key="fake-key", model="gemini-2.5-flash")
    with patch.object(provider._client.models, "generate_content", side_effect=_client_error(429)):
        with pytest.raises(RateLimitError):
            provider.analyze_screen(image_path, "goal", "prompt")


def test_other_client_error_normalized_to_generic_provider_error(tmp_path):
    image_path = tmp_path / "shot.png"
    image_path.write_bytes(DUMMY_IMAGE_BYTES)

    provider = GeminiProvider(api_key="fake-key", model="gemini-2.5-flash")
    with patch.object(provider._client.models, "generate_content", side_effect=_client_error(400)):
        with pytest.raises(VisionProviderError):
            provider.analyze_screen(image_path, "goal", "prompt")


def test_successful_call_returns_normalized_result(tmp_path):
    image_path = tmp_path / "shot.png"
    image_path.write_bytes(DUMMY_IMAGE_BYTES)

    mock_response = MagicMock()
    mock_response.text = '{"application": "Microsoft Outlook"}'
    mock_response.usage_metadata.prompt_token_count = 123
    mock_response.usage_metadata.candidates_token_count = 45

    provider = GeminiProvider(api_key="fake-key", model="gemini-2.5-flash")
    with patch.object(provider._client.models, "generate_content", return_value=mock_response):
        result = provider.analyze_screen(image_path, "goal", "prompt")

    assert result.parsed_json == {"application": "Microsoft Outlook"}
    assert result.input_tokens == 123
    assert result.output_tokens == 45
    assert result.model == "gemini-2.5-flash"
    assert result.latency_ms >= 0
