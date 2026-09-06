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

# Diagnostic-only tolerance (2026-09-06) for EMAIL_MOUSE_POSITION_CONFIRMED
# — a cheap post-move cursor-position read logged for observability,
# never a correction loop and never a reason to fail the click flow. A
# tiny pixel tolerance absorbs harmless rounding, never a meaningful
# geometry margin.
CURSOR_POSITION_TOLERANCE_PX = 2

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

# --- Email-reading scroll DISTANCE fix (2026-09-06 long-email live
# failure) --------------------------------------------------------------
# A live long-email run safe-stopped as CONTENT_NOT_FULLY_READ
# (unresolved_after_no_progress) after 4 sections — correctly: the no-
# progress detector is NOT weakened here. The human supervisor observed
# each scroll moving the email only a very small amount. Offline replay
# against the run's own saved screenshots (benchmarks/email_reading/
# measure_scroll_displacement.py, image-alignment on the reading-pane
# ROI, no OCR) confirmed it precisely: EMAIL_BODY_SCROLL_AMOUNT=-6
# produced an estimated ~6-7px of actual vertical content movement per
# scroll, against a ~702px-tall visible reading pane — under 1% of the
# viewport per scroll, four consecutive scrolls in the failed run barely
# advanced past the email's own greeting line. This is a scroll-DISTANCE
# problem only; content-completion semantics, current_message_continues_
# below/end_of_message_visible/conversation_history_visible_below, and
# the no-progress safe-stop are all untouched.
#
# EMAIL_BODY_SCROLL_AMOUNT itself (above) is intentionally left
# UNCHANGED — app.outlook.reply.py's own Reply-search scroll loop reuses
# scroll_email_body()/that exact constant (see that module's docstring,
# "never a second Reply-specific scroll function"), and Reply's own
# behavior is explicitly out of scope for this fix. Email reading gets
# its OWN, independent scroll amount/function
# (app.automation.scrolling.scroll_email_reading_body) — same
# established pattern this project already uses elsewhere for "same
# geometry, independently calibrated constant" (e.g.
# SEND_COMPOSER_READING_PANE_LEFT_FRACTION vs REPLY_VISION_CROP_LEFT_
# FRACTION) — so retuning one can never silently change the other.
#
# EMAIL_READING_SCROLL_AMOUNT is a deliberately large, bounded jump from
# the measured broken baseline (20x -6), aimed at the middle of the
# task's target range (45%-60% of the visible viewport per scroll) —
# but Windows/Outlook wheel-unit-to-pixel response is NOT assumed linear
# at this magnitude (explicitly warned against), so this value is a
# reasoned STARTING POINT, not a guaranteed result. It MUST be confirmed
# (and re-tuned if necessary) against the actual EMAIL_SCROLL_MOVEMENT_
# RESULT difference_score logged on the next live long-email run — see
# this fix's final report, "exact next long-email live validation
# sequence."
#
# EMAIL_READING_SCROLL_AMOUNT_BOOSTED is the ONE-TIME stronger scroll
# app.outlook.read_email.EmailUnderstandingSteps allows on the NEXT
# required scroll only, if the movement guard finds the current scroll's
# own visual change is not clearly above the measured broken baseline —
# never a second physical scroll issued back-to-back without a fresh
# Vision observation in between (see MAX_EMAIL_BODY_SCROLL_ATTEMPTS'
# existing per-section loop, unchanged).
EMAIL_READING_SCROLL_AMOUNT = -120
EMAIL_READING_SCROLL_AMOUNT_BOOSTED = -220

# Reading-pane ROI (fractions of the full screenshot) used ONLY by the
# post-scroll movement guard (EMAIL_SCROLL_MOVEMENT_RESULT) — a generic,
# diagnostic-purpose region, NOT a claim about any exact UI boundary.
# y_min is deliberately BELOW the reading pane's own sticky subject-
# line banner/ribbon: an early version of the offline replay above
# included that banner and it stayed pixel-identical across every
# scroll regardless of true body movement, making a real (if tiny)
# scroll look like "no change at all." Excluding it is what let the
# true small-but-real shift become measurable, and the same exclusion
# applies here so the runtime guard isn't fooled the same way.
EMAIL_BODY_SCROLL_MOVEMENT_ROI_X_MIN_FRACTION = 0.36
EMAIL_BODY_SCROLL_MOVEMENT_ROI_X_MAX_FRACTION = 0.98
EMAIL_BODY_SCROLL_MOVEMENT_ROI_Y_MIN_FRACTION = 0.30
EMAIL_BODY_SCROLL_MOVEMENT_ROI_Y_MAX_FRACTION = 0.95

# Difference-score thresholds (same 0-255 mean-abs-grayscale-difference
# scale as EMAIL_PRECLICK_FRESHNESS_THRESHOLD / app.safety.
# screen_freshness.compute_roi_difference_score — reused unchanged, not
# reimplemented), calibrated against the SAME offline replay: the
# failed run's own (too-small, ~6-7px) scrolls produced a difference_
# score of ~19-21 in this ROI; a truly stuck/no-op scroll (cursor never
# reaching the pane, focus lost, etc.) produces ~0.
#   changed         = difference_score > CHANGED_THRESHOLD (a low bar —
#                      "some real visual change happened at all", never
#                      a magnitude judgement)
#   scroll_sufficient = difference_score >= SUFFICIENT_THRESHOLD (set to
#                      roughly double the measured too-small baseline —
#                      a new scroll must clearly beat the KNOWN-broken
#                      baseline to count as sufficient; deliberately
#                      conservative, not a precise 45%-60% detector,
#                      since that would require live data at the new
#                      magnitude this project does not yet have)
# Neither threshold determines content completion — that remains
# entirely Vision's (current_message_continues_below etc.) job.
EMAIL_BODY_SCROLL_MOVEMENT_CHANGED_THRESHOLD = 3.0
EMAIL_BODY_SCROLL_MOVEMENT_SUFFICIENT_THRESHOLD = 40.0

# --- Target-email row bbox plausibility (2026-09-06 live fix) -------------
# A live run's Vision-returned candidate row_bbox extended horizontally
# far past the real message-list column and into the reading pane
# (raw_bbox x_max ~57% of a 1920px-wide screen) — no check anywhere
# validated the ROW BBOX ITSELF against plausible message-list geometry,
# only the derived click point's sidebar safety (see LEFT_SIDEBAR_MAX_X_
# FRACTION above). Clicking a fraction of a too-wide bbox still risks
# landing outside the real, on-screen row.
#
# MESSAGE_LIST_RIGHT_MAX_X_FRACTION is a normalized (fraction-of-screen-
# width) safety boundary between the message-list column and the reading
# pane, calibrated against the current Outlook message-list layout — it
# is NOT a hardcoded pixel coordinate, and the runtime remains fully
# resolution-independent (every comparison against it is done in the
# same 0-1 fraction space, then converted to pixels per-screenshot).
#
# History: originally derived (2026-09-06) as the plain midpoint of two
# fractions established for an UNRELATED purpose — MESSAGE_LIST_SCROLL_
# ANCHOR_X_FRACTION (0.35, a mouse-scroll cursor position "safely inside"
# the list) and EMAIL_BODY_SCROLL_ANCHOR_X_FRACTION (0.7, the same for
# the reading pane) — giving 0.525. That midpoint was always a
# conservative ESTIMATE, never a measurement of the real Outlook column
# divider; it was never derived from, and is not, ground truth.
#
# Calibration (2026-09-06, same day, follow-up): live evidence with
# prompt-hardened, geometry-refined Vision output — a schema-valid
# Claude response that explicitly measured its bbox's right edge to the
# last visible character of the row's own date field (not a container/
# background edge) — placed the TRUE column edge at roughly 0.54 of
# screen width, comfortably past the 0.525 estimate. Recalibrated to
# 0.55: a small, deliberate step just past that observed real edge
# (leaving a modest safety margin), not a broad relaxation — still
# comfortably short of the reading pane, which starts much further
# right (see EMAIL_BODY_SCROLL_ANCHOR_X_FRACTION above). This remains a
# real, enforced deterministic safety boundary: bbox_extends_beyond_
# message_list is not removed or weakened, and a grossly oversized bbox
# is still rejected regardless of provider or confidence.
MESSAGE_LIST_RIGHT_MAX_X_FRACTION = 0.55

# --- Target-email click-X policy (2026-09-06, same-day follow-up) --------
# Two live runs (Claude primary, Gemini fallback) both passed geometry
# validation (including the calibrated boundary above) yet produced very
# different click-X results from the SAME real email row — Claude's
# validated bbox this run was [258, 548] (normalized), Gemini's earlier
# successful run was reconstructed at roughly [171, 371] — because the
# old click-X policy was `x_min + 0.6 * bbox_width`, directly dependent
# on each provider's own (sometimes differently-scoped) reported bbox
# width. Claude's click landed at x=829, inside its own bbox but past
# the row's actual clickable content in practice; Gemini's landed at
# x=559 and worked. Re-deriving X purely from provider-reported bbox
# width is unreliable whenever providers disagree on how wide the row
# "is" — even though both bboxes independently passed every geometry
# check.
#
# Fix: click X is now a fraction WITHIN THE KNOWN MESSAGE-LIST COLUMN
# ITSELF (LEFT_SIDEBAR_MAX_X_FRACTION .. MESSAGE_LIST_RIGHT_MAX_X_FRACTION)
# — a deterministic, provider-neutral zone Python already knows,
# independent of any one candidate's own reported bbox width. 0.35 lands
# solidly in the sender/subject/preview text region (well past the
# narrow avatar/checkbox strip at the column's own left edge), clear of
# the date/timestamp text and any empty padding nearer the column's
# right edge. See app/outlook/find_email.py::_validate_and_record_
# candidate() for how this is combined with each candidate's own
# validated bbox (still clamped to lie within it, and Y still comes
# from the bbox's own vertical center — Vision still owns identity/
# bbox/row-location; only the X-within-the-row decision moved to
# deterministic Python).
MESSAGE_ROW_CLICK_SAFE_ZONE_FRACTION = 0.35

# --- Pre-click screenshot freshness guard (2026-09-06, same-day follow-up)
# ---------------------------------------------------------------------------
# A live run proved click-point math and physical execution were both
# correct (EMAIL_MOUSE_POSITION_CONFIRMED matches=True) yet the wrong
# email opened — the grounding screenshot was ~16 seconds old by the
# time the physical click executed (three sequential Vision calls: the
# search, the identity refinement, the geometry refinement), and
# Outlook's message list can change in that window (new mail, a
# Focused/Other resort). Rather than adding another Vision call right
# before every click (which itself costs several seconds and opens a
# NEW staleness window), a fast, fully local, deterministic pixel-
# difference comparison of a fresh screenshot against the original
# grounding screenshot's own message-list region runs immediately
# before any physical action — see
# app.safety.screen_freshness.check_message_list_roi_freshness().
#
# Mean absolute grayscale pixel difference over the ROI, 0-255 scale.
# Grayscale (not per-channel RGB) and a mean (not a stricter per-pixel
# max) deliberately reduce sensitivity to anti-aliasing/compression
# noise, which is never a real content change. Conservative: chosen to
# sit comfortably above typical PNG re-encoding noise (usually <2 on
# this scale) and comfortably below a genuine row-content change
# (typically tens of points once a meaningfully-sized region of text
# actually differs).
EMAIL_PRECLICK_FRESHNESS_THRESHOLD = 6.0

# The vertical band compared is the target row's own y-range (from its
# validated bbox) PLUS this padding, expressed as a fraction of screen
# height (never fixed pixels) — wide enough to also catch a neighboring
# row being inserted/removed just above or below the target row, not
# just changes to the row's own exact pixels.
EMAIL_PRECLICK_ROI_Y_PADDING_FRACTION = 0.05

# Exactly one bounded re-ground cycle is allowed if the pre-click
# freshness check finds the message-list ROI has changed — the fresh
# screenshot becomes the new grounding screenshot, the full provider-
# neutral matching/identity-refinement/geometry pipeline reruns against
# it, and then ONE more freshness check runs before trusting the result.
# No loop: a second detected change after that is a safe stop
# (TARGET_EMAIL_SCREEN_CHANGED_BEFORE_CLICK), never a second re-ground.
MAX_TARGET_EMAIL_STALE_REGROUND_ATTEMPTS = 1

# Row-shape plausibility bounds, expressed as fractions of screen width/
# height (never fixed pixels) — a real Outlook message-list row is a
# thin horizontal band spanning most of the list column's width. These
# reject both "just the avatar/checkbox sliver" (too narrow) and "spans
# a huge, implausible chunk of the screen" (too wide/tall) hallucinated
# boxes, and a near-zero-height sliver (too short). Deliberately loose —
# generic anti-hallucination bounds, not tuned to any one screenshot.
EMAIL_ROW_MIN_WIDTH_FRACTION = 0.08
EMAIL_ROW_MAX_WIDTH_FRACTION = 0.40
EMAIL_ROW_MIN_HEIGHT_FRACTION = 0.02
EMAIL_ROW_MAX_HEIGHT_FRACTION = 0.15

# Bounded, SAME-screenshot row-bbox refinement (2026-09-06 live fix,
# follow-up to the plausibility check above): a live run had Claude
# correctly identify the target candidate but return a geometrically
# implausible row_bbox (rejected as designed, zero click). Rather than
# safe-stopping immediately or asking the fallback provider to "vote" on
# the same unsafe geometry, ONE narrow, identity-already-confirmed
# refinement request is allowed — see app/outlook/find_email.py's
# _refine_row_bbox(). Hard-capped at 1: no refinement loop, ever.
MAX_EMAIL_ROW_BBOX_REFINEMENTS = 1

# How much of the previous section's tail is carried into the next
# section's prompt as already_read_tail, for overlap detection —
# verbatim, never summarized (summarizing would lose the exact boundary
# text needed to detect/strip overlap).
EMAIL_SECTION_TAIL_CHARS = 200

# --- Find Reply (Phase 5) — reuses scroll_email_body() (Phase 4), no
# second Reply-specific scroll function ---
MAX_REPLY_SEARCH_SCROLL_ATTEMPTS = 5
REPLY_SEARCH_SCROLL_STABILIZE_WAIT_SECONDS = 1.0

# --- REPLY_SEARCH Vision-input crop (2026-09-06) --------------------------
# Live evidence: the correct email opened, was fully read, and Claude's
# REPLY_SEARCH call returned a schema-valid response correctly IDENTIFYING
# the Reply control semantically (never confused with Reply All/Forward)
# — yet the physical cursor did not land on it, and the Reply editor never
# opened (REPLY_EDITOR_NOT_OPEN). A static, Claude-only, no-physical-action
# benchmark against the exact failing screenshot (benchmarks/claude/
# experiments/run_reply_grounding_experiment.py) proved this is a SPATIAL
# grounding problem at full-screen resolution, not a semantic one:
# FULL_SCREEN bbox spatially correct 0/5 (landing in unrelated blank
# space, not merely "Reply confused with Forward"), while a full-height,
# right-side crop (excluding the message list / left navigation) was
# spatially correct 5/5 and faster — the same pattern that already fixed
# TARGET_EMAIL_SEARCH's row-bbox grounding via the message-list crop.
#
# This constant is the NORMALIZED (fraction-of-screen-width) left edge of
# that crop for REPLY_SEARCH's Vision input specifically — it is NOT a
# physical click coordinate, and it is NOT (unless separately verified)
# the actual Outlook message-list/reading-pane divider position, which
# can vary with window layout; it is simply the calibrated crop boundary
# that reliably keeps the real Reply control in view while excluding
# enough unrelated left-side UI (message list, left navigation) to make
# Claude's bbox grounding reliable.
#
# Calibration (2026-09-06, same day, follow-up benchmark — see
# benchmarks/claude/experiments/run_reply_crop_calibration.py): 0.35,
# 0.38, and 0.40 were each tested (3 static Claude-only trials against
# the same frozen screenshot, scoring actual bbox spatial correctness,
# not just semantic labeling) — all three were 3/3 spatially correct.
# 0.35 (the WIDEST of the three, i.e. the smallest left fraction/most
# retained context) was selected deliberately: more visual context is
# safer (lower risk of ever cropping out a genuine Reply control on a
# different email/window layout), and 0.35 also produced the most
# tightly clustered (most stable) bbox-to-ground-truth distances of the
# three candidates tested.
REPLY_VISION_CROP_LEFT_FRACTION = 0.35

# Bounded foreground re-check before a physical action (2026-09-06 live
# fix — see app.safety.foreground.confirm_outlook_foreground_with_
# recheck()): a live run saw the foreground briefly read as the Windows
# Alt-Tab/task-switcher overlay ("Task Switching") right before the
# Reply move, a transient window-manager state rather than a genuine
# loss of Outlook focus. Adds zero delay on the common path (Outlook
# already foreground); only retries, briefly and boundedly, when the
# FIRST check is not Outlook. 3 * 0.5s = 1.5s worst case before the
# existing safe-stop behavior fires exactly as before.
FOREGROUND_RECHECK_MAX_ATTEMPTS = 3
FOREGROUND_RECHECK_WAIT_SECONDS = 0.5

# --- Cross-phase (wired in since Phase 2) ---
PROVIDER_RETRY_COUNT = 1                   # bounded provider-retry wrapper — app/fallback/recovery.py

# --- Send (Phase 7) — reuses VISION_CONFIDENCE_THRESHOLD, MOVE_DURATION_SECONDS,
# PROVIDER_RETRY_COUNT above; no Send-specific confidence/move tuning needed. ---
SEND_CLICK_MAX = 1                         # hard ceiling, enforced independently in code — never just this constant
MAX_SEND_VERIFICATION_ATTEMPTS = 2
SEND_VERIFICATION_INITIAL_WAIT_SECONDS = 1.5
SEND_VERIFICATION_RETRY_WAIT_SECONDS = 1.0

# --- SEND_GROUNDING two-stage Vision crop architecture (2026-09-06) -------
# Live evidence: the full Reply/Draft flow completed correctly and
# SEND_GROUNDING returned a schema-valid, semantically-correct response
# ("this is Send") with no Gemini fallback — yet the physical click did
# not land on the real Send button, ending in SEND_VERIFICATION_UNCERTAIN.
# A static, Claude-only, no-physical-action benchmark against the exact
# failing screenshot (benchmarks/claude/experiments/
# run_send_grounding_experiment.py) proved this is the SAME class of
# problem already fixed for TARGET_EMAIL_SEARCH and REPLY_SEARCH: full-
# screen (and even a moderate reading-pane-crop) bbox grounding is
# spatially unreliable for the Send button specifically, because Send,
# its own dropdown chevron, and Discard sit unusually close together —
# a reading-pane crop alone reliably lands right at/just past the
# boundary between Send and its dropdown, not on Send itself.
#
# Unlike Reply/TARGET_EMAIL_SEARCH (where ONE deterministic crop was
# enough), Send needed a validated TWO-STAGE architecture (benchmarks/
# claude/experiments/run_dynamic_send_grounding_experiment.py):
#   Stage 1 (SEND_COMPOSER_LOCALIZATION): coarse, region-only
#     localization of the reply composer's whole action-bar ROW (Send +
#     dropdown + Discard together) from the SAME proven reading-pane
#     crop — never asked to find Send itself, and its own bbox is never
#     directly actionable.
#   Stage 2 (SEND_GROUNDING, unchanged semantics): a crop DYNAMICALLY
#     DERIVED in Python from Stage 1's own bbox (never a fixed/hand-
#     measured pixel box) is where the existing, already-proven exact-
#     Send grounding request actually runs.
#
# SEND_COMPOSER_READING_PANE_LEFT_FRACTION is Stage 1's Vision-input
# crop boundary — the SAME normalized value already proven for
# REPLY_SEARCH (app.config.settings.REPLY_VISION_CROP_LEFT_FRACTION),
# but declared as SEND's OWN independent constant rather than importing
# Reply's: these are two conceptually separate Vision tasks that merely
# happen to share the same generic crop geometry (app.vision.crop's
# generic horizontal-crop utility) and the same calibration evidence —
# Send must never become dependent on Reply's own business logic or
# constant, so a future independent recalibration of either one can
# never silently affect the other.
SEND_COMPOSER_READING_PANE_LEFT_FRACTION = 0.35

# Stage 2's dynamic crop is derived EXCLUSIVELY from Stage 1's own
# action-bar bbox, expanded by these fractions of THAT bbox's own
# width/height (never a fixed pixel amount) — this is "Policy A" from
# the dynamic-chain benchmark, the ONLY one of three tested expansion
# policies that reproduced reliable exact-Send spatial grounding under
# rigorous (real-button-bounds) scoring: Policy A 4/5 at exact pixel
# match, 5/5 within a small tolerance; the two more generous policies
# (B, C) that were also tested were markedly less reliable (down to 0/5
# for the most generous one) — wider is NOT safer here, unlike the
# earlier TARGET_EMAIL_SEARCH/REPLY_SEARCH crop-size findings. Expansion
# is applied per-side: horizontal padding = this fraction * the action-
# bar bbox's own width, added to BOTH the left and right edges
# independently; vertical padding = this fraction * the action-bar
# bbox's own height, added to BOTH the top and bottom edges
# independently — see app.vision.crop.derive_send_composer_crop_bounds_px(),
# which reproduces benchmarks/claude/experiments/
# run_dynamic_send_grounding_experiment.py::derive_stage2_crop_px()'s
# exact math.
SEND_DYNAMIC_CROP_HORIZONTAL_EXPANSION_FRACTION = 0.15
SEND_DYNAMIC_CROP_VERTICAL_EXPANSION_FRACTION = 1.50

# Cross-stage consistency safety gate (2026-09-06 follow-up): the static
# production-path regression (5 Claude-only chains against the frozen
# evidence screenshot) found Stage 2 spatial grounding correct in 4/5
# trials — the one miss landed on a DIFFERENT nearby control roughly
# 65-70px above the real Send button, while Stage 2 still semantically
# reported "Send". A wrong bbox that is nonetheless semantically
# labeled "Send" is exactly the failure mode the two-stage architecture
# cannot catch on its own — hence
# app.safety.validators.validate_send_candidate_against_action_bar():
# Stage 2's own bbox center must fall inside the region Stage 1 itself
# already identified as containing the action bar (Send + dropdown +
# Discard), or the candidate is rejected regardless of Stage 2's own
# confidence/identity claim. MAX_SEND_GROUNDING_REFINEMENTS bounds a
# SAME-screenshot, SAME-dynamic-crop, single re-ground attempt (mirrors
# app.outlook.find_email's MAX_EMAIL_ROW_BBOX_REFINEMENTS pattern
# exactly) — no loop, no repeated retries until Claude agrees, no
# provider fallback/voting: geometric inconsistency is a semantic/safety
# disagreement, never a technical failure.
MAX_SEND_GROUNDING_REFINEMENTS = 1


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
