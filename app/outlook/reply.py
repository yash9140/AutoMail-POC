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

import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import pyautogui

from app.automation.screen_capture import DEFAULT_OUTPUT_DIR, ScreenCaptureError, capture_screen
from app.automation.scrolling import scroll_email_body
from app.config.settings import (
    CURSOR_POSITION_TOLERANCE_PX,
    FOREGROUND_RECHECK_MAX_ATTEMPTS,
    FOREGROUND_RECHECK_WAIT_SECONDS,
    MAX_REPLY_EDITOR_VERIFICATION_ATTEMPTS,
    MAX_REPLY_SEARCH_SCROLL_ATTEMPTS,
    MOVE_DURATION_SECONDS,
    REPLY_EDITOR_INITIAL_WAIT_SECONDS,
    REPLY_EDITOR_RETRY_WAIT_SECONDS,
    REPLY_SEARCH_SCROLL_STABILIZE_WAIT_SECONDS,
    REPLY_VISION_CROP_LEFT_FRACTION,
    VISION_CONFIDENCE_THRESHOLD,
)
from app.metrics.step_metrics import estimate_cost
from app.playbook.failure_reasons import LaunchFailureReason
from app.safety.foreground import (
    confirm_outlook_foreground_with_recheck,
    get_foreground_window_title,
    is_outlook_foreground,
)
from app.safety.validators import GroundingCheckFailure, validate_grounding
from app.vision.crop import HorizontalVisionCrop, create_reply_vision_crop
from app.vision.grounding import normalize_1000_to_pixels
from app.vision.models import ReplySearchResponse
from app.vision.service import VisionRequest
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

_reply_grounding_logger = logging.getLogger("app.outlook.reply.grounding")
if not _reply_grounding_logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s [REPLY_GROUNDING] %(message)s"))
    _reply_grounding_logger.addHandler(_handler)
    _reply_grounding_logger.setLevel(logging.INFO)
    _reply_grounding_logger.propagate = False


class ReplyDiscoverySteps:
    """Mixin — expects self.result, self.vision, self.check_abort(),
    self._accumulate(), self._reply_vision_crop_cache from the composing
    class (app.outlook.draft.ReplyDraftSteps)."""

    def _check_reply_editor_open(self, image_path: Path) -> bool:
        prompt_text = STATE_CHECK_PROMPT_PATH.read_text(encoding="utf-8").format(
            action="(state check only, no action performed)", expected_state=REPLY_EDITOR_EXPECTED_STATE
        )
        outcome = self.vision.analyze(VisionRequest(
            stage=STAGE_REPLY_EDITOR_VERIFICATION, screenshot_path=image_path,
            goal="Check whether reply editor is open", prompt_text=prompt_text,
            response_model=VerificationResponseV2,
        ))
        self.result.provider_retries += outcome.primary_retries + outcome.fallback_retries
        self.result.fallback_uses += 1 if outcome.fallback_used else 0
        if outcome.parsed is None:
            return False
        call_metrics = outcome.call_metrics
        metrics = CallMetrics(
            latency_ms=call_metrics.latency_ms, input_tokens=call_metrics.input_tokens,
            output_tokens=call_metrics.output_tokens,
            estimated_cost=estimate_cost(
                call_metrics.provider_name, call_metrics.model, call_metrics.input_tokens, call_metrics.output_tokens,
            ),
        )
        self._accumulate(metrics, self.result.ground_reply_metrics)
        return outcome.parsed.verified

    def _get_reply_vision_crop(self, screenshot_path: str, screen_width: int, screen_height: int) -> HorizontalVisionCrop:
        """Returns the REPLY_SEARCH Vision-input crop for screenshot_path,
        building and caching it on first use (2026-09-06 — see
        benchmarks/claude/experiments/run_reply_grounding_experiment.py +
        run_reply_crop_calibration.py for the static evidence this is
        based on: full-screen Reply-control bbox grounding was stably
        wrong — landing in unrelated blank space, not merely confusing
        Reply with Forward — while a full-height, right-side crop
        starting at REPLY_VISION_CROP_LEFT_FRACTION was stably correct).

        Cached per screenshot path — a NEW screenshot (a rescan attempt,
        or any future re-ground) gets its own freshly-built crop, since
        it is keyed by that screenshot's own path. A SEPARATE cache from
        FindOpenEmailSteps' message-list crop cache — never shared,
        never confused, even though both use the same underlying
        app.vision.crop horizontal-crop pattern.

        Defensive fallback only (2026-09-06 — see the live
        AttributeError this guards against: SendFlowSteps.__init__ did
        not initialize this cache, since it duplicates ReplyDraftSteps.
        __init__'s field assignments rather than calling it cooperatively
        — now fixed at the source in both constructors). This getattr
        guard is NOT a substitute for that constructor fix; it only
        protects against a FUTURE composing class making the same
        mistake, so a missing cache degrades to "no caching this call"
        rather than a crash."""
        cache = getattr(self, "_reply_vision_crop_cache", None)
        if cache is None:
            cache = {}
            self._reply_vision_crop_cache = cache
        cached = cache.get(screenshot_path)
        if cached is not None:
            return cached
        crop = create_reply_vision_crop(
            Path(screenshot_path), screen_width, screen_height, REPLY_VISION_CROP_LEFT_FRACTION,
        )
        cache[screenshot_path] = crop
        _reply_grounding_logger.info(
            "REPLY_VISION_CROP_CREATED original_size=%dx%d crop_bounds=(%d,%d,%d,%d) crop_size=%dx%d "
            "left_fraction=%s",
            screen_width, screen_height, crop.left, crop.top, crop.right, crop.bottom, crop.width, crop.height,
            REPLY_VISION_CROP_LEFT_FRACTION,
        )
        return crop

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

            crop = self._get_reply_vision_crop(capture.path, capture.width, capture.height)
            prompt_text = REPLY_SEARCH_PROMPT_PATH.read_text(encoding="utf-8").format(
                width=crop.width, height=crop.height,
            )
            outcome = self.vision.analyze(VisionRequest(
                stage=STAGE_REPLY_SEARCH, screenshot_path=crop.crop_path,
                goal="Locate the Reply control", prompt_text=prompt_text, response_model=ReplySearchResponse,
            ))
            self.result.provider_retries += outcome.primary_retries + outcome.fallback_retries
            self.result.fallback_uses += 1 if outcome.fallback_used else 0
            if outcome.parsed is None:
                self.result.result = "ERROR"
                self.result.failure_reason = LaunchFailureReason.TECHNICAL_PROVIDER_ERROR
                self.result.notes = outcome.error
                return False
            structured = outcome.parsed
            call_metrics = outcome.call_metrics

            # Deterministic Python remap: the bbox is returned relative to
            # the crop image Vision was actually shown — never trusted as
            # full-screen-relative from here on. Vision is never asked to
            # do this conversion itself.
            if structured.bbox is not None and len(structured.bbox) == 4:
                crop_relative_bbox = list(structured.bbox)
                _reply_grounding_logger.info(
                    "REPLY_VISION_BBOX_CROP_RELATIVE stage=%s raw_bbox=%r", STAGE_REPLY_SEARCH, crop_relative_bbox,
                )
                structured.bbox = crop.remap_bbox_to_full_screen(crop_relative_bbox)
                _reply_grounding_logger.info(
                    "REPLY_VISION_BBOX_REMAPPED stage=%s crop_bbox=%r full_screen_bbox=%r",
                    STAGE_REPLY_SEARCH, crop_relative_bbox, structured.bbox,
                )

            metrics = CallMetrics(
                latency_ms=call_metrics.latency_ms, input_tokens=call_metrics.input_tokens,
                output_tokens=call_metrics.output_tokens,
                estimated_cost=estimate_cost(
                    call_metrics.provider_name, call_metrics.model, call_metrics.input_tokens, call_metrics.output_tokens,
                ),
            )
            self._accumulate(metrics, self.result.ground_reply_metrics)
            self.result.reply_grounding_metrics = metrics

            _reply_grounding_logger.info(
                "REPLY_SEARCH_RESULT reply_visible=%s control_identity=%r control_type=%r bbox=%r "
                "confidence=%s provider_used=%s",
                structured.reply_visible, structured.control_identity, structured.control_type,
                structured.bbox, structured.confidence, outcome.provider_used,
            )

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

        _reply_grounding_logger.info(
            "REPLY_GROUNDING_VALIDATION valid=%s reason=%s raw_bbox=%r converted_x=%s converted_y=%s "
            "confidence=%s",
            validation.valid, validation.failure, bbox, validation.converted_x, validation.converted_y,
            structured.confidence,
        )

        if not validation.valid:
            self.result.result = "FAIL"
            self.result.failure_reason = LaunchFailureReason.REPLY_GROUNDING_INVALID
            if validation.notes:
                self.result.notes = validation.notes
            return False

        _reply_grounding_logger.info(
            "REPLY_CLICK_POINT click_x=%s click_y=%s", self.result.reply_converted_x, self.result.reply_converted_y,
        )

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

        # Bounded foreground re-check (2026-09-06 live fix) — see
        # app.safety.foreground.confirm_outlook_foreground_with_recheck()'s
        # docstring: a single instantaneous foreground read can catch a
        # transient shell overlay (e.g. the Alt-Tab task-switcher) rather
        # than a genuine loss of Outlook focus. Zero added delay when
        # Outlook is already foreground; only a few short, bounded
        # retries otherwise. Still an unconditional safe-stop (no
        # movement, no click) if Outlook never reappears.
        title = confirm_outlook_foreground_with_recheck(FOREGROUND_RECHECK_MAX_ATTEMPTS, FOREGROUND_RECHECK_WAIT_SECONDS)
        self.result.foreground_before_reply_move = title
        _reply_grounding_logger.info(
            "REPLY_CLICK_PRECHECK foreground_ok=%s abort_requested=%s click_x=%s click_y=%s",
            is_outlook_foreground(title), self.abort_controller.is_abort_requested(),
            self.result.reply_converted_x, self.result.reply_converted_y,
        )
        if not is_outlook_foreground(title):
            self.result.result = "FAIL"
            self.result.failure_reason = LaunchFailureReason.OUTLOOK_FOREGROUND_LOST
            self.result.notes = f"Outlook not foreground before Reply move; foreground was {title!r}. NO movement, no click."
            return False

        assert pyautogui.FAILSAFE is True
        pyautogui.moveTo(self.result.reply_converted_x, self.result.reply_converted_y, duration=MOVE_DURATION_SECONDS)
        _reply_grounding_logger.info(
            "REPLY_MOUSE_MOVE_EXECUTED click_x=%s click_y=%s",
            self.result.reply_converted_x, self.result.reply_converted_y,
        )

        # Diagnostic-only cursor-position confirmation — a cheap read,
        # never a correction loop, never a reason to fail on a tiny
        # rounding difference (see CURSOR_POSITION_TOLERANCE_PX).
        try:
            actual_x, actual_y = pyautogui.position()
            matches = (
                abs(actual_x - self.result.reply_converted_x) <= CURSOR_POSITION_TOLERANCE_PX
                and abs(actual_y - self.result.reply_converted_y) <= CURSOR_POSITION_TOLERANCE_PX
            )
            _reply_grounding_logger.info(
                "REPLY_MOUSE_POSITION_CONFIRMED expected_x=%s expected_y=%s actual_x=%s actual_y=%s matches=%s",
                self.result.reply_converted_x, self.result.reply_converted_y, actual_x, actual_y, matches,
            )
        except Exception:  # noqa: BLE001 — diagnostic only, must never affect the click flow
            pass

        if self.check_abort("before_reply_click"):
            return False

        title2 = confirm_outlook_foreground_with_recheck(FOREGROUND_RECHECK_MAX_ATTEMPTS, FOREGROUND_RECHECK_WAIT_SECONDS)
        self.result.foreground_before_reply_click = title2
        if not is_outlook_foreground(title2):
            self.result.result = "FAIL"
            self.result.failure_reason = LaunchFailureReason.OUTLOOK_FOREGROUND_LOST
            self.result.notes = f"Outlook not foreground before Reply click; foreground was {title2!r}. NO CLICK performed."
            return False

        pyautogui.click()  # single click only
        _reply_grounding_logger.info(
            "REPLY_CLICK_EXECUTED click_x=%s click_y=%s", self.result.reply_converted_x, self.result.reply_converted_y,
        )
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
            outcome = self.vision.analyze(VisionRequest(
                stage=STAGE_REPLY_EDITOR_VERIFICATION, screenshot_path=Path(capture.path),
                goal="Verify the reply editor is open", prompt_text=prompt_text,
                response_model=VerificationResponseV2,
            ))
            self.result.provider_retries += outcome.primary_retries + outcome.fallback_retries
            self.result.fallback_uses += 1 if outcome.fallback_used else 0
            if outcome.parsed is None:
                attempt.error = outcome.error
                self.result.reply_editor_verification_attempts.append(attempt)
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
            self._accumulate(metrics, self.result.verify_reply_editor_metrics)
            attempt.metrics = metrics

            attempt.schema_valid = True
            attempt.verified = structured.verified
            attempt.detected_state = structured.detected_state
            attempt.confidence = structured.confidence
            attempt.reason = structured.reason
            self.result.reply_editor_verification_attempts.append(attempt)

            _reply_grounding_logger.info(
                "REPLY_EDITOR_VERIFICATION_RESULT observation_attempt=%s verified=%s detected_state=%r "
                "confidence=%s provider_used=%s",
                attempt_number, structured.verified, structured.detected_state, structured.confidence,
                outcome.provider_used,
            )

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
