"""Google Gemini Vision provider implementation (RND-003).

Uses the official `google-genai` SDK (Gemini Developer API). Normalizes
SDK-specific exceptions into the VisionProviderError hierarchy from
rnd/providers/base.py so the experiment runner doesn't need to know
anything Gemini-specific.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx
from google import genai
from google.genai import errors as genai_errors
from google.genai import types

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


class GeminiProvider(VisionProvider):
    provider_name = "gemini"

    def __init__(self, api_key: str, model: str, timeout_seconds: float = 30.0):
        if not api_key:
            raise MissingAPIKeyError("GEMINI_API_KEY is not set")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self._client = genai.Client(api_key=api_key)

    def analyze_screen(self, image_path: Path, goal: str, prompt_text: str) -> ProviderCallResult:
        image_path = Path(image_path)
        image_bytes = image_path.read_bytes()

        start = time.perf_counter()
        try:
            response = self._client.models.generate_content(
                model=self.model,
                contents=[
                    types.Part.from_bytes(data=image_bytes, mime_type="image/png"),
                    prompt_text,
                ],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0,
                    http_options=types.HttpOptions(timeout=int(self.timeout_seconds * 1000)),
                ),
            )
        except genai_errors.ClientError as exc:
            code = getattr(exc, "code", None)
            message = getattr(exc, "message", None) or str(exc)
            if code in (401, 403):
                raise AuthenticationError(f"Gemini authentication failed (HTTP {code}): {message}") from exc
            if code == 404:
                raise InvalidModelError(f"Gemini model '{self.model}' not found (HTTP 404): {message}") from exc
            if code == 429:
                raise RateLimitError(f"Gemini rate limit exceeded (HTTP 429): {message}") from exc
            raise VisionProviderError(f"Gemini client error (HTTP {code}): {message}") from exc
        except genai_errors.ServerError as exc:
            code = getattr(exc, "code", None)
            message = getattr(exc, "message", None) or str(exc)
            raise VisionProviderError(f"Gemini server error (HTTP {code}): {message}") from exc
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(f"Gemini request timed out after {self.timeout_seconds}s") from exc
        except httpx.NetworkError as exc:
            raise NetworkError(f"Network failure calling Gemini: {exc}") from exc
        except genai_errors.APIError as exc:
            raise VisionProviderError(f"Gemini API error: {exc}") from exc

        latency_ms = (time.perf_counter() - start) * 1000

        raw_text = response.text or ""
        try:
            parsed_json = json.loads(raw_text)
        except (json.JSONDecodeError, TypeError):
            parsed_json = None

        if not raw_text.strip():
            raise MalformedResponseError("Gemini returned an empty response body")

        usage = getattr(response, "usage_metadata", None)
        input_tokens = getattr(usage, "prompt_token_count", None) if usage else None
        output_tokens = getattr(usage, "candidates_token_count", None) if usage else None

        return ProviderCallResult(
            raw_text=raw_text,
            parsed_json=parsed_json,
            model=self.model,
            latency_ms=latency_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
