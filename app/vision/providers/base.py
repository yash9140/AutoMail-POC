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
longer imports from rnd/ — see docs/poc/01_ARCHITECTURE.md). The rnd/
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


class VisionProvider(ABC):
    provider_name: str

    @abstractmethod
    def analyze_screen(self, image_path: Path, goal: str, prompt_text: str) -> ProviderCallResult:
        """Send image_path + prompt_text (which already embeds the goal and image
        dimensions) to the provider and return the raw result.

        Must raise one of the VisionProviderError subclasses above on failure —
        never let a raw SDK exception escape uncaught.
        """
        raise NotImplementedError
