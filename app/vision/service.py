"""Provider-neutral Vision service (2026-09-05, Claude-primary/Gemini-
fallback architecture).

This is the ONE place in the runtime that owns:
  - same-provider retry (delegated to app.fallback.recovery.
    call_with_provider_retry, unchanged)
  - cross-provider fallback, technical failures only
  - response-schema validation

Every app/outlook/*.py step calls VisionService.analyze() instead of
touching a VisionProvider or call_with_provider_retry directly — this
replaces what used to be ~14 near-identical call sites each doing their
own call_with_provider_retry(...) + try/except ValidationError.

Fallback contract:
  - Fallback is attempted ONLY when the primary provider's result is a
    TECHNICAL failure — every same-provider retry attempt raised a
    VisionProviderError, OR the primary's response was schema-invalid
    (empty/non-JSON text, or JSON that doesn't match response_model).
  - A schema-VALID response is always treated as a real answer, whether
    the semantic content is "yes" or "no" (e.g. EmailSearchResponse
    with candidate_count=0 is schema-valid) — fallback is NEVER used to
    re-ask a question the primary already validly answered. That
    distinction is enforced structurally here: _validate() is the only
    thing that decides "did we get an answer," and it knows nothing
    about what the answer means.
  - Fallback reuses the SAME screenshot_path passed in — no recapture
    between primary and fallback. The caller must not have performed
    any physical action between constructing the request and getting
    this call's result.
  - Fallback itself defaults to max_retries=0 (one bounded attempt, not
    a second full retry cycle) — deliberately asymmetric with the
    primary's retry budget.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Generic, Optional, Type, TypeVar

from dotenv import load_dotenv
from pydantic import BaseModel, ValidationError

from app.fallback.recovery import call_with_provider_retry
from app.vision.providers.anthropic_provider import AnthropicProvider
from app.vision.providers.base import VisionProvider
from app.vision.providers.gemini_provider import GeminiProvider

T = TypeVar("T", bound=BaseModel)

_service_logger = logging.getLogger("app.vision.service")
if not _service_logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s [VISION] %(message)s"))
    _service_logger.addHandler(_handler)
    _service_logger.setLevel(logging.INFO)
    _service_logger.propagate = False

_startup_logger = logging.getLogger("app.vision.service.startup")
if not _startup_logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s [STARTUP] %(message)s"))
    _startup_logger.addHandler(_handler)
    _startup_logger.setLevel(logging.INFO)
    _startup_logger.propagate = False


@dataclass
class VisionRequest(Generic[T]):
    """Describes the business/perception task, not the provider. stage
    identifies which of the pipeline's Vision stages this is (same
    stage-tag vocabulary app/fallback/recovery.py's logging already
    uses, e.g. "TARGET_EMAIL_SEARCH", "DRAFT_GENERATION")."""

    stage: str
    screenshot_path: Path
    goal: str
    prompt_text: str
    response_model: Type[T]


@dataclass
class VisionCallMetrics:
    """Metrics for the ONE call whose result was actually used — the
    successful primary call, or the successful fallback call. Failed
    attempts (primary retries that raised, or a primary technical
    failure that triggered fallback) are not separately itemized here;
    their count is on primary_retries/fallback_retries on VisionOutcome."""

    provider_name: str
    model: str
    latency_ms: float
    input_tokens: Optional[int]
    output_tokens: Optional[int]


@dataclass
class VisionOutcome(Generic[T]):
    parsed: Optional[T]
    provider_used: Optional[str] = None
    model_used: Optional[str] = None
    fallback_used: bool = False
    primary_retries: int = 0
    fallback_retries: int = 0
    call_metrics: Optional[VisionCallMetrics] = None
    error: Optional[str] = None
    # Only meaningful when parsed is None. True when the LAST attempt
    # (fallback's, if one was made; otherwise primary's) returned a
    # response that didn't validate against response_model — a
    # provider-level ProviderCallResult WAS received, it just wasn't
    # schema-valid. False when the last attempt exhausted its retry
    # budget via raised VisionProviderError exceptions instead (a true
    # transport/technical failure, no response body to even inspect).
    # Existed because a few call sites (e.g. email understanding, draft
    # generation) deliberately route these two terminal outcomes to
    # different failure_reason/playbook-terminal-state — see each call
    # site for why; most just use TECHNICAL_PROVIDER_ERROR for both and
    # don't need to look at this field at all.
    schema_invalid: bool = False


def _validate(response_model: Type[T], parsed_json: Optional[dict]) -> Optional[T]:
    """The ONE place a raw provider JSON payload becomes (or fails to
    become) a validated common application response model. Returns None
    for anything that isn't a schema-valid instance — empty/absent JSON,
    or JSON that doesn't match response_model — never partially trusts
    a malformed payload."""
    if not parsed_json:
        return None
    try:
        return response_model.model_validate(parsed_json)
    except ValidationError:
        return None


class VisionService:
    """Owns exactly one primary VisionProvider and an optional fallback
    VisionProvider. Constructed once per run in the composition root
    (app.config.settings.get_vision_service()) — app/outlook/*.py never
    constructs a VisionProvider directly.

    default_max_retries is a plain int (not read from app.config.settings
    here) so this module never imports settings — settings.py is the
    thing that constructs VisionService, so settings -> service would be
    circular if service also imported settings. get_vision_service()
    passes PROVIDER_RETRY_COUNT in explicitly at construction time."""

    def __init__(
        self, primary: VisionProvider, fallback: Optional[VisionProvider] = None, default_max_retries: int = 1,
    ) -> None:
        self.primary = primary
        self.fallback = fallback
        self.default_max_retries = default_max_retries
        # Running totals across this VisionService's whole lifetime (one
        # run) — for end-of-run provider-usage metrics (claude_calls/
        # gemini_calls/fallback_count style reporting). Counts analyze()
        # invocations that reached each provider, not raw HTTP calls —
        # a same-provider retry inside call_with_provider_retry doesn't
        # add to these.
        self.primary_calls = 0
        self.fallback_calls = 0

    def analyze(self, request: VisionRequest[T], *, max_retries: Optional[int] = None) -> VisionOutcome[T]:
        effective_max_retries = self.default_max_retries if max_retries is None else max_retries

        primary_outcome = call_with_provider_retry(
            lambda: self.primary.analyze_screen(request.screenshot_path, request.goal, request.prompt_text),
            stage=request.stage, provider_name=self.primary.provider_name, max_retries=effective_max_retries,
        )
        self.primary_calls += 1

        primary_parsed: Optional[T] = None
        primary_error = primary_outcome.error
        primary_schema_invalid = False
        if primary_outcome.result is not None:
            primary_parsed = _validate(request.response_model, primary_outcome.result.parsed_json)
            if primary_parsed is None:
                primary_error = "Primary provider response was not schema-valid."
                primary_schema_invalid = True

        if primary_parsed is not None:
            call = primary_outcome.result
            _service_logger.info(
                "VISION_RESULT stage=%s provider_used=%s fallback_used=False total_provider_duration_ms=%.1f",
                request.stage, self.primary.provider_name, call.latency_ms,
            )
            return VisionOutcome(
                parsed=primary_parsed, provider_used=self.primary.provider_name, model_used=call.model,
                fallback_used=False, primary_retries=primary_outcome.retries_used, fallback_retries=0,
                call_metrics=VisionCallMetrics(
                    provider_name=self.primary.provider_name, model=call.model, latency_ms=call.latency_ms,
                    input_tokens=call.input_tokens, output_tokens=call.output_tokens,
                ),
            )

        # Primary technical failure: either every retry attempt raised
        # VisionProviderError, or its response didn't validate against
        # response_model. Never reached by a schema-valid semantic "no"/
        # "not found"/"mismatch" answer — that returns above.
        if self.fallback is None:
            _service_logger.warning(
                "VISION_RESULT stage=%s provider_used=None fallback_used=False error=%r", request.stage, primary_error,
            )
            return VisionOutcome(
                parsed=None, provider_used=None, fallback_used=False,
                primary_retries=primary_outcome.retries_used, error=primary_error,
                schema_invalid=primary_schema_invalid,
            )

        _service_logger.warning(
            "VISION_FALLBACK_TRIGGERED stage=%s primary_provider=%s fallback_provider=%s technical_reason=%r",
            request.stage, self.primary.provider_name, self.fallback.provider_name, primary_error,
        )
        fallback_outcome = call_with_provider_retry(
            lambda: self.fallback.analyze_screen(request.screenshot_path, request.goal, request.prompt_text),
            stage=request.stage, provider_name=self.fallback.provider_name, max_retries=0,
        )
        self.fallback_calls += 1

        fallback_parsed: Optional[T] = None
        fallback_error = fallback_outcome.error
        fallback_schema_invalid = False
        if fallback_outcome.result is not None:
            fallback_parsed = _validate(request.response_model, fallback_outcome.result.parsed_json)
            if fallback_parsed is None:
                fallback_error = "Fallback provider response was not schema-valid."
                fallback_schema_invalid = True

        if fallback_parsed is not None:
            call = fallback_outcome.result
            _service_logger.info(
                "VISION_RESULT stage=%s provider_used=%s fallback_used=True total_provider_duration_ms=%.1f",
                request.stage, self.fallback.provider_name, call.latency_ms,
            )
            return VisionOutcome(
                parsed=fallback_parsed, provider_used=self.fallback.provider_name, model_used=call.model,
                fallback_used=True, primary_retries=primary_outcome.retries_used,
                fallback_retries=fallback_outcome.retries_used,
                call_metrics=VisionCallMetrics(
                    provider_name=self.fallback.provider_name, model=call.model, latency_ms=call.latency_ms,
                    input_tokens=call.input_tokens, output_tokens=call.output_tokens,
                ),
            )

        combined_error = f"primary: {primary_error}; fallback: {fallback_error}"
        _service_logger.warning(
            "VISION_RESULT stage=%s provider_used=None fallback_used=True error=%r", request.stage, combined_error,
        )
        return VisionOutcome(
            parsed=None, provider_used=None, fallback_used=True,
            primary_retries=primary_outcome.retries_used, fallback_retries=fallback_outcome.retries_used,
            error=combined_error, schema_invalid=fallback_schema_invalid,
        )


# --- Composition root: constructs the primary+fallback VisionService
# (2026-09-05). Deliberately lives here rather than in
# app.config.settings — that module is imported BY app.fallback.recovery
# (for PROVIDER_RETRY_COUNT), which this module already imports, so
# settings importing this module back would be circular. app.config.
# settings.get_provider() (single-provider, no fallback) is left
# completely untouched for backward compatibility — this is purely
# additive. ---

from app.config.settings import (  # noqa: E402
    PROJECT_ROOT,
    PROVIDER_RETRY_COUNT,
    SUPPORTED_AI_PROVIDERS,
    get_ai_provider_name,
)


def _construct_named_provider(provider_name: str, timeout: float) -> tuple[VisionProvider, str]:
    """Constructs exactly one provider by name, reading ONLY that
    provider's own *_API_KEY/*_MODEL env vars — same per-provider
    isolation app.config.settings.get_provider() already enforces
    (deliberately duplicated here in miniature rather than refactoring
    that already-proven function)."""
    if provider_name == "gemini":
        model = os.environ.get("GEMINI_MODEL", "")
        api_key = os.environ.get("GEMINI_API_KEY", "")
        if not api_key or not model:
            raise SystemExit(
                "GEMINI_API_KEY / GEMINI_MODEL must be set in .env to use gemini as a vision provider "
                "(primary or fallback)."
            )
        return GeminiProvider(api_key=api_key, model=model, timeout_seconds=timeout), model
    if provider_name == "anthropic":
        model = os.environ.get("ANTHROPIC_MODEL", "")
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key or not model:
            raise SystemExit(
                "ANTHROPIC_API_KEY / ANTHROPIC_MODEL must be set in .env to use anthropic as a vision provider "
                "(primary or fallback)."
            )
        return AnthropicProvider(api_key=api_key, model=model, timeout_seconds=timeout), model
    raise SystemExit(f"Unsupported vision provider {provider_name!r}. Supported: {SUPPORTED_AI_PROVIDERS}.")


def get_primary_vision_provider_name() -> str:
    """PRIMARY_VISION_PROVIDER, falling back to AI_PROVIDER (today's
    single-provider selector, via get_ai_provider_name()) when unset —
    so an existing .env with only AI_PROVIDER=... set keeps selecting
    the exact same primary provider, unchanged."""
    load_dotenv(PROJECT_ROOT / ".env")
    raw = os.environ.get("PRIMARY_VISION_PROVIDER", "").strip().lower()
    name = raw or get_ai_provider_name()
    if name not in SUPPORTED_AI_PROVIDERS:
        raise SystemExit(
            f"Unsupported PRIMARY_VISION_PROVIDER={name!r} in .env. Supported: {SUPPORTED_AI_PROVIDERS}."
        )
    return name


def get_fallback_vision_provider_name() -> Optional[str]:
    """FALLBACK_VISION_PROVIDER — unset, or equal to the primary, means
    no fallback (identical to today's zero-fallback behavior)."""
    load_dotenv(PROJECT_ROOT / ".env")
    raw = os.environ.get("FALLBACK_VISION_PROVIDER", "").strip().lower()
    if not raw:
        return None
    if raw not in SUPPORTED_AI_PROVIDERS:
        raise SystemExit(
            f"Unsupported FALLBACK_VISION_PROVIDER={raw!r} in .env. Supported: {SUPPORTED_AI_PROVIDERS}."
        )
    if raw == get_primary_vision_provider_name():
        return None  # no self-fallback
    return raw


def get_vision_service() -> tuple[VisionService, str]:
    """Composition root for the Claude-primary/Gemini-fallback
    architecture. Returns (VisionService, primary_model) — primary_model
    kept in the return tuple to match get_provider()'s existing
    (provider, model) shape for callers that only need the model name
    for logging.

    Backward compatible: PRIMARY_VISION_PROVIDER unset -> primary is
    whatever AI_PROVIDER already selects. FALLBACK_VISION_PROVIDER unset
    -> fallback is None, i.e. IDENTICAL zero-fallback behavior to
    get_provider() today. Each provider's own *_API_KEY/*_MODEL env vars
    are read ONLY when that provider is actually primary or fallback —
    an unconfigured third provider's env vars are never touched."""
    timeout = float(os.environ.get("VISION_REQUEST_TIMEOUT_SECONDS", "30"))

    primary_name = get_primary_vision_provider_name()
    primary_provider, primary_model = _construct_named_provider(primary_name, timeout)

    fallback_name = get_fallback_vision_provider_name()
    fallback_provider: Optional[VisionProvider] = None
    if fallback_name is not None:
        fallback_provider, _ = _construct_named_provider(fallback_name, timeout)

    _startup_logger.info("PRIMARY_VISION_PROVIDER=%s", primary_name)
    _startup_logger.info("MODEL=%s", primary_model)
    _startup_logger.info("FALLBACK_VISION_PROVIDER=%s", fallback_name or "disabled")

    return VisionService(primary_provider, fallback_provider, default_max_retries=PROVIDER_RETRY_COUNT), primary_model
