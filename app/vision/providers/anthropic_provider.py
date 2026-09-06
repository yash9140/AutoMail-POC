"""Anthropic Claude Vision provider implementation.

Uses the official `anthropic` SDK's Messages API with base64 image input.
Normalizes SDK-specific exceptions into the VisionProviderError hierarchy
from app/vision/providers/base.py.

Copied from rnd/providers/anthropic_provider.py for the final POC
runtime. Empirically unproven against a real API call (blocked on
funding during RND-003B) — mock-tested only, same as the rnd/ original.
"""

from __future__ import annotations

import base64
import json
import logging
import time
from pathlib import Path
from typing import Optional

import anthropic

from app.vision.providers.base import (
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

# --- Response-termination / JSON-structure diagnostics (2026-09-06) ------
# Live evidence: a REPLY_SEARCH call returned response_char_count=1525 with
# json_parse_failed=True, and response_char_count alone cannot distinguish
# MAX_TOKENS truncation from markdown-fenced output, leading prose, or
# other malformed JSON — each needs a DIFFERENT fix (raise the output
# budget vs. add fence-stripping/parser hardening vs. something else), and
# none of those should be guessed at. These two log lines expose exactly
# the facts needed to tell those cases apart, never the response content
# itself (no raw_text, no email body) — see _json_structure_diagnostics().
_anthropic_logger = logging.getLogger("app.vision.providers.anthropic")
if not _anthropic_logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s [ANTHROPIC] %(message)s"))
    _anthropic_logger.addHandler(_handler)
    _anthropic_logger.setLevel(logging.INFO)
    _anthropic_logger.propagate = False


def _classify_leading_char(ch: str) -> str:
    """Categorizes (never returns) the first non-whitespace character of a
    response — a category label only, e.g. distinguishing a markdown
    fence (backtick) from a JSON object (brace) from stray prose
    (letter), without ever logging the character/text itself."""
    if ch == "{":
        return "brace"
    if ch == "`":
        return "backtick"
    if ch in "[\"'":
        return "other_json_like"
    if ch.isalpha():
        return "letter"
    return "other"


def _json_structure_diagnostics(text: str) -> dict:
    """Safe, content-free structural facts about a raw response that
    failed json.loads() — used to distinguish (A) truncated JSON, (B)
    markdown-fenced JSON the defensive stripping in analyze_screen()
    didn't fully remove, (C) prose before/after the JSON object, (D)
    other malformed JSON syntax. Never returns or logs any substring of
    `text` itself — only shape/character-class facts."""
    stripped = text.strip()
    if not stripped:
        return {
            "json_starts_with_object": False,
            "json_ends_with_object": False,
            "contains_markdown_fence": "```" in text,
            "brace_balance": 0,
            "leading_non_whitespace_char_type": "empty",
            "trailing_non_whitespace_after_object": False,
        }

    brace_balance = stripped.count("{") - stripped.count("}")
    json_starts_with_object = stripped.startswith("{")
    json_ends_with_object = stripped.endswith("}")
    leading_char_type = _classify_leading_char(stripped[0])

    # Find the first top-level '{...}' object (if any) and check whether
    # any non-whitespace content trails it — evidence of prose appended
    # after a syntactically-complete JSON object.
    trailing_non_whitespace_after_object = False
    first_brace = stripped.find("{")
    if first_brace != -1:
        depth = 0
        close_index = -1
        for i in range(first_brace, len(stripped)):
            if stripped[i] == "{":
                depth += 1
            elif stripped[i] == "}":
                depth -= 1
                if depth == 0:
                    close_index = i
                    break
        if close_index != -1 and stripped[close_index + 1:].strip():
            trailing_non_whitespace_after_object = True

    return {
        "json_starts_with_object": json_starts_with_object,
        "json_ends_with_object": json_ends_with_object,
        "contains_markdown_fence": "```" in text,
        "brace_balance": brace_balance,
        "leading_non_whitespace_char_type": leading_char_type,
        "trailing_non_whitespace_after_object": trailing_non_whitespace_after_object,
    }


# --- Narrow JSON-envelope extraction fallback (2026-09-06) ----------------
# Live evidence + a 5-trial static REPLY_SEARCH benchmark (5/5 reproduced)
# proved the failure mode: stop_reason=end_turn every time (MAX_TOKENS
# truncation disproven), contains_markdown_fence=False every time (not a
# fencing issue), yet json_starts_with_object=False with a syntactically
# COMPLETE, balanced JSON object present (brace_balance=0,
# json_ends_with_object=True) — Claude is prepending an explanatory
# sentence before an otherwise-perfect JSON object, despite the prompt's
# explicit "no prose before/after" instruction. json.loads() on the whole
# trimmed string fails purely because of that leading (and/or trailing)
# prose, not because the JSON itself is malformed.
#
# This is a narrow, safe fallback for EXACTLY that shape — never a general
# "try harder to parse anything" mechanism. It uses json.JSONDecoder().
# raw_decode(), which already correctly understands nested objects/arrays,
# escaped quotes, and braces appearing inside string values — never a
# naive '{'/'}' character count (which breaks on any of those).
#
# Object-only (per this project's response contract — every
# app.vision.models.*Response schema is a top-level object, never a
# list): only ever attempted from a literal '{' character, and the
# decoded value is required to be a dict — a top-level '[...]' is never
# silently accepted as if it were the expected object.
#
# Conservative on ambiguity: if MORE THAN ONE independently-decodable
# top-level JSON value follows in the text (after the first one ends),
# this returns None rather than guessing which one the caller "meant" —
# every current prompt in this project asks for exactly one JSON object,
# so a second one appearing is itself a sign something is wrong, not a
# case to silently resolve by picking the first.
def _extract_json_object(text: str) -> Optional[dict]:
    decoder = json.JSONDecoder()
    search_from = 0
    first_result: Optional[dict] = None

    while True:
        start_index = text.find("{", search_from)
        if start_index == -1:
            break
        candidate_text = text[start_index:]
        try:
            parsed, consumed_chars = decoder.raw_decode(candidate_text)
        except json.JSONDecodeError:
            search_from = start_index + 1
            continue
        if not isinstance(parsed, dict):
            search_from = start_index + 1
            continue
        if first_result is None:
            first_result = parsed
            search_from = start_index + consumed_chars
            continue
        # A second, independently-decodable top-level object follows —
        # ambiguous; never merge, never guess, never return either one.
        return None

    return first_result


class AnthropicProvider(VisionProvider):
    provider_name = "anthropic"

    def __init__(self, api_key: str, model: str, timeout_seconds: float = 30.0):
        if not api_key:
            raise MissingAPIKeyError("ANTHROPIC_API_KEY is not set")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self._client = anthropic.Anthropic(api_key=api_key, timeout=timeout_seconds)

    def analyze_screen(self, image_path: Path, goal: str, prompt_text: str, stage: str = "") -> ProviderCallResult:
        image_path = Path(image_path)
        image_b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")

        start = time.perf_counter()
        try:
            # Note: this SDK version's Messages.create() has no `temperature`
            # parameter (removed in the current Claude 5-generation API
            # surface).
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

        stop_reason: Optional[str] = getattr(response, "stop_reason", None)
        usage = getattr(response, "usage", None)
        input_tokens = getattr(usage, "input_tokens", None) if usage else None
        output_tokens = getattr(usage, "output_tokens", None) if usage else None

        # Diagnostics-only (2026-09-06) — logged for EVERY call (success or
        # parse failure) so a failing call's stop_reason/output_tokens can
        # be compared against a succeeding one for the same stage. Never
        # logs raw_text/email content — only counts and an SDK-reported
        # enum value.
        _anthropic_logger.info(
            "ANTHROPIC_RESPONSE_METADATA stage=%s stop_reason=%s input_tokens=%s output_tokens=%s "
            "response_char_count=%s",
            stage, stop_reason, input_tokens, output_tokens, len(raw_text),
        )

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

        if parsed_json is None:
            # Narrow envelope-extraction fallback — see _extract_json_object()'s
            # module-level docstring. Only ever reached when the normal parse
            # above failed; never replaces or precedes it.
            parsed_json = _extract_json_object(text_to_parse)
            if parsed_json is not None:
                _anthropic_logger.info(
                    "ANTHROPIC_JSON_ENVELOPE_EXTRACTED stage=%s response_char_count=%s",
                    stage, len(raw_text),
                )

        if parsed_json is None:
            diagnostics = _json_structure_diagnostics(raw_text)
            _anthropic_logger.info(
                "ANTHROPIC_JSON_STRUCTURE_DIAGNOSTICS stage=%s json_starts_with_object=%s "
                "json_ends_with_object=%s contains_markdown_fence=%s brace_balance=%s "
                "leading_non_whitespace_char_type=%s trailing_non_whitespace_after_object=%s",
                stage, diagnostics["json_starts_with_object"], diagnostics["json_ends_with_object"],
                diagnostics["contains_markdown_fence"], diagnostics["brace_balance"],
                diagnostics["leading_non_whitespace_char_type"],
                diagnostics["trailing_non_whitespace_after_object"],
            )

        return ProviderCallResult(
            raw_text=raw_text,
            parsed_json=parsed_json,
            model=response.model or self.model,
            latency_ms=latency_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            stop_reason=stop_reason,
        )
