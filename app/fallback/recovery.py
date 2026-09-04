"""Bounded provider-retry wrapper.

Retries a Vision provider call up to `PROVIDER_RETRY_COUNT` additional
times, but ONLY on a technical failure (`VisionProviderError` — HTTP
429/503/504/403, timeout, network error). A semantic Vision response
(schema-valid JSON saying "no" / low confidence / wrong target) is
never retried here — that's a real answer, not a technical outage, and
retrying it would blur the "provider error vs. semantic failure"
distinction this project has repeatedly insisted on keeping separate.

This is a pure call-retry — it never performs a second physical mouse/
keyboard action, and never triggers another Windows-Search-launch or
maximize attempt. It only re-issues the same read-only Vision call.

Also the ONE shared choke-point every Vision call in this codebase goes
through (see app/outlook/*.py — even the two call sites that pass
max_retries=0, e.g. draft generation/verification, route through here
so their stage-tagged logging and provider_retries bookkeeping stay
consistent with every other call, without adding a retry where none
previously existed) — so VISION_CALL_START/END/FAILED structured
diagnostics live in exactly one place. Never logs secrets (API keys,
auth headers) — only provider/stage/timing/exception-class/status-code.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable, Generic, Optional, TypeVar

from app.config.settings import PROVIDER_RETRY_COUNT
from app.vision.providers.base import VisionProviderError

T = TypeVar("T")

_call_logger = logging.getLogger("app.fallback.recovery.vision_calls")
if not _call_logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s [VISION] %(message)s"))
    _call_logger.addHandler(_handler)
    _call_logger.setLevel(logging.INFO)
    _call_logger.propagate = False


@dataclass
class ProviderCallOutcome(Generic[T]):
    result: Optional[T]
    retries_used: int
    error: Optional[str] = None


def call_with_provider_retry(
    fn: Callable[[], T], stage: str, provider_name: str, max_retries: int = PROVIDER_RETRY_COUNT,
) -> ProviderCallOutcome[T]:
    """Calls fn() once, then up to max_retries more times if it keeps
    raising VisionProviderError. Any other exception propagates
    immediately, uncaught — only provider-technical failures are
    retried here. stage identifies which of the 11 live-runtime Vision
    stages this call belongs to (e.g. TARGET_EMAIL_SEARCH,
    DRAFT_GENERATION) — required so VISION_CALL_* logs and failures are
    diagnosable without guessing which stage failed."""
    last_error: Optional[str] = None
    for attempt in range(max_retries + 1):
        _call_logger.info("VISION_CALL_START provider=%s stage=%s attempt=%s", provider_name, stage, attempt + 1)
        start = time.perf_counter()
        try:
            result = fn()
        except VisionProviderError as exc:
            duration_ms = (time.perf_counter() - start) * 1000
            # status_code lives on the ORIGINAL SDK exception (exc.__cause__,
            # from `raise XError(...) from exc`), not on our own normalized
            # VisionProviderError subclasses — check both, safe either way.
            status_code = getattr(exc, "status_code", None) or getattr(exc.__cause__, "status_code", None)
            _call_logger.warning(
                "VISION_CALL_FAILED provider=%s stage=%s attempt=%s exception_type=%s status_code=%s duration_ms=%.1f",
                provider_name, stage, attempt + 1, type(exc).__name__, status_code, duration_ms,
            )
            last_error = str(exc)
            continue
        duration_ms = (time.perf_counter() - start) * 1000
        _call_logger.info(
            "VISION_CALL_END provider=%s stage=%s attempt=%s duration_ms=%.1f",
            provider_name, stage, attempt + 1, duration_ms,
        )
        return ProviderCallOutcome(result=result, retries_used=attempt)
    return ProviderCallOutcome(result=None, retries_used=max_retries, error=last_error)
