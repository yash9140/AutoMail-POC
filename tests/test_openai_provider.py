"""Unit tests for OpenAIProvider's error normalization and API-key handling.

No real network calls — the openai client's chat.completions.create is
mocked. Real API integration is exercised separately via
rnd/experiments/provider_readiness_check.py --execute, only after
explicit human approval.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import openai
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rnd.providers.base import (  # noqa: E402
    AuthenticationError,
    InvalidModelError,
    MissingAPIKeyError,
    RateLimitError,
    VisionProviderError,
)
from rnd.providers.openai_provider import OpenAIProvider  # noqa: E402

DUMMY_IMAGE_BYTES = b"\x89PNG\r\n\x1a\n" + b"0" * 32
_REQUEST = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")


def _status_error(cls, code: int, message: str = "mocked error"):
    resp = httpx.Response(status_code=code, request=_REQUEST, json={"error": {"message": message}})
    return cls(message, response=resp, body={"error": {"message": message}})


def test_missing_api_key_raises_before_any_network_call():
    with pytest.raises(MissingAPIKeyError):
        OpenAIProvider(api_key="", model="gpt-4o")


def test_authentication_error_normalized(tmp_path):
    image_path = tmp_path / "shot.png"
    image_path.write_bytes(DUMMY_IMAGE_BYTES)

    provider = OpenAIProvider(api_key="fake-key", model="gpt-4o")
    err = _status_error(openai.AuthenticationError, 401)
    with patch.object(provider._client.chat.completions, "create", side_effect=err):
        with pytest.raises(AuthenticationError):
            provider.analyze_screen(image_path, "goal", "prompt")


def test_not_found_normalized_to_invalid_model_error(tmp_path):
    image_path = tmp_path / "shot.png"
    image_path.write_bytes(DUMMY_IMAGE_BYTES)

    provider = OpenAIProvider(api_key="fake-key", model="not-a-real-model")
    err = _status_error(openai.NotFoundError, 404)
    with patch.object(provider._client.chat.completions, "create", side_effect=err):
        with pytest.raises(InvalidModelError):
            provider.analyze_screen(image_path, "goal", "prompt")


def test_rate_limit_normalized(tmp_path):
    image_path = tmp_path / "shot.png"
    image_path.write_bytes(DUMMY_IMAGE_BYTES)

    provider = OpenAIProvider(api_key="fake-key", model="gpt-4o")
    err = _status_error(openai.RateLimitError, 429)
    with patch.object(provider._client.chat.completions, "create", side_effect=err):
        with pytest.raises(RateLimitError):
            provider.analyze_screen(image_path, "goal", "prompt")


def test_other_status_error_normalized_to_generic_provider_error(tmp_path):
    image_path = tmp_path / "shot.png"
    image_path.write_bytes(DUMMY_IMAGE_BYTES)

    provider = OpenAIProvider(api_key="fake-key", model="gpt-4o")
    err = _status_error(openai.APIStatusError, 400)
    with patch.object(provider._client.chat.completions, "create", side_effect=err):
        with pytest.raises(VisionProviderError):
            provider.analyze_screen(image_path, "goal", "prompt")


def _make_mock_response(content: str, model: str = "gpt-4o", prompt_tokens: int = 100, completion_tokens: int = 20):
    mock_message = MagicMock()
    mock_message.content = content
    mock_choice = MagicMock()
    mock_choice.message = mock_message
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    mock_response.model = model
    mock_response.usage.prompt_tokens = prompt_tokens
    mock_response.usage.completion_tokens = completion_tokens
    return mock_response


def test_successful_call_returns_normalized_result(tmp_path):
    image_path = tmp_path / "shot.png"
    image_path.write_bytes(DUMMY_IMAGE_BYTES)

    mock_response = _make_mock_response('{"application": "Microsoft Outlook"}')

    provider = OpenAIProvider(api_key="fake-key", model="gpt-4o")
    with patch.object(provider._client.chat.completions, "create", return_value=mock_response):
        result = provider.analyze_screen(image_path, "goal", "prompt")

    assert result.parsed_json == {"application": "Microsoft Outlook"}
    assert result.input_tokens == 100
    assert result.output_tokens == 20
    assert result.model == "gpt-4o"
    assert result.latency_ms >= 0


def test_malformed_json_response_yields_none_parsed_json(tmp_path):
    image_path = tmp_path / "shot.png"
    image_path.write_bytes(DUMMY_IMAGE_BYTES)

    mock_response = _make_mock_response("not valid json at all")

    provider = OpenAIProvider(api_key="fake-key", model="gpt-4o")
    with patch.object(provider._client.chat.completions, "create", return_value=mock_response):
        result = provider.analyze_screen(image_path, "goal", "prompt")

    assert result.parsed_json is None
    assert result.raw_text == "not valid json at all"
