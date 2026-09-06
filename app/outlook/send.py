"""Send-once + sent-state verification — pure step logic, plus the final
assembled SendFlowSteps class chaining Phases 1-7.

Phase 7: Send is the first externally-visible, irreversible action in
this POC's final runtime. Every safety property from the RND-008
predecessor (rnd/models/controlled_send.py — never modified, historical
evidence only) is retained and hardened:

  - send_click_count is an independent hard guard, checked immediately
    before the physical click AND set unconditionally to 1 (never +=)
    immediately after it — no code path can ever push it past 1.
  - Grounding reuses the same shared validate_grounding() path as every
    other click target (email row, Reply) — no clamping, no repair, no
    fixed/historical coordinates.
  - Sent verification (verify_sent) is bounded and OBSERVATION-ONLY —
    every retry is a fresh screenshot + a fresh Vision look, never a
    second click. If Send was already physically clicked once and every
    verification attempt is inconclusive, that is reported as
    SEND_VERIFICATION_UNCERTAIN, never as "not sent" — see
    LaunchFailureReason.SEND_VERIFICATION_UNCERTAIN's docstring.
  - Send approval is a PRE-RUN precondition (result.pre_run_send_approval),
    never a mid-run popup — validate_send_preconditions() only ever
    checks a flag that must already be true before this stage starts.

Defined as a mixin (SendSteps) sharing one result object, one
abort/accumulate machinery, and one provider/model with every earlier
phase — see SendFlowSteps below, which composes it onto ReplyDraftSteps
(Phases 1-6, app/outlook/draft.py — unmodified) exactly as draft.py
composed its own mixins onto FindOpenEmailSteps.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import pyautogui
from pydantic import BaseModel, Field

from app.automation.screen_capture import DEFAULT_OUTPUT_DIR, ScreenCaptureError, capture_screen
from app.config.settings import (
    CURSOR_POSITION_TOLERANCE_PX,
    MAX_SEND_GROUNDING_REFINEMENTS,
    MAX_SEND_VERIFICATION_ATTEMPTS,
    MOVE_DURATION_SECONDS,
    SEND_COMPOSER_READING_PANE_LEFT_FRACTION,
    SEND_DYNAMIC_CROP_HORIZONTAL_EXPANSION_FRACTION,
    SEND_DYNAMIC_CROP_VERTICAL_EXPANSION_FRACTION,
    SEND_VERIFICATION_INITIAL_WAIT_SECONDS,
    SEND_VERIFICATION_RETRY_WAIT_SECONDS,
    VISION_CONFIDENCE_THRESHOLD,
)
from app.metrics.step_metrics import estimate_cost
from app.outlook.draft import ReplyDraftResult, ReplyDraftSteps
from app.outlook.find_email import TARGET_EMAIL_SENDER, TARGET_EMAIL_SUBJECT, FindOpenEmailSteps
from app.playbook.failure_reasons import LaunchFailureReason
from app.safety.abort_controller import AbortController
from app.safety.foreground import get_foreground_window_title, is_outlook_foreground
from app.safety.validators import (
    GroundingCheckFailure,
    validate_grounding,
    validate_send_candidate_against_action_bar,
)
from app.vision.crop import (
    HorizontalVisionCrop,
    RectangularVisionCrop,
    create_horizontal_vision_crop,
    create_rectangular_vision_crop,
    derive_send_composer_crop_bounds_px,
)
from app.vision.grounding import normalize_1000_to_pixels
from app.vision.models import ComposerActionBarLocalizationResponse, SendSearchResponse
from app.vision.service import VisionRequest, VisionService
from rnd.models.click_execution import VerificationResponseV2
from rnd.models.find_open_email import StepMetrics
from rnd.models.outlook_launch import CallMetrics

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "app" / "vision" / "prompts"
SEND_SEARCH_PROMPT_PATH = PROMPTS_DIR / "send_search_v1.txt"
SEND_COMPOSER_LOCALIZATION_PROMPT_PATH = PROMPTS_DIR / "send_composer_localization_v1.txt"
SEND_GROUNDING_REFINE_PROMPT_PATH = PROMPTS_DIR / "send_grounding_refine_v1.txt"
STATE_CHECK_PROMPT_PATH = Path(__file__).resolve().parents[2] / "rnd" / "prompts" / "state_verification_v2.txt"

# Vision-call stage identifiers (app/fallback/recovery.py structured logging).
STAGE_SEND_COMPOSER_LOCALIZATION = "SEND_COMPOSER_LOCALIZATION"
STAGE_SEND_GROUNDING = "SEND_GROUNDING"
STAGE_SEND_GROUNDING_REFINE = "SEND_GROUNDING_REFINE"
STAGE_SEND_VERIFICATION = "SEND_VERIFICATION"

_send_grounding_logger = logging.getLogger("app.outlook.send.grounding")
if not _send_grounding_logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s [SEND_GROUNDING] %(message)s"))
    _send_grounding_logger.addHandler(_handler)
    _send_grounding_logger.setLevel(logging.INFO)
    _send_grounding_logger.propagate = False

# Outlook can represent "sent" in more than one valid way (composer
# closed and the reading pane/thread shown again, a transient sent
# confirmation banner, the message now visible in the conversation) —
# the expected-state statement intentionally allows any of these,
# rather than demanding one exact indicator that may not appear in
# every Outlook layout/version.
SENT_EXPECTED_STATE = (
    "The message has been sent: the reply composer/editor is no longer "
    "open for editing, AND at least one of the following is visible — a "
    "sent confirmation or banner, the reading pane/inbox view returned "
    "to (no longer showing an active compose box), or the just-sent "
    "message now visible in the conversation/thread."
)


class SendVerificationAttempt(BaseModel):
    """One observation-only sent-state verification attempt — same shape
    as app.outlook.reply.ReplyEditorVerificationAttempt /
    app.outlook.draft.DraftVerificationAttempt. NEVER followed by a
    second Send click, on any outcome."""

    attempt_number: int
    delay_seconds: float
    timestamp: Optional[str] = None
    screenshot: Optional[str] = None

    verified: Optional[bool] = None
    detected_state: Optional[str] = None
    confidence: Optional[float] = None
    visual_evidence: Optional[str] = None
    reason: Optional[str] = None

    metrics: CallMetrics = Field(default_factory=CallMetrics)
    schema_valid: bool = False
    error: Optional[str] = None


class SendSteps:
    """Mixin — expects self.result, self.vision, self.check_abort(),
    self._accumulate() from the composing class (SendFlowSteps below)."""

    def _draft_confirmed_ready(self) -> bool:
        """The DRAFT_READY precondition, expressed over the deterministic
        fields Phase 6 already populates — never re-derived from Vision."""
        return bool(
            self.result.text_entry_executed
            and self.result.draft_verified_at is not None
            and self.result.semantic_match is True
            and self.result.draft_verification_reply_editor_open is True
        )

    def validate_send_preconditions(self) -> bool:
        """PRE_SEND_VALIDATION. Every precondition is checked before a
        single Vision call or physical action happens; any failure here
        is a safe stop with zero Send clicks. Approval is checked FIRST
        and is never solicited here — result.pre_run_send_approval must
        already have been set (by the composing class's constructor)
        before this method is ever called."""
        if not self.result.pre_run_send_approval:
            self.result.result = "FAIL"
            self.result.failure_reason = LaunchFailureReason.SEND_NOT_APPROVED
            self.result.notes = "Send was not approved before this run started. No grounding, no click."
            return False

        if self.check_abort("before_send_preconditions"):
            return False

        if self.result.send_click_count != 0:
            self.result.result = "FAIL"
            self.result.failure_reason = LaunchFailureReason.SEND_PRECONDITION_FAILED
            self.result.notes = "send_click_count is already non-zero. Refusing to proceed toward Send."
            return False

        if not self._draft_confirmed_ready():
            self.result.result = "FAIL"
            self.result.failure_reason = LaunchFailureReason.SEND_PRECONDITION_FAILED
            self.result.notes = "Draft was not confirmed verified (DRAFT_READY). Not proceeding to Send."
            return False

        title = get_foreground_window_title()
        if not is_outlook_foreground(title):
            self.result.result = "FAIL"
            self.result.failure_reason = LaunchFailureReason.OUTLOOK_FOREGROUND_LOST
            self.result.notes = f"Outlook not foreground before Send preconditions; foreground was {title!r}."
            return False

        return True

    def _get_send_reading_pane_crop(self, screenshot_path: str, screen_width: int, screen_height: int) -> HorizontalVisionCrop:
        """Returns SEND_COMPOSER_LOCALIZATION's (Stage 1) Vision-input
        crop for screenshot_path, building and caching it on first use
        (2026-09-06 — see benchmarks/claude/experiments/
        run_dynamic_send_grounding_experiment.py for the static evidence
        this is based on). SAME generic horizontal-crop geometry already
        proven for REPLY_SEARCH (app.vision.crop.create_horizontal_
        vision_crop), but its OWN independent constant
        (SEND_COMPOSER_READING_PANE_LEFT_FRACTION) and its OWN cache —
        never shared with ReplyDiscoverySteps._reply_vision_crop_cache,
        so Send never becomes dependent on Reply's own business logic.

        Defensive fallback only (mirrors the fix for the earlier
        SendFlowSteps._reply_vision_crop_cache AttributeError — see
        SendFlowSteps.__init__ for the REAL constructor-level fix this
        guards alongside, never replaces)."""
        cache = getattr(self, "_send_reading_pane_crop_cache", None)
        if cache is None:
            cache = {}
            self._send_reading_pane_crop_cache = cache
        cached = cache.get(screenshot_path)
        if cached is not None:
            return cached
        crop = create_horizontal_vision_crop(
            Path(screenshot_path), screen_width, screen_height, SEND_COMPOSER_READING_PANE_LEFT_FRACTION,
            filename_suffix="send_composer_reading_pane_crop",
        )
        cache[screenshot_path] = crop
        _send_grounding_logger.info(
            "SEND_COMPOSER_CROP_CREATED original_size=%dx%d crop_bounds=(%d,%d,%d,%d) crop_size=%dx%d "
            "left_fraction=%s",
            screen_width, screen_height, crop.left, crop.top, crop.right, crop.bottom, crop.width, crop.height,
            SEND_COMPOSER_READING_PANE_LEFT_FRACTION,
        )
        return crop

    def _locate_send_composer_action_bar(self, capture) -> Optional[tuple[float, float, float, float]]:
        """Stage 1 — SEND_COMPOSER_LOCALIZATION. Coarse, region-only
        localization of the reply composer's action-bar row (Send +
        dropdown + Discard, as ONE region) from the deterministic
        reading-pane crop. This bbox is NEVER itself an actionable Send
        target — even if Vision's own `reason` happens to mention Send,
        only Stage 2's own SendSearchResponse.bbox may become
        actionable (see _ground_exact_send()).

        Returns the action-bar bbox remapped to FULL-SCREEN PIXELS
        (y_min, x_min, y_max, x_max), or None if Stage 1 was not usable
        — composer/action-bar not confidently visible, missing bbox, or
        confidence below the existing VISION_CONFIDENCE_THRESHOLD policy
        (the SAME threshold every other grounding stage in this project
        uses — no Send-specific threshold invented). self.result is
        already set to a terminal FAIL/ERROR state whenever None is
        returned."""
        crop = self._get_send_reading_pane_crop(capture.path, capture.width, capture.height)

        prompt_text = SEND_COMPOSER_LOCALIZATION_PROMPT_PATH.read_text(encoding="utf-8").format(
            width=crop.width, height=crop.height,
        )
        outcome = self.vision.analyze(VisionRequest(
            stage=STAGE_SEND_COMPOSER_LOCALIZATION, screenshot_path=crop.crop_path,
            goal="Locate the reply composer's action-bar region", prompt_text=prompt_text,
            response_model=ComposerActionBarLocalizationResponse,
        ))
        self.result.provider_retries += outcome.primary_retries + outcome.fallback_retries
        self.result.fallback_uses += 1 if outcome.fallback_used else 0
        if outcome.parsed is None:
            self.result.result = "ERROR"
            self.result.failure_reason = LaunchFailureReason.TECHNICAL_PROVIDER_ERROR
            self.result.notes = outcome.error
            return None
        structured = outcome.parsed
        call_metrics = outcome.call_metrics

        metrics = CallMetrics(
            latency_ms=call_metrics.latency_ms, input_tokens=call_metrics.input_tokens,
            output_tokens=call_metrics.output_tokens,
            estimated_cost=estimate_cost(
                call_metrics.provider_name, call_metrics.model, call_metrics.input_tokens, call_metrics.output_tokens,
            ),
        )
        self._accumulate(metrics, self.result.ground_send_metrics)
        self.result.send_composer_localization_metrics = metrics

        _send_grounding_logger.info(
            "SEND_COMPOSER_LOCALIZATION_RESULT composer_visible=%s action_bar_visible=%s crop_relative_bbox=%r "
            "confidence=%s provider_used=%s fallback_used=%s",
            structured.composer_visible, structured.action_bar_visible, structured.action_bar_bbox,
            structured.confidence, outcome.provider_used, outcome.fallback_used,
        )

        valid = bool(
            structured.composer_visible and structured.action_bar_visible
            and structured.action_bar_bbox is not None and len(structured.action_bar_bbox) == 4
            and structured.confidence >= VISION_CONFIDENCE_THRESHOLD
        )
        if not valid:
            self.result.result = "FAIL"
            self.result.failure_reason = LaunchFailureReason.SEND_GROUNDING_INVALID
            self.result.notes = (
                "SEND_COMPOSER_LOCALIZATION could not confidently locate the reply composer's action bar "
                f"(composer_visible={structured.composer_visible}, action_bar_visible={structured.action_bar_visible}, "
                f"confidence={structured.confidence}). NO Send grounding attempted."
            )
            return None

        _send_grounding_logger.info(
            "SEND_VISION_BBOX_CROP_RELATIVE stage=%s raw_bbox=%r",
            STAGE_SEND_COMPOSER_LOCALIZATION, structured.action_bar_bbox,
        )
        full_norm_bbox = crop.remap_bbox_to_full_screen(structured.action_bar_bbox)
        self.result.send_composer_action_bar_bbox_full_screen = [round(v, 1) for v in full_norm_bbox]
        _send_grounding_logger.info(
            "SEND_VISION_BBOX_REMAPPED stage=%s crop_bbox=%r full_screen_bbox=%r",
            STAGE_SEND_COMPOSER_LOCALIZATION, structured.action_bar_bbox, full_norm_bbox,
        )

        y_min_norm, x_min_norm, y_max_norm, x_max_norm = full_norm_bbox
        full_px_bbox = (
            y_min_norm / 1000 * capture.height, x_min_norm / 1000 * capture.width,
            y_max_norm / 1000 * capture.height, x_max_norm / 1000 * capture.width,
        )
        return full_px_bbox

    def _ground_exact_send(self, capture, action_bar_bbox_full_px: tuple[float, float, float, float]) -> Optional[SendSearchResponse]:
        """Stage 2 — SEND_GROUNDING against a crop DYNAMICALLY DERIVED
        (Policy A — see app.vision.crop.derive_send_composer_crop_bounds_px)
        from Stage 1's own action-bar bbox, never a fixed/hand-measured
        pixel box. Reuses the EXISTING, already-proven exact-Send
        semantic task (SendSearchResponse — Send != Send dropdown !=
        Discard) unchanged; only the image input changes.

        Returns the parsed SendSearchResponse with .bbox already
        remapped to FULL-SCREEN NORMALIZED coordinates (ready for the
        existing, unchanged _validate_and_click_send() flow), or None if
        this stage technically failed (self.result already set to a
        terminal ERROR state)."""
        reading_pane_crop = self._get_send_reading_pane_crop(capture.path, capture.width, capture.height)
        clamp_bounds_px = (reading_pane_crop.left, reading_pane_crop.top, reading_pane_crop.right, reading_pane_crop.bottom)

        crop_bounds_px = derive_send_composer_crop_bounds_px(
            action_bar_bbox_full_px, SEND_DYNAMIC_CROP_HORIZONTAL_EXPANSION_FRACTION,
            SEND_DYNAMIC_CROP_VERTICAL_EXPANSION_FRACTION, clamp_bounds_px,
        )
        left, top, right, bottom = crop_bounds_px
        try:
            dynamic_crop = create_rectangular_vision_crop(
                Path(capture.path), capture.width, capture.height, left, top, right, bottom,
                filename_suffix="send_dynamic_composer_crop",
            )
        except ValueError as exc:
            self.result.result = "FAIL"
            self.result.failure_reason = LaunchFailureReason.SEND_GROUNDING_INVALID
            self.result.notes = f"Dynamically-derived Send composer crop was degenerate: {exc}"
            return None

        self.result.send_dynamic_crop_bounds = list(crop_bounds_px)
        # Kept for the ONE bounded, same-crop cross-stage refinement
        # attempt (see _refine_send_grounding()) — never reused across a
        # fresh screenshot/run, always overwritten at the start of the
        # very next Stage 2 attempt.
        self._send_last_dynamic_crop = dynamic_crop
        _send_grounding_logger.info(
            "SEND_DYNAMIC_CROP_CREATED source_action_bar_bbox=%r crop_bounds=%r crop_size=%dx%d policy=A",
            [round(v, 1) for v in action_bar_bbox_full_px], crop_bounds_px, dynamic_crop.width, dynamic_crop.height,
        )

        prompt_text = SEND_SEARCH_PROMPT_PATH.read_text(encoding="utf-8").format(
            width=dynamic_crop.width, height=dynamic_crop.height,
        )
        outcome = self.vision.analyze(VisionRequest(
            stage=STAGE_SEND_GROUNDING, screenshot_path=dynamic_crop.crop_path,
            goal="Locate the Send control", prompt_text=prompt_text, response_model=SendSearchResponse,
        ))
        self.result.provider_retries += outcome.primary_retries + outcome.fallback_retries
        self.result.fallback_uses += 1 if outcome.fallback_used else 0
        if outcome.parsed is None:
            self.result.result = "ERROR"
            self.result.failure_reason = LaunchFailureReason.TECHNICAL_PROVIDER_ERROR
            self.result.notes = outcome.error
            return None
        structured = outcome.parsed
        call_metrics = outcome.call_metrics

        metrics = CallMetrics(
            latency_ms=call_metrics.latency_ms, input_tokens=call_metrics.input_tokens,
            output_tokens=call_metrics.output_tokens,
            estimated_cost=estimate_cost(
                call_metrics.provider_name, call_metrics.model, call_metrics.input_tokens, call_metrics.output_tokens,
            ),
        )
        self._accumulate(metrics, self.result.ground_send_metrics)
        self.result.send_grounding_metrics = metrics

        _send_grounding_logger.info(
            "SEND_GROUNDING_RESULT send_visible=%s control_identity=%r control_type=%r confidence=%s "
            "provider_used=%s fallback_used=%s",
            structured.send_visible, structured.control_identity, structured.control_type, structured.confidence,
            outcome.provider_used, outcome.fallback_used,
        )

        if structured.bbox is not None and len(structured.bbox) == 4:
            crop_relative_bbox = list(structured.bbox)
            _send_grounding_logger.info(
                "SEND_VISION_BBOX_CROP_RELATIVE stage=%s raw_bbox=%r", STAGE_SEND_GROUNDING, crop_relative_bbox,
            )
            structured.bbox = dynamic_crop.remap_bbox_to_full_screen(crop_relative_bbox)
            _send_grounding_logger.info(
                "SEND_VISION_BBOX_REMAPPED stage=%s crop_bbox=%r full_screen_bbox=%r",
                STAGE_SEND_GROUNDING, crop_relative_bbox, structured.bbox,
            )

        return structured

    def ground_and_click_send(self) -> bool:
        """Grounds the Send control from a FRESH screenshot (never the
        one that verified the draft) and, if valid, clicks it exactly
        once. Reuses call_with_provider_retry() — provider retries repeat
        only the Vision request, never any physical action.

        2026-09-06: replaced the old one-stage (full-screenshot ->
        exact Send) grounding with a two-stage architecture — see
        _locate_send_composer_action_bar() (Stage 1, coarse action-bar
        region) and _ground_exact_send() (Stage 2, exact Send within a
        dynamically-derived crop) — proven by a static benchmark
        (benchmarks/claude/experiments/run_dynamic_send_grounding_
        experiment.py) after full-screen/reading-pane-crop exact-Send
        bbox grounding was found to be spatially unreliable. Both stages
        run against the SAME single screenshot captured below — no
        recapture between them, no physical action has occurred yet.
        Everything from here on (semantic identity check, bbox
        validation, click-point derivation, physical click) is
        UNCHANGED from before this fix."""
        if self.check_abort("before_send_grounding"):
            return False

        title = get_foreground_window_title()
        if not is_outlook_foreground(title):
            self.result.result = "FAIL"
            self.result.failure_reason = LaunchFailureReason.OUTLOOK_FOREGROUND_LOST
            self.result.notes = f"Outlook not foreground before Send grounding; foreground was {title!r}."
            return False

        self.result.send_grounding_started_at = datetime.now().isoformat()
        grounding_start = time.monotonic()

        try:
            capture = capture_screen(DEFAULT_OUTPUT_DIR)
        except ScreenCaptureError as exc:
            self.result.result = "ERROR"
            self.result.failure_reason = LaunchFailureReason.TECHNICAL_PROVIDER_ERROR
            self.result.notes = f"Capture failed: {exc}"
            return False

        action_bar_bbox_full_px = self._locate_send_composer_action_bar(capture)
        if action_bar_bbox_full_px is None:
            return False

        structured = self._ground_exact_send(capture, action_bar_bbox_full_px)
        if structured is None:
            return False

        if not self._check_send_identity(structured):
            return False

        self.result.send_grounded_at = datetime.now().isoformat()
        self.result.send_grounding_ms = round((time.monotonic() - grounding_start) * 1000, 1)

        return self._validate_and_click_send(structured, capture.width, capture.height, action_bar_bbox_full_px)

    def _check_send_identity(self, structured: SendSearchResponse) -> bool:
        """The semantic Send-vs-dropdown/Discard/anything-else identity
        check — unchanged rule, factored out so both the INITIAL Stage-2
        candidate and a (bounded, same-crop) cross-stage refinement
        candidate go through the exact same check rather than two
        slightly-diverging copies of it."""
        self.result.send_grounding_target_raw = structured.control_identity
        self.result.send_grounding_confidence = structured.confidence

        identity = structured.control_identity.strip().lower()
        identity_ok = structured.send_visible and identity == "send"

        if not identity_ok:
            self.result.result = "FAIL"
            self.result.failure_reason = LaunchFailureReason.SEND_GROUNDING_INVALID
            if structured.send_visible:
                self.result.notes = (
                    f"Found {structured.control_identity!r} ({structured.control_type!r}) instead of Send. "
                    "Rejected — never accepted on a label merely containing 'send'."
                )
            else:
                self.result.notes = "Send control not visible in the current screenshot."
            return False
        return True

    def _validate_bbox_and_cross_stage(
        self, structured: SendSearchResponse, screen_width: int, screen_height: int,
        action_bar_bbox_full_px: tuple[float, float, float, float],
    ) -> Optional[bool]:
        """Runs the EXISTING, unchanged structural bbox validation
        (validate_grounding — degenerate/out-of-bounds/low-confidence
        checks) followed by the NEW cross-stage consistency check
        (2026-09-06 follow-up: app.safety.validators.
        validate_send_candidate_against_action_bar()) against Stage 1's
        own action-bar bbox. Used for BOTH the initial Stage-2 candidate
        and (if a bounded refinement is attempted) the refined one, so
        the two never diverge.

        Returns None if the bbox itself is structurally invalid
        (self.result already set to a terminal FAIL/ERROR state — no
        refinement is attempted for a structurally invalid bbox, only
        for one that is structurally valid but cross-stage inconsistent).
        Returns True if the candidate is fully accepted (structurally
        valid AND cross-stage consistent) — self.result.send_converted_x/y
        are ready for the click. Returns False if the candidate is
        structurally valid but cross-stage validation rejected it — NOT
        yet a terminal state; the caller decides whether to attempt the
        one bounded refinement or safe-stop."""
        bbox = structured.bbox
        if bbox is None or len(bbox) != 4:
            self.result.result = "FAIL"
            self.result.failure_reason = LaunchFailureReason.SEND_GROUNDING_INVALID
            self.result.notes = f"Send control reported visible but had no valid bbox: {bbox!r}."
            return None

        y_min, x_min, y_max, x_max = bbox
        center_x, center_y = (x_min + x_max) / 2, (y_min + y_max) / 2

        validation = validate_grounding(
            raw_x=center_x, raw_y=center_y, box_2d=bbox, confidence=structured.confidence,
            image_width=screen_width, image_height=screen_height,
            confidence_threshold=VISION_CONFIDENCE_THRESHOLD,
            sidebar_max_x_fraction=None,  # Send is never near the left sidebar — heuristic not meaningful here
        )

        self.result.send_grounding_bbox_raw = list(bbox)
        send_bbox_px: Optional[list[float]] = None
        if validation.converted_x is not None:
            px_min, py_min = normalize_1000_to_pixels(x_min, y_min, screen_width, screen_height)
            px_max, py_max = normalize_1000_to_pixels(x_max, y_max, screen_width, screen_height)
            send_bbox_px = [py_min, px_min, py_max, px_max]
            self.result.send_grounding_bbox_pixels = [round(py_min), round(px_min), round(py_max), round(px_max)]
            self.result.send_raw_x, self.result.send_raw_y = center_x, center_y
            self.result.send_converted_x, self.result.send_converted_y = validation.converted_x, validation.converted_y
            self.result.send_coordinate_in_screen_bounds = validation.failure != GroundingCheckFailure.OUT_OF_BOUNDS

        _send_grounding_logger.info(
            "SEND_GROUNDING_VALIDATION valid=%s reason=%s raw_bbox=%r converted_x=%s converted_y=%s confidence=%s",
            validation.valid, validation.failure, bbox, validation.converted_x, validation.converted_y,
            structured.confidence,
        )

        if not validation.valid:
            self.result.result = "FAIL"
            self.result.failure_reason = LaunchFailureReason.SEND_GROUNDING_INVALID
            if validation.notes:
                self.result.notes = validation.notes
            return None

        # --- Cross-stage consistency gate (2026-09-06) ---
        cross_result = validate_send_candidate_against_action_bar(list(action_bar_bbox_full_px), send_bbox_px)
        self.result.send_cross_stage_center_inside_action_bar = cross_result.center_inside_action_bar
        self.result.send_cross_stage_bbox_intersects_action_bar = cross_result.bbox_intersects_action_bar
        self.result.send_cross_stage_validation_accepted = cross_result.accepted
        _send_grounding_logger.info(
            "SEND_CROSS_STAGE_VALIDATION action_bar_bbox=%r send_bbox=%r send_center=%r "
            "center_inside_action_bar=%s bbox_intersects_action_bar=%s accepted=%s reason=%s",
            [round(v, 1) for v in action_bar_bbox_full_px], [round(v, 1) for v in send_bbox_px],
            [round(cross_result.send_center_x, 1), round(cross_result.send_center_y, 1)],
            cross_result.center_inside_action_bar, cross_result.bbox_intersects_action_bar,
            cross_result.accepted, cross_result.reason,
        )
        return cross_result.accepted

    def _validate_and_click_send(
        self, structured: SendSearchResponse, screen_width: int, screen_height: int,
        action_bar_bbox_full_px: tuple[float, float, float, float],
    ) -> bool:
        """Validates the located Send control's bbox against the SAME
        screenshot dimensions that produced it, then the cross-stage
        consistency gate. An invalid/inconsistent bbox is always a safe
        stop — never repaired, clamped, or inferred — EXCEPT that a
        cross-stage-only rejection of the INITIAL candidate gets exactly
        ONE bounded, same-screenshot, same-dynamic-crop re-ground
        attempt (MAX_SEND_GROUNDING_REFINEMENTS=1 — no loop, never a
        provider-technical-failure, never Gemini fallback triggered by
        this geometric disagreement alone) before safe-stopping."""
        accepted = self._validate_bbox_and_cross_stage(structured, screen_width, screen_height, action_bar_bbox_full_px)
        if accepted is None:
            return False
        if accepted:
            _send_grounding_logger.info(
                "SEND_CLICK_POINT click_x=%s click_y=%s", self.result.send_converted_x, self.result.send_converted_y,
            )
            return self._click_send()

        # Cross-stage validation rejected the INITIAL candidate. This is
        # a semantic/safety disagreement between the two Vision stages —
        # never a technical failure, never a reason to invoke Gemini
        # fallback on its own.
        if MAX_SEND_GROUNDING_REFINEMENTS < 1:
            self.result.result = "FAIL"
            self.result.failure_reason = LaunchFailureReason.SEND_GROUNDING_INVALID
            self.result.notes = (
                "Send candidate failed cross-stage consistency validation against the Stage-1 action-bar "
                "region. No physical action taken."
            )
            return False

        dynamic_crop = self._send_last_dynamic_crop
        if dynamic_crop is None:
            # Defensive only — _ground_exact_send() always sets this
            # before ever returning a non-None structured result, so
            # this should be unreachable; treated as a safe stop rather
            # than assumed impossible.
            self.result.result = "FAIL"
            self.result.failure_reason = LaunchFailureReason.SEND_GROUNDING_INVALID
            self.result.notes = "Cross-stage validation failed and no dynamic crop was available for refinement."
            return False

        refined_structured = self._refine_send_grounding(dynamic_crop)
        if refined_structured is None:
            return False  # technical failure — self.result already set

        if not self._check_send_identity(refined_structured):
            return False

        refined_accepted = self._validate_bbox_and_cross_stage(
            refined_structured, screen_width, screen_height, action_bar_bbox_full_px,
        )
        if refined_accepted is None:
            return False
        if not refined_accepted:
            self.result.result = "FAIL"
            self.result.failure_reason = LaunchFailureReason.SEND_GROUNDING_INVALID
            self.result.notes = (
                "Send candidate still failed cross-stage consistency validation after the one bounded, "
                "same-crop refinement attempt. Safe-stopping — no physical action taken."
            )
            return False

        _send_grounding_logger.info(
            "SEND_CLICK_POINT click_x=%s click_y=%s", self.result.send_converted_x, self.result.send_converted_y,
        )
        return self._click_send()

    def _refine_send_grounding(self, dynamic_crop: RectangularVisionCrop) -> Optional[SendSearchResponse]:
        """ONE bounded, SAME-screenshot, SAME-dynamic-crop Send
        re-grounding request (MAX_SEND_GROUNDING_REFINEMENTS=1 — no
        loop, no second attempt, no provider voting). Only ever called
        after the INITIAL Stage-2 candidate passed its own structural
        bbox validation but failed validate_send_candidate_against_
        action_bar() — mirrors app.outlook.find_email's
        MAX_EMAIL_ROW_BBOX_REFINEMENTS pattern exactly.

        Reuses dynamic_crop VERBATIM — the exact same Policy-A crop
        image Stage 2's initial attempt analyzed. No physical action has
        occurred yet and no fresh screenshot is taken; this is still the
        identical visual state. Gemini fallback here (if it engages at
        all) still follows the SAME technical-failure-only policy as
        every other Vision call — never triggered by the cross-stage
        disagreement itself, which is a semantic/safety rejection, not a
        technical one."""
        _send_grounding_logger.info(
            "SEND_GROUNDING_REFINE_REQUESTED crop_bounds=(%d,%d,%d,%d)",
            dynamic_crop.left, dynamic_crop.top, dynamic_crop.right, dynamic_crop.bottom,
        )

        prompt_text = SEND_GROUNDING_REFINE_PROMPT_PATH.read_text(encoding="utf-8").format(
            width=dynamic_crop.width, height=dynamic_crop.height,
        )
        outcome = self.vision.analyze(VisionRequest(
            stage=STAGE_SEND_GROUNDING_REFINE, screenshot_path=dynamic_crop.crop_path,
            goal="Re-inspect the cropped composer action-bar area for the exact primary Send button",
            prompt_text=prompt_text, response_model=SendSearchResponse,
        ))
        self.result.send_grounding_refinement_attempted = True
        self.result.provider_retries += outcome.primary_retries + outcome.fallback_retries
        self.result.fallback_uses += 1 if outcome.fallback_used else 0
        if outcome.parsed is None:
            self.result.result = "ERROR"
            self.result.failure_reason = LaunchFailureReason.TECHNICAL_PROVIDER_ERROR
            self.result.notes = outcome.error
            return None
        structured = outcome.parsed
        call_metrics = outcome.call_metrics

        metrics = CallMetrics(
            latency_ms=call_metrics.latency_ms, input_tokens=call_metrics.input_tokens,
            output_tokens=call_metrics.output_tokens,
            estimated_cost=estimate_cost(
                call_metrics.provider_name, call_metrics.model, call_metrics.input_tokens, call_metrics.output_tokens,
            ),
        )
        self._accumulate(metrics, self.result.ground_send_metrics)
        self.result.send_grounding_refinement_metrics = metrics

        _send_grounding_logger.info(
            "SEND_GROUNDING_REFINE_RESULT send_visible=%s control_identity=%r control_type=%r confidence=%s "
            "provider_used=%s fallback_used=%s",
            structured.send_visible, structured.control_identity, structured.control_type, structured.confidence,
            outcome.provider_used, outcome.fallback_used,
        )

        if structured.bbox is not None and len(structured.bbox) == 4:
            crop_relative_bbox = list(structured.bbox)
            _send_grounding_logger.info(
                "SEND_VISION_BBOX_CROP_RELATIVE stage=%s raw_bbox=%r", STAGE_SEND_GROUNDING_REFINE, crop_relative_bbox,
            )
            structured.bbox = dynamic_crop.remap_bbox_to_full_screen(crop_relative_bbox)
            _send_grounding_logger.info(
                "SEND_VISION_BBOX_REMAPPED stage=%s crop_bbox=%r full_screen_bbox=%r",
                STAGE_SEND_GROUNDING_REFINE, crop_relative_bbox, structured.bbox,
            )

        return structured

    def _click_send(self) -> bool:
        """The ONLY physical Send-click path in this codebase. Guarded
        independently of ordinary control flow: refuses to proceed if
        send_click_count is already non-zero, and hard-sets it to 1
        (never +=) immediately after the one click it performs. Never
        uses a keyboard shortcut (Ctrl+Enter, Alt+S) or a second/
        fallback click route."""
        if self.result.send_click_count != 0:
            self.result.result = "FAIL"
            self.result.failure_reason = LaunchFailureReason.SEND_PRECONDITION_FAILED
            self.result.notes = "send_click_count already non-zero — refusing a second Send click."
            return False

        if self.check_abort("before_send_move"):
            return False

        title = get_foreground_window_title()
        self.result.foreground_before_send_move = title
        foreground_ok = is_outlook_foreground(title)
        _send_grounding_logger.info(
            "SEND_CLICK_PRECHECK foreground_ok=%s abort_requested=%s click_x=%s click_y=%s",
            foreground_ok, self.abort_controller.is_abort_requested(),
            self.result.send_converted_x, self.result.send_converted_y,
        )
        if not foreground_ok:
            self.result.result = "FAIL"
            self.result.failure_reason = LaunchFailureReason.OUTLOOK_FOREGROUND_LOST
            self.result.notes = f"Outlook not foreground before Send move; foreground was {title!r}. NO movement, no click."
            return False

        action_start = time.monotonic()
        assert pyautogui.FAILSAFE is True
        _send_grounding_logger.info(
            "SEND_MOUSE_MOVE_ABOUT_TO_EXECUTE click_x=%s click_y=%s",
            self.result.send_converted_x, self.result.send_converted_y,
        )
        pyautogui.moveTo(self.result.send_converted_x, self.result.send_converted_y, duration=MOVE_DURATION_SECONDS)
        _send_grounding_logger.info(
            "SEND_MOUSE_MOVE_EXECUTED click_x=%s click_y=%s",
            self.result.send_converted_x, self.result.send_converted_y,
        )

        # Diagnostic-only cursor-position confirmation — a cheap read,
        # never a correction loop, never a reason to fail the click flow.
        try:
            actual_x, actual_y = pyautogui.position()
            matches = (
                abs(actual_x - self.result.send_converted_x) <= CURSOR_POSITION_TOLERANCE_PX
                and abs(actual_y - self.result.send_converted_y) <= CURSOR_POSITION_TOLERANCE_PX
            )
            _send_grounding_logger.info(
                "SEND_MOUSE_POSITION_CONFIRMED expected_x=%s expected_y=%s actual_x=%s actual_y=%s matches=%s",
                self.result.send_converted_x, self.result.send_converted_y, actual_x, actual_y, matches,
            )
        except Exception:  # noqa: BLE001 — diagnostic only, must never affect the click flow
            pass

        if self.check_abort("before_send_click"):
            return False

        title2 = get_foreground_window_title()
        self.result.foreground_before_send_click = title2
        if not is_outlook_foreground(title2):
            self.result.result = "FAIL"
            self.result.failure_reason = LaunchFailureReason.OUTLOOK_FOREGROUND_LOST
            self.result.notes = f"Outlook not foreground before Send click; foreground was {title2!r}. NO CLICK performed."
            return False

        _send_grounding_logger.info(
            "SEND_CLICK_ABOUT_TO_EXECUTE click_x=%s click_y=%s",
            self.result.send_converted_x, self.result.send_converted_y,
        )
        pyautogui.click()  # the one and only Send click for this entire run
        _send_grounding_logger.info(
            "SEND_CLICK_EXECUTED click_x=%s click_y=%s", self.result.send_converted_x, self.result.send_converted_y,
        )
        self.result.send_clicked_at = datetime.now().isoformat()
        self.result.send_click_executed = True
        self.result.send_execution_occurred = True
        self.result.mouse_click_count += 1
        self.result.send_click_count = 1
        self.result.send_action_ms = round((time.monotonic() - action_start) * 1000, 1)
        _send_grounding_logger.info("SEND_ACTION_STATE send_action_executed=%s", self.result.send_execution_occurred)
        return True

    def verify_sent(self) -> bool:
        """Bounded (MAX_SEND_VERIFICATION_ATTEMPTS), observation-only —
        every attempt is a fresh screenshot + a fresh Vision look; NONE
        of them may call click/moveTo/write/press. If every attempt is
        inconclusive, send_click_count is untouched (stays 1) and the
        failure is SEND_VERIFICATION_UNCERTAIN — physical Send already
        happened once and is never repeated or treated as "not sent"."""
        if not self.result.send_click_executed:
            raise RuntimeError("Cannot verify sent state: Send was never clicked.")

        verify_start = time.monotonic()
        for attempt_number in range(1, MAX_SEND_VERIFICATION_ATTEMPTS + 1):
            if self.check_abort("before_sent_verification"):
                # Send already executed — abort only stops further
                # observation; it can never undo or retry the click.
                return False

            delay = (
                SEND_VERIFICATION_INITIAL_WAIT_SECONDS if attempt_number == 1
                else SEND_VERIFICATION_RETRY_WAIT_SECONDS
            )
            time.sleep(delay)

            attempt = SendVerificationAttempt(
                attempt_number=attempt_number, delay_seconds=delay, timestamp=datetime.now().isoformat()
            )
            try:
                capture = capture_screen(DEFAULT_OUTPUT_DIR)
            except ScreenCaptureError as exc:
                attempt.error = str(exc)
                self.result.send_verification_attempts.append(attempt)
                self.result.result = "ERROR"
                self.result.failure_reason = LaunchFailureReason.TECHNICAL_PROVIDER_ERROR
                self.result.notes = f"Sent-state verification capture failed: {exc}"
                return False
            attempt.screenshot = capture.filename

            prompt_text = STATE_CHECK_PROMPT_PATH.read_text(encoding="utf-8").format(
                action="Clicked Send exactly once", expected_state=SENT_EXPECTED_STATE,
            )
            outcome = self.vision.analyze(VisionRequest(
                stage=STAGE_SEND_VERIFICATION, screenshot_path=Path(capture.path),
                goal="Verify the message was sent", prompt_text=prompt_text, response_model=VerificationResponseV2,
            ))
            self.result.provider_retries += outcome.primary_retries + outcome.fallback_retries
            self.result.fallback_uses += 1 if outcome.fallback_used else 0
            if outcome.parsed is None:
                attempt.error = outcome.error
                self.result.send_verification_attempts.append(attempt)
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
            self._accumulate(metrics, self.result.verify_send_metrics)
            attempt.metrics = metrics

            attempt.schema_valid = True
            attempt.verified = structured.verified
            attempt.detected_state = structured.detected_state
            attempt.confidence = structured.confidence
            attempt.visual_evidence = structured.visual_evidence
            attempt.reason = structured.reason
            self.result.send_verification_attempts.append(attempt)

            if structured.verified:
                self.result.sent_verified = True
                self.result.send_verified_at = datetime.now().isoformat()
                self.result.send_verification_ms = round((time.monotonic() - verify_start) * 1000, 1)
                self.result.result = "PASS"
                return True

            # Not confirmed yet — observation only; no re-click, ever,
            # regardless of how many attempts remain.

        # Exhausted every attempt without confirmation. Send was still
        # physically executed exactly once — this is genuine uncertainty,
        # never "not sent", and never authorizes a resend.
        self.result.sent_verified = False
        self.result.send_verification_ms = round((time.monotonic() - verify_start) * 1000, 1)
        self.result.result = "FAIL"
        self.result.failure_reason = LaunchFailureReason.SEND_VERIFICATION_UNCERTAIN
        self.result.notes = (
            f"Send was clicked exactly once, but sent state could not be confirmed after "
            f"{MAX_SEND_VERIFICATION_ATTEMPTS} observation-only attempt(s). Not resending — physical "
            "Send execution is never repeated regardless of verification outcome."
        )
        return False


class SendResult(ReplyDraftResult):
    """Phase 7 additions, as a subclass rather than editing
    rnd/models/controlled_send.py directly — that file is historical
    R&D evidence and is never modified. send_click_count/mouse_click_count
    are inherited unchanged (already present, default 0, all the way
    from rnd/models/outlook_launch.py) — reused, never duplicated."""

    # --- Preconditions / approval ---
    pre_run_send_approval: bool = False

    # --- Send grounding ---
    send_grounding_started_at: Optional[str] = None
    send_grounded_at: Optional[str] = None
    send_grounding_ms: Optional[float] = None
    send_grounding_target_raw: Optional[str] = None
    send_grounding_confidence: Optional[float] = None
    send_grounding_bbox_raw: Optional[list[float]] = None
    send_grounding_bbox_pixels: Optional[list[float]] = None
    send_raw_x: Optional[float] = None
    send_raw_y: Optional[float] = None
    send_converted_x: Optional[int] = None
    send_converted_y: Optional[int] = None
    send_coordinate_in_screen_bounds: Optional[bool] = None
    send_grounding_metrics: CallMetrics = CallMetrics()
    ground_send_metrics: StepMetrics = StepMetrics()

    # --- SEND_COMPOSER_LOCALIZATION (Stage 1 of the two-stage Send-
    # grounding architecture, 2026-09-06) — see app/outlook/send.py::
    # _locate_send_composer_action_bar(). Never itself an actionable
    # Send target — only used to derive Stage 2's dynamic crop bounds.
    send_composer_localization_metrics: CallMetrics = CallMetrics()
    send_composer_action_bar_bbox_full_screen: Optional[list[float]] = None
    send_dynamic_crop_bounds: Optional[list[int]] = None

    # --- Cross-stage consistency gate (2026-09-06 follow-up) — see
    # app.safety.validators.validate_send_candidate_against_action_bar()
    # and app/outlook/send.py::_validate_bbox_and_cross_stage(). Reflects
    # whichever candidate (initial, or refined if a refinement was
    # attempted) was actually used for the accept/reject decision.
    send_cross_stage_center_inside_action_bar: Optional[bool] = None
    send_cross_stage_bbox_intersects_action_bar: Optional[bool] = None
    send_cross_stage_validation_accepted: Optional[bool] = None
    send_grounding_refinement_attempted: bool = False
    send_grounding_refinement_metrics: CallMetrics = CallMetrics()

    # --- Send execution ---
    foreground_before_send_move: Optional[str] = None
    foreground_before_send_click: Optional[str] = None
    send_clicked_at: Optional[str] = None
    send_click_executed: bool = False
    send_action_ms: Optional[float] = None
    send_execution_occurred: bool = False

    # --- Sent verification ---
    send_verification_attempts: list[SendVerificationAttempt] = Field(default_factory=list)
    send_verified_at: Optional[str] = None
    send_verification_ms: Optional[float] = None
    sent_verified: Optional[bool] = None
    verify_send_metrics: StepMetrics = StepMetrics()


class SendFlowSteps(SendSteps, ReplyDraftSteps):
    """Final assembled class for the complete Phases 1-7 run. Mirrors
    ReplyDraftSteps.__init__ exactly (app/outlook/draft.py) — same
    session/provider/find_open wiring — substituting SendResult for
    ReplyDraftResult so this stage's own new fields exist on the result,
    and recording send approval as a PRE-RUN constructor argument (never
    solicited mid-run)."""

    def __init__(
        self, abort_controller: AbortController, vision: VisionService, model: str,
        send_approval_granted: bool,
        target_sender: str = TARGET_EMAIL_SENDER, target_subject: str = TARGET_EMAIL_SUBJECT,
    ) -> None:
        """target_sender/target_subject default to the module-level dev/
        test constants (app/outlook/find_email.py) ONLY as a fallback for
        callers that don't supply their own — real UI-driven runs
        (app/workers/send_worker.py::SendWorker, wired from
        app/controllers/automation_controller.py) always pass the actual
        user-entered values explicitly, so the defaults are never silently
        used there."""
        self.abort_controller = abort_controller
        self.vision = vision
        self.model = model
        self.result = SendResult()
        self.find_open = FindOpenEmailSteps(
            abort_controller, vision, model, target_sender=target_sender, target_subject=target_subject,
        )
        self.result.target_subject = self.find_open.result.target_subject
        self.result.target_sender = self.find_open.result.target_sender
        self._session_start_monotonic: Optional[float] = None
        self.result.pre_run_send_approval = send_approval_granted
        # REPLY_SEARCH Vision-input crop cache (2026-09-06) — see
        # ReplyDiscoverySteps._get_reply_vision_crop() in app/outlook/
        # reply.py. SendFlowSteps does NOT call ReplyDraftSteps.__init__()
        # (this constructor duplicates its field assignments instead of a
        # cooperative super().__init__() — see this class's own docstring,
        # "Mirrors ReplyDraftSteps.__init__ exactly"), so this field must
        # be initialized here too, independently — a live run crashed with
        # AttributeError('SendFlowSteps' object has no attribute
        # '_reply_vision_crop_cache') because it was only ever added to
        # ReplyDraftSteps.__init__, never here.
        self._reply_vision_crop_cache: dict[str, "HorizontalVisionCrop"] = {}
        # Bounded email-body scroll-boost flag (2026-09-06 long-email
        # scroll-distance fix) — see app.outlook.read_email.
        # EmailUnderstandingSteps._scroll_email_body_with_safety_checks().
        # SendFlowSteps does not call ReplyDraftSteps.__init__() (same
        # non-cooperative-constructor pattern as every other field on
        # this class), so this must be initialized here too.
        self._email_body_scroll_boost_pending: bool = False
        # SEND_COMPOSER_LOCALIZATION Vision-input crop cache (2026-09-06)
        # — see SendSteps._get_send_reading_pane_crop() above. Initialized
        # directly here (the real SendFlowSteps constructor path
        # app/workers/send_worker.py actually uses), not only via a
        # defensive getattr guard — same lesson as the
        # _reply_vision_crop_cache fix just above: a NEW field must be
        # initialized through the ACTUAL constructor a real run
        # instantiates, never assumed to arrive via some other class's
        # __init__ this one does not call.
        self._send_reading_pane_crop_cache: dict[str, "HorizontalVisionCrop"] = {}
        # Holds Stage 2's dynamic crop for the duration of one
        # ground_and_click_send() call, so the bounded cross-stage
        # refinement attempt (see _refine_send_grounding()) can reuse the
        # EXACT same crop image rather than re-deriving/re-cropping —
        # same real-constructor-initialization lesson as the two cache
        # fields above.
        self._send_last_dynamic_crop: Optional["RectangularVisionCrop"] = None
