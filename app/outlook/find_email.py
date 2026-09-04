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
from pydantic import BaseModel, Field, ValidationError

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from app.automation.screen_capture import DEFAULT_OUTPUT_DIR, ScreenCaptureError, capture_screen  # noqa: E402
from app.automation.scrolling import scroll_message_list  # noqa: E402
from app.config.settings import (  # noqa: E402
    LEFT_SIDEBAR_MAX_X_FRACTION,
    MAX_EMAIL_OPEN_VERIFICATION_ATTEMPTS,
    MAX_MESSAGE_LIST_SCROLL_ATTEMPTS,
    MESSAGE_LIST_SCROLL_STABILIZE_WAIT_SECONDS,
    MOVE_DURATION_SECONDS,
    POST_CLICK_INITIAL_WAIT_SECONDS,
    POST_CLICK_RETRY_WAIT_SECONDS,
    VISION_CONFIDENCE_THRESHOLD,
)
from app.fallback.recovery import call_with_provider_retry  # noqa: E402
from app.metrics.step_metrics import estimate_cost  # noqa: E402
from app.outlook.launch import OutlookLaunchSteps, _provider  # noqa: E402
from app.playbook.failure_reasons import LaunchFailureReason  # noqa: E402
from app.safety.abort_controller import AbortController  # noqa: E402
from app.safety.foreground import get_foreground_window_title, is_outlook_foreground  # noqa: E402
from app.safety.validators import GroundingCheckFailure, validate_grounding  # noqa: E402
from app.vision.debug_overlay import save_grounding_debug_artifact  # noqa: E402
from app.vision.grounding import normalize_1000_to_pixels  # noqa: E402
from app.vision.models import EmailCandidate, EmailSearchResponse  # noqa: E402
from app.vision.providers.base import VisionProvider  # noqa: E402
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

# Vision-call stage identifiers (app/fallback/recovery.py structured logging).
STAGE_TARGET_EMAIL_SEARCH = "TARGET_EMAIL_SEARCH"
STAGE_EMAIL_OPEN_VERIFICATION = "EMAIL_OPEN_VERIFICATION"

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

# --- Message-row click-point policy (2026-09-04 live fix) -------------------
# A live run failed EMAIL_GROUNDING_SIDEBAR_REJECTED on a real Outlook
# inbox: the selected candidate's row_bbox had x_min sitting almost
# exactly AT the message-list's real left boundary (confirmed via a
# real Vision call against the actual failing screenshot: x_min
# normalized ~170, i.e. ~17% of screen width — essentially identical to
# LEFT_SIDEBAR_MAX_X_FRACTION itself). The geometric CENTER of a row
# bbox is only as safe as half that bbox's width away from the sidebar
# boundary — for a bbox whose left edge already sits at the boundary,
# that margin can be thin, and any per-call Vision imprecision in
# either edge can push the center to the wrong side.
#
# Fix: click a point biased toward the RIGHT side of the row (where the
# sender/subject TEXT actually is — a real message row always has its
# checkbox/unread-dot/avatar confined to a narrow strip at the very
# left edge, never in the middle or right of the row) rather than the
# raw geometric center. Expressed as a FRACTION of the bbox's own
# width — never a fixed pixel offset, never tied to any specific
# screen resolution or Outlook layout — so it scales correctly at any
# resolution and for a row anywhere on screen. k=0.6 was chosen simply
# as "meaningfully right of center, but still comfortably inside a
# normally-proportioned row" — not tuned to the live failure's exact
# numbers. This does NOT replace the sidebar safety check (which stays
# exactly as strict) — it only picks a more robust point to test
# against that same check, and the point is still validated as lying
# inside the candidate's own bbox before anything is trusted.
MESSAGE_ROW_CLICK_X_PROPORTION = 0.6

# --- Second live failure (2026-09-04, same day): the proportional point
# ABOVE still landed inside the sidebar zone (rejected coordinate x=301,
# boundary x<=326) — proof that a 60%-of-bbox-width bias alone isn't
# always enough when the reported row_bbox itself is narrow/left-skewed
# enough (e.g. Vision still bounding mostly the avatar/checkbox area
# despite the strengthened prompt). A fixed proportion of a bad bbox is
# still just a fraction of something too far left.
#
# Second layer of defense: clamp the click point to never be closer to
# the sidebar boundary than a small buffer PAST it — but never past the
# bbox's own right edge (x_max). This directly reuses the EXISTING,
# already-approved LEFT_SIDEBAR_MAX_X_FRACTION threshold (not a new
# invented screen position) plus a buffer expressed in the same 0-1000
# normalized space every bbox/threshold in this project already uses
# (so it scales with resolution exactly like MAX_PLAUSIBLE_RESULT_BBOX_
# NORMALIZED_SPAN does for OUTLOOK_SEARCH — never a raw pixel offset
# tied to one screen). The result:
#   - if the bbox extends AT ALL meaningfully past the sidebar boundary,
#     the click point is pushed to a safely-past-boundary point instead
#     of whatever the flat proportion happened to land on;
#   - if the bbox doesn't reach past the boundary+buffer at all, the
#     point is clamped to the bbox's own x_max (the best any policy can
#     do) — and the existing sidebar check downstream still correctly
#     rejects it, because the row genuinely doesn't extend into a safe
#     click area.
SIDEBAR_SAFETY_BUFFER_NORMALIZED = 15.0  # ~1.5% of screen width, 0-1000 normalized — not a pixel offset

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
    email_click_count: int = 0
    email_open_verified_at: Optional[str] = None
    email_open_ms: Optional[float] = None


class FindOpenEmailSteps:
    def __init__(
        self,
        abort_controller: AbortController,
        provider: VisionProvider,
        model: str,
        target_sender: str = TARGET_EMAIL_SENDER,
        target_subject: str = TARGET_EMAIL_SUBJECT,
    ) -> None:
        if not target_sender or not target_sender.strip():
            raise ValueError("target_sender is required and must be non-empty.")
        self.abort_controller = abort_controller
        self.provider = provider
        self.model = model
        self.result = FindEmailResult(target_subject=target_subject or "", target_sender=target_sender)
        self.launch = OutlookLaunchSteps(abort_controller, provider, model)
        self._session_start_monotonic: Optional[float] = None
        self._find_start_monotonic: Optional[float] = None

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
            ok = self.launch.click_outlook_result()
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

        subject_clause = self.result.target_subject.strip() or "(not specified — match on sender alone)"

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

            prompt_text = EMAIL_SEARCH_PROMPT_PATH.read_text(encoding="utf-8").format(
                width=capture.width, height=capture.height,
                target_sender=self.result.target_sender, target_subject_clause=subject_clause,
            )
            outcome = call_with_provider_retry(
                lambda: self.provider.analyze_screen(Path(capture.path), "Search for the target email", prompt_text),
                stage=STAGE_TARGET_EMAIL_SEARCH, provider_name=self.provider.provider_name,
            )
            self.result.provider_retries += outcome.retries_used
            if outcome.result is None:
                self.result.result = "ERROR"
                self.result.failure_reason = LaunchFailureReason.TECHNICAL_PROVIDER_ERROR
                self.result.notes = outcome.error
                return False
            call = outcome.result

            metrics = CallMetrics(
                latency_ms=call.latency_ms, input_tokens=call.input_tokens, output_tokens=call.output_tokens,
                estimated_cost=estimate_cost(self.provider.provider_name, call.model, call.input_tokens, call.output_tokens),
            )
            self._accumulate(metrics, self.result.find_email_metrics)
            self.result.email_grounding_metrics = metrics

            try:
                structured = EmailSearchResponse.model_validate(call.parsed_json) if call.parsed_json else None
            except ValidationError:
                structured = None
            if structured is None:
                self.result.result = "ERROR"
                self.result.failure_reason = LaunchFailureReason.TECHNICAL_PROVIDER_ERROR
                self.result.notes = "Email-search response was not schema-valid."
                return False

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
                    return False
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
                return False

            if candidate is not None:
                self.result.provisional_match = is_provisional
                return self._validate_and_record_candidate(candidate, capture.width, capture.height, capture.path)

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

    def _validate_and_record_candidate(
        self, candidate: EmailCandidate, screen_width: int, screen_height: int, screenshot_path: str,
    ) -> bool:
        """Validates the single resolved candidate's row_bbox against the
        SAME screenshot dimensions that produced it (screen_width/height
        are passed in directly from the originating capture, never a
        stale value from an earlier scroll attempt). An invalid bbox is
        always a safe stop — never repaired, clamped, or inferred, and
        never a reason to scroll and try again."""
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

        y_min, x_min, y_max, x_max = row_bbox
        # Click-point policy: biased toward the row's text region rather
        # than the raw geometric center — see MESSAGE_ROW_CLICK_X_PROPORTION's
        # docstring for the full justification. Then clamped to never be
        # closer to the sidebar than SIDEBAR_SAFETY_BUFFER_NORMALIZED past
        # LEFT_SIDEBAR_MAX_X_FRACTION — see that constant's docstring for
        # why (the proportional point alone still wasn't always enough).
        # Still always a point INSIDE the candidate's own bbox: the clamp
        # only ever pushes the point RIGHT, and never past x_max.
        proportional_x = x_min + MESSAGE_ROW_CLICK_X_PROPORTION * (x_max - x_min)
        sidebar_boundary_normalized = LEFT_SIDEBAR_MAX_X_FRACTION * 1000
        safe_floor_x = sidebar_boundary_normalized + SIDEBAR_SAFETY_BUFFER_NORMALIZED
        click_raw_x = min(max(proportional_x, safe_floor_x), x_max)
        click_raw_y = (y_min + y_max) / 2
        _email_logger.info(
            "EMAIL_CLICK_POINT policy=text_interior proportion=%s proportional_x=%s "
            "safe_floor_x=%s boundary_clamped=%s raw_click_point=(%s, %s)",
            MESSAGE_ROW_CLICK_X_PROPORTION, proportional_x, safe_floor_x,
            click_raw_x != proportional_x, click_raw_x, click_raw_y,
        )

        validation = validate_grounding(
            raw_x=click_raw_x,
            raw_y=click_raw_y,
            box_2d=row_bbox,
            confidence=candidate.confidence,
            image_width=screen_width,
            image_height=screen_height,
            confidence_threshold=EMAIL_GROUNDING_CONFIDENCE_THRESHOLD,
            sidebar_max_x_fraction=LEFT_SIDEBAR_MAX_X_FRACTION,
            prefer_provided_point=True,  # use our text-interior point, not validate_grounding()'s default bbox-center
        )

        self.result.email_grounding_bbox_raw = list(row_bbox)
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
                overlay_text=f"sender={candidate.sender!r} confidence={candidate.confidence}",
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

    def click_target_email(self) -> bool:
        if self.check_abort("before_email_move"):
            return False

        title = get_foreground_window_title()
        self.result.foreground_before_email_move = title
        if not is_outlook_foreground(title):
            self.result.result = "FAIL"
            self.result.failure_reason = LaunchFailureReason.OUTLOOK_FOREGROUND_LOST
            self.result.notes = f"Outlook not foreground before email move; foreground was {title!r}. NO movement, no click."
            return False

        assert pyautogui.FAILSAFE is True
        pyautogui.moveTo(self.result.email_converted_x, self.result.email_converted_y, duration=MOVE_DURATION_SECONDS)

        if self.check_abort("before_email_click"):
            return False

        title2 = get_foreground_window_title()
        self.result.foreground_before_email_click = title2
        if not is_outlook_foreground(title2):
            self.result.result = "FAIL"
            self.result.failure_reason = LaunchFailureReason.OUTLOOK_FOREGROUND_LOST
            self.result.notes = f"Outlook not foreground before email click; foreground was {title2!r}. NO CLICK performed."
            return False

        pyautogui.click()  # single click only
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
            outcome = call_with_provider_retry(
                lambda: self.provider.analyze_screen(Path(capture.path), "Verify the correct email opened", prompt_text),
                stage=STAGE_EMAIL_OPEN_VERIFICATION, provider_name=self.provider.provider_name,
            )
            self.result.provider_retries += outcome.retries_used
            if outcome.result is None:
                attempt.error = outcome.error
                self.result.email_open_verification_attempts.append(attempt)
                self.result.result = "ERROR"
                self.result.failure_reason = LaunchFailureReason.TECHNICAL_PROVIDER_ERROR
                self.result.notes = outcome.error
                return False
            call = outcome.result

            metrics = CallMetrics(
                latency_ms=call.latency_ms, input_tokens=call.input_tokens, output_tokens=call.output_tokens,
                estimated_cost=estimate_cost(self.provider.provider_name, call.model, call.input_tokens, call.output_tokens),
            )
            self._accumulate(metrics, self.result.verify_email_opened_metrics)
            attempt.metrics = metrics

            try:
                structured = EmailOpenVerificationResponse.model_validate(call.parsed_json) if call.parsed_json else None
            except ValidationError:
                structured = None

            if structured is None:
                attempt.error = "Response was not schema-valid."
                self.result.email_open_verification_attempts.append(attempt)
                self.result.result = "ERROR"
                self.result.failure_reason = LaunchFailureReason.TECHNICAL_PROVIDER_ERROR
                self.result.notes = "Email-open-verification response was not schema-valid."
                return False

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

            # AUTHORITATIVE post-open check: deterministic normalized
            # FULL-TEXT equality on the raw sender/subject Vision actually
            # read from the now-open email — never Vision's own self-
            # reported subject_match/sender_match booleans. This is what
            # makes it safe to have clicked a PROVISIONAL_TARGET_MATCH
            # (a truncated-subject candidate): that candidate is never
            # treated as confirmed until THIS check passes against the
            # complete, now-fully-visible subject. Same "Vision reports,
            # Python decides" rule as every other match in this file.
            sender_ok = _normalize_text(structured.sender_detected) == _normalize_text(self.result.target_sender)
            subject_ok = (not subject_required) or (
                _normalize_text(structured.subject_detected) == _normalize_text(self.result.target_subject)
            )
            if structured.email_open and sender_ok and subject_ok and structured.body_visible:
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
