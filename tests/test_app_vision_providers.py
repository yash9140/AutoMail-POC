"""Smoke tests for app/vision/providers/*.py — the app/-local copies of
rnd/providers/*.py used by the final POC runtime. Full error-mapping
coverage for Gemini lives in tests/test_gemini_provider.py (against the
rnd/ original, left untouched); this file only proves the app/ copies
wire up identically (same exception classes, same construction
behavior) so app/config/settings.py::get_provider() is trustworthy.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from google.genai import errors as genai_errors

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.vision.providers.anthropic_provider import AnthropicProvider  # noqa: E402
from app.vision.providers.base import (  # noqa: E402
    AuthenticationError,
    MissingAPIKeyError,
    VisionProviderError,
)
from app.vision.providers.gemini_provider import GeminiProvider  # noqa: E402
from app.vision.providers.openai_provider import OpenAIProvider  # noqa: E402

DUMMY_IMAGE_BYTES = b"\x89PNG\r\n\x1a\n" + b"0" * 32


def test_gemini_missing_api_key_raises():
    with pytest.raises(MissingAPIKeyError):
        GeminiProvider(api_key="", model="gemini-3.6-flash")


def test_openai_missing_api_key_raises():
    with pytest.raises(MissingAPIKeyError):
        OpenAIProvider(api_key="", model="gpt-4o")


def test_anthropic_missing_api_key_raises():
    with pytest.raises(MissingAPIKeyError):
        AnthropicProvider(api_key="", model="claude-opus-4")


def test_gemini_successful_call_returns_normalized_result(tmp_path):
    image_path = tmp_path / "shot.png"
    image_path.write_bytes(DUMMY_IMAGE_BYTES)

    mock_response = MagicMock()
    mock_response.text = '{"application": "Microsoft Outlook"}'
    mock_response.usage_metadata.prompt_token_count = 123
    mock_response.usage_metadata.candidates_token_count = 45

    provider = GeminiProvider(api_key="fake-key", model="gemini-3.6-flash")
    with patch.object(provider._client.models, "generate_content", return_value=mock_response):
        result = provider.analyze_screen(image_path, "goal", "prompt")

    assert result.parsed_json == {"application": "Microsoft Outlook"}
    assert result.input_tokens == 123
    assert result.output_tokens == 45


def test_gemini_403_normalized_to_authentication_error(tmp_path):
    image_path = tmp_path / "shot.png"
    image_path.write_bytes(DUMMY_IMAGE_BYTES)

    provider = GeminiProvider(api_key="fake-key", model="gemini-3.6-flash")
    err = genai_errors.ClientError(code=403, response_json={"error": {"message": "denied"}})
    with patch.object(provider._client.models, "generate_content", side_effect=err):
        with pytest.raises(AuthenticationError):
            provider.analyze_screen(image_path, "goal", "prompt")


def test_all_three_providers_share_the_same_error_hierarchy():
    assert issubclass(MissingAPIKeyError, VisionProviderError)
    assert issubclass(AuthenticationError, VisionProviderError)
