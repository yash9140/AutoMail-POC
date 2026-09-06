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

import logging
import time
from datetime import datetime
from pathlib import Path

from app.automation.screen_capture import DEFAULT_OUTPUT_DIR, ScreenCaptureError, capture_screen
from app.automation.scrolling import scroll_email_reading_body
from app.config.settings import (
    EMAIL_BODY_SCROLL_ANCHOR_X_FRACTION,
    EMAIL_BODY_SCROLL_ANCHOR_Y_FRACTION,
    EMAIL_BODY_SCROLL_MOVEMENT_CHANGED_THRESHOLD,
    EMAIL_BODY_SCROLL_MOVEMENT_ROI_X_MAX_FRACTION,
    EMAIL_BODY_SCROLL_MOVEMENT_ROI_X_MIN_FRACTION,
    EMAIL_BODY_SCROLL_MOVEMENT_ROI_Y_MAX_FRACTION,
    EMAIL_BODY_SCROLL_MOVEMENT_ROI_Y_MIN_FRACTION,
    EMAIL_BODY_SCROLL_MOVEMENT_SUFFICIENT_THRESHOLD,
    EMAIL_BODY_SCROLL_STABILIZE_WAIT_SECONDS,
    EMAIL_READING_SCROLL_AMOUNT,
    EMAIL_READING_SCROLL_AMOUNT_BOOSTED,
    EMAIL_SECTION_TAIL_CHARS,
    MAX_EMAIL_BODY_SCROLL_ATTEMPTS,
)
from app.metrics.step_metrics import estimate_cost
from app.playbook.failure_reasons import LaunchFailureReason
from app.safety.foreground import get_foreground_window_title, is_outlook_foreground
from app.safety.screen_freshness import compute_roi_difference_score
from app.vision.models import (
    EmailHolisticAssessmentResponse,
    EmailSection,
    EmailSectionExtractionResponse,
    ReplyExpectation,
)
from app.vision.service import VisionRequest
from rnd.models.outlook_launch import CallMetrics

_email_read_logger = logging.getLogger("app.outlook.read_email.completion")
if not _email_read_logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s [EMAIL_READ] %(message)s"))
    _email_read_logger.addHandler(_handler)
    _email_read_logger.setLevel(logging.INFO)
    _email_read_logger.propagate = False

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


def _email_body_scroll_movement_roi_px(screen_width: int, screen_height: int) -> tuple[int, int, int, int]:
    """Pure geometry for the post-scroll movement guard's ROI — see
    app.config.settings.EMAIL_BODY_SCROLL_MOVEMENT_ROI_*_FRACTION for why
    y_min sits below the reading pane's own sticky subject-line banner."""
    return (
        round(EMAIL_BODY_SCROLL_MOVEMENT_ROI_X_MIN_FRACTION * screen_width),
        round(EMAIL_BODY_SCROLL_MOVEMENT_ROI_Y_MIN_FRACTION * screen_height),
        round(EMAIL_BODY_SCROLL_MOVEMENT_ROI_X_MAX_FRACTION * screen_width),
        round(EMAIL_BODY_SCROLL_MOVEMENT_ROI_Y_MAX_FRACTION * screen_height),
    )


class EmailUnderstandingSteps:
    """Mixin — expects self.result, self.vision, self.check_abort(),
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
            outcome = self.vision.analyze(VisionRequest(
                stage=STAGE_EMAIL_SECTION_EXTRACTION, screenshot_path=Path(capture.path),
                goal="Read this section of the open email", prompt_text=prompt_text,
                response_model=EmailSectionExtractionResponse,
            ))
            self.result.provider_retries += outcome.primary_retries + outcome.fallback_retries
            self.result.fallback_uses += 1 if outcome.fallback_used else 0
            if outcome.parsed is None:
                self.result.result = "ERROR"
                if outcome.schema_invalid:
                    self.result.failure_reason = LaunchFailureReason.EMAIL_UNDERSTANDING_FAILED
                    self.result.notes = f"Email-section-extraction response was not schema-valid (section {section_index})."
                else:
                    self.result.failure_reason = LaunchFailureReason.TECHNICAL_PROVIDER_ERROR
                    self.result.notes = outcome.error
                return False
            raw = outcome.parsed
            call_metrics = outcome.call_metrics

            metrics = CallMetrics(
                latency_ms=call_metrics.latency_ms, input_tokens=call_metrics.input_tokens,
                output_tokens=call_metrics.output_tokens,
                estimated_cost=estimate_cost(
                    call_metrics.provider_name, call_metrics.model, call_metrics.input_tokens, call_metrics.output_tokens,
                ),
            )
            self._accumulate(metrics, self.result.understand_email_metrics)
            self.result.email_understanding_metrics = metrics

            stripped_content = _strip_overlap(raw.extracted_visible_content, raw.overlap_text)

            # STRICT completion rule: content_complete is NEVER read
            # directly from Vision — it is always this AND/OR, computed
            # here in Python. more_content_below=false alone is never
            # enough (that was the original live bug: Vision reported it
            # false on seeing a Reply control / signature / believing
            # "enough" was read, before the true end of a long email was
            # ever seen). end_of_message_visible requires Vision to have
            # POSITIVE evidence of the actual end (see the prompt's
            # explicit forbidden-reasons list for that field) — OR
            # conversation_history_visible_below (2026-09-06 threaded-
            # conversation fix): a live run had a short, COMPLETE target
            # message correctly followed by a separate historical reply
            # card, and the reader confused "another conversation item is
            # below" with "the current message continues below," scrolling
            # repeatedly before safe-stopping as CONTENT_NOT_FULLY_READ on
            # an email that was already fully read. Seeing a structurally
            # distinct next conversation item IS positive evidence that
            # THIS message ended — the original end_of_message_visible
            # definition (blank space / exhausted scrollbar only) never
            # accounted for Outlook's threaded-conversation UI. Both
            # more_content_below and conversation_history_visible_below
            # are themselves scoped to the CURRENT TARGET message by the
            # prompt (see EmailSectionExtractionResponse's docstring) —
            # this Python logic trusts that scoping, it does not
            # re-derive it.
            reached_true_end = (not raw.more_content_below) and (
                raw.end_of_message_visible or raw.conversation_history_visible_below
            )

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
                conversation_history_visible_below=raw.conversation_history_visible_below,
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

            _email_read_logger.info(
                "EMAIL_SECTION_ANALYSIS section_index=%s current_message_continues_below=%s "
                "current_message_end_visible=%s conversation_history_visible_below=%s "
                "new_target_content_chars=%s no_new_content=%s",
                section_index, raw.more_content_below, raw.end_of_message_visible,
                raw.conversation_history_visible_below, len(stripped_content), no_progress,
            )

            if no_progress and not reached_true_end:
                self.result.content_complete = False
                self.result.result = "FAIL"
                self.result.failure_reason = LaunchFailureReason.CONTENT_NOT_FULLY_READ
                _email_read_logger.info(
                    "EMAIL_CONTENT_DECISION content_complete=False reason=unresolved_after_no_progress"
                )
                _email_read_logger.info("EMAIL_SCROLL_DECISION should_scroll=False reason=no_progress_detected")
                self.result.notes = (
                    f"Body scroll before section {section_index} produced no new target-message content, "
                    "but the current target message's true end was not confirmed (current_message_continues_"
                    "below and/or current_message_end_visible/conversation_history_visible_below evidence "
                    "still incomplete). A stuck/ineffective scroll is never silently retried into 'more "
                    "attempts' or treated as reaching the end."
                )
                return False

            if reached_true_end:
                self.result.content_complete = True
                reason = "current_message_boundary_visible" if raw.end_of_message_visible else "conversation_history_visible_below"
                _email_read_logger.info("EMAIL_CONTENT_DECISION content_complete=True reason=%s", reason)
                if not self._run_holistic_assessment(capture):
                    return False
                return self._finalize_understanding(reading_start)

            if section_index < MAX_EMAIL_BODY_SCROLL_ATTEMPTS:
                if self.check_abort("before_email_body_scroll"):
                    return False
                _email_read_logger.info(
                    "EMAIL_SCROLL_DECISION should_scroll=True reason=current_message_continues_below"
                )
                if not self._scroll_email_body_with_safety_checks(capture):
                    return False
                self.result.email_body_scroll_attempts += 1
                continue

            # Bounded limit reached without confirmed true-end evidence —
            # a genuine incomplete read, never silently treated as complete.
            self.result.content_complete = False
            self.result.result = "FAIL"
            self.result.failure_reason = LaunchFailureReason.CONTENT_NOT_FULLY_READ
            _email_read_logger.info("EMAIL_CONTENT_DECISION content_complete=False reason=continues_below")
            _email_read_logger.info("EMAIL_SCROLL_DECISION should_scroll=False reason=bounded_limit_reached")
            self.result.notes = (
                f"Current target message's true end was not confirmed (current_message_continues_below "
                f"and/or current_message_end_visible/conversation_history_visible_below) "
                f"after {MAX_EMAIL_BODY_SCROLL_ATTEMPTS} bounded section reads. Not proceeding to Reply/draft "
                "generation on an incomplete read."
            )
            return False

        # Unreachable — the loop above always returns.
        return False

    def _scroll_email_body_with_safety_checks(self, pre_scroll_capture) -> bool:
        """The ONE physical email-body-scroll path (2026-09-06 scroll-
        distance fix) — foreground/abort-checked immediately before the
        physical action, then a bounded, deterministic post-scroll
        movement guard (never a content-completion decision — see
        EMAIL_SCROLL_MOVEMENT_RESULT's own docstring note below; Vision's
        existing current_message_continues_below/no-progress logic is
        completely untouched and still runs on the NEXT loop iteration
        exactly as before).

        Uses app.config.settings.EMAIL_READING_SCROLL_AMOUNT normally;
        EMAIL_READING_SCROLL_AMOUNT_BOOSTED exactly ONCE per run, only
        immediately after a scroll whose own movement guard found the
        visual change not clearly above the measured too-small-scroll
        baseline (see settings for the exact evidence) — never two
        physical scrolls back-to-back without this same fresh-Vision-
        observation loop in between; the boost flag is consumed
        (reset to False) the instant it is used, so at most one scroll
        per run is ever boosted before being re-evaluated.

        Returns False (with self.result already set to a terminal
        FAIL/ERROR state) on foreground loss or a capture failure — NO
        physical scroll in the foreground-loss case; the scroll has
        already happened by the time a capture failure could occur, but
        no FURTHER action is taken either way."""
        title = get_foreground_window_title()
        foreground_ok = is_outlook_foreground(title)
        abort_requested = self.abort_controller.is_abort_requested()
        cursor_x = round(pre_scroll_capture.width * EMAIL_BODY_SCROLL_ANCHOR_X_FRACTION)
        cursor_y = round(pre_scroll_capture.height * EMAIL_BODY_SCROLL_ANCHOR_Y_FRACTION)
        _email_read_logger.info(
            "EMAIL_SCROLL_PRECHECK foreground_ok=%s abort_requested=%s cursor_x=%s cursor_y=%s",
            foreground_ok, abort_requested, cursor_x, cursor_y,
        )
        if abort_requested:
            return False  # check_abort() immediately before this call already set self.result
        if not foreground_ok:
            self.result.result = "FAIL"
            self.result.failure_reason = LaunchFailureReason.OUTLOOK_FOREGROUND_LOST
            self.result.notes = f"Outlook not foreground before email-body scroll; foreground was {title!r}. NO scroll performed."
            return False

        boost_pending = getattr(self, "_email_body_scroll_boost_pending", False)
        scroll_amount = EMAIL_READING_SCROLL_AMOUNT_BOOSTED if boost_pending else EMAIL_READING_SCROLL_AMOUNT
        scroll_method = "wheel_boosted" if boost_pending else "wheel"
        self._email_body_scroll_boost_pending = False  # consumed — never re-applied without a fresh too-small measurement

        _email_read_logger.info(
            "EMAIL_SCROLL_ABOUT_TO_EXECUTE scroll_method=%s scroll_amount=%s", scroll_method, scroll_amount,
        )
        scroll_email_reading_body(pre_scroll_capture.width, pre_scroll_capture.height, scroll_amount)
        _email_read_logger.info("EMAIL_SCROLL_EXECUTED")
        time.sleep(EMAIL_BODY_SCROLL_STABILIZE_WAIT_SECONDS)

        try:
            post_scroll_capture = capture_screen(DEFAULT_OUTPUT_DIR)
        except ScreenCaptureError as exc:
            self.result.result = "ERROR"
            self.result.failure_reason = LaunchFailureReason.TECHNICAL_PROVIDER_ERROR
            self.result.notes = f"Post-scroll movement-check capture failed: {exc}"
            return False

        roi = _email_body_scroll_movement_roi_px(pre_scroll_capture.width, pre_scroll_capture.height)
        difference_score = compute_roi_difference_score(pre_scroll_capture.path, post_scroll_capture.path, roi)
        changed = difference_score > EMAIL_BODY_SCROLL_MOVEMENT_CHANGED_THRESHOLD
        sufficient = difference_score >= EMAIL_BODY_SCROLL_MOVEMENT_SUFFICIENT_THRESHOLD
        attempt_number = self.result.email_body_scroll_attempts + 1
        # estimated_shift_px: NOT computed at runtime. Offline replay
        # (benchmarks/email_reading/measure_scroll_displacement.py)
        # needed a landmark specific to one frozen email's own content to
        # get a reliable pixel-shift number without periodicity aliasing
        # from repeating paragraph lines — no such generic, reliable,
        # cheap estimator exists for arbitrary live email content, so
        # this field is always logged as None; difference_score/changed
        # is the real (and sufficient) runtime signal.
        _email_read_logger.info(
            "EMAIL_SCROLL_MOVEMENT_RESULT changed=%s difference_score=%.2f estimated_shift_px=%s attempt=%s",
            changed, difference_score, None, attempt_number,
        )
        if not sufficient:
            self._email_body_scroll_boost_pending = True
        return True

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
        outcome = self.vision.analyze(VisionRequest(
            stage=STAGE_EMAIL_HOLISTIC_ASSESSMENT, screenshot_path=Path(capture.path),
            goal="Classify the email's reply expectation", prompt_text=prompt_text,
            response_model=EmailHolisticAssessmentResponse,
        ))
        self.result.provider_retries += outcome.primary_retries + outcome.fallback_retries
        self.result.fallback_uses += 1 if outcome.fallback_used else 0
        if outcome.parsed is None:
            self.result.result = "ERROR"
            if outcome.schema_invalid:
                self.result.failure_reason = LaunchFailureReason.EMAIL_UNDERSTANDING_FAILED
                self.result.notes = "Email holistic-assessment response was not schema-valid."
            else:
                self.result.failure_reason = LaunchFailureReason.TECHNICAL_PROVIDER_ERROR
                self.result.notes = outcome.error
            return False
        raw = outcome.parsed
        call_metrics = outcome.call_metrics

        metrics = CallMetrics(
            latency_ms=call_metrics.latency_ms, input_tokens=call_metrics.input_tokens,
            output_tokens=call_metrics.output_tokens,
            estimated_cost=estimate_cost(
                call_metrics.provider_name, call_metrics.model, call_metrics.input_tokens, call_metrics.output_tokens,
            ),
        )
        self._accumulate(metrics, self.result.understand_email_metrics)

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
