"""Anthropic Claude Vision provider implementation.

Uses the official `anthropic` SDK's Messages API with base64 image input.
Normalizes SDK-specific exceptions into the VisionProviderError hierarchy
from rnd/providers/base.py so calling code doesn't need to know anything
Anthropic-specific.
"""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path

import anthropic

from rnd.providers.base import (
    AuthenticationError,
    InvalidModelError,
    MalformedResponseError,
    MissingAPIKeyError,
    NetworkError,
    ProviderCallResult,
    ProviderTimeoutError,
    RateLimitError,
    VisionProvider,
    VisionProviderError,
)

MAX_TOKENS = 1024


class AnthropicProvider(VisionProvider):
    provider_name = "anthropic"

    def __init__(self, api_key: str, model: str, timeout_seconds: float = 30.0):
        if not api_key:
            raise MissingAPIKeyError("ANTHROPIC_API_KEY is not set")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self._client = anthropic.Anthropic(api_key=api_key, timeout=timeout_seconds)

    def analyze_screen(self, image_path: Path, goal: str, prompt_text: str) -> ProviderCallResult:
        image_path = Path(image_path)
        image_b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")

        start = time.perf_counter()
        try:
            # Note: this SDK version's Messages.create() has no `temperature`
            # parameter (removed in the current Claude 5-generation API
            # surface) — discovered via a real TypeError during this
            # project's first live Anthropic call, not assumed in advance.
            response = self._client.messages.create(
                model=self.model,
                max_tokens=MAX_TOKENS,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {"type": "base64", "media_type": "image/png", "data": image_b64},
                            },
                            {"type": "text", "text": prompt_text},
                        ],
                    }
                ],
            )
        except anthropic.AuthenticationError as exc:
            raise AuthenticationError(f"Anthropic authentication failed: {exc}") from exc
        except anthropic.NotFoundError as exc:
            raise InvalidModelError(f"Anthropic model '{self.model}' not found: {exc}") from exc
        except anthropic.RateLimitError as exc:
            raise RateLimitError(f"Anthropic rate limit exceeded: {exc}") from exc
        except anthropic.APITimeoutError as exc:
            raise ProviderTimeoutError(f"Anthropic request timed out after {self.timeout_seconds}s: {exc}") from exc
        except anthropic.APIConnectionError as exc:
            raise NetworkError(f"Network failure calling Anthropic: {exc}") from exc
        except anthropic.APIStatusError as exc:
            raise VisionProviderError(f"Anthropic API error (HTTP {exc.status_code}): {exc}") from exc
        except anthropic.AnthropicError as exc:
            raise VisionProviderError(f"Anthropic SDK error: {exc}") from exc

        latency_ms = (time.perf_counter() - start) * 1000

        if not response.content:
            raise MalformedResponseError("Anthropic returned no content blocks in the response")

        raw_text = "".join(block.text for block in response.content if getattr(block, "type", None) == "text")
        if not raw_text.strip():
            raise MalformedResponseError("Anthropic returned an empty response body")

        # Claude doesn't have a strict "JSON mode" like OpenAI/Gemini — it may
        # wrap the JSON in markdown code fences despite instructions not to.
        # Strip those defensively before parsing, without inventing content.
        text_to_parse = raw_text.strip()
        if text_to_parse.startswith("```"):
            text_to_parse = text_to_parse.strip("`")
            if text_to_parse.startswith("json"):
                text_to_parse = text_to_parse[4:]
            text_to_parse = text_to_parse.strip()

        try:
            parsed_json = json.loads(text_to_parse)
        except (ValueError, TypeError):
            parsed_json = None

        usage = getattr(response, "usage", None)
        input_tokens = getattr(usage, "input_tokens", None) if usage else None
        output_tokens = getattr(usage, "output_tokens", None) if usage else None

        return ProviderCallResult(
            raw_text=raw_text,
            parsed_json=parsed_json,
            model=response.model or self.model,
            latency_ms=latency_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
