"""Final POC — centralized configuration.

Every tunable safety limit/threshold/timing constant used across
app/outlook/*.py lives here, named and documented, instead of scattered
one-per-module as it was during the RND stages. These are safety
POLICIES (bounded limits, thresholds, timeouts) — never UI geometry or
click coordinates, which always come from live Vision grounding.

Phase 1 note: every constant below that has a direct RND-stage origin
keeps its EXACT original value, so moving call sites to import from here
is a pure reorganization with no behavior change. Constants marked
"declared for a later phase" are not yet referenced by any step module —
they exist now so later phases (scrolling, Send) have one place to add
their policy to, rather than inventing a new scattered constant.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

from app.vision.providers.anthropic_provider import AnthropicProvider
from app.vision.providers.base import VisionProvider
from app.vision.providers.gemini_provider import GeminiProvider

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# --- Provider selection (2026-09-04, Gemini demo-prep): the runtime
# provider is chosen EXPLICITLY by the AI_PROVIDER env var — "anthropic"
# or "gemini" — read fresh from .env on every get_provider() call, never
# a hardcoded module constant (a stale constant here would silently
# disagree with whatever .env actually says). There is NO fallback
# between providers in either direction: each branch below reads ONLY
# that provider's own *_API_KEY/*_MODEL env vars, and a missing/invalid
# key for the SELECTED provider is a hard SystemExit, never a silent
# switch to the other one. A technical failure of the selected provider
# is still a bounded, same-provider-only retry (app/fallback/
# recovery.py) that ends in TECHNICAL_PROVIDER_ERROR. OpenAI provider
# code remains in app/vision/providers/ for old tests/R&D but is never
# imported here and is unreachable from this function either way.
#
# DEFAULT_AI_PROVIDER is used ONLY when AI_PROVIDER is absent from .env
# entirely (never overrides an explicitly-set value) — kept as
# "anthropic" so existing deployments/tests that predate this env-var
# switch keep behaving exactly as before without needing a new .env
# entry.
DEFAULT_AI_PROVIDER = "anthropic"
SUPPORTED_AI_PROVIDERS = ("anthropic", "gemini")

_startup_logger = logging.getLogger("app.config.settings.startup")
if not _startup_logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s [STARTUP] %(message)s"))
    _startup_logger.addHandler(_handler)
    _startup_logger.setLevel(logging.INFO)
    _startup_logger.propagate = False

# --- Timing: Windows Search / Outlook launch (from outlook_launch_steps.py) ---
WINDOWS_KEY_STABILIZE_SECONDS = 0.9
SEARCH_TYPE_STABILIZE_SECONDS = 1.2
TYPE_INTERVAL_SECONDS = 0.03
MOVE_DURATION_SECONDS = 0.6
OUTLOOK_LAUNCH_INITIAL_WAIT_SECONDS = 2.0
OUTLOOK_LAUNCH_POLL_INTERVAL_SECONDS = 1.0
OUTLOOK_LAUNCH_TIMEOUT_SECONDS = 18.0

# Readiness hardening (splash vs. ready) — up to 2 attempts, each with its
# own wait, before OUTLOOK_READY_TIMEOUT is a genuine failure.
READINESS_INITIAL_WAIT_SECONDS = 6.0
READINESS_RETRY_WAIT_SECONDS = 3.0
MAX_READINESS_ATTEMPTS = 2

# Alias for the constant name named explicitly in the POC plan's
# "safety limits" list — same value/semantics as OUTLOOK_LAUNCH_TIMEOUT_SECONDS,
# which remains the name actually used by app/outlook/launch.py to avoid a
# gratuitous rename of already-tested code in this pass.
OUTLOOK_READY_TIMEOUT = OUTLOOK_LAUNCH_TIMEOUT_SECONDS

# Maximize enforcement (Phase 2).
MAXIMIZE_STABILIZE_WAIT_SECONDS = 1.0
UI_STABILIZATION_WAIT = MAXIMIZE_STABILIZE_WAIT_SECONDS  # alias — the generic policy name

# Aliases for the constant names named explicitly in the POC plan's
# "safety limits" list — same values/semantics as the originals, which
# remain the names actually used by app/outlook/launch.py.
OUTLOOK_READINESS_MAX_ATTEMPTS = MAX_READINESS_ATTEMPTS
OUTLOOK_READINESS_RETRY_WAIT = READINESS_RETRY_WAIT_SECONDS

# --- Vision confidence: one shared threshold (from outlook_launch_steps.py
# GROUNDING_CONFIDENCE_THRESHOLD and find_open_email_steps.py
# EMAIL_GROUNDING_CONFIDENCE_THRESHOLD — both were 0.6, so consolidating
# them into one named constant changes nothing behaviorally). ---
VISION_CONFIDENCE_THRESHOLD = 0.6

# --- Find/open email (from find_open_email_steps.py) ---
POST_CLICK_INITIAL_WAIT_SECONDS = 1.5
POST_CLICK_RETRY_WAIT_SECONDS = 1.0
MAX_EMAIL_OPEN_VERIFICATION_ATTEMPTS = 2

# Secondary safety heuristic only (never the source of target geometry —
# see app/safety/validators.py) — rejects a converted coordinate that
# falls in the left-nav-sidebar fraction of screen width, regardless of
# Vision's own stated confidence/region claim.
LEFT_SIDEBAR_MAX_X_FRACTION = 0.17

# --- Reply / draft (from reply_draft_steps.py) ---
FOCUS_SETTLE_DELAY_SECONDS = 0.6
REPLY_EDITOR_INITIAL_WAIT_SECONDS = 1.5
REPLY_EDITOR_RETRY_WAIT_SECONDS = 1.0
MAX_REPLY_EDITOR_VERIFICATION_ATTEMPTS = 2
POST_TYPING_WAIT_SECONDS = 1.5
DRAFT_MIN_LENGTH = 5
PLACEHOLDER_MARKERS = ("[insert", "TODO", "lorem ipsum", "<placeholder>", "XXX")

# Draft verification retry (Phase 6) — same bounded observation-only-retry
# shape as MAX_REPLY_EDITOR_VERIFICATION_ATTEMPTS: a fresh screenshot per
# attempt, never a re-click/re-type on retry.
MAX_DRAFT_VERIFICATION_ATTEMPTS = 2
DRAFT_VERIFICATION_RETRY_WAIT_SECONDS = 1.0

# --- Find email (Phase 3) ---
MAX_MESSAGE_LIST_SCROLL_ATTEMPTS = 5
MESSAGE_LIST_SCROLL_AMOUNT = -6            # scroll-wheel units passed to the automation layer; negative = scroll down (toward older mail)
MESSAGE_LIST_SCROLL_STABILIZE_WAIT_SECONDS = 1.0
# Where the mouse is positioned before a scroll wheel event — expressed
# as fractions of the current screenshot's dimensions (never fixed
# pixels), safely clear of the left-nav sidebar. This positions the
# cursor only; it is NEVER used to compute a click location — click
# points always come from validated Vision-reported bboxes.
MESSAGE_LIST_SCROLL_ANCHOR_X_FRACTION = 0.35
MESSAGE_LIST_SCROLL_ANCHOR_Y_FRACTION = 0.5

# --- Read email / long-email accumulation (Phase 4) ---
MAX_EMAIL_BODY_SCROLL_ATTEMPTS = 5
EMAIL_BODY_SCROLL_AMOUNT = -6              # scroll-wheel units; negative = scroll down (further into the body)
EMAIL_BODY_SCROLL_STABILIZE_WAIT_SECONDS = 1.0
# Mouse position before a body scroll — a fraction of the current
# screenshot, inside the reading pane only (right of the message list,
# clear of the sidebar). Positions the cursor only — never a click
# location. Distinct from MESSAGE_LIST_SCROLL_ANCHOR_*, which targets
# the message-list pane instead.
EMAIL_BODY_SCROLL_ANCHOR_X_FRACTION = 0.7
EMAIL_BODY_SCROLL_ANCHOR_Y_FRACTION = 0.5
# How much of the previous section's tail is carried into the next
# section's prompt as already_read_tail, for overlap detection —
# verbatim, never summarized (summarizing would lose the exact boundary
# text needed to detect/strip overlap).
EMAIL_SECTION_TAIL_CHARS = 200

# --- Find Reply (Phase 5) — reuses scroll_email_body() (Phase 4), no
# second Reply-specific scroll function ---
MAX_REPLY_SEARCH_SCROLL_ATTEMPTS = 5
REPLY_SEARCH_SCROLL_STABILIZE_WAIT_SECONDS = 1.0

# --- Cross-phase (wired in since Phase 2) ---
PROVIDER_RETRY_COUNT = 1                   # bounded provider-retry wrapper — app/fallback/recovery.py

# --- Send (Phase 7) — reuses VISION_CONFIDENCE_THRESHOLD, MOVE_DURATION_SECONDS,
# PROVIDER_RETRY_COUNT above; no Send-specific confidence/move tuning needed. ---
SEND_CLICK_MAX = 1                         # hard ceiling, enforced independently in code — never just this constant
MAX_SEND_VERIFICATION_ATTEMPTS = 2
SEND_VERIFICATION_INITIAL_WAIT_SECONDS = 1.5
SEND_VERIFICATION_RETRY_WAIT_SECONDS = 1.0


def get_ai_provider_name() -> str:
    """Loads .env and returns the configured provider name ("anthropic"
    or "gemini") without constructing anything — the single place that
    reads AI_PROVIDER, so get_provider() and any caller that just needs
    to know which provider is active (e.g. app/outlook/launch.py's
    OUTLOOK_SEARCH strategy dispatch) agree on the exact same value."""
    load_dotenv(PROJECT_ROOT / ".env")
    name = os.environ.get("AI_PROVIDER", DEFAULT_AI_PROVIDER).strip().lower()
    if name not in SUPPORTED_AI_PROVIDERS:
        raise SystemExit(
            f"Unsupported AI_PROVIDER={name!r} in .env. Supported: {SUPPORTED_AI_PROVIDERS}. "
            "There is no fallback — fix .env instead of relying on a default."
        )
    return name


def get_provider() -> tuple[VisionProvider, str]:
    """Loads .env and constructs whichever Vision/LLM provider
    AI_PROVIDER selects — "anthropic" (Claude) or "gemini" — read fresh
    on every call via get_ai_provider_name(), never a stale cached
    constant. Renamed from the `_provider()` helper duplicated in
    outlook_launch_steps.py and rnd/experiments/rnd008_controlled_send.py
    — one copy now.

    Each branch below reads ONLY that provider's own *_API_KEY/*_MODEL
    env vars — the anthropic branch never looks at GEMINI_*/OPENAI_*,
    and the gemini branch never looks at ANTHROPIC_*/OPENAI_*. A
    missing/invalid key for the SELECTED provider is a hard SystemExit;
    there is no fallback path here to disable, in either direction."""
    provider_name = get_ai_provider_name()
    timeout = float(os.environ.get("VISION_REQUEST_TIMEOUT_SECONDS", "30"))

    if provider_name == "gemini":
        model = os.environ.get("GEMINI_MODEL", "")
        api_key = os.environ.get("GEMINI_API_KEY", "")
        if not api_key or not model:
            raise SystemExit("GEMINI_API_KEY / GEMINI_MODEL must be set in .env when AI_PROVIDER=gemini")
        _startup_logger.info("AI_PROVIDER=%s", provider_name)
        _startup_logger.info("MODEL=%s", model)
        _startup_logger.info("FALLBACK_PROVIDER=disabled")
        return GeminiProvider(api_key=api_key, model=model, timeout_seconds=timeout), model

    model = os.environ.get("ANTHROPIC_MODEL", "")
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key or not model:
        raise SystemExit("ANTHROPIC_API_KEY / ANTHROPIC_MODEL must be set in .env when AI_PROVIDER=anthropic")
    _startup_logger.info("AI_PROVIDER=%s", provider_name)
    _startup_logger.info("MODEL=%s", model)
    _startup_logger.info("FALLBACK_PROVIDER=disabled")
    return AnthropicProvider(api_key=api_key, model=model, timeout_seconds=timeout), model


def vision_request_timeout_seconds() -> float:
    return float(os.environ.get("VISION_REQUEST_TIMEOUT_SECONDS", "30"))
