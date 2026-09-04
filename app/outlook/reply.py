"""Reply discovery, click, and editor verification — pure step logic.

Phase 5: prepare_reply_editor() is a bounded search-and-scroll loop
(mirroring app/outlook/find_email.py's Phase 3 pattern) rather than a
single-screenshot grounding call — if Reply is not visible, the email
body is scrolled (reusing app/outlook/read_email.py's Phase 4
scroll_email_body(), never a second Reply-specific scroll function) and
searched again, up to MAX_REPLY_SEARCH_SCROLL_ATTEMPTS. Grounding uses a
full-control bbox (app.vision.models.ReplySearchResponse) validated
through the same shared app.safety.validators.validate_grounding() path
as every other click target. A control identified as anything other
than exactly "Reply" (Reply All, Forward, etc.) is rejected immediately
— never accepted because its label merely contains the word "Reply".

Defined as a mixin (ReplyDiscoverySteps) sharing one result object, one
abort/accumulate machinery, and one provider/model with the read_email
and draft phases via app/outlook/draft.py::ReplyDraftSteps, which
assembles all three.
"""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import pyautogui
from pydantic import ValidationError

from app.automation.screen_capture import DEFAULT_OUTPUT_DIR, ScreenCaptureError, capture_screen
from app.automation.scrolling import scroll_email_body
from app.config.settings import (
    MAX_REPLY_EDITOR_VERIFICATION_ATTEMPTS,
    MAX_REPLY_SEARCH_SCROLL_ATTEMPTS,
    MOVE_DURATION_SECONDS,
    REPLY_EDITOR_INITIAL_WAIT_SECONDS,
    REPLY_EDITOR_RETRY_WAIT_SECONDS,
    REPLY_SEARCH_SCROLL_STABILIZE_WAIT_SECONDS,
    VISION_CONFIDENCE_THRESHOLD,
)
from app.fallback.recovery import call_with_provider_retry
from app.metrics.step_metrics import estimate_cost
from app.playbook.failure_reasons import LaunchFailureReason
from app.safety.foreground import get_foreground_window_title, is_outlook_foreground
from app.safety.validators import GroundingCheckFailure, validate_grounding
from app.vision.grounding import normalize_1000_to_pixels
from app.vision.models import ReplySearchResponse
from rnd.models.click_execution import VerificationResponseV2
from rnd.models.outlook_launch import CallMetrics
from rnd.models.reply_draft import ReplyEditorVerificationAttempt

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "app" / "vision" / "prompts"
REPLY_SEARCH_PROMPT_PATH = PROMPTS_DIR / "reply_search_v1.txt"
STATE_CHECK_PROMPT_PATH = Path(__file__).resolve().parents[2] / "rnd" / "prompts" / "state_verification_v2.txt"

REPLY_EDITOR_EXPECTED_STATE = "The reply composer/editor is visibly open and ready for text entry."

# Kept as a module-level alias so existing call sites/tests that
# reference this name keep working.
GROUNDING_CONFIDENCE_THRESHOLD = VISION_CONFIDENCE_THRESHOLD

# Vision-call stage identifiers (app/fallback/recovery.py structured logging).
STAGE_REPLY_SEARCH = "REPLY_SEARCH"
STAGE_REPLY_EDITOR_VERIFICATION = "REPLY_EDITOR_VERIFICATION"


class ReplyDiscoverySteps:
    """Mixin — expects self.result, self.provider, self.check_abort(),
    self._accumulate() from the composing class (app.outlook.draft.ReplyDraftSteps)."""

    def _check_reply_editor_open(self, image_path: Path) -> bool:
        prompt_text = STATE_CHECK_PROMPT_PATH.read_text(encoding="utf-8").format(
            action="(state check only, no action performed)", expected_state=REPLY_EDITOR_EXPECTED_STATE
        )
        outcome = call_with_provider_retry(
            lambda: self.provider.analyze_screen(image_path, "Check whether reply editor is open", prompt_text),
            stage=STAGE_REPLY_EDITOR_VERIFICATION, provider_name=self.provider.provider_name,
        )
        self.result.provider_retries += outcome.retries_used
        if outcome.result is None:
            return False
        call = outcome.result
        metrics = CallMetrics(
            latency_ms=call.latency_ms, input_tokens=call.input_tokens, output_tokens=call.output_tokens,
            estimated_cost=estimate_cost(self.provider.provider_name, call.model, call.input_tokens, call.output_tokens),
        )
        self._accumulate(metrics, self.result.ground_reply_metrics)
        if call.parsed_json is None:
            return False
        try:
            structured = VerificationResponseV2.model_validate(call.parsed_json)
        except ValidationError:
            return False
        return structured.verified

    def prepare_reply_editor(self) -> bool:
        if self.result.content_complete is not True:
            self.result.result = "FAIL"
            self.result.failure_reason = LaunchFailureReason.CONTENT_NOT_FULLY_READ
            self.result.notes = (
                "Cannot search for Reply: email content was not confirmed fully read "
                "(content_complete is not True)."
            )
            return False

        self.result.find_reply_started_at = datetime.now().isoformat()
        self._reply_find_start_monotonic: Optional[float] = time.monotonic()

        if self.check_abort("before_reply_grounding"):
            return False

        title = get_foreground_window_title()
        if not is_outlook_foreground(title):
            self.result.result = "FAIL"
            self.result.failure_reason = LaunchFailureReason.OUTLOOK_FOREGROUND_LOST
            self.result.notes = f"Outlook not foreground before reply search; foreground was {title!r}."
            return False

        try:
            capture = capture_screen(DEFAULT_OUTPUT_DIR)
        except ScreenCaptureError as exc:
            self.result.result = "ERROR"
            self.result.failure_reason = LaunchFailureReason.TECHNICAL_PROVIDER_ERROR
            self.result.notes = f"Capture failed: {exc}"
            return False

        already_open = self._check_reply_editor_open(Path(capture.path))
        self.result.reply_editor_already_open = already_open
        if already_open:
            # Avoids the RND-004 "repeated click Reply" bias — no click
            # when the editor is already open.
            self._mark_reply_found()
            return True

        for attempt_number in range(1, MAX_REPLY_SEARCH_SCROLL_ATTEMPTS + 1):
            if attempt_number > 1:
                if self.check_abort("before_reply_search"):
                    return False
                title = get_foreground_window_title()
                if not is_outlook_foreground(title):
                    self.result.result = "FAIL"
                    self.result.failure_reason = LaunchFailureReason.OUTLOOK_FOREGROUND_LOST
                    self.result.notes = f"Outlook not foreground during reply search; foreground was {title!r}."
                    return False
                try:
                    capture = capture_screen(DEFAULT_OUTPUT_DIR)
                except ScreenCaptureError as exc:
                    self.result.result = "ERROR"
                    self.result.failure_reason = LaunchFailureReason.TECHNICAL_PROVIDER_ERROR
                    self.result.notes = f"Capture failed: {exc}"
                    return False

            prompt_text = REPLY_SEARCH_PROMPT_PATH.read_text(encoding="utf-8").format(
                width=capture.width, height=capture.height,
            )
            outcome = call_with_provider_retry(
                lambda: self.provider.analyze_screen(Path(capture.path), "Locate the Reply control", prompt_text),
                stage=STAGE_REPLY_SEARCH, provider_name=self.provider.provider_name,
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
            self._accumulate(metrics, self.result.ground_reply_metrics)
            self.result.reply_grounding_metrics = metrics

            try:
                structured = ReplySearchResponse.model_validate(call.parsed_json) if call.parsed_json else None
            except ValidationError:
                structured = None
            if structured is None:
                self.result.result = "ERROR"
                self.result.failure_reason = LaunchFailureReason.TECHNICAL_PROVIDER_ERROR
                self.result.notes = "Reply-search response was not schema-valid."
                return False

            identity = structured.control_identity.strip().lower()
            identity_ok = structured.reply_visible and identity == "reply"

            if structured.reply_visible and not identity_ok:
                # A control WAS found, but it isn't Reply itself — never
                # accepted just because its label contains "reply".
                self.result.result = "FAIL"
                self.result.failure_reason = LaunchFailureReason.REPLY_TARGET_NOT_FOUND
                self.result.notes = (
                    f"Found {structured.control_identity!r} ({structured.control_type!r}) instead of Reply. "
                    "Rejected — not scrolling further for a semantic mismatch."
                )
                return False

            if identity_ok:
                return self._validate_and_click_reply(structured, capture.width, capture.height)

            # Not visible in this view.
            if attempt_number < MAX_REPLY_SEARCH_SCROLL_ATTEMPTS:
                if self.check_abort("before_reply_search_scroll"):
                    return False
                scroll_email_body(capture.width, capture.height)
                self.result.reply_search_scroll_attempts += 1
                time.sleep(REPLY_SEARCH_SCROLL_STABILIZE_WAIT_SECONDS)
                continue

            self.result.result = "FAIL"
            self.result.failure_reason = LaunchFailureReason.REPLY_NOT_FOUND
            self.result.notes = (
                f"Reply control not found after {MAX_REPLY_SEARCH_SCROLL_ATTEMPTS} search attempt(s) "
                "(including bounded email-body scrolling)."
            )
            return False

        # Unreachable — the loop above always returns.
        return False

    def _validate_and_click_reply(self, structured: ReplySearchResponse, screen_width: int, screen_height: int) -> bool:
        """Validates the located Reply control's bbox against the SAME
        screenshot dimensions that produced it (screen_width/height are
        passed in directly from the originating capture, never a stale
        value from an earlier scroll attempt). An invalid bbox is always
        a safe stop — never repaired, clamped, or inferred."""
        bbox = structured.bbox
        self.result.reply_grounding_target_raw = structured.control_identity
        self.result.reply_grounding_confidence = structured.confidence

        if bbox is None or len(bbox) != 4:
            self.result.result = "FAIL"
            self.result.failure_reason = LaunchFailureReason.REPLY_GROUNDING_INVALID
            self.result.notes = f"Reply control reported visible but had no valid bbox: {bbox!r}."
            return False

        y_min, x_min, y_max, x_max = bbox
        center_x, center_y = (x_min + x_max) / 2, (y_min + y_max) / 2

        validation = validate_grounding(
            raw_x=center_x, raw_y=center_y, box_2d=bbox, confidence=structured.confidence,
            image_width=screen_width, image_height=screen_height,
            confidence_threshold=GROUNDING_CONFIDENCE_THRESHOLD,
            sidebar_max_x_fraction=None,  # Reply is never near the left sidebar — heuristic not meaningful here
        )

        self.result.reply_grounding_bbox_raw = list(bbox)
        if validation.converted_x is not None:
            px_min, py_min = normalize_1000_to_pixels(x_min, y_min, screen_width, screen_height)
            px_max, py_max = normalize_1000_to_pixels(x_max, y_max, screen_width, screen_height)
            self.result.reply_grounding_bbox_pixels = [round(py_min), round(px_min), round(py_max), round(px_max)]
            self.result.reply_raw_x, self.result.reply_raw_y = center_x, center_y
            self.result.reply_converted_x, self.result.reply_converted_y = validation.converted_x, validation.converted_y
            self.result.reply_coordinate_in_screen_bounds = validation.failure != GroundingCheckFailure.OUT_OF_BOUNDS

        if not validation.valid:
            self.result.result = "FAIL"
            self.result.failure_reason = LaunchFailureReason.REPLY_GROUNDING_INVALID
            if validation.notes:
                self.result.notes = validation.notes
            return False

        self._mark_reply_found()
        return self._click_reply()

    def _mark_reply_found(self) -> None:
        self.result.reply_found_at = datetime.now().isoformat()
        start = getattr(self, "_reply_find_start_monotonic", None)
        if start is not None:
            self.result.reply_search_ms = round((time.monotonic() - start) * 1000, 1)

    def _click_reply(self) -> bool:
        if self.check_abort("before_reply_move"):
            return False

        title = get_foreground_window_title()
        self.result.foreground_before_reply_move = title
        if not is_outlook_foreground(title):
            self.result.result = "FAIL"
            self.result.failure_reason = LaunchFailureReason.OUTLOOK_FOREGROUND_LOST
            self.result.notes = f"Outlook not foreground before Reply move; foreground was {title!r}. NO movement, no click."
            return False

        assert pyautogui.FAILSAFE is True
        pyautogui.moveTo(self.result.reply_converted_x, self.result.reply_converted_y, duration=MOVE_DURATION_SECONDS)

        if self.check_abort("before_reply_click"):
            return False

        title2 = get_foreground_window_title()
        self.result.foreground_before_reply_click = title2
        if not is_outlook_foreground(title2):
            self.result.result = "FAIL"
            self.result.failure_reason = LaunchFailureReason.OUTLOOK_FOREGROUND_LOST
            self.result.notes = f"Outlook not foreground before Reply click; foreground was {title2!r}. NO CLICK performed."
            return False

        pyautogui.click()  # single click only
        self.result.reply_click_timestamp = datetime.now().isoformat()
        self.result.reply_click_executed = True
        self.result.mouse_click_count += 1
        self.result.reply_click_count += 1
        return True

    def verify_reply_editor(self) -> bool:
        verify_start = time.monotonic()
        for attempt_number in range(1, MAX_REPLY_EDITOR_VERIFICATION_ATTEMPTS + 1):
            if self.check_abort("before_reply_editor_verification"):
                return False

            delay = REPLY_EDITOR_INITIAL_WAIT_SECONDS if attempt_number == 1 else REPLY_EDITOR_RETRY_WAIT_SECONDS
            time.sleep(delay)

            title = get_foreground_window_title()
            if not is_outlook_foreground(title):
                self.result.result = "FAIL"
                self.result.failure_reason = LaunchFailureReason.OUTLOOK_FOREGROUND_LOST
                self.result.notes = f"Outlook lost foreground during reply-editor verification (attempt {attempt_number}); foreground was {title!r}."
                return False

            attempt = ReplyEditorVerificationAttempt(
                attempt_number=attempt_number, delay_seconds=delay, timestamp=datetime.now().isoformat()
            )
            try:
                capture = capture_screen(DEFAULT_OUTPUT_DIR)
            except ScreenCaptureError as exc:
                attempt.error = str(exc)
                self.result.reply_editor_verification_attempts.append(attempt)
                self.result.result = "ERROR"
                self.result.failure_reason = LaunchFailureReason.TECHNICAL_PROVIDER_ERROR
                self.result.notes = f"Reply-editor verification capture failed: {exc}"
                return False
            attempt.screenshot = capture.filename

            prompt_text = STATE_CHECK_PROMPT_PATH.read_text(encoding="utf-8").format(
                action="Clicked Reply (or editor was already open)", expected_state=REPLY_EDITOR_EXPECTED_STATE
            )
            outcome = call_with_provider_retry(
                lambda: self.provider.analyze_screen(Path(capture.path), "Verify the reply editor is open", prompt_text),
                stage=STAGE_REPLY_EDITOR_VERIFICATION, provider_name=self.provider.provider_name,
            )
            self.result.provider_retries += outcome.retries_used
            if outcome.result is None:
                attempt.error = outcome.error
                self.result.reply_editor_verification_attempts.append(attempt)
                self.result.result = "ERROR"
                self.result.failure_reason = LaunchFailureReason.TECHNICAL_PROVIDER_ERROR
                self.result.notes = outcome.error
                return False
            call = outcome.result

            metrics = CallMetrics(
                latency_ms=call.latency_ms, input_tokens=call.input_tokens, output_tokens=call.output_tokens,
                estimated_cost=estimate_cost(self.provider.provider_name, call.model, call.input_tokens, call.output_tokens),
            )
            self._accumulate(metrics, self.result.verify_reply_editor_metrics)
            attempt.metrics = metrics

            try:
                structured = VerificationResponseV2.model_validate(call.parsed_json) if call.parsed_json else None
            except ValidationError:
                structured = None
            if structured is None:
                attempt.error = "Response was not schema-valid."
                self.result.reply_editor_verification_attempts.append(attempt)
                self.result.result = "ERROR"
                self.result.failure_reason = LaunchFailureReason.TECHNICAL_PROVIDER_ERROR
                self.result.notes = "Reply-editor-verification response was not schema-valid."
                return False

            attempt.schema_valid = True
            attempt.verified = structured.verified
            attempt.detected_state = structured.detected_state
            attempt.confidence = structured.confidence
            attempt.reason = structured.reason
            self.result.reply_editor_verification_attempts.append(attempt)

            if structured.verified:
                self.result.reply_editor_verified = True
                self.result.reply_editor_verified_at = datetime.now().isoformat()
                self.result.reply_editor_open_ms = round((time.monotonic() - verify_start) * 1000, 1)
                return True

            # Not confirmed yet — no re-click, only another verification
            # look (fresh screenshot) if attempts remain.

        self.result.reply_editor_verified = False
        self.result.result = "FAIL"
        self.result.failure_reason = LaunchFailureReason.REPLY_EDITOR_NOT_OPEN
        self.result.notes = f"Reply editor not confirmed open after {MAX_REPLY_EDITOR_VERIFICATION_ATTEMPTS} attempts."
        return False
