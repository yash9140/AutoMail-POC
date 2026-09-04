"""Email understanding — pure step logic, no Qt dependency.

Phase 4: understand_email() is a bounded scroll-and-accumulate loop
over the reading pane, structured per section (app.vision.models.
EmailSection) — see docs/architecture/04_SCROLLING_AND_LONG_EMAIL.md for the
full design. A short email is simply the sections_seen == 1 case of
this same loop; there is no separate code path for "short" vs "long."

Vision sees exactly one screenshot per call, always. Continuity across
calls is carried by a verbatim prompt-injected tail of the previous
section's content (already_read_tail) — never by asking Vision to
"remember" anything, and overlap between sections is stripped by
deterministic Python (never trusted as "already handled" by Vision).

Defined as a mixin (EmailUnderstandingSteps) rather than a standalone
class because it shares one result object, one abort/accumulate
machinery, and one provider/model with the Reply and Draft phases that
follow it in the same chained run — see app/outlook/draft.py for the
final assembled ReplyDraftSteps class (and ReplyDraftResult, which
carries this phase's new fields as a subclass of the historical
RND009DResult — rnd/ is never edited).
"""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from app.automation.screen_capture import DEFAULT_OUTPUT_DIR, ScreenCaptureError, capture_screen
from app.automation.scrolling import scroll_email_body
from app.config.settings import (
    EMAIL_BODY_SCROLL_STABILIZE_WAIT_SECONDS,
    EMAIL_SECTION_TAIL_CHARS,
    MAX_EMAIL_BODY_SCROLL_ATTEMPTS,
)
from app.fallback.recovery import call_with_provider_retry
from app.metrics.step_metrics import estimate_cost
from app.playbook.failure_reasons import LaunchFailureReason
from app.safety.foreground import get_foreground_window_title, is_outlook_foreground
from app.vision.models import (
    EmailHolisticAssessmentResponse,
    EmailSection,
    EmailSectionExtractionResponse,
    ReplyExpectation,
)
from rnd.models.outlook_launch import CallMetrics

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "app" / "vision" / "prompts"
# Split into two calls (2026-09-04 latency fix — see EmailSectionExtractionResponse's
# docstring in app/vision/models.py): the old single email_section_understanding_v1.txt
# 16-field mega-request was a repeatable live Gemini-timeout cause. Extraction runs on
# EVERY section (light); holistic assessment runs ONCE, after the true end is confirmed.
EMAIL_SECTION_EXTRACTION_PROMPT_PATH = PROMPTS_DIR / "email_section_extraction_v1.txt"
EMAIL_HOLISTIC_ASSESSMENT_PROMPT_PATH = PROMPTS_DIR / "email_holistic_assessment_v1.txt"

# Vision-call stage identifiers (app/fallback/recovery.py structured logging) —
# distinct tags so each half's own latency/retries are diagnosable separately.
STAGE_EMAIL_SECTION_EXTRACTION = "EMAIL_SECTION_EXTRACTION"
STAGE_EMAIL_HOLISTIC_ASSESSMENT = "EMAIL_HOLISTIC_ASSESSMENT"


def _strip_overlap(content: str, overlap_text: str) -> str:
    """Deterministic, caller-side overlap removal — if the reported
    overlap_text genuinely appears at the start of this section's
    content, strip it; otherwise leave content untouched (never trust
    an overlap claim that doesn't actually verify against the raw
    text)."""
    if overlap_text and content.startswith(overlap_text):
        return content[len(overlap_text):]
    return content


def _merge_unique(*lists: list[str]) -> list[str]:
    seen: set[str] = set()
    merged: list[str] = []
    for lst in lists:
        for item in lst:
            key = item.strip().lower()
            if key and key not in seen:
                seen.add(key)
                merged.append(item)
    return merged


class EmailUnderstandingSteps:
    """Mixin — expects self.result, self.provider, self.check_abort(),
    self._accumulate() from the composing class (app.outlook.draft.ReplyDraftSteps)."""

    def understand_email(self) -> bool:
        if not (self.result.email_open_verification and self.result.email_open_verification.email_open):
            raise RuntimeError("Cannot understand email before EMAIL_OPENED is confirmed.")

        self.result.email_reading_started_at = datetime.now().isoformat()
        reading_start = time.monotonic()

        for section_index in range(1, MAX_EMAIL_BODY_SCROLL_ATTEMPTS + 1):
            if self.check_abort("before_email_reading"):
                return False

            title = get_foreground_window_title()
            if not is_outlook_foreground(title):
                self.result.result = "FAIL"
                self.result.failure_reason = LaunchFailureReason.OUTLOOK_FOREGROUND_LOST
                self.result.notes = f"Outlook not foreground before reading section {section_index}; foreground was {title!r}."
                return False

            try:
                capture = capture_screen(DEFAULT_OUTPUT_DIR)
            except ScreenCaptureError as exc:
                self.result.result = "ERROR"
                self.result.failure_reason = LaunchFailureReason.TECHNICAL_PROVIDER_ERROR
                self.result.notes = f"Capture failed: {exc}"
                return False

            already_read_tail = self._accumulated_tail()
            prompt_text = EMAIL_SECTION_EXTRACTION_PROMPT_PATH.read_text(encoding="utf-8").format(
                already_read_tail=already_read_tail, section_index=section_index,
            )
            outcome = call_with_provider_retry(
                lambda: self.provider.analyze_screen(Path(capture.path), "Read this section of the open email", prompt_text),
                stage=STAGE_EMAIL_SECTION_EXTRACTION, provider_name=self.provider.provider_name,
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
            self._accumulate(metrics, self.result.understand_email_metrics)
            self.result.email_understanding_metrics = metrics

            try:
                raw = EmailSectionExtractionResponse.model_validate(call.parsed_json) if call.parsed_json else None
            except ValidationError:
                raw = None
            if raw is None:
                self.result.result = "ERROR"
                self.result.failure_reason = LaunchFailureReason.EMAIL_UNDERSTANDING_FAILED
                self.result.notes = f"Email-section-extraction response was not schema-valid (section {section_index})."
                return False

            stripped_content = _strip_overlap(raw.extracted_visible_content, raw.overlap_text)

            # STRICT completion rule: content_complete is NEVER read
            # directly from Vision — it is always this AND, computed
            # here in Python. more_content_below=false alone is never
            # enough (that was the live bug: Vision reported it false on
            # seeing a Reply control / signature / believing "enough"
            # was read, before the true end of a long email was ever
            # seen) — end_of_message_visible requires Vision to have
            # POSITIVE evidence of the actual end (see the prompt's
            # explicit forbidden-reasons list for that field).
            reached_true_end = (not raw.more_content_below) and raw.end_of_message_visible

            # Scroll-progress verification, using the SAME existing
            # overlap-tail mechanism this file already relies on (never
            # brittle pixel/coordinate diffing): if a scroll produced no
            # content beyond what was already read — confirmed by EITHER
            # Vision's own no_new_content report OR the deterministic
            # stripped-content-is-empty check — the scroll made no real
            # progress. Never silently retried into "more attempts";
            # never treated as if the true end had thereby been reached.
            no_progress = section_index > 1 and (raw.no_new_content or not stripped_content.strip())

            section = EmailSection(
                section_index=section_index,
                extracted_visible_content=stripped_content,
                important_points=raw.important_points,
                requested_actions=raw.requested_actions,
                names_entities=raw.names_entities,
                dates=raw.dates,
                commitments=raw.commitments,
                overlap_with_previous=raw.overlap_text or None,
                more_content_below=raw.more_content_below,
                end_of_message_visible=raw.end_of_message_visible,
                no_new_content=no_progress,
                confidence=raw.confidence,
                # reply_expectation/requires_user_decision/requires_reply/
                # sender_intent/requested_action_summary are left at
                # EmailSection's own defaults here — they are filled in
                # by _run_holistic_assessment() below, called exactly
                # ONCE, only when reached_true_end. Computing a holistic
                # assessment on every intermediate section was wasted
                # work: _finalize_understanding() only ever used the
                # LAST section's values anyway (see its docstring).
            )
            self.result.email_sections.append(section)
            self.result.sections_seen = len(self.result.email_sections)

            if no_progress and not reached_true_end:
                self.result.content_complete = False
                self.result.result = "FAIL"
                self.result.failure_reason = LaunchFailureReason.CONTENT_NOT_FULLY_READ
                self.result.notes = (
                    f"Body scroll before section {section_index} produced no new content, but the email's "
                    "true end was not confirmed (more_content_below and/or end_of_message_visible evidence "
                    "still incomplete). A stuck/ineffective scroll is never silently retried into 'more "
                    "attempts' or treated as reaching the end."
                )
                return False

            if reached_true_end:
                self.result.content_complete = True
                if not self._run_holistic_assessment(capture):
                    return False
                return self._finalize_understanding(reading_start)

            if section_index < MAX_EMAIL_BODY_SCROLL_ATTEMPTS:
                if self.check_abort("before_email_body_scroll"):
                    return False
                scroll_email_body(capture.width, capture.height)
                self.result.email_body_scroll_attempts += 1
                time.sleep(EMAIL_BODY_SCROLL_STABILIZE_WAIT_SECONDS)
                continue

            # Bounded limit reached without confirmed true-end evidence —
            # a genuine incomplete read, never silently treated as complete.
            self.result.content_complete = False
            self.result.result = "FAIL"
            self.result.failure_reason = LaunchFailureReason.CONTENT_NOT_FULLY_READ
            self.result.notes = (
                f"Email body's true end was not confirmed (more_content_below and/or end_of_message_visible) "
                f"after {MAX_EMAIL_BODY_SCROLL_ATTEMPTS} bounded section reads. Not proceeding to Reply/draft "
                "generation on an incomplete read."
            )
            return False

        # Unreachable — the loop above always returns.
        return False

    def _accumulated_tail(self) -> str:
        if not self.result.email_sections:
            return ""
        full_text = "".join(s.extracted_visible_content for s in self.result.email_sections)
        return full_text[-EMAIL_SECTION_TAIL_CHARS:]

    def _run_holistic_assessment(self, capture) -> bool:
        """Second, lighter call — made EXACTLY ONCE per email, immediately
        after the true end is confirmed — classifying reply_expectation/
        requires_user_decision/sender_intent/requested_action_summary/
        confidence from the now-COMPLETE accumulated text, instead of
        bundling this into every section's extraction call (2026-09-04
        latency fix — see EmailSectionExtractionResponse's docstring in
        app/vision/models.py). Mutates the LAST EmailSection in place
        with the result; _finalize_understanding() already only ever
        read from that last section."""
        full_text = "".join(s.extracted_visible_content for s in self.result.email_sections)
        prompt_text = EMAIL_HOLISTIC_ASSESSMENT_PROMPT_PATH.read_text(encoding="utf-8").format(
            full_email_text=full_text,
        )
        outcome = call_with_provider_retry(
            lambda: self.provider.analyze_screen(Path(capture.path), "Classify the email's reply expectation", prompt_text),
            stage=STAGE_EMAIL_HOLISTIC_ASSESSMENT, provider_name=self.provider.provider_name,
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
        self._accumulate(metrics, self.result.understand_email_metrics)

        try:
            raw = EmailHolisticAssessmentResponse.model_validate(call.parsed_json) if call.parsed_json else None
        except ValidationError:
            raw = None
        if raw is None:
            self.result.result = "ERROR"
            self.result.failure_reason = LaunchFailureReason.EMAIL_UNDERSTANDING_FAILED
            self.result.notes = "Email holistic-assessment response was not schema-valid."
            return False

        last = self.result.email_sections[-1]
        last.reply_expectation = raw.reply_expectation
        last.requires_user_decision = raw.requires_user_decision
        last.requires_reply = (raw.reply_expectation != ReplyExpectation.SHOULD_NOT_REPLY)  # derived legacy alias
        last.sender_intent = raw.sender_intent
        last.requested_action_summary = raw.requested_action_summary
        last.confidence = raw.confidence
        return True

    def _finalize_understanding(self, reading_start: float) -> bool:
        """Builds the final understanding from ALL accumulated sections
        (concatenated content with overlaps already stripped, important_
        points/requested_actions/names_entities/dates/commitments unioned
        across sections) — never from a single running summary string
        that could have silently dropped detail. reply_expectation/
        requires_user_decision/sender_intent/requested_action_summary/
        confidence come from the LAST section's holistic assessment (the
        only one made with full context).

        Deterministic reply-necessity policy (app.vision.models.
        ReplyExpectation) — "does the sender require a reply" is NOT the
        same question as "does the user intend to reply": this POC is
        inherently a user-initiated TARGETED reply run (target_sender is
        required; there is no code path that reaches here without the
        user having explicitly started one), so that intent is already a
        standing fact, not something to re-derive per email. MUST_REPLY
        and OPTIONAL_REPLY therefore both continue; only SHOULD_NOT_REPLY
        (a normal Reply would be inappropriate/unsafe/non-conversational)
        is a genuine safe stop. requires_user_decision is independent —
        recorded here for downstream awareness (e.g. draft generation
        must not invent the user's actual decision), never itself a gate
        at this stage."""
        sections = self.result.email_sections
        last = sections[-1]

        self.result.email_understanding_summary = "".join(s.extracted_visible_content for s in sections)
        self.result.email_understanding_sender_intent = last.sender_intent
        self.result.reply_expectation = last.reply_expectation
        self.result.requires_user_decision = last.requires_user_decision
        self.result.requires_reply = last.requires_reply  # derived legacy alias — see EmailSection docstring
        self.result.email_understanding_requested_action = last.requested_action_summary
        self.result.email_understanding_important_points = _merge_unique(*(s.important_points for s in sections))
        self.result.email_understanding_confidence = last.confidence

        self.result.email_reading_ms = round((time.monotonic() - reading_start) * 1000, 1)

        if last.reply_expectation == ReplyExpectation.SHOULD_NOT_REPLY:
            self.result.result = "FAIL"
            self.result.failure_reason = LaunchFailureReason.REPLY_NOT_APPROPRIATE
            self.result.notes = (
                "Email understanding classified this email as SHOULD_NOT_REPLY "
                f"(sender_intent={last.sender_intent!r}). Not generating a reply."
            )
            return False

        return True
