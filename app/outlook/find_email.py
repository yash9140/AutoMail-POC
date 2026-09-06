"""Find + open the configured target email — pure step logic, no Qt.

Moved from app/playbook/find_open_email_steps.py (RND-009C) for the
final POC runtime. Composes OutlookLaunchSteps (app/outlook/launch.py)
for phases 1-2 (Windows Search -> Outlook launch -> readiness) rather
than duplicating that logic.

Phase 3: find_target_email() replaces the old single-screenshot,
single-candidate ground_target_email() with a bounded search-and-scroll
loop over a candidate LIST (app.vision.models.EmailSearchResponse) —
sender required, subject optional, ambiguity resolved only when
deterministic, never guessed. Grounding validation (bbox self-
consistency, normalized-range, degenerate-bbox, pixel-bounds, sidebar-
fraction, confidence) is unchanged from Phase 1's shared
validate_grounding() — a rejected bbox is always a safe stop, never a
scroll trigger and never a repaired/clamped coordinate.

Same architecture rule as every prior stage: the playbook decides what
happens next; Vision only reports what it sees against a playbook-
supplied target; PyAutoGUI only executes the one action it's told;
every abort checkpoint is checked before the corresponding action.
"""

from __future__ import annotations

import logging
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import pyautogui
from pydantic import BaseModel, Field

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from app.automation.screen_capture import DEFAULT_OUTPUT_DIR, ScreenCaptureError, capture_screen  # noqa: E402
from app.automation.scrolling import scroll_message_list  # noqa: E402
from app.config.settings import (  # noqa: E402
    CURSOR_POSITION_TOLERANCE_PX,
    EMAIL_PRECLICK_FRESHNESS_THRESHOLD,
    EMAIL_PRECLICK_ROI_Y_PADDING_FRACTION,
    EMAIL_ROW_MAX_HEIGHT_FRACTION,
    EMAIL_ROW_MAX_WIDTH_FRACTION,
    EMAIL_ROW_MIN_HEIGHT_FRACTION,
    EMAIL_ROW_MIN_WIDTH_FRACTION,
    LEFT_SIDEBAR_MAX_X_FRACTION,
    MAX_EMAIL_OPEN_VERIFICATION_ATTEMPTS,
    MAX_EMAIL_ROW_BBOX_REFINEMENTS,
    MAX_MESSAGE_LIST_SCROLL_ATTEMPTS,
    MAX_TARGET_EMAIL_STALE_REGROUND_ATTEMPTS,
    MESSAGE_LIST_RIGHT_MAX_X_FRACTION,
    MESSAGE_LIST_SCROLL_STABILIZE_WAIT_SECONDS,
    MESSAGE_ROW_CLICK_SAFE_ZONE_FRACTION,
    MOVE_DURATION_SECONDS,
    POST_CLICK_INITIAL_WAIT_SECONDS,
    POST_CLICK_RETRY_WAIT_SECONDS,
    VISION_CONFIDENCE_THRESHOLD,
)
from app.metrics.step_metrics import estimate_cost  # noqa: E402
from app.outlook.launch import OutlookLaunchSteps, _provider  # noqa: E402
from app.playbook.failure_reasons import LaunchFailureReason  # noqa: E402
from app.safety.abort_controller import AbortController  # noqa: E402
from app.safety.foreground import get_foreground_window_title, is_outlook_foreground  # noqa: E402
from app.safety.screen_freshness import (  # noqa: E402
    check_message_list_roi_freshness,
    save_freshness_debug_artifact,
)
from app.safety.sender_identity import parse_sender, sender_matches, sender_mode  # noqa: E402
from app.safety.validators import (  # noqa: E402
    EMAIL_ROW_BBOX_GEOMETRY_REASONS,
    GroundingCheckFailure,
    validate_email_row_bbox,
    validate_grounding,
)
from app.vision.crop import MessageListCrop, create_message_list_crop  # noqa: E402
from app.vision.debug_overlay import save_grounding_debug_artifact  # noqa: E402
from app.vision.grounding import normalize_1000_to_pixels  # noqa: E402
from app.vision.models import (  # noqa: E402
    EmailCandidate,
    EmailRowBBoxRefinementResponse,
    EmailRowIdentityRefinementResponse,
    EmailSearchResponse,
)
from app.vision.service import VisionRequest, VisionService  # noqa: E402
from rnd.models.find_open_email import (  # noqa: E402
    EmailOpenVerificationAttempt,
    EmailOpenVerificationResponse,
    RND009CResult,
    StepMetrics,
)
from rnd.models.outlook_launch import CallMetrics  # noqa: E402

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "app" / "vision" / "prompts"
EMAIL_SEARCH_PROMPT_PATH = PROMPTS_DIR / "email_search_v1.txt"
EMAIL_OPEN_VERIFICATION_PROMPT_PATH = PROMPTS_DIR / "email_open_verification_v2.txt"
EMAIL_ROW_BBOX_REFINE_PROMPT_PATH = PROMPTS_DIR / "email_row_bbox_refine_v1.txt"
EMAIL_ROW_IDENTITY_REFINE_PROMPT_PATH = PROMPTS_DIR / "email_row_identity_refine_v1.txt"

# Vision-call stage identifiers (app/fallback/recovery.py structured logging).
STAGE_TARGET_EMAIL_SEARCH = "TARGET_EMAIL_SEARCH"
STAGE_EMAIL_OPEN_VERIFICATION = "EMAIL_OPEN_VERIFICATION"
STAGE_TARGET_EMAIL_ROW_BBOX_REFINE = "TARGET_EMAIL_ROW_BBOX_REFINE"
STAGE_TARGET_EMAIL_ROW_IDENTITY_REFINE = "TARGET_EMAIL_ROW_IDENTITY_REFINE"

# Bounded identity-aware row-grounding refinement (2026-09-06, same-day
# follow-up to the geometry-only bbox refinement above) — see
# _refine_row_identity()'s docstring. Hard-capped at 1, exactly like
# MAX_EMAIL_ROW_BBOX_REFINEMENTS: no loop, ever.
MAX_EMAIL_ROW_IDENTITY_REFINEMENTS = 1

# Kept as a module-level alias so existing call sites/tests that
# reference this name keep working.
EMAIL_GROUNDING_CONFIDENCE_THRESHOLD = VISION_CONFIDENCE_THRESHOLD

# Default target — Vision never chooses this; the playbook does.
# Still module-level defaults for backward compatibility with existing
# callers that construct FindOpenEmailSteps with no override (the
# dashboard wiring that actually sources these from user input is
# Phase 8's concern). target_sender is REQUIRED; target_subject is
# OPTIONAL — an empty target_subject means "match on sender alone."
TARGET_EMAIL_SUBJECT = "Mail for project"
TARGET_EMAIL_SENDER = "Yash"

# --- Message-row click-point policy (2026-09-06, deterministic safe-zone
# replacement) ---------------------------------------------------------------
# History: two earlier iterations of this policy (2026-09-04) computed
# the click X as `x_min + PROPORTION * bbox_width` — first with
# PROPORTION=0.6 alone, then with a sidebar-boundary floor clamp added
# — both still fundamentally DERIVED FROM each candidate's own reported
# bbox width. A later live comparison (2026-09-06) showed Claude and
# Gemini's independently-reported, independently-VALID bboxes for the
# SAME real row disagreeing enough on width that this formula produced
# a working click for one provider and a non-working click for the
# other. Deriving action-X from Vision's self-reported width is
# unreliable whenever providers disagree on it — even when every
# geometry check independently passes.
#
# Fix: click X is now MESSAGE_ROW_CLICK_SAFE_ZONE_FRACTION (see
# app/config/settings.py for the full rationale) applied to the KNOWN
# message-list column bounds (LEFT_SIDEBAR_MAX_X_FRACTION ..
# MESSAGE_LIST_RIGHT_MAX_X_FRACTION) — a deterministic point Python
# already knows independent of any one candidate's own bbox width, then
# CLAMPED to lie within that specific candidate's own validated bbox
# (so it always remains a real point on that exact row, and the
# existing point-in-box / sidebar / confidence checks in
# validate_grounding() below still apply completely unchanged). Y is
# untouched — still the validated bbox's own vertical center.
SIDEBAR_SAFETY_BUFFER_NORMALIZED = 15.0  # ~1.5% of screen width, 0-1000 normalized — not a pixel offset; the clamp floor below

# Diagnostic-only, opt-in: set EMAIL_GROUNDING_DEBUG=1 to save a
# debug/email_row_grounding_<timestamp>.png overlay artifact per
# candidate grounding. Never enabled by default; never a runtime click
# source (see app/vision/debug_overlay.py).
_DEBUG_ARTIFACTS_ENABLED = os.environ.get("EMAIL_GROUNDING_DEBUG") == "1"

_email_logger = logging.getLogger("app.outlook.find_email.grounding")
if not _email_logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s [EMAIL_GROUNDING] %(message)s"))
    _email_logger.addHandler(_handler)
    _email_logger.setLevel(logging.INFO)
    _email_logger.propagate = False

# Diagnostics-only logger for verify_email_opened() (EMAIL_OPEN_VERIFICATION
# stage). Added 2026-09-05 to see exactly why a schema-valid Vision
# response can still be rejected by the deterministic sender/subject/
# body_visible check below — logging only, no behavior change. Kept
# separate from _email_logger (grounding/click diagnostics) so the two
# concerns stay distinguishable in log output.
_email_open_verify_logger = logging.getLogger("app.outlook.find_email.open_verification")
if not _email_open_verify_logger.handlers:
    _open_verify_handler = logging.StreamHandler()
    _open_verify_handler.setFormatter(logging.Formatter("%(asctime)s [EMAIL_OPEN_VERIFY] %(message)s"))
    _email_open_verify_logger.addHandler(_open_verify_handler)
    _email_open_verify_logger.setLevel(logging.INFO)
    _email_open_verify_logger.propagate = False

_GROUNDING_FAILURE_TO_REASON = {
    GroundingCheckFailure.INVALID_BBOX: LaunchFailureReason.EMAIL_GROUNDING_INVALID,
    GroundingCheckFailure.DEGENERATE_BBOX: LaunchFailureReason.EMAIL_GROUNDING_INVALID,
    GroundingCheckFailure.OUT_OF_NORMALIZED_RANGE: LaunchFailureReason.EMAIL_GROUNDING_INVALID,
    GroundingCheckFailure.POINT_OUTSIDE_BBOX: LaunchFailureReason.EMAIL_GROUNDING_POINT_OUTSIDE_BBOX,
    GroundingCheckFailure.MISSING_COORDINATES: LaunchFailureReason.EMAIL_GROUNDING_INVALID,
    GroundingCheckFailure.OUT_OF_BOUNDS: LaunchFailureReason.EMAIL_GROUNDING_OUT_OF_BOUNDS,
    GroundingCheckFailure.SIDEBAR_REJECTED: LaunchFailureReason.EMAIL_GROUNDING_SIDEBAR_REJECTED,
    GroundingCheckFailure.LOW_CONFIDENCE: LaunchFailureReason.EMAIL_GROUNDING_INVALID,
}


class CandidateMatchEvidence(BaseModel):
    """One candidate's full matching decision, retained for EVERY
    candidate Vision reports on EVERY search attempt (not just the one
    ultimately clicked) — makes the live sender/subject matching
    decision fully auditable after the fact. See docs/poc — the
    Attempt-3 live run (2026-09-02) opened the wrong "Yash"-prefixed
    email because sender matching used substring containment
    ("Yash" in "Yash Dhanraj" -> True); this record exists so a future
    mismatch is diagnosable from the result alone, not by re-reading
    raw screenshots.

    match_type distinguishes a fully-confirmed exact match from a
    PROVISIONAL_TARGET_MATCH (a truncated-subject prefix match — see
    _evaluate_candidate) — the latter is eligible to click but is NEVER
    treated as confirmed until verify_email_opened()'s post-open,
    full-subject, deterministic re-check passes."""

    attempt_number: int
    target_sender: str
    target_subject: str
    candidate_sender: str
    candidate_subject: str
    subject_truncated: bool = False
    sender_match: bool
    subject_match: Optional[bool] = None  # None when target_subject is empty (not applicable)
    eligible: bool
    match_type: Optional[str] = None  # "exact" | "provisional" | None (not eligible)
    rejection_reason: Optional[str] = None  # "sender_mismatch" | "subject_mismatch" | None


def _normalize_text(s: str) -> str:
    """Deterministic normalization for comparing visible Outlook sender/
    subject text: casefold, trim, collapse repeated internal whitespace.
    No fuzzy/similarity matching of any kind — two strings either
    normalize to the same value or they don't."""
    return re.sub(r"\s+", " ", (s or "").strip()).casefold()


# Matches ONLY a trailing UI truncation marker — literal '.' characters
# and/or the unicode ellipsis '…', with any surrounding trailing
# whitespace. Never touches interior text. Applied ONLY to a candidate
# Vision has explicitly reported as subject_truncated=True — never used
# to "clean up" a full, untruncated subject.
_TRAILING_TRUNCATION_MARKER_RE = re.compile(r"[\s.…]+$")


def _strip_truncation_marker(text: str) -> str:
    return _TRAILING_TRUNCATION_MARKER_RE.sub("", text or "")


def _evaluate_candidate(
    candidate: EmailCandidate, target_sender: str, target_subject: str, attempt_number: int,
) -> CandidateMatchEvidence:
    """Sender: REQUIRES a normalized EXACT match — never substring/prefix
    (e.g. target_sender="Yash" must NOT match candidate_sender=
    "Yash Dhanraj"; they are different senders in the mailbox).

    Subject, when target_subject is non-empty, has TWO distinct cases:

    1. FULL SUBJECT (candidate.subject_truncated is False): requires a
       normalized EXACT match, unweakened — a visibly truncated subject
       normalizing to a different string than the full target is simply
       not this case.

    2. TRUNCATED SUBJECT (candidate.subject_truncated is True): never
       exact-matched (it can't be — the text is incomplete). Eligible
       only as a PROVISIONAL match: the visible text (with only its
       trailing truncation marker removed — never any other guessing)
       must be non-empty AND be an exact prefix of the normalized target
       subject. This is a real, deterministic string-prefix check, never
       fuzzy/similarity matching. A provisional match is never confirmed
       here — see verify_email_opened()'s post-open, full-subject,
       deterministic re-check.

    When target_subject is empty, subject is not applicable and
    eligibility depends on sender alone ("subject optional" behavior,
    unchanged)."""
    sender_match = _normalize_text(candidate.sender) == _normalize_text(target_sender)
    target_subject_stripped = (target_subject or "").strip()

    if not target_subject_stripped:
        return CandidateMatchEvidence(
            attempt_number=attempt_number, target_sender=target_sender, target_subject="",
            candidate_sender=candidate.sender, candidate_subject=candidate.subject,
            subject_truncated=candidate.subject_truncated,
            sender_match=sender_match, subject_match=None,
            eligible=sender_match, match_type=("exact" if sender_match else None),
            rejection_reason=(None if sender_match else "sender_mismatch"),
        )

    if not candidate.subject_truncated:
        subject_match = _normalize_text(candidate.subject) == _normalize_text(target_subject_stripped)
        eligible = sender_match and subject_match
        if not sender_match:
            rejection_reason: Optional[str] = "sender_mismatch"
        elif not subject_match:
            rejection_reason = "subject_mismatch"
        else:
            rejection_reason = None
        return CandidateMatchEvidence(
            attempt_number=attempt_number, target_sender=target_sender, target_subject=target_subject_stripped,
            candidate_sender=candidate.sender, candidate_subject=candidate.subject,
            subject_truncated=False,
            sender_match=sender_match, subject_match=subject_match,
            eligible=eligible, match_type=("exact" if eligible else None),
            rejection_reason=rejection_reason,
        )

    # TRUNCATED SUBJECT — provisional prefix match only.
    visible_normalized = _normalize_text(_strip_truncation_marker(candidate.subject))
    target_normalized = _normalize_text(target_subject_stripped)
    is_prefix = bool(visible_normalized) and target_normalized.startswith(visible_normalized)
    eligible = sender_match and is_prefix
    if not sender_match:
        rejection_reason = "sender_mismatch"
    elif not is_prefix:
        rejection_reason = "subject_mismatch"
    else:
        rejection_reason = None

    return CandidateMatchEvidence(
        attempt_number=attempt_number, target_sender=target_sender, target_subject=target_subject_stripped,
        candidate_sender=candidate.sender, candidate_subject=candidate.subject,
        subject_truncated=True,
        sender_match=sender_match, subject_match=is_prefix,
        eligible=eligible, match_type=("provisional" if eligible else None),
        rejection_reason=rejection_reason,
    )


def _try_resolve_latest(candidates: list[EmailCandidate]) -> Optional[EmailCandidate]:
    """Attempts to deterministically identify the single most recent
    candidate from their date_or_order strings. Returns None (still
    ambiguous) unless EVERY candidate's date_or_order parses as an ISO
    8601 date/datetime AND there is a unique maximum — never a fuzzy
    heuristic, and never uses Vision's own confidence as a tie-breaker."""
    parsed: list[tuple[datetime, EmailCandidate]] = []
    for c in candidates:
        try:
            parsed.append((datetime.fromisoformat((c.date_or_order or "").strip()), c))
        except ValueError:
            return None
    parsed.sort(key=lambda pair: pair[0])
    if len(parsed) >= 2 and parsed[-1][0] == parsed[-2][0]:
        return None  # exact tie — not deterministic
    return parsed[-1][1]


class FindEmailResult(RND009CResult):
    """Phase 2 + Phase 3 additions, as a subclass rather than editing
    rnd/models/find_open_email.py directly — that file is historical
    R&D evidence and is never modified.

    email_search_response replaces the RND-009C-era single-candidate
    email_grounding field (typed to a different, incompatible schema);
    email_grounding_metrics is inherited and reused as-is (same
    CallMetrics type, no duplication)."""

    # --- Phase 2 metrics, propagated from OutlookLaunchResult ---
    outlook_launch_started_at: Optional[str] = None
    maximize_required: Optional[bool] = None
    maximize_executed: bool = False
    maximize_duration_ms: Optional[float] = None
    post_maximize_screenshot: Optional[str] = None
    outlook_ready_at: Optional[str] = None
    outlook_ready_ms: Optional[float] = None
    provider_retries: int = 0
    fallback_uses: int = 0

    # --- Phase 3: find email ---
    find_email_started_at: Optional[str] = None
    email_found_at: Optional[str] = None
    email_search_ms: Optional[float] = None
    message_list_scroll_attempts: int = 0
    email_candidate_count: int = 0
    email_search_response: Optional[EmailSearchResponse] = None
    candidate_match_log: list[CandidateMatchEvidence] = Field(default_factory=list)
    provisional_match: bool = False
    email_grounding_bbox_raw: Optional[list[float]] = None
    email_grounding_bbox_pixels: Optional[list[float]] = None
    email_grounding_debug_artifact: Optional[str] = None
    # Bounded row-bbox refinement (2026-09-06) — see _refine_row_bbox().
    row_bbox_refinement_attempted: bool = False
    row_bbox_refinement_response: Optional[EmailRowBBoxRefinementResponse] = None
    row_bbox_refinement_succeeded: bool = False
    # Bounded identity-aware row-grounding refinement (2026-09-06,
    # same-day follow-up) — see _refine_row_identity().
    row_identity_refinement_attempted: bool = False
    row_identity_refinement_response: Optional[EmailRowIdentityRefinementResponse] = None
    row_identity_refinement_succeeded: bool = False
    # Pre-click screenshot freshness guard (2026-09-06) — see
    # _ensure_target_row_still_fresh_or_reground(). Tracks the grounding
    # screenshot currently in effect (updated if a stale re-ground
    # occurs) and whether the one bounded re-ground attempt has been used.
    target_email_grounding_screenshot_path: Optional[str] = None
    target_email_grounding_capture_width: Optional[int] = None
    target_email_grounding_capture_height: Optional[int] = None
    stale_reground_attempted: bool = False
    preclick_freshness_checks: int = 0
    preclick_freshness_last_changed: Optional[bool] = None
    preclick_freshness_last_score: Optional[float] = None
    email_click_count: int = 0
    email_open_verified_at: Optional[str] = None
    email_open_ms: Optional[float] = None


class FindOpenEmailSteps:
    def __init__(
        self,
        abort_controller: AbortController,
        vision: VisionService,
        model: str,
        target_sender: str = TARGET_EMAIL_SENDER,
        target_subject: str = TARGET_EMAIL_SUBJECT,
    ) -> None:
        if not target_sender or not target_sender.strip():
            raise ValueError("target_sender is required and must be non-empty.")
        self.abort_controller = abort_controller
        self.vision = vision
        self.model = model
        self.result = FindEmailResult(target_subject=target_subject or "", target_sender=target_sender)
        self.launch = OutlookLaunchSteps(abort_controller, vision, model)
        self._session_start_monotonic: Optional[float] = None
        self._find_start_monotonic: Optional[float] = None
        # Message-list-column crop cache (2026-09-06), keyed by the
        # ORIGINAL full screenshot's own path — see _get_message_list_crop().
        # Guarantees TARGET_EMAIL_SEARCH, TARGET_EMAIL_ROW_IDENTITY_REFINE
        # and TARGET_EMAIL_ROW_BBOX_REFINE reuse the exact same crop image
        # for the exact same screenshot, and that a fresh screenshot
        # (scroll, or a post-freshness-guard re-ground) always gets its
        # own freshly-built crop.
        self._message_list_crop_cache: dict[str, MessageListCrop] = {}

    def start_session(self) -> None:
        self._session_start_monotonic = time.monotonic()
        self.result.session_start = datetime.now().isoformat()

    def finalize_session(self) -> None:
        self.result.session_end = datetime.now().isoformat()
        if self._session_start_monotonic is not None:
            self.result.total_elapsed_ms = round((time.monotonic() - self._session_start_monotonic) * 1000, 1)

    def _accumulate(self, metrics: CallMetrics, step: StepMetrics) -> None:
        self.result.total_vision_calls += 1
        self.result.total_input_tokens += metrics.input_tokens or 0
        self.result.total_output_tokens += metrics.output_tokens or 0
        self.result.total_estimated_cost = round(self.result.total_estimated_cost + (metrics.estimated_cost or 0), 6)
        self.result.total_latency_ms = round(self.result.total_latency_ms + (metrics.latency_ms or 0), 3)
        step.vision_calls += 1
        step.input_tokens += metrics.input_tokens or 0
        step.output_tokens += metrics.output_tokens or 0
        step.estimated_cost = round(step.estimated_cost + (metrics.estimated_cost or 0), 6)
        step.latency_ms = round(step.latency_ms + (metrics.latency_ms or 0), 3)

    def check_abort(self, stage: str) -> bool:
        if self.abort_controller.is_abort_requested():
            self.result.result = "ABORTED"
            self.result.failure_reason = LaunchFailureReason.USER_ABORTED
            self.result.safety_aborts += 1
            self.result.notes = f"Abort requested at stage: {stage}."
            return True
        return False

    # --- Phases 1-2: reuse OutlookLaunchSteps verbatim, then fold its
    # result into this stage's own result record ---

    def run_launch_and_readiness(self) -> bool:
        r, lr = self.result, self.launch.result

        ok = self.launch.press_windows_key()
        ok = ok and self.launch.type_search_query("Outlook")
        capture = self.launch.capture_search_screenshot() if ok else None
        ok = ok and capture is not None
        ok = ok and self.launch.ground_search_result(capture)
        if ok:
            self.launch.record_human_approval(True)  # bounded session approval covers this
            ok = self.launch.activate_outlook_result()
        ok = ok and self.launch.poll_for_outlook_foreground()
        ok = ok and self.launch.enforce_maximized()
        ok = ok and self.launch.verify_outlook_readiness()

        # Fold the launch phase's result into this stage's own record —
        # kept as a flat, self-contained model (not a nested sub-object)
        # matching this project's per-stage-own-model convention.
        r.windows_key_timestamp = lr.windows_key_timestamp
        r.search_query_typed_timestamp = lr.search_query_typed_timestamp
        r.search_screenshot = lr.search_screenshot
        r.foreground_before_search_check = lr.foreground_before_search_check
        r.outlook_grounding_raw_x = lr.raw_x
        r.outlook_grounding_raw_y = lr.raw_y
        r.outlook_converted_x = lr.converted_x
        r.outlook_converted_y = lr.converted_y
        r.outlook_click_timestamp = lr.outlook_click_timestamp
        r.outlook_launch_click_executed = lr.outlook_launch_click_executed
        r.outlook_launch_duration_ms = lr.outlook_launch_duration_ms
        r.foreground_after_launch = lr.foreground_after_launch
        r.foreground_verified = lr.foreground_verified
        r.readiness_attempts = lr.readiness_attempts
        r.ready_for_interaction = lr.ready_for_interaction
        r.mouse_click_count += lr.mouse_click_count
        r.keyboard_action_count += lr.keyboard_action_count
        r.human_interventions += lr.human_interventions
        r.safety_aborts += lr.safety_aborts
        r.total_vision_calls += lr.total_vision_calls
        r.total_input_tokens += lr.total_input_tokens
        r.total_output_tokens += lr.total_output_tokens
        r.total_estimated_cost = round(r.total_estimated_cost + lr.total_estimated_cost, 6)
        r.total_latency_ms = round(r.total_latency_ms + lr.total_latency_ms, 3)

        # Phase 2 launch metrics, propagated into the Phase 3 result.
        r.outlook_launch_started_at = lr.outlook_launch_started_at
        r.maximize_required = lr.maximize_required
        r.maximize_executed = lr.maximize_executed
        r.maximize_duration_ms = lr.maximize_duration_ms
        r.post_maximize_screenshot = lr.post_maximize_screenshot
        r.outlook_ready_at = lr.outlook_ready_at
        r.outlook_ready_ms = lr.outlook_ready_ms
        r.provider_retries += lr.provider_retries
        r.fallback_uses += lr.fallback_uses

        r.launch_outlook_metrics = StepMetrics(
            vision_calls=1 if lr.grounding_metrics.input_tokens else 0,
            input_tokens=lr.grounding_metrics.input_tokens or 0,
            output_tokens=lr.grounding_metrics.output_tokens or 0,
            estimated_cost=lr.grounding_metrics.estimated_cost or 0.0,
            latency_ms=lr.grounding_metrics.latency_ms or 0.0,
        )
        r.verify_outlook_ready_metrics = StepMetrics(
            vision_calls=len(lr.readiness_attempts),
            input_tokens=sum((a.metrics.input_tokens or 0) for a in lr.readiness_attempts),
            output_tokens=sum((a.metrics.output_tokens or 0) for a in lr.readiness_attempts),
            estimated_cost=round(sum((a.metrics.estimated_cost or 0) for a in lr.readiness_attempts), 6),
            latency_ms=round(sum((a.metrics.latency_ms or 0) for a in lr.readiness_attempts), 3),
        )

        if not ok:
            r.result = lr.result
            r.failure_reason = lr.failure_reason
            r.notes = lr.notes
        return ok

    # --- Phase 3: find target email (bounded search + scroll) ---

    def find_target_email(self) -> bool:
        if not self.result.ready_for_interaction:
            raise RuntimeError("Cannot find email: Outlook readiness (ready_for_interaction) was not confirmed.")

        self._find_start_monotonic = time.monotonic()
        self.result.find_email_started_at = datetime.now().isoformat()

        for attempt_number in range(1, MAX_MESSAGE_LIST_SCROLL_ATTEMPTS + 1):
            if self.check_abort("before_email_search"):
                return False

            title = get_foreground_window_title()
            self.result.foreground_before_email_grounding = title
            if not is_outlook_foreground(title):
                self.result.result = "FAIL"
                self.result.failure_reason = LaunchFailureReason.OUTLOOK_FOREGROUND_LOST
                self.result.notes = f"Outlook not foreground before email search; foreground was {title!r}."
                return False

            try:
                capture = capture_screen(DEFAULT_OUTPUT_DIR)
            except ScreenCaptureError as exc:
                self.result.result = "ERROR"
                self.result.failure_reason = LaunchFailureReason.TECHNICAL_PROVIDER_ERROR
                self.result.notes = f"Inbox capture failed: {exc}"
                return False
            self.result.inbox_screenshot = capture.filename
            # Pre-click freshness guard (2026-09-06) compares a later,
            # fresh screenshot against THIS one — kept in self.result so
            # click_target_email() (called well after this method
            # returns) can still reach it.
            self.result.target_email_grounding_screenshot_path = capture.path
            self.result.target_email_grounding_capture_width = capture.width
            self.result.target_email_grounding_capture_height = capture.height

            candidate, provider_used = self._ground_target_email_candidate(capture, attempt_number)
            if candidate is not None:
                return self._validate_and_record_candidate(
                    candidate, capture.width, capture.height, capture.path, provider_used=provider_used,
                )
            if self.result.result in ("FAIL", "ERROR"):
                # A terminal outcome (technical failure, ambiguity,
                # identity-refinement declined) was already fully
                # recorded by _ground_target_email_candidate().
                return False

            # Zero matches in this view — scroll and look again, if attempts remain.
            if attempt_number < MAX_MESSAGE_LIST_SCROLL_ATTEMPTS:
                if self.check_abort("before_message_list_scroll"):
                    return False
                scroll_message_list(capture.width, capture.height)
                self.result.message_list_scroll_attempts += 1
                time.sleep(MESSAGE_LIST_SCROLL_STABILIZE_WAIT_SECONDS)
                continue

            self.result.result = "FAIL"
            self.result.failure_reason = LaunchFailureReason.TARGET_EMAIL_NOT_FOUND
            self.result.notes = (
                f"No candidate matched target_sender={self.result.target_sender!r} "
                f"target_subject={self.result.target_subject!r} after {MAX_MESSAGE_LIST_SCROLL_ATTEMPTS} "
                "search attempt(s) (including bounded message-list scrolling)."
            )
            return False

        # Unreachable — the loop above always returns.
        return False

    def _get_message_list_crop(self, screenshot_path: str, screen_width: int, screen_height: int) -> MessageListCrop:
        """Returns the message-list-column crop for screenshot_path,
        building and caching it on first use (2026-09-06 — see
        benchmarks/claude/experiments/run_row_confusion_experiment.py +
        analyze_row_confusion_results.py for the static evidence this is
        based on: full-screen row-bbox grounding was stably WRONG,
        cropping to this exact horizontal message-list column made it
        stably CORRECT, and cropping any tighter made it worse again).

        Cached per screenshot path so TARGET_EMAIL_SEARCH,
        TARGET_EMAIL_ROW_IDENTITY_REFINE and TARGET_EMAIL_ROW_BBOX_REFINE
        all reuse the exact same crop image for one grounding cycle — a
        NEW screenshot (scroll, or a freshness-guard re-ground) always
        gets its own freshly-built crop, since it is keyed by that
        screenshot's own path."""
        cached = self._message_list_crop_cache.get(screenshot_path)
        if cached is not None:
            return cached
        crop = create_message_list_crop(
            Path(screenshot_path), screen_width, screen_height,
            LEFT_SIDEBAR_MAX_X_FRACTION, MESSAGE_LIST_RIGHT_MAX_X_FRACTION,
        )
        self._message_list_crop_cache[screenshot_path] = crop
        _email_logger.info(
            "EMAIL_VISION_CROP_CREATED original_size=%dx%d crop_bounds=(%d,%d,%d,%d) crop_size=%dx%d",
            screen_width, screen_height, crop.left, crop.top, crop.right, crop.bottom, crop.width, crop.height,
        )
        return crop

    def _ground_target_email_candidate(self, capture, attempt_number: int = 1):
        """Runs ONE TARGET_EMAIL_SEARCH vision call against the GIVEN
        screenshot capture, resolves candidate matching (ambiguity-safe,
        exact-over-provisional priority — unchanged from before this was
        extracted into its own method), and runs the bounded identity-
        row refinement when eligible. Used both by find_target_email()'s
        own scroll-and-search loop AND by the bounded, same-day pre-click
        stale re-ground path in click_target_email() — identical
        matching/identity-refinement rules apply either way, per this
        task's own "a fresh re-ground must still follow all safety" rule.

        Returns (candidate, provider_used). candidate is None in two
        distinct cases the caller must tell apart via self.result.result:
        - self.result.result already "ERROR"/"FAIL": a terminal outcome
          (technical failure, ambiguity, identity-refinement declined)
          is already fully recorded — the caller must return False
          immediately, never guessing a fallback meaning.
        - self.result.result still falsy/unset: zero candidates matched
          in THIS screenshot — not itself a failure; the caller decides
          what that means in its own context (the original scroll-and-
          look-again loop scrolls and retries; the bounded pre-click
          re-ground path treats it as a safe stop, since re-grounding is
          a one-shot re-check, not a new scrolling search)."""
        crop = self._get_message_list_crop(capture.path, capture.width, capture.height)
        subject_clause = self.result.target_subject.strip() or "(not specified — match on sender alone)"
        prompt_text = EMAIL_SEARCH_PROMPT_PATH.read_text(encoding="utf-8").format(
            width=crop.width, height=crop.height,
            target_sender=self.result.target_sender, target_subject_clause=subject_clause,
        )
        outcome = self.vision.analyze(VisionRequest(
            stage=STAGE_TARGET_EMAIL_SEARCH, screenshot_path=crop.crop_path,
            goal="Search for the target email", prompt_text=prompt_text,
            response_model=EmailSearchResponse,
        ))
        self.result.provider_retries += outcome.primary_retries + outcome.fallback_retries
        self.result.fallback_uses += 1 if outcome.fallback_used else 0
        if outcome.parsed is None:
            self.result.result = "ERROR"
            self.result.failure_reason = LaunchFailureReason.TECHNICAL_PROVIDER_ERROR
            self.result.notes = outcome.error
            return None, None
        structured = outcome.parsed
        call_metrics = outcome.call_metrics

        # Deterministic Python remap: every candidate's row_bbox is
        # returned relative to the crop image Vision was actually shown —
        # never trusted as full-screen-relative from here on. Vision is
        # never asked to do this conversion itself.
        for candidate_obj in structured.candidates:
            if candidate_obj.row_bbox is not None and len(candidate_obj.row_bbox) == 4:
                crop_relative_bbox = list(candidate_obj.row_bbox)
                _email_logger.info(
                    "EMAIL_VISION_BBOX_CROP_RELATIVE stage=%s raw_bbox=%r",
                    STAGE_TARGET_EMAIL_SEARCH, crop_relative_bbox,
                )
                candidate_obj.row_bbox = crop.remap_bbox_to_full_screen(crop_relative_bbox)
                _email_logger.info(
                    "EMAIL_VISION_BBOX_REMAPPED stage=%s crop_bbox=%r full_screen_bbox=%r",
                    STAGE_TARGET_EMAIL_SEARCH, crop_relative_bbox, candidate_obj.row_bbox,
                )

        metrics = CallMetrics(
            latency_ms=call_metrics.latency_ms, input_tokens=call_metrics.input_tokens,
            output_tokens=call_metrics.output_tokens,
            estimated_cost=estimate_cost(
                call_metrics.provider_name, call_metrics.model, call_metrics.input_tokens, call_metrics.output_tokens,
            ),
        )
        self._accumulate(metrics, self.result.find_email_metrics)
        self.result.email_grounding_metrics = metrics

        self.result.email_search_response = structured

        evidence = [
            _evaluate_candidate(c, self.result.target_sender, self.result.target_subject, attempt_number)
            for c in structured.candidates
        ]
        self.result.candidate_match_log.extend(evidence)

        # Exact matches always take priority over provisional
        # (truncated-subject) ones — a provisional match is only ever
        # considered when there is no unambiguous exact match in this
        # view. The two pools are never merged/resolved together.
        exact_pairs = [(c, ev) for c, ev in zip(structured.candidates, evidence) if ev.match_type == "exact"]
        provisional_pairs = [
            (c, ev) for c, ev in zip(structured.candidates, evidence) if ev.match_type == "provisional"
        ]
        self.result.email_candidate_count = len(exact_pairs) + len(provisional_pairs)

        candidate: Optional[EmailCandidate] = None
        is_provisional = False

        if len(exact_pairs) == 1:
            candidate = exact_pairs[0][0]
        elif len(exact_pairs) > 1:
            candidate = _try_resolve_latest([c for c, _ in exact_pairs])
            if candidate is None:
                self.result.result = "FAIL"
                self.result.failure_reason = LaunchFailureReason.MULTIPLE_TARGET_EMAILS_FOUND
                self.result.notes = (
                    f"{len(exact_pairs)} candidates matched target_sender={self.result.target_sender!r} "
                    f"target_subject={self.result.target_subject!r}, and recency could not be confidently "
                    "determined. Not guessing between them."
                )
                return None, None
        elif len(provisional_pairs) == 1:
            candidate = provisional_pairs[0][0]
            is_provisional = True
        elif len(provisional_pairs) > 1:
            self.result.result = "FAIL"
            self.result.failure_reason = LaunchFailureReason.MULTIPLE_TARGET_EMAILS_FOUND
            self.result.notes = (
                f"{len(provisional_pairs)} candidates from target_sender={self.result.target_sender!r} have "
                f"truncated subjects that could each be a prefix of target_subject="
                f"{self.result.target_subject!r}. Not guessing between them — see PROVISIONAL_TARGET_MATCH "
                "policy in app/outlook/find_email.py."
            )
            return None, None

        if candidate is None:
            return None, outcome.provider_used  # zero matches — not a terminal failure by itself

        self.result.provisional_match = is_provisional

        # Same-sender-row identity risk (2026-09-06 live fix): a live run
        # showed Vision correctly ACCEPT this exact candidate's sender+
        # subject yet attach a row_bbox belonging to a DIFFERENT row from
        # the same sender — geometry validation alone cannot detect that
        # (the wrong-row bbox was a perfectly plausible row shape). One
        # bounded identity-aware re-grounding call is eligible when any
        # of the following make that risk real: (a) this observation
        # reported MORE THAN ONE row from the target sender at all (the
        # concrete, measurable signal — even though only one passed
        # subject filtering, Vision could still have cross-wired the
        # bbox while distinguishing them); (b) the resolved match is
        # PROVISIONAL (a truncated-subject prefix match is an inherently
        # weaker identity binding — see _evaluate_candidate()'s
        # docstring); or (c) Vision's own confidence in this candidate is
        # below the existing threshold. Deliberately NOT triggered merely
        # because target_subject is non-empty — when exactly one row
        # shares the target sender in this observation, there is no
        # OTHER row to cross-wire onto, so refining would only add
        # latency without addressing any real risk.
        same_sender_row_count = sum(
            1 for c in structured.candidates
            if _normalize_text(c.sender) == _normalize_text(self.result.target_sender)
        )
        needs_identity_refinement = (
            same_sender_row_count > 1
            or is_provisional
            or candidate.confidence < VISION_CONFIDENCE_THRESHOLD
        )
        if needs_identity_refinement and MAX_EMAIL_ROW_IDENTITY_REFINEMENTS > 0:
            grounded_bbox, technical_failure = self._refine_row_identity(
                candidate, capture.width, capture.height, capture.path,
            )
            if technical_failure:
                return None, None
            if grounded_bbox is None:
                self.result.result = "FAIL"
                self.result.failure_reason = LaunchFailureReason.EMAIL_ROW_IDENTITY_UNCONFIRMED
                self.result.notes = (
                    f"Row identity re-grounding could not confirm sender={candidate.sender!r} "
                    f"subject={candidate.subject!r} inside a returned bbox. NO click computed."
                )
                _email_logger.info("EMAIL_CLICK_BLOCKED reason=%s", self.result.failure_reason)
                return None, None
            self.result.row_identity_refinement_succeeded = True
            candidate = candidate.model_copy(update={"row_bbox": grounded_bbox})

        return candidate, outcome.provider_used

    def _check_row_bbox_geometry(
        self, row_bbox: list[float], screen_width: int, screen_height: int, screenshot_path: str,
        provider_used: Optional[str], pass_label: str,
    ):
        """Runs validate_email_row_bbox() — the ONE shared validator, used
        identically for the original AND (if attempted) the refined bbox
        — logs EMAIL_ROW_BBOX_VALIDATION (pass=%s so initial vs. refined
        is always distinguishable in logs), and saves a diagnostic-only
        debug overlay when EMAIL_GROUNDING_DEBUG=1. Never computes a
        click point and never mutates self.result's terminal fields —
        the caller decides what an invalid result means."""
        row_validation = validate_email_row_bbox(
            row_bbox=row_bbox, image_width=screen_width, image_height=screen_height,
            message_list_right_max_x_fraction=MESSAGE_LIST_RIGHT_MAX_X_FRACTION,
            min_row_width_fraction=EMAIL_ROW_MIN_WIDTH_FRACTION,
            max_row_width_fraction=EMAIL_ROW_MAX_WIDTH_FRACTION,
            min_row_height_fraction=EMAIL_ROW_MIN_HEIGHT_FRACTION,
            max_row_height_fraction=EMAIL_ROW_MAX_HEIGHT_FRACTION,
            sidebar_max_x_fraction=LEFT_SIDEBAR_MAX_X_FRACTION,
        )
        row_screen_bbox = [
            row_validation.bbox_top_y, row_validation.bbox_left_x,
            row_validation.bbox_bottom_y, row_validation.bbox_right_x,
        ]
        _email_logger.info(
            "EMAIL_ROW_BBOX_VALIDATION pass=%s provider_used=%s raw_bbox=%r screen_bbox=%r "
            "message_list_left_x=%s message_list_right_x=%s bbox_width_px=%s bbox_height_px=%s "
            "valid=%s reason=%s",
            pass_label, provider_used, row_bbox, row_screen_bbox,
            row_validation.message_list_left_x, row_validation.message_list_right_x,
            row_validation.bbox_width_px, row_validation.bbox_height_px,
            row_validation.valid, row_validation.reason,
        )
        if _DEBUG_ARTIFACTS_ENABLED:
            artifact_path = save_grounding_debug_artifact(
                Path(screenshot_path), row_screen_bbox, None, label=f"email_row_grounding_{pass_label}",
                overlay_text=f"pass={pass_label} valid={row_validation.valid} reason={row_validation.reason}",
                boundary_lines=[
                    (row_validation.message_list_left_x, "orange", "sidebar"),
                    (row_validation.message_list_right_x, "cyan", "list_right"),
                ],
                valid=row_validation.valid,
            )
            if artifact_path is not None:
                self.result.email_grounding_debug_artifact = str(artifact_path)
        return row_validation

    def _refine_row_identity(
        self, candidate: EmailCandidate, screen_width: int, screen_height: int, screenshot_path: str,
    ):
        """ONE bounded, SAME-screenshot IDENTITY-AWARE row-grounding
        refinement (2026-09-06 — MAX_EMAIL_ROW_IDENTITY_REFINEMENTS=1,
        no loop, no second attempt). Distinct from _refine_row_bbox()
        (geometry-only tightening of an ALREADY-TRUSTED row): this
        exists because a live run showed Vision correctly ACCEPT a
        candidate's sender+subject (via _evaluate_candidate) yet attach
        a row_bbox belonging to a DIFFERENT row from the same sender —
        the bbox alone was a perfectly plausible message-list row shape,
        so geometry validation could not have caught it.

        Only called by find_target_email() when the resolved candidate
        carries real same-sender-row risk (see the trigger condition
        there) — never reachable for "no candidate", "multiple
        candidates", a malformed provider response, foreground lost, or
        an abort, all of which keep their existing, unchanged behavior
        entirely upstream of this method. Never re-decides business
        identity: the prompt explicitly forbids re-searching the inbox,
        switching sender, or choosing among candidates.

        Reuses screenshot_path VERBATIM — the exact same screenshot the
        original TARGET_EMAIL_SEARCH call analyzed. No physical action
        has occurred yet, so it is still the identical visual state.

        Goes through self.vision (the same VisionService as every other
        call in this file) — Claude stays primary, Gemini stays
        fallback-on-TECHNICAL-failure-only; this is NOT provider
        fallback and never manually constructs a provider.

        Returns (grounded_row_bbox_or_None, technical_failure).
        technical_failure=True means self.result has ALREADY been set to
        the ERROR/TECHNICAL_PROVIDER_ERROR terminal state and the caller
        must return False immediately. grounded_row_bbox is None when
        the refinement could not confirm this candidate's OWN
        sender+subject inside a returned bbox — always a safe stop,
        never a same-sender-but-wrong-subject bbox, and never a
        guessed/repaired binding."""
        self.result.row_identity_refinement_attempted = True
        _email_logger.info(
            "EMAIL_ROW_IDENTITY_REFINEMENT_REQUESTED target_sender=%r target_subject=%r original_bbox=%r",
            candidate.sender, candidate.subject, candidate.row_bbox,
        )

        crop = self._get_message_list_crop(screenshot_path, screen_width, screen_height)
        prompt_text = EMAIL_ROW_IDENTITY_REFINE_PROMPT_PATH.read_text(encoding="utf-8").format(
            target_sender=candidate.sender, target_subject_beginning=candidate.subject,
            width=crop.width, height=crop.height,
        )
        outcome = self.vision.analyze(VisionRequest(
            stage=STAGE_TARGET_EMAIL_ROW_IDENTITY_REFINE, screenshot_path=crop.crop_path,
            goal="Re-ground the already-identified sender+subject to its own exact message-list row",
            prompt_text=prompt_text, response_model=EmailRowIdentityRefinementResponse,
        ))
        self.result.provider_retries += outcome.primary_retries + outcome.fallback_retries
        self.result.fallback_uses += 1 if outcome.fallback_used else 0
        if outcome.parsed is None:
            self.result.result = "ERROR"
            self.result.failure_reason = LaunchFailureReason.TECHNICAL_PROVIDER_ERROR
            self.result.notes = outcome.error
            return None, True

        grounded = outcome.parsed
        if grounded.row_bbox is not None and len(grounded.row_bbox) == 4:
            crop_relative_bbox = list(grounded.row_bbox)
            _email_logger.info(
                "EMAIL_VISION_BBOX_CROP_RELATIVE stage=%s raw_bbox=%r",
                STAGE_TARGET_EMAIL_ROW_IDENTITY_REFINE, crop_relative_bbox,
            )
            grounded = grounded.model_copy(update={"row_bbox": crop.remap_bbox_to_full_screen(crop_relative_bbox)})
            _email_logger.info(
                "EMAIL_VISION_BBOX_REMAPPED stage=%s crop_bbox=%r full_screen_bbox=%r",
                STAGE_TARGET_EMAIL_ROW_IDENTITY_REFINE, crop_relative_bbox, grounded.row_bbox,
            )
        self.result.row_identity_refinement_response = grounded
        call_metrics = outcome.call_metrics
        metrics = CallMetrics(
            latency_ms=call_metrics.latency_ms, input_tokens=call_metrics.input_tokens,
            output_tokens=call_metrics.output_tokens,
            estimated_cost=estimate_cost(
                call_metrics.provider_name, call_metrics.model, call_metrics.input_tokens, call_metrics.output_tokens,
            ),
        )
        self._accumulate(metrics, self.result.find_email_metrics)

        _email_logger.info(
            "EMAIL_ROW_IDENTITY_REFINEMENT_RESULT grounded_sender=%r grounded_subject=%r refined_bbox=%r "
            "confidence=%s provider_used=%s reason=%r",
            grounded.grounded_sender, grounded.grounded_subject, grounded.row_bbox,
            grounded.confidence, outcome.provider_used, grounded.reason,
        )

        # AUTHORITATIVE re-check: deterministic normalized equality on
        # the raw sender/subject Vision actually grounded INSIDE the
        # returned bbox — never Vision's own placement decision trusted
        # blindly. Same truncated-subject-is-a-prefix rule as
        # _evaluate_candidate(), applied here against THIS candidate's
        # own (possibly truncated) subject — never the full target_subject,
        # which may not be what's literally visible on screen.
        sender_match = _normalize_text(grounded.grounded_sender) == _normalize_text(candidate.sender)
        if candidate.subject_truncated:
            visible_normalized = _normalize_text(_strip_truncation_marker(candidate.subject))
            grounded_normalized = _normalize_text(grounded.grounded_subject)
            subject_match = bool(visible_normalized) and grounded_normalized.startswith(visible_normalized)
        else:
            subject_match = _normalize_text(grounded.grounded_subject) == _normalize_text(candidate.subject)

        has_bbox = grounded.row_bbox is not None and len(grounded.row_bbox) == 4
        accepted = bool(has_bbox and sender_match and subject_match)
        _email_logger.info(
            "EMAIL_ROW_IDENTITY_DECISION sender_match=%s subject_match=%s accepted=%s",
            sender_match, subject_match, accepted,
        )
        if not accepted:
            return None, False

        return grounded.row_bbox, False

    def _refine_row_bbox(
        self, candidate: EmailCandidate, original_bbox: list[float],
        screen_width: int, screen_height: int, screenshot_path: str,
    ):
        """ONE bounded, SAME-screenshot row-bbox-ONLY refinement request
        (2026-09-06 live fix — MAX_EMAIL_ROW_BBOX_REFINEMENTS=1, no loop,
        no second attempt). Only ever called when the candidate's
        IDENTITY (sender/subject) has ALREADY been deterministically
        accepted by find_target_email() and validate_email_row_bbox()
        rejected the row_bbox for a pure GEOMETRY reason (see
        app.safety.validators.EMAIL_ROW_BBOX_GEOMETRY_REASONS) — never
        reachable for "no candidate", "multiple candidates", sender/
        subject mismatch, a malformed provider response, foreground
        lost, or an abort, all of which keep their existing, unchanged
        behavior entirely upstream of this method.

        Reuses screenshot_path VERBATIM — the exact same screenshot the
        original TARGET_EMAIL_SEARCH call analyzed. No physical action
        has occurred yet, so it is still the identical visual state; a
        fresh capture is neither needed nor taken.

        Goes through self.vision (the same VisionService as every other
        call in this file) — Claude stays primary, Gemini stays
        fallback-on-TECHNICAL-failure-only, exactly per existing policy;
        this is NOT provider fallback and never manually constructs a
        provider. A schema-valid-but-still-geometrically-unsafe refined
        bbox is never escalated further — that would be asking a second
        provider to "vote" on a dangerous bbox, which is never done.

        Returns (refined_row_bbox_or_None, confidence_to_use,
        technical_failure). technical_failure=True means self.result has
        ALREADY been set to the ERROR/TECHNICAL_PROVIDER_ERROR terminal
        state and the caller must return False immediately without any
        further click attempt."""
        self.result.row_bbox_refinement_attempted = True
        _email_logger.info(
            "EMAIL_ROW_BBOX_REFINEMENT_REQUESTED sender=%r visible_subject=%r original_bbox=%r refinement_attempt=%s",
            candidate.sender, candidate.subject, original_bbox, 1,
        )

        crop = self._get_message_list_crop(screenshot_path, screen_width, screen_height)
        # original_bbox is full-screen-normalized (it came from a prior
        # remap) — phrased back into THIS crop's own coordinate space so
        # the "previous row_bbox" hint in the prompt refers to the same
        # image Vision is actually being shown this call.
        original_bbox_crop_relative = crop.bbox_to_crop_relative(original_bbox)
        prompt_text = EMAIL_ROW_BBOX_REFINE_PROMPT_PATH.read_text(encoding="utf-8").format(
            target_sender=candidate.sender, visible_subject=candidate.subject,
            original_bbox=original_bbox_crop_relative, width=crop.width, height=crop.height,
        )
        outcome = self.vision.analyze(VisionRequest(
            stage=STAGE_TARGET_EMAIL_ROW_BBOX_REFINE, screenshot_path=crop.crop_path,
            goal="Refine the row bbox for the already-identified target email candidate",
            prompt_text=prompt_text, response_model=EmailRowBBoxRefinementResponse,
        ))
        self.result.provider_retries += outcome.primary_retries + outcome.fallback_retries
        self.result.fallback_uses += 1 if outcome.fallback_used else 0
        if outcome.parsed is None:
            # Existing VisionService technical-failure policy applies
            # exactly as everywhere else in this file — a schema-invalid
            # or retry-exhausted refinement call is a TECHNICAL failure,
            # never conflated with a geometry safe-stop.
            self.result.result = "ERROR"
            self.result.failure_reason = LaunchFailureReason.TECHNICAL_PROVIDER_ERROR
            self.result.notes = outcome.error
            return None, candidate.confidence, True

        refined = outcome.parsed
        if refined.row_bbox is not None and len(refined.row_bbox) == 4:
            crop_relative_bbox = list(refined.row_bbox)
            _email_logger.info(
                "EMAIL_VISION_BBOX_CROP_RELATIVE stage=%s raw_bbox=%r",
                STAGE_TARGET_EMAIL_ROW_BBOX_REFINE, crop_relative_bbox,
            )
            refined = refined.model_copy(update={"row_bbox": crop.remap_bbox_to_full_screen(crop_relative_bbox)})
            _email_logger.info(
                "EMAIL_VISION_BBOX_REMAPPED stage=%s crop_bbox=%r full_screen_bbox=%r",
                STAGE_TARGET_EMAIL_ROW_BBOX_REFINE, crop_relative_bbox, refined.row_bbox,
            )
        self.result.row_bbox_refinement_response = refined
        call_metrics = outcome.call_metrics
        metrics = CallMetrics(
            latency_ms=call_metrics.latency_ms, input_tokens=call_metrics.input_tokens,
            output_tokens=call_metrics.output_tokens,
            estimated_cost=estimate_cost(
                call_metrics.provider_name, call_metrics.model, call_metrics.input_tokens, call_metrics.output_tokens,
            ),
        )
        self._accumulate(metrics, self.result.find_email_metrics)

        _email_logger.info(
            "EMAIL_ROW_BBOX_REFINEMENT_RESULT target_visible=%s refined_bbox=%r confidence=%s "
            "provider_used=%s reason=%r",
            refined.target_visible, refined.row_bbox, refined.confidence, outcome.provider_used, refined.reason,
        )

        if not refined.target_visible or refined.row_bbox is None or len(refined.row_bbox) != 4:
            return None, candidate.confidence, False

        return refined.row_bbox, refined.confidence, False

    def _validate_and_record_candidate(
        self, candidate: EmailCandidate, screen_width: int, screen_height: int, screenshot_path: str,
        provider_used: Optional[str] = None,
    ) -> bool:
        """Validates the single resolved candidate's row_bbox against the
        SAME screenshot dimensions that produced it (screen_width/height
        are passed in directly from the originating capture, never a
        stale value from an earlier scroll attempt). An invalid bbox is
        always a safe stop — never repaired, clamped, or inferred.

        Sequence (2026-09-06 — added ONE bounded, same-screenshot
        refinement step between geometry-rejection and safe-stop):
        1. validate_email_row_bbox() on the ORIGINAL bbox (pass=initial)
           — is it a plausible message-list row? Runs BEFORE any click
           point is computed — an implausible bbox is never actionable.
        2. If invalid for a pure GEOMETRY reason (never for "no
           candidate"/ambiguity/sender-subject-mismatch — those are
           already resolved before this method is ever called, and never
           for a malformed bbox — that stays validate_grounding()'s job):
           ONE _refine_row_bbox() call, reusing the SAME screenshot, then
           validate_email_row_bbox() again on the REFINED bbox
           (pass=refined) — the SAME validator, never a weaker one.
        3. Still invalid (original reason non-geometric, refinement
           produced nothing usable, or the refined bbox is STILL
           implausible) → safe stop, zero click, EMAIL_ROW_BBOX_IMPLAUSIBLE.
        4. Only a bbox that passes step 1 or step 2 proceeds to click-
           point derivation and validate_grounding() (unchanged: bbox
           self-consistency, pixel-bounds, sidebar-fraction on the
           DERIVED CLICK POINT, confidence).
        Both providers (Claude primary, Gemini fallback) go through this
        exact same sequence — no provider-name branch anywhere here."""
        _email_logger.info(
            "EMAIL_CANDIDATE_SELECTED sender=%r visible_subject=%r subject_truncated=%s raw_bbox=%r",
            candidate.sender, candidate.subject, candidate.subject_truncated, candidate.row_bbox,
        )

        row_bbox = candidate.row_bbox
        if row_bbox is None or len(row_bbox) != 4:
            self.result.result = "FAIL"
            self.result.failure_reason = LaunchFailureReason.EMAIL_GROUNDING_INVALID
            self.result.notes = f"Candidate had no valid row_bbox: {row_bbox!r}."
            _email_logger.info("EMAIL_CLICK_BLOCKED reason=%s", self.result.failure_reason)
            return False

        self.result.email_grounding_bbox_raw = list(row_bbox)
        effective_confidence = candidate.confidence

        row_validation = self._check_row_bbox_geometry(
            row_bbox, screen_width, screen_height, screenshot_path, provider_used, pass_label="initial",
        )

        if not row_validation.valid:
            if row_validation.reason in EMAIL_ROW_BBOX_GEOMETRY_REASONS and MAX_EMAIL_ROW_BBOX_REFINEMENTS > 0:
                refined_bbox, refined_confidence, technical_failure = self._refine_row_bbox(
                    candidate, row_bbox, screen_width, screen_height, screenshot_path,
                )
                if technical_failure:
                    return False
                if refined_bbox is not None:
                    row_bbox = refined_bbox
                    effective_confidence = refined_confidence
                    self.result.email_grounding_bbox_raw = list(row_bbox)
                    row_validation = self._check_row_bbox_geometry(
                        row_bbox, screen_width, screen_height, screenshot_path, provider_used, pass_label="refined",
                    )
                    self.result.row_bbox_refinement_succeeded = row_validation.valid

            if not row_validation.valid:
                self.result.result = "FAIL"
                self.result.failure_reason = LaunchFailureReason.EMAIL_ROW_BBOX_IMPLAUSIBLE
                refinement_note = " after one bounded refinement attempt" if self.result.row_bbox_refinement_attempted else ""
                self.result.notes = (
                    f"Candidate row_bbox failed message-list plausibility check{refinement_note}: "
                    f"{row_validation.reason}. NO click computed."
                )
                _email_logger.info("EMAIL_CLICK_BLOCKED reason=%s", self.result.failure_reason)
                return False

        y_min, x_min, y_max, x_max = row_bbox
        # Click-point policy (2026-09-06): X is a DETERMINISTIC point
        # within the KNOWN message-list column bounds — see
        # MESSAGE_ROW_CLICK_SAFE_ZONE_FRACTION's docstring in
        # app/config/settings.py for the full justification — computed
        # independently of this candidate's own reported bbox width, so
        # it no longer depends on providers agreeing about how wide a
        # row "is". Then clamped to lie within THIS candidate's own
        # validated bbox (so it always remains a real point on this
        # exact row — the existing point-in-box / sidebar / confidence
        # checks in validate_grounding() below still apply unchanged),
        # and never closer to the sidebar than SIDEBAR_SAFETY_BUFFER_
        # NORMALIZED past LEFT_SIDEBAR_MAX_X_FRACTION (unchanged
        # defense-in-depth floor from the prior policy). Y is unchanged
        # — still the validated bbox's own vertical center.
        message_list_left_normalized = LEFT_SIDEBAR_MAX_X_FRACTION * 1000
        message_list_right_normalized = MESSAGE_LIST_RIGHT_MAX_X_FRACTION * 1000
        safe_zone_x = message_list_left_normalized + MESSAGE_ROW_CLICK_SAFE_ZONE_FRACTION * (
            message_list_right_normalized - message_list_left_normalized
        )
        safe_floor_x = message_list_left_normalized + SIDEBAR_SAFETY_BUFFER_NORMALIZED
        click_raw_x = min(max(safe_zone_x, x_min, safe_floor_x), x_max)
        click_raw_y = (y_min + y_max) / 2
        _email_logger.info(
            "EMAIL_CLICK_POINT policy=message_list_safe_zone message_list_left_x=%s message_list_right_x=%s "
            "safe_zone_fraction=%s computed_click_x=%s row_center_y=%s raw_click_point=(%s, %s)",
            round(message_list_left_normalized / 1000 * screen_width),
            round(message_list_right_normalized / 1000 * screen_width),
            MESSAGE_ROW_CLICK_SAFE_ZONE_FRACTION, click_raw_x, click_raw_y, click_raw_x, click_raw_y,
        )

        validation = validate_grounding(
            raw_x=click_raw_x,
            raw_y=click_raw_y,
            box_2d=row_bbox,
            # effective_confidence is the ORIGINAL candidate's confidence,
            # UNLESS a refinement replaced row_bbox — in that case it is
            # the refinement response's OWN confidence in the geometry it
            # actually reported, since the original confidence pertained
            # to the now-discarded bbox.
            confidence=effective_confidence,
            image_width=screen_width,
            image_height=screen_height,
            confidence_threshold=EMAIL_GROUNDING_CONFIDENCE_THRESHOLD,
            sidebar_max_x_fraction=LEFT_SIDEBAR_MAX_X_FRACTION,
            prefer_provided_point=True,  # use our text-interior point, not validate_grounding()'s default bbox-center
        )

        screen_bbox_pixels = None
        click_point = None
        if validation.converted_x is not None:
            # Reconstruct the full pixel bbox using the same conversion
            # function validate_grounding already applied to the click
            # point — deterministic, no second source of truth.
            px_min, py_min = normalize_1000_to_pixels(x_min, y_min, screen_width, screen_height)
            px_max, py_max = normalize_1000_to_pixels(x_max, y_max, screen_width, screen_height)
            screen_bbox_pixels = [round(py_min), round(px_min), round(py_max), round(px_max)]
            self.result.email_grounding_bbox_pixels = screen_bbox_pixels
            self.result.email_raw_x, self.result.email_raw_y = click_raw_x, click_raw_y
            self.result.email_converted_x, self.result.email_converted_y = validation.converted_x, validation.converted_y
            self.result.email_coordinate_in_screen_bounds = validation.failure != GroundingCheckFailure.OUT_OF_BOUNDS
            click_point = (validation.converted_x, validation.converted_y)

        sidebar_boundary_x = round(screen_width * LEFT_SIDEBAR_MAX_X_FRACTION)
        _email_logger.info(
            "EMAIL_LAYOUT_SAFETY sidebar_boundary_x=%s click_x=%s safe=%s",
            sidebar_boundary_x, validation.converted_x, validation.valid,
        )

        if _DEBUG_ARTIFACTS_ENABLED:
            artifact_path = save_grounding_debug_artifact(
                Path(screenshot_path), screen_bbox_pixels, click_point, label="email_row_grounding",
                overlay_text=(
                    f"sender={candidate.sender!r} confidence={effective_confidence} "
                    f"valid={validation.valid} reason={validation.failure}"
                ),
                boundary_lines=[
                    (row_validation.message_list_left_x, "orange", "sidebar"),
                    (row_validation.message_list_right_x, "cyan", "list_right"),
                ],
                valid=validation.valid,
            )
            if artifact_path is not None:
                self.result.email_grounding_debug_artifact = str(artifact_path)

        if not validation.valid:
            self.result.result = "FAIL"
            self.result.failure_reason = _GROUNDING_FAILURE_TO_REASON[validation.failure]
            if validation.notes:
                self.result.notes = validation.notes
            _email_logger.info("EMAIL_CLICK_BLOCKED reason=%s", self.result.failure_reason)
            return False

        self.result.email_found_at = datetime.now().isoformat()
        if self._find_start_monotonic is not None:
            self.result.email_search_ms = round((time.monotonic() - self._find_start_monotonic) * 1000, 1)
        return True

    def _compute_preclick_roi_pixels(
        self, row_bbox: list[float], screen_width: int, screen_height: int,
    ) -> tuple[int, int, int, int]:
        """Derives the message-list ROI (left, top, right, bottom, native
        screen pixels) the pre-click freshness guard compares — the
        KNOWN message-list column bounds horizontally (same constants
        the click-X safe zone and bbox-right-boundary validation already
        use), and the target row's own y-range padded by
        EMAIL_PRECLICK_ROI_Y_PADDING_FRACTION of screen height vertically
        (wide enough to also catch a neighboring row being inserted/
        removed just above or below, not just changes to the row's own
        exact pixels) — never the whole desktop, never fixed pixels."""
        y_min, _x_min, y_max, _x_max = row_bbox
        left = round(LEFT_SIDEBAR_MAX_X_FRACTION * screen_width)
        right = round(MESSAGE_LIST_RIGHT_MAX_X_FRACTION * screen_width)
        row_top_px = round(y_min / 1000 * screen_height)
        row_bottom_px = round(y_max / 1000 * screen_height)
        padding_px = round(EMAIL_PRECLICK_ROI_Y_PADDING_FRACTION * screen_height)
        top = max(0, row_top_px - padding_px)
        bottom = min(screen_height, row_bottom_px + padding_px)
        return (left, top, right, bottom)

    def _ensure_target_row_still_fresh_or_reground(self) -> bool:
        """Fast, fully local (no Vision call) pre-click freshness guard
        (2026-09-06) — see app.safety.screen_freshness for the deterministic
        pixel-difference comparison. Runs immediately after the click
        point is fully computed and the first foreground precheck has
        passed, but BEFORE any pyautogui.moveTo()/click().

        Common path (message-list ROI unchanged since grounding): zero
        added Vision calls, returns True immediately — the existing
        click flow proceeds exactly as before this guard existed.

        Recovery path (ROI changed): the stale coordinates are NEVER
        used. Reuses the ALREADY-CAPTURED fresh screenshot as the new
        grounding screenshot and runs the full provider-neutral
        matching/identity-refinement/geometry pipeline against it (one
        bounded attempt — MAX_TARGET_EMAIL_STALE_REGROUND_ATTEMPTS=1,
        no loop), updating self.result.email_converted_x/y to the
        freshly-grounded point. Then ONE more freshness check runs
        (comparing that re-ground's own screenshot against a third,
        even-fresher capture) before finally trusting it — if the
        screen changed again, this is a safe stop
        (TARGET_EMAIL_SCREEN_CHANGED_BEFORE_CLICK), never a second
        re-ground and never a click on stale geometry either way.

        Returns True only when it is safe to proceed to the physical
        move/click using self.result.email_converted_x/y as they stand
        when this method returns. Returns False when self.result has
        ALREADY been set to a terminal FAIL/ERROR state — the caller
        must return False immediately."""
        original_path = self.result.target_email_grounding_screenshot_path
        screen_width = self.result.target_email_grounding_capture_width
        screen_height = self.result.target_email_grounding_capture_height
        row_bbox = self.result.email_grounding_bbox_raw

        if original_path is None or screen_width is None or screen_height is None or row_bbox is None:
            # No grounding-screenshot bookkeeping to compare against —
            # unreachable in the real find_target_email() -> click_
            # target_email() sequence (which always populates this
            # before a click point exists), so there is nothing
            # meaningful to detect staleness against. Never a reason to
            # invent a false "changed" signal or block the click.
            return True

        _email_logger.info("EMAIL_PRECLICK_FRESHNESS_CHECK_START")
        start = time.monotonic()
        try:
            fresh_capture = capture_screen(DEFAULT_OUTPUT_DIR)
        except ScreenCaptureError as exc:
            self.result.result = "ERROR"
            self.result.failure_reason = LaunchFailureReason.TECHNICAL_PROVIDER_ERROR
            self.result.notes = f"Pre-click freshness screenshot failed: {exc}"
            return False

        roi = self._compute_preclick_roi_pixels(row_bbox, screen_width, screen_height)
        freshness = check_message_list_roi_freshness(
            original_path, fresh_capture.path, roi, EMAIL_PRECLICK_FRESHNESS_THRESHOLD,
        )
        elapsed_ms = round((time.monotonic() - start) * 1000, 1)
        self.result.preclick_freshness_checks += 1
        self.result.preclick_freshness_last_changed = freshness.changed
        self.result.preclick_freshness_last_score = freshness.difference_score
        _email_logger.info(
            "EMAIL_PRECLICK_FRESHNESS_RESULT changed=%s difference_score=%.2f threshold=%s roi=%r elapsed_ms=%s",
            freshness.changed, freshness.difference_score, freshness.threshold, freshness.roi, elapsed_ms,
        )
        if _DEBUG_ARTIFACTS_ENABLED:
            save_freshness_debug_artifact(original_path, fresh_capture.path, roi)

        if not freshness.changed:
            return True

        # Screen changed — the stale coordinates are never used. Exactly
        # one bounded re-ground cycle, using the fresh screenshot already
        # captured above as the new grounding screenshot.
        if self.result.stale_reground_attempted or MAX_TARGET_EMAIL_STALE_REGROUND_ATTEMPTS <= 0:
            self.result.result = "FAIL"
            self.result.failure_reason = LaunchFailureReason.TARGET_EMAIL_SCREEN_CHANGED_BEFORE_CLICK
            self.result.notes = "Message-list ROI changed again before a physical action could be taken. NO click computed."
            return False
        self.result.stale_reground_attempted = True

        candidate, provider_used = self._ground_target_email_candidate(fresh_capture)
        if candidate is None:
            if self.result.result not in ("FAIL", "ERROR"):
                self.result.result = "FAIL"
                self.result.failure_reason = LaunchFailureReason.TARGET_EMAIL_NOT_FOUND
                self.result.notes = (
                    "Target email candidate no longer found after a pre-click screen-change re-ground."
                )
            return False

        if not self._validate_and_record_candidate(
            candidate, fresh_capture.width, fresh_capture.height, fresh_capture.path, provider_used=provider_used,
        ):
            return False

        # Re-ground succeeded against the fresh screenshot — it becomes
        # the new grounding screenshot for bookkeeping, then ONE more
        # freshness check runs before finally trusting it.
        self.result.target_email_grounding_screenshot_path = fresh_capture.path
        self.result.target_email_grounding_capture_width = fresh_capture.width
        self.result.target_email_grounding_capture_height = fresh_capture.height

        _email_logger.info("EMAIL_PRECLICK_FRESHNESS_CHECK_START")
        start2 = time.monotonic()
        try:
            second_fresh_capture = capture_screen(DEFAULT_OUTPUT_DIR)
        except ScreenCaptureError as exc:
            self.result.result = "ERROR"
            self.result.failure_reason = LaunchFailureReason.TECHNICAL_PROVIDER_ERROR
            self.result.notes = f"Pre-click freshness screenshot failed: {exc}"
            return False

        roi2 = self._compute_preclick_roi_pixels(
            self.result.email_grounding_bbox_raw, fresh_capture.width, fresh_capture.height,
        )
        freshness2 = check_message_list_roi_freshness(
            fresh_capture.path, second_fresh_capture.path, roi2, EMAIL_PRECLICK_FRESHNESS_THRESHOLD,
        )
        elapsed_ms2 = round((time.monotonic() - start2) * 1000, 1)
        self.result.preclick_freshness_checks += 1
        self.result.preclick_freshness_last_changed = freshness2.changed
        self.result.preclick_freshness_last_score = freshness2.difference_score
        _email_logger.info(
            "EMAIL_PRECLICK_FRESHNESS_RESULT changed=%s difference_score=%.2f threshold=%s roi=%r elapsed_ms=%s",
            freshness2.changed, freshness2.difference_score, freshness2.threshold, freshness2.roi, elapsed_ms2,
        )
        if _DEBUG_ARTIFACTS_ENABLED:
            save_freshness_debug_artifact(fresh_capture.path, second_fresh_capture.path, roi2)
        if freshness2.changed:
            self.result.result = "FAIL"
            self.result.failure_reason = LaunchFailureReason.TARGET_EMAIL_SCREEN_CHANGED_BEFORE_CLICK
            self.result.notes = "Message-list ROI changed again after the bounded re-ground. NO click computed."
            return False

        return True

    def click_target_email(self) -> bool:
        """Exactly one physical click, unconditionally on the validated
        (self.result.email_converted_x, email_converted_y) point computed
        earlier by _validate_and_record_candidate(). No retry, no second
        click, no keyboard fallback — a post-click verification failure
        is ALWAYS handled by verify_email_opened() as an observation-only
        retry, never by coming back here.

        2026-09-06 diagnostics-only addition: explicit EMAIL_* log lines
        at every step of this method, added because a live run's
        EMAIL_OPEN_VERIFICATION_FAILED result could not be definitively
        attributed to "click never executed" vs. "click executed at an
        unhelpful point" vs. "foreground/abort blocked it" from the log
        alone. None of these logs change control flow or add any new
        physical action — the method's behavior is otherwise identical
        to before."""
        if self.check_abort("before_email_move"):
            return False

        title = get_foreground_window_title()
        self.result.foreground_before_email_move = title
        foreground_ok = is_outlook_foreground(title)
        _email_logger.info(
            "EMAIL_CLICK_PRECHECK foreground_ok=%s abort_requested=%s screen_x=%s screen_y=%s",
            foreground_ok, self.abort_controller.is_abort_requested(),
            self.result.email_converted_x, self.result.email_converted_y,
        )
        if not foreground_ok:
            self.result.result = "FAIL"
            self.result.failure_reason = LaunchFailureReason.OUTLOOK_FOREGROUND_LOST
            self.result.notes = f"Outlook not foreground before email move; foreground was {title!r}. NO movement, no click."
            return False

        if not self._ensure_target_row_still_fresh_or_reground():
            return False

        assert pyautogui.FAILSAFE is True
        _email_logger.info(
            "EMAIL_MOUSE_MOVE_ABOUT_TO_EXECUTE screen_x=%s screen_y=%s",
            self.result.email_converted_x, self.result.email_converted_y,
        )
        pyautogui.moveTo(self.result.email_converted_x, self.result.email_converted_y, duration=MOVE_DURATION_SECONDS)
        _email_logger.info(
            "EMAIL_MOUSE_MOVE_EXECUTED screen_x=%s screen_y=%s",
            self.result.email_converted_x, self.result.email_converted_y,
        )

        # Diagnostic-only cursor-position confirmation — a cheap read,
        # never a correction loop, never a reason to fail on a tiny
        # rounding difference (see CURSOR_POSITION_TOLERANCE_PX).
        try:
            actual_x, actual_y = pyautogui.position()
            matches = (
                abs(actual_x - self.result.email_converted_x) <= CURSOR_POSITION_TOLERANCE_PX
                and abs(actual_y - self.result.email_converted_y) <= CURSOR_POSITION_TOLERANCE_PX
            )
            _email_logger.info(
                "EMAIL_MOUSE_POSITION_CONFIRMED expected_x=%s expected_y=%s actual_x=%s actual_y=%s matches=%s",
                self.result.email_converted_x, self.result.email_converted_y, actual_x, actual_y, matches,
            )
        except Exception:  # noqa: BLE001 — diagnostic only, must never affect the click flow
            pass

        if self.check_abort("before_email_click"):
            return False

        title2 = get_foreground_window_title()
        self.result.foreground_before_email_click = title2
        if not is_outlook_foreground(title2):
            self.result.result = "FAIL"
            self.result.failure_reason = LaunchFailureReason.OUTLOOK_FOREGROUND_LOST
            self.result.notes = f"Outlook not foreground before email click; foreground was {title2!r}. NO CLICK performed."
            return False

        _email_logger.info(
            "EMAIL_CLICK_ABOUT_TO_EXECUTE screen_x=%s screen_y=%s",
            self.result.email_converted_x, self.result.email_converted_y,
        )
        pyautogui.click()  # single click only
        _email_logger.info(
            "EMAIL_CLICK_EXECUTED screen_x=%s screen_y=%s",
            self.result.email_converted_x, self.result.email_converted_y,
        )
        self.result.email_open_click_timestamp = datetime.now().isoformat()
        self.result.email_open_click_executed = True
        self.result.mouse_click_count += 1
        self.result.email_click_count += 1
        return True

    # --- Phase 4: verify correct email opened ---

    def verify_email_opened(self) -> bool:
        subject_required = bool(self.result.target_subject and self.result.target_subject.strip())
        subject_clause = self.result.target_subject.strip() or "(not specified — match on sender alone)"
        verify_start = time.monotonic()

        for attempt_number in range(1, MAX_EMAIL_OPEN_VERIFICATION_ATTEMPTS + 1):
            if self.check_abort("during_post_click_wait"):
                return False

            delay = POST_CLICK_INITIAL_WAIT_SECONDS if attempt_number == 1 else POST_CLICK_RETRY_WAIT_SECONDS
            time.sleep(delay)

            if self.check_abort("before_verification"):
                return False

            _email_open_verify_logger.info("EMAIL_POST_CLICK_VERIFICATION_START observation_attempt=%s", attempt_number)

            attempt = EmailOpenVerificationAttempt(
                attempt_number=attempt_number, delay_seconds=delay, timestamp=datetime.now().isoformat()
            )
            try:
                capture = capture_screen(DEFAULT_OUTPUT_DIR)
            except ScreenCaptureError as exc:
                attempt.error = str(exc)
                self.result.email_open_verification_attempts.append(attempt)
                self.result.result = "ERROR"
                self.result.failure_reason = LaunchFailureReason.TECHNICAL_PROVIDER_ERROR
                self.result.notes = f"Post-click capture failed: {exc}"
                return False
            attempt.screenshot = capture.filename

            prompt_text = EMAIL_OPEN_VERIFICATION_PROMPT_PATH.read_text(encoding="utf-8").format(
                target_sender=self.result.target_sender, target_subject_clause=subject_clause,
            )
            outcome = self.vision.analyze(VisionRequest(
                stage=STAGE_EMAIL_OPEN_VERIFICATION, screenshot_path=Path(capture.path),
                goal="Verify the correct email opened", prompt_text=prompt_text,
                response_model=EmailOpenVerificationResponse,
            ))
            self.result.provider_retries += outcome.primary_retries + outcome.fallback_retries
            self.result.fallback_uses += 1 if outcome.fallback_used else 0
            if outcome.parsed is None:
                attempt.error = outcome.error
                self.result.email_open_verification_attempts.append(attempt)
                self.result.result = "ERROR"
                self.result.failure_reason = LaunchFailureReason.TECHNICAL_PROVIDER_ERROR
                self.result.notes = outcome.error
                return False
            structured = outcome.parsed
            call_metrics = outcome.call_metrics

            metrics = CallMetrics(
                latency_ms=call_metrics.latency_ms, input_tokens=call_metrics.input_tokens,
                output_tokens=call_metrics.output_tokens,
                estimated_cost=estimate_cost(
                    call_metrics.provider_name, call_metrics.model, call_metrics.input_tokens, call_metrics.output_tokens,
                ),
            )
            self._accumulate(metrics, self.result.verify_email_opened_metrics)
            attempt.metrics = metrics

            attempt.schema_valid = True
            attempt.email_open = structured.email_open
            attempt.subject_detected = structured.subject_detected
            attempt.sender_detected = structured.sender_detected
            attempt.subject_match = structured.subject_match  # Vision's own judgment — audit only, never the gate
            attempt.sender_match = structured.sender_match    # (see the deterministic recheck below)
            attempt.body_visible = structured.body_visible
            attempt.confidence = structured.confidence
            attempt.reason = structured.reason
            self.result.email_open_verification_attempts.append(attempt)
            self.result.email_open_verification = structured

            # Diagnostics only (no behavior change): log the raw parsed
            # fields before the deterministic accept/reject decision below.
            # observation_attempt is THIS method's own bounded-retry index
            # (1..MAX_EMAIL_OPEN_VERIFICATION_ATTEMPTS, one fresh screenshot
            # each) — distinct from the provider-level "attempt=" numbering
            # logged inside app.fallback.recovery.call_with_provider_retry,
            # which restarts at 1 on every one of these calls (it counts
            # same-provider technical retries WITHIN a single observation,
            # not observations themselves). Two observations that each
            # succeed on their first provider try will each log
            # VISION_CALL_START ... attempt=1 — that is expected, not a bug.
            _email_open_verify_logger.info(
                "EMAIL_OPEN_VERIFY_RESULT observation_attempt=%s provider_used=%s fallback_used=%s "
                "email_open=%s body_visible=%s visible_sender=%r visible_subject=%r "
                "vision_subject_match=%s vision_sender_match=%s confidence=%s reason=%r",
                attempt_number, outcome.provider_used, outcome.fallback_used,
                structured.email_open, structured.body_visible,
                structured.sender_detected, structured.subject_detected,
                structured.subject_match, structured.sender_match, structured.confidence, structured.reason,
            )

            # AUTHORITATIVE post-open check: deterministic sender/subject
            # equality on the raw sender/subject Vision actually read from
            # the now-open email — never Vision's own self-reported
            # subject_match/sender_match booleans (kept as audit-only
            # fields on `structured`, logged above, never consulted here).
            # This is what makes it safe to have clicked a
            # PROVISIONAL_TARGET_MATCH (a truncated-subject candidate):
            # that candidate is never treated as confirmed until THIS
            # check passes against the complete, now-fully-visible
            # subject. Same "Vision reports, Python decides" rule as every
            # other match in this file.
            #
            # Sender comparison (2026-09-06 fix) goes through
            # sender_matches() rather than a flat normalized-string
            # equality — a live run showed the correct email opened, with
            # Vision correctly reading sender="Yash Dhanraj"-equivalent
            # identity as "Yash<yashdhanraj9140@gmail.com>", get rejected
            # as sender_mismatch purely because target_sender="Yash" was
            # compared as an opaque string against the full "Name <email>"
            # display text. sender_matches() parses both sides into
            # (display_name, email_address) and compares the right
            # component(s) deterministically — see app/safety/
            # sender_identity.py's module docstring for the exact rules.
            # Still exact-equality only: no substring/startswith/fuzzy
            # matching was introduced.
            target_parsed = parse_sender(self.result.target_sender)
            detected_parsed = parse_sender(structured.sender_detected)
            sender_ok = sender_matches(self.result.target_sender, structured.sender_detected)
            subject_ok = (not subject_required) or (
                _normalize_text(structured.subject_detected) == _normalize_text(self.result.target_subject)
            )
            accepted = bool(structured.email_open and sender_ok and subject_ok and structured.body_visible)
            if not accepted:
                if not structured.email_open:
                    reject_reason = "email_open_false"
                elif not sender_ok:
                    reject_reason = "sender_mismatch"
                elif not subject_ok:
                    reject_reason = "subject_mismatch"
                else:
                    reject_reason = "body_not_visible"
            else:
                reject_reason = "n/a"
            _email_open_verify_logger.info(
                "EMAIL_OPEN_VERIFY_DECISION observation_attempt=%s accepted=%s sender_ok=%s subject_ok=%s "
                "subject_required=%s body_visible=%s reason=%s "
                "normalized_target_sender=%r normalized_sender_detected=%r "
                "normalized_target_subject=%r normalized_subject_detected=%r "
                "target_sender_mode=%s detected_display_name=%r email_present=%s",
                attempt_number, accepted, sender_ok, subject_ok,
                subject_required, structured.body_visible, reject_reason,
                _normalize_text(self.result.target_sender), _normalize_text(structured.sender_detected),
                _normalize_text(self.result.target_subject) if subject_required else "",
                _normalize_text(structured.subject_detected) if subject_required else "",
                sender_mode(target_parsed), detected_parsed.display_name, detected_parsed.has_email,
            )
            if accepted:
                self.result.result = "PASS"
                self.result.email_open_verified_at = datetime.now().isoformat()
                self.result.email_open_ms = round((time.monotonic() - verify_start) * 1000, 1)
                return True

            # Not ready / not matched yet — no re-click, only another
            # verification look if attempts remain.

        last = self.result.email_open_verification
        if last is not None:
            sender_ok_last = _normalize_text(last.sender_detected) == _normalize_text(self.result.target_sender)
            subject_ok_last = (not subject_required) or (
                _normalize_text(last.subject_detected) == _normalize_text(self.result.target_subject)
            )
        if last is not None and last.email_open and not (subject_ok_last and sender_ok_last):
            self.result.result = "FAIL"
            self.result.failure_reason = LaunchFailureReason.WRONG_EMAIL_OPENED
            self.result.notes = (
                f"An email opened but did not match the target after {MAX_EMAIL_OPEN_VERIFICATION_ATTEMPTS} attempts: "
                f"subject_detected={last.subject_detected!r} sender_detected={last.sender_detected!r}."
            )
        else:
            self.result.result = "FAIL"
            self.result.failure_reason = LaunchFailureReason.EMAIL_OPEN_VERIFICATION_FAILED
            self.result.notes = f"Email open/body-visible not confirmed after {MAX_EMAIL_OPEN_VERIFICATION_ATTEMPTS} attempts."
        return False
