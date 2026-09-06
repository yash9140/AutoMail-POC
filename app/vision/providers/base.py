"""Minimal Vision AI provider abstraction.

Providers only have to do one thing: take a raw image + goal + prompt
version, make the real API call, and return the raw text/JSON plus
whatever timing/usage numbers the SDK exposes — normalized into
`ProviderCallResult`. All error conditions are normalized into the
`VisionProviderError` subclasses below so calling code doesn't need to
know which SDK's exception type means "bad API key" vs "rate limited".

Schema validation, coordinate-bounds checking, retries, and result
persistence are NOT this module's job.

Copied from rnd/providers/base.py for the final POC runtime (app/ no
longer imports from rnd/ — see docs/architecture/01_ARCHITECTURE.md). The rnd/
original is left untouched since several rnd/experiments/*.py scripts
still depend on it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


class VisionProviderError(RuntimeError):
    """Base class for all normalized provider errors."""


class MissingAPIKeyError(VisionProviderError):
    """No API key was configured for this provider."""


class AuthenticationError(VisionProviderError):
    """The API key was rejected by the provider (invalid/expired/revoked)."""


class NetworkError(VisionProviderError):
    """A network-level failure occurred before a response was received."""


class ProviderTimeoutError(VisionProviderError):
    """The request exceeded the configured timeout."""


class RateLimitError(VisionProviderError):
    """The provider rejected the request due to rate limiting."""


class InvalidModelError(VisionProviderError):
    """The configured model name is not valid/available for this provider."""


class MalformedResponseError(VisionProviderError):
    """The provider returned a response that could not be parsed as JSON at all."""


@dataclass
class ProviderCallResult:
    raw_text: str
    parsed_json: Optional[dict]
    model: str
    latency_ms: float
    input_tokens: Optional[int]
    output_tokens: Optional[int]
    # SDK-reported termination reason (2026-09-06 REPLY_SEARCH structured-
    # output diagnostics), e.g. Anthropic's "end_turn" | "max_tokens" |
    # "stop_sequence" | "tool_use". None for a provider/SDK version that
    # doesn't expose one (e.g. this field is currently populated only by
    # AnthropicProvider) — never fabricated, never inferred.
    stop_reason: Optional[str] = None


class VisionProvider(ABC):
    provider_name: str

    @abstractmethod
    def analyze_screen(self, image_path: Path, goal: str, prompt_text: str, stage: str = "") -> ProviderCallResult:
        """Send image_path + prompt_text (which already embeds the goal and image
        dimensions) to the provider and return the raw result.

        stage (2026-09-06, diagnostics-only addition) is the same stage tag
        app/fallback/recovery.py's VISION_CALL_* logging already uses (e.g.
        "REPLY_SEARCH") — passed through so provider-level diagnostic
        logging (currently: AnthropicProvider's ANTHROPIC_RESPONSE_METADATA/
        ANTHROPIC_JSON_STRUCTURE_DIAGNOSTICS) can be attributed to the right
        stage without the provider needing to know anything else about the
        business flow. Purely optional/additive — never read by any
        decision path, never changes retry/fallback/validation behavior.

        Must raise one of the VisionProviderError subclasses above on failure —
        never let a raw SDK exception escape uncaught.
        """
        raise NotImplementedError
