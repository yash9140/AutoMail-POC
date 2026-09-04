"""Unit tests for AnthropicProvider's error normalization and API-key
handling. No real network calls — the anthropic client's messages.create
is mocked. Real API integration is exercised separately via
rnd/experiments/provider_readiness_check.py --execute, only after
explicit human approval.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import anthropic
import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rnd.providers.anthropic_provider import AnthropicProvider  # noqa: E402
from rnd.providers.base import (  # noqa: E402
    AuthenticationError,
    InvalidModelError,
    MissingAPIKeyError,
    RateLimitError,
    VisionProviderError,
)

DUMMY_IMAGE_BYTES = b"\x89PNG\r\n\x1a\n" + b"0" * 32
_REQUEST = httpx.Request("POST", "https://api.anthropic.com/v1/messages")


def _status_error(cls, code: int, message: str = "mocked error"):
    resp = httpx.Response(status_code=code, request=_REQUEST, json={"error": {"message": message}})
    return cls(message, response=resp, body={"error": {"message": message}})


def test_missing_api_key_raises_before_any_network_call():
    with pytest.raises(MissingAPIKeyError):
        AnthropicProvider(api_key="", model="claude-opus-4")


def test_authentication_error_normalized(tmp_path):
    image_path = tmp_path / "shot.png"
    image_path.write_bytes(DUMMY_IMAGE_BYTES)

    provider = AnthropicProvider(api_key="fake-key", model="claude-opus-4")
    err = _status_error(anthropic.AuthenticationError, 401)
    with patch.object(provider._client.messages, "create", side_effect=err):
        with pytest.raises(AuthenticationError):
            provider.analyze_screen(image_path, "goal", "prompt")


def test_not_found_normalized_to_invalid_model_error(tmp_path):
    image_path = tmp_path / "shot.png"
    image_path.write_bytes(DUMMY_IMAGE_BYTES)

    provider = AnthropicProvider(api_key="fake-key", model="not-a-real-model")
    err = _status_error(anthropic.NotFoundError, 404)
    with patch.object(provider._client.messages, "create", side_effect=err):
        with pytest.raises(InvalidModelError):
            provider.analyze_screen(image_path, "goal", "prompt")


def test_rate_limit_normalized(tmp_path):
    image_path = tmp_path / "shot.png"
    image_path.write_bytes(DUMMY_IMAGE_BYTES)

    provider = AnthropicProvider(api_key="fake-key", model="claude-opus-4")
    err = _status_error(anthropic.RateLimitError, 429)
    with patch.object(provider._client.messages, "create", side_effect=err):
        with pytest.raises(RateLimitError):
            provider.analyze_screen(image_path, "goal", "prompt")


def test_other_status_error_normalized_to_generic_provider_error(tmp_path):
    image_path = tmp_path / "shot.png"
    image_path.write_bytes(DUMMY_IMAGE_BYTES)

    provider = AnthropicProvider(api_key="fake-key", model="claude-opus-4")
    err = _status_error(anthropic.APIStatusError, 400)
    with patch.object(provider._client.messages, "create", side_effect=err):
        with pytest.raises(VisionProviderError):
            provider.analyze_screen(image_path, "goal", "prompt")


def _make_mock_response(text: str, model: str = "claude-opus-4", input_tokens: int = 150, output_tokens: int = 30):
    mock_block = MagicMock()
    mock_block.type = "text"
    mock_block.text = text
    mock_response = MagicMock()
    mock_response.content = [mock_block]
    mock_response.model = model
    mock_response.usage.input_tokens = input_tokens
    mock_response.usage.output_tokens = output_tokens
    return mock_response


def test_successful_call_returns_normalized_result(tmp_path):
    image_path = tmp_path / "shot.png"
    image_path.write_bytes(DUMMY_IMAGE_BYTES)

    mock_response = _make_mock_response('{"application": "Microsoft Outlook"}')

    provider = AnthropicProvider(api_key="fake-key", model="claude-opus-4")
    with patch.object(provider._client.messages, "create", return_value=mock_response):
        result = provider.analyze_screen(image_path, "goal", "prompt")

    assert result.parsed_json == {"application": "Microsoft Outlook"}
    assert result.input_tokens == 150
    assert result.output_tokens == 30
    assert result.model == "claude-opus-4"


def test_markdown_fenced_json_is_stripped_before_parsing(tmp_path):
    image_path = tmp_path / "shot.png"
    image_path.write_bytes(DUMMY_IMAGE_BYTES)

    fenced = '```json\n{"application": "Microsoft Outlook"}\n```'
    mock_response = _make_mock_response(fenced)

    provider = AnthropicProvider(api_key="fake-key", model="claude-opus-4")
    with patch.object(provider._client.messages, "create", return_value=mock_response):
        result = provider.analyze_screen(image_path, "goal", "prompt")

    assert result.parsed_json == {"application": "Microsoft Outlook"}
    assert result.raw_text == fenced  # raw_text preserves the original, unstripped response


def test_malformed_json_response_yields_none_parsed_json(tmp_path):
    image_path = tmp_path / "shot.png"
    image_path.write_bytes(DUMMY_IMAGE_BYTES)

    mock_response = _make_mock_response("not valid json at all")

    provider = AnthropicProvider(api_key="fake-key", model="claude-opus-4")
    with patch.object(provider._client.messages, "create", return_value=mock_response):
        result = provider.analyze_screen(image_path, "goal", "prompt")

    assert result.parsed_json is None
