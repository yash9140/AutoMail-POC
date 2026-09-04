"""Draft generation, controlled typing, and draft verification, plus the
assembled ReplyDraftSteps class chaining every phase of this stage.

Split out of app/playbook/reply_draft_steps.py::ReplyDraftSteps
(RND-009D) into three files for the final POC runtime — behavior-
preserving move, same method bodies:
  - app/outlook/read_email.py::EmailUnderstandingSteps (email
    understanding)
  - app/outlook/reply.py::ReplyDiscoverySteps (Reply discovery/click/
    editor verification)
  - this file's DraftSteps mixin (generate/type/verify draft)
assembled here into ONE ReplyDraftSteps class via mixin inheritance —
they share one result object, one abort/accumulate/session-timer
machinery, and compose app.outlook.find_email.FindOpenEmailSteps for
phases 1-4, exactly as the single pre-split class did.

The type_draft() method body below is moved CHARACTER-FOR-CHARACTER
from the original — see tests/test_typing_regression.py, which proves
byte-for-byte behavioral equivalence via a golden segment/Enter trace.
Never calls pyautogui.write() with a string containing '\\n'; every
line is its own segment, Enter is pressed explicitly between segments,
and foreground is re-checked before EVERY segment write and EVERY Enter
press — on drift, aborts immediately with no auto-refocus, no retype.
"""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import pyautogui
from pydantic import BaseModel, Field, ValidationError

from app.automation.screen_capture import DEFAULT_OUTPUT_DIR, ScreenCaptureError, capture_screen
from app.config.settings import (
    DRAFT_MIN_LENGTH,
    DRAFT_VERIFICATION_RETRY_WAIT_SECONDS,
    FOCUS_SETTLE_DELAY_SECONDS,
    MAX_DRAFT_VERIFICATION_ATTEMPTS,
    PLACEHOLDER_MARKERS,
    POST_TYPING_WAIT_SECONDS,
    TYPE_INTERVAL_SECONDS,
)
from app.fallback.recovery import call_with_provider_retry
from app.metrics.step_metrics import estimate_cost
from app.outlook.find_email import FindOpenEmailSteps
from app.outlook.read_email import EmailUnderstandingSteps
from app.outlook.reply import ReplyDiscoverySteps
from app.playbook.failure_reasons import LaunchFailureReason
from app.safety.abort_controller import AbortController
from app.safety.foreground import get_foreground_window_title, is_outlook_foreground
from app.vision.models import EmailSection
from app.vision.providers.base import VisionProvider
from rnd.models.contextual_reply import ReplyGenerationResponse
from rnd.models.find_open_email import StepMetrics
from rnd.models.outlook_launch import CallMetrics
from rnd.models.reply_draft import DraftVerificationResponseV2, RND009DResult

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "rnd" / "prompts"
REPLY_GENERATION_PROMPT_PATH = PROMPTS_DIR / "reply_generation_v1.txt"
DRAFT_VERIFICATION_PROMPT_PATH = PROMPTS_DIR / "draft_verification_v2.txt"

# Vision-call stage identifiers (app/fallback/recovery.py structured
# logging).
#
# DRAFT_VERIFICATION's call site passes max_retries=0 deliberately:
# verify_draft() already has its OWN outer retry loop (up to
# MAX_DRAFT_VERIFICATION_ATTEMPTS), and each of ITS attempts takes a
# fresh screenshot — a strictly better retry than call_with_provider_
# retry's blind re-ask against the same stale capture, so a second layer
# of retry here would be redundant, not additive.
#
# DRAFT_GENERATION has no such outer loop, so it must not be zero-retry
# too (live-run bug, 2026-09-04: it was the only single-shot Vision call
# in the entire pipeline with zero retries, so one transient timeout —
# already proven, elsewhere in this session, to be a real, recurring
# Gemini characteristic and not a code defect — was instantly fatal with
# no fallback, while every other stage's identical transient failure
# self-heals on its default retry, exactly as OUTLOOK_SEARCH did in the
# same live run that surfaced this). Uses the default PROVIDER_RETRY_
# COUNT like every other single-shot stage; the retry is a pure re-ask
# against the SAME already-captured screenshot — no new capture, no new
# physical action — so it stays within call_with_provider_retry's own
# "technical failure only, never a semantic answer" retry contract.
STAGE_DRAFT_GENERATION = "DRAFT_GENERATION"
STAGE_DRAFT_VERIFICATION = "DRAFT_VERIFICATION"


class DraftVerificationAttempt(BaseModel):
    """One observation-only draft-verification attempt (Phase 6) — same
    shape as app.outlook.reply.ReplyEditorVerificationAttempt. A failed
    attempt never re-types or re-clicks; only another fresh screenshot
    is taken on the next attempt, up to MAX_DRAFT_VERIFICATION_ATTEMPTS."""

    attempt_number: int
    delay_seconds: float
    timestamp: Optional[str] = None
    screenshot: Optional[str] = None

    reply_editor_open: Optional[bool] = None
    semantic_match: Optional[bool] = None
    detected_draft: Optional[str] = None
    confidence: Optional[float] = None
    reason: Optional[str] = None

    metrics: CallMetrics = Field(default_factory=CallMetrics)
    schema_valid: bool = False
    error: Optional[str] = None


def _validate_draft_quality(draft: str) -> tuple[bool, str]:
    """Local, programmatic gate — no provider call. Non-empty, reasonable
    length, no obvious placeholder text, no send-shortcut-like artifacts."""
    if not draft or not draft.strip():
        return False, "Draft is empty."
    if len(draft.strip()) < DRAFT_MIN_LENGTH:
        return False, f"Draft is shorter than the minimum expected length ({DRAFT_MIN_LENGTH} chars)."
    lowered = draft.lower()
    for marker in PLACEHOLDER_MARKERS:
        if marker.lower() in lowered:
            return False, f"Draft contains an obvious placeholder marker: {marker!r}."
    return True, "Draft passed local quality checks."


class DraftSteps:
    """Mixin — expects self.result, self.provider, self.check_abort(),
    self._accumulate() from the composing class (ReplyDraftSteps below)."""

    def generate_draft(self) -> bool:
        if self.result.content_complete is False:
            raise RuntimeError(
                "Cannot generate a draft: the email was not fully read "
                "(content_complete=False). Not drafting from an incomplete read."
            )
        if self.check_abort("before_draft_generation"):
            return False

        self._draft_start_monotonic = time.monotonic()
        self.result.draft_generation_started_at = datetime.now().isoformat()

        prompt_text = REPLY_GENERATION_PROMPT_PATH.read_text(encoding="utf-8").format(
            email_summary=self.result.email_understanding_summary,
            sender_intent=self.result.email_understanding_sender_intent,
            requested_action=self.result.email_understanding_requested_action,
            important_points=self.result.email_understanding_important_points,
        )
        try:
            capture = capture_screen(DEFAULT_OUTPUT_DIR)
        except ScreenCaptureError as exc:
            self.result.result = "ERROR"
            self.result.failure_reason = LaunchFailureReason.TECHNICAL_PROVIDER_ERROR
            self.result.notes = f"Capture failed: {exc}"
            return False

        outcome = call_with_provider_retry(
            lambda: self.provider.analyze_screen(Path(capture.path), "Draft a reply", prompt_text),
            stage=STAGE_DRAFT_GENERATION, provider_name=self.provider.provider_name,
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
        self._accumulate(metrics, self.result.generate_draft_metrics)
        self.result.reply_generation_metrics = metrics

        try:
            structured = ReplyGenerationResponse.model_validate(call.parsed_json) if call.parsed_json else None
        except ValidationError:
            structured = None
        if structured is None:
            self.result.result = "ERROR"
            self.result.failure_reason = LaunchFailureReason.DRAFT_GENERATION_FAILED
            self.result.notes = "Reply-generation response was not schema-valid."
            return False

        self.result.draft_generated_at = datetime.now().isoformat()
        self.result.draft_generation_ms = round((time.monotonic() - self._draft_start_monotonic) * 1000, 1)

        self.result.draft_reply = structured.draft_reply
        self.result.draft_reasoning_summary = structured.reasoning_summary
        self.result.draft_generation_confidence = structured.confidence

        passed, notes = _validate_draft_quality(structured.draft_reply)
        self.result.draft_quality_passed = passed
        self.result.draft_quality_notes = notes
        if not passed:
            self.result.result = "FAIL"
            self.result.failure_reason = LaunchFailureReason.DRAFT_VALIDATION_FAILED
            self.result.notes = notes
            return False

        return True

    def type_draft(self) -> bool:
        if not self.result.draft_quality_passed:
            raise RuntimeError("Cannot type: draft did not pass the local quality gate.")
        if self.check_abort("before_typing"):
            return False

        title4 = get_foreground_window_title()
        if not is_outlook_foreground(title4):
            self.result.typing_result = "ABORTED"
            self.result.result = "ABORTED"
            self.result.failure_reason = LaunchFailureReason.OUTLOOK_FOREGROUND_LOST
            self.result.partial_typing = False
            self.result.notes = "Foreground changed before typing. Nothing typed."
            return False

        time.sleep(FOCUS_SETTLE_DELAY_SECONDS)

        typing_start_monotonic = time.monotonic()
        self.result.typing_started_at = datetime.now().isoformat()

        assert pyautogui.FAILSAFE is True, "Refusing to type: FAILSAFE disabled."
        draft = self.result.draft_reply
        segments = draft.split("\n")
        segments_written = 0
        for i, segment in enumerate(segments):
            if segment:
                title_seg = get_foreground_window_title()
                if not is_outlook_foreground(title_seg):
                    self.result.typing_result = "ABORTED"
                    self.result.result = "ABORTED"
                    self.result.failure_reason = LaunchFailureReason.FOREGROUND_CHANGED_DURING_TYPING
                    self.result.segments_typed_before_abort = i
                    self.result.segment_index_aborted_at = i
                    self.result.typing_abort_stage = "before_write"
                    self.result.typed_text = "\n".join(segments[:i])
                    self.result.partial_typing = True
                    self.result.notes = (
                        f"Foreground changed to {title_seg!r} before segment {i} of {len(segments)} could be typed. "
                        f"{i} segment(s) had already been typed. No further keys sent."
                    )
                    return False
                pyautogui.write(segment, interval=TYPE_INTERVAL_SECONDS)
                segments_written += 1
            if i < len(segments) - 1:
                title_enter = get_foreground_window_title()
                if not is_outlook_foreground(title_enter):
                    self.result.typing_result = "ABORTED"
                    self.result.result = "ABORTED"
                    self.result.failure_reason = LaunchFailureReason.FOREGROUND_CHANGED_DURING_TYPING
                    self.result.segments_typed_before_abort = i + 1
                    self.result.segment_index_aborted_at = i
                    self.result.typing_abort_stage = "before_enter"
                    self.result.typed_text = "\n".join(segments[: i + 1])
                    self.result.partial_typing = True
                    self.result.notes = (
                        f"Foreground changed to {title_enter!r} before the Enter press following segment {i}. "
                        f"{i + 1} segment(s) had already been typed. No further keys sent."
                    )
                    return False
                pyautogui.press("enter")

        self.result.typed_text = draft
        self.result.text_entry_executed = True
        self.result.typing_result = "PASS"
        self.result.partial_typing = False
        self.result.typing_completed_at = datetime.now().isoformat()
        self.result.typing_ms = round((time.monotonic() - typing_start_monotonic) * 1000, 1)
        self.result.typing_segment_count = segments_written
        return True

    def verify_draft(self) -> bool:
        """Bounded, observation-only verification loop (Phase 6) — same
        shape as app.outlook.reply.ReplyDiscoverySteps.verify_reply_editor().
        Each attempt takes a FRESH screenshot; a failed attempt never
        re-types or re-clicks anything, it only tries another observation
        if attempts remain. Exhaustion is always DRAFT_VERIFICATION_FAILED."""
        if not self.result.text_entry_executed:
            raise RuntimeError("Cannot verify: nothing was typed.")
        if self.check_abort("before_draft_verification"):
            return False

        expected_draft = self.result.draft_reply
        self.result.expected_draft = expected_draft
        prompt_text = DRAFT_VERIFICATION_PROMPT_PATH.read_text(encoding="utf-8").format(expected_draft=expected_draft)

        for attempt_number in range(1, MAX_DRAFT_VERIFICATION_ATTEMPTS + 1):
            if attempt_number > 1 and self.check_abort("before_draft_verification_retry"):
                return False

            delay = POST_TYPING_WAIT_SECONDS if attempt_number == 1 else DRAFT_VERIFICATION_RETRY_WAIT_SECONDS
            time.sleep(delay)

            title = get_foreground_window_title()
            self.result.foreground_before_draft_capture = title
            if not is_outlook_foreground(title):
                self.result.result = "ABORTED"
                self.result.failure_reason = LaunchFailureReason.OUTLOOK_FOREGROUND_LOST
                self.result.notes = (
                    f"Verification aborted: Outlook was not foreground at capture time (attempt {attempt_number}). "
                    "No screenshot captured."
                )
                return False

            attempt = DraftVerificationAttempt(
                attempt_number=attempt_number, delay_seconds=delay, timestamp=datetime.now().isoformat()
            )

            try:
                capture = capture_screen(DEFAULT_OUTPUT_DIR)
            except ScreenCaptureError as exc:
                attempt.error = str(exc)
                self.result.draft_verification_attempts.append(attempt)
                self.result.result = "ERROR"
                self.result.failure_reason = LaunchFailureReason.TECHNICAL_PROVIDER_ERROR
                self.result.notes = f"Verification capture failed: {exc}"
                return False
            attempt.screenshot = capture.filename
            self.result.post_typing_screenshot = capture.filename

            outcome = call_with_provider_retry(
                lambda: self.provider.analyze_screen(Path(capture.path), "Verify typed draft", prompt_text),
                stage=STAGE_DRAFT_VERIFICATION, provider_name=self.provider.provider_name, max_retries=0,
            )
            self.result.provider_retries += outcome.retries_used
            if outcome.result is None:
                attempt.error = outcome.error
                self.result.draft_verification_attempts.append(attempt)
                self.result.result = "ERROR"
                self.result.failure_reason = LaunchFailureReason.TECHNICAL_PROVIDER_ERROR
                self.result.notes = outcome.error
                return False
            call = outcome.result

            metrics = CallMetrics(
                latency_ms=call.latency_ms, input_tokens=call.input_tokens, output_tokens=call.output_tokens,
                estimated_cost=estimate_cost(self.provider.provider_name, call.model, call.input_tokens, call.output_tokens),
            )
            self._accumulate(metrics, self.result.verify_draft_metrics)
            self.result.draft_verification_metrics = metrics
            attempt.metrics = metrics

            try:
                structured = DraftVerificationResponseV2.model_validate(call.parsed_json) if call.parsed_json else None
            except ValidationError:
                structured = None
            if structured is None:
                attempt.error = "Response was not schema-valid."
                self.result.draft_verification_attempts.append(attempt)
                self.result.result = "ERROR"
                self.result.failure_reason = LaunchFailureReason.TECHNICAL_PROVIDER_ERROR
                self.result.notes = "Draft-verification response was not schema-valid."
                return False

            attempt.schema_valid = True
            attempt.reply_editor_open = structured.reply_editor_open
            attempt.semantic_match = structured.semantic_match
            attempt.detected_draft = structured.detected_draft
            attempt.confidence = structured.confidence
            attempt.reason = structured.reason
            self.result.draft_verification_attempts.append(attempt)

            self.result.detected_draft = structured.detected_draft
            self.result.exact_match = structured.detected_draft.strip() == expected_draft.strip()
            self.result.semantic_match = structured.semantic_match
            self.result.draft_verification_reply_editor_open = structured.reply_editor_open

            if structured.reply_editor_open and structured.semantic_match:
                self.result.result = "PASS"
                self.result.draft_verified_at = datetime.now().isoformat()
                draft_start = getattr(self, "_draft_start_monotonic", None)
                if draft_start is not None:
                    self.result.draft_ready_ms = round((time.monotonic() - draft_start) * 1000, 1)
                return True

            # Not confirmed yet — no re-type, no re-click; only another
            # fresh-screenshot observation if attempts remain.

        self.result.result = "FAIL"
        self.result.failure_reason = LaunchFailureReason.DRAFT_VERIFICATION_FAILED
        self.result.notes = (
            f"Draft verification failed: editor not open and/or content did not semantically match "
            f"after {MAX_DRAFT_VERIFICATION_ATTEMPTS} attempt(s)."
        )
        return False


class ReplyDraftResult(RND009DResult):
    """Phase 4 + Phase 5 additions, as a subclass rather than editing
    rnd/models/reply_draft.py directly — that file is historical R&D
    evidence and is never modified.

    email_sections retains EVERY accumulated section in full (never
    collapsed into a single running summary) so a date/commitment/name
    mentioned only in an early section is never lost — see
    app/outlook/read_email.py and docs/architecture/04_SCROLLING_AND_LONG_EMAIL.md.

    reply_expectation/requires_user_decision are the authoritative reply-
    necessity classification (app.vision.models.ReplyExpectation),
    added here rather than on the historical RND009DResult.requires_reply
    field — that field is retained unchanged as a derived, backward-
    compatible legacy alias only (see read_email.py's
    _finalize_understanding())."""

    provider_retries: int = 0
    reply_expectation: Optional[str] = None
    requires_user_decision: Optional[bool] = None

    # --- Phase 4: read email ---
    email_reading_started_at: Optional[str] = None
    email_reading_ms: Optional[float] = None
    email_sections: list[EmailSection] = Field(default_factory=list)
    sections_seen: int = 0
    email_body_scroll_attempts: int = 0
    content_complete: Optional[bool] = None

    # --- Phase 5: find reply ---
    find_reply_started_at: Optional[str] = None
    reply_found_at: Optional[str] = None
    reply_search_ms: Optional[float] = None
    reply_search_scroll_attempts: int = 0
    reply_grounding_confidence: Optional[float] = None
    reply_grounding_bbox_raw: Optional[list[float]] = None
    reply_grounding_bbox_pixels: Optional[list[float]] = None
    reply_click_count: int = 0
    reply_editor_verified_at: Optional[str] = None
    reply_editor_open_ms: Optional[float] = None

    # --- Phase 6: generate + type + verify draft ---
    draft_generation_started_at: Optional[str] = None
    draft_generated_at: Optional[str] = None
    draft_generation_ms: Optional[float] = None

    typing_started_at: Optional[str] = None
    typing_completed_at: Optional[str] = None
    typing_ms: Optional[float] = None
    typing_segment_count: Optional[int] = None
    partial_typing: Optional[bool] = None

    draft_verification_attempts: list[DraftVerificationAttempt] = Field(default_factory=list)
    draft_verified_at: Optional[str] = None
    draft_ready_ms: Optional[float] = None


class ReplyDraftSteps(EmailUnderstandingSteps, ReplyDiscoverySteps, DraftSteps):
    def __init__(self, abort_controller: AbortController, provider: VisionProvider, model: str) -> None:
        self.abort_controller = abort_controller
        self.provider = provider
        self.model = model
        self.result = ReplyDraftResult()
        self.find_open = FindOpenEmailSteps(abort_controller, provider, model)
        self.result.target_subject = self.find_open.result.target_subject
        self.result.target_sender = self.find_open.result.target_sender
        self._session_start_monotonic: Optional[float] = None

    def start_session(self) -> None:
        self._session_start_monotonic = time.monotonic()
        self.result.session_start = datetime.now().isoformat()

    def finalize_session(self) -> None:
        self.result.session_end = datetime.now().isoformat()
        if self._session_start_monotonic is not None:
            self.result.total_elapsed_ms = round((time.monotonic() - self._session_start_monotonic) * 1000, 1)

    def _accumulate(self, metrics: CallMetrics, step: Optional[StepMetrics] = None) -> None:
        self.result.total_vision_calls += 1
        self.result.total_input_tokens += metrics.input_tokens or 0
        self.result.total_output_tokens += metrics.output_tokens or 0
        self.result.total_estimated_cost = round(self.result.total_estimated_cost + (metrics.estimated_cost or 0), 6)
        self.result.total_latency_ms = round(self.result.total_latency_ms + (metrics.latency_ms or 0), 3)
        if step is not None:
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

    # --- Phases 1-4: reuse FindOpenEmailSteps verbatim, then fold its
    # result into this stage's own record ---

    def run_find_and_open_email(self) -> bool:
        fo = self.find_open
        fo.start_session()  # own internal timer; this stage's own start_session() governs the reported total
        ok = fo.run_launch_and_readiness()
        ok = ok and fo.find_target_email()
        ok = ok and fo.click_target_email()
        ok = ok and fo.verify_email_opened()
        fo.finalize_session()

        # Copy every RND009CResult field (base class of RND009DResult)
        # onto this stage's own result — inheritance makes this a clean,
        # single loop rather than 40+ lines of manual field assignment.
        # Uses getattr() (the actual attribute value, preserving nested
        # Pydantic model instances) rather than model_dump() (which would
        # recursively flatten those nested models into plain dicts and
        # break every .field access downstream, e.g.
        # self.result.email_open_verification.email_open).
        from rnd.models.find_open_email import RND009CResult as _Base

        for field_name in _Base.model_fields:
            if field_name in ("session_start", "session_end", "total_elapsed_ms"):
                continue  # this stage's own start_session()/finalize_session() own these
            setattr(self.result, field_name, getattr(fo.result, field_name))

        # provider_retries is declared on FindEmailResult (app/outlook/find_email.py),
        # an app-level addition over RND009CResult — it is NOT in
        # RND009CResult.model_fields, so the loop above never copies it.
        # Folded in explicitly here, same convention as
        # FindOpenEmailSteps.run_launch_and_readiness()'s own
        # `r.provider_retries += lr.provider_retries` one layer down —
        # every Phase 1-4 provider retry must reach this stage's total.
        self.result.provider_retries += fo.result.provider_retries

        if not ok:
            self.result.result = fo.result.result
            self.result.failure_reason = fo.result.failure_reason
            self.result.notes = fo.result.notes
        return ok
