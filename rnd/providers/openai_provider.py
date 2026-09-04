"""OpenAI Vision provider implementation.

Uses the official `openai` SDK's Chat Completions API with image input and
JSON-mode structured output. Normalizes SDK-specific exceptions into the
VisionProviderError hierarchy from rnd/providers/base.py so calling code
doesn't need to know anything OpenAI-specific.
"""

from __future__ import annotations

import base64
import time
from pathlib import Path

import openai

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


class OpenAIProvider(VisionProvider):
    provider_name = "openai"

    def __init__(self, api_key: str, model: str, timeout_seconds: float = 30.0):
        if not api_key:
            raise MissingAPIKeyError("OPENAI_API_KEY is not set")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self._client = openai.OpenAI(api_key=api_key, timeout=timeout_seconds)

    def analyze_screen(self, image_path: Path, goal: str, prompt_text: str) -> ProviderCallResult:
        image_path = Path(image_path)
        image_b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")

        start = time.perf_counter()
        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt_text},
                            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
                        ],
                    }
                ],
                response_format={"type": "json_object"},
                temperature=0,
            )
        except openai.AuthenticationError as exc:
            raise AuthenticationError(f"OpenAI authentication failed: {exc}") from exc
        except openai.NotFoundError as exc:
            raise InvalidModelError(f"OpenAI model '{self.model}' not found: {exc}") from exc
        except openai.RateLimitError as exc:
            raise RateLimitError(f"OpenAI rate limit exceeded: {exc}") from exc
        except openai.APITimeoutError as exc:
            raise ProviderTimeoutError(f"OpenAI request timed out after {self.timeout_seconds}s: {exc}") from exc
        except openai.APIConnectionError as exc:
            raise NetworkError(f"Network failure calling OpenAI: {exc}") from exc
        except openai.APIStatusError as exc:
            raise VisionProviderError(f"OpenAI API error (HTTP {exc.status_code}): {exc}") from exc
        except openai.OpenAIError as exc:
            raise VisionProviderError(f"OpenAI SDK error: {exc}") from exc

        latency_ms = (time.perf_counter() - start) * 1000

        if not response.choices:
            raise MalformedResponseError("OpenAI returned no choices in the response")

        raw_text = response.choices[0].message.content or ""
        if not raw_text.strip():
            raise MalformedResponseError("OpenAI returned an empty response body")

        try:
            import json

            parsed_json = json.loads(raw_text)
        except (ValueError, TypeError):
            parsed_json = None

        usage = getattr(response, "usage", None)
        input_tokens = getattr(usage, "prompt_tokens", None) if usage else None
        output_tokens = getattr(usage, "completion_tokens", None) if usage else None

        return ProviderCallResult(
            raw_text=raw_text,
            parsed_json=parsed_json,
            model=response.model or self.model,
            latency_ms=latency_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
