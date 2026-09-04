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

import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import pyautogui
from pydantic import BaseModel, Field

from app.automation.screen_capture import DEFAULT_OUTPUT_DIR, ScreenCaptureError, capture_screen
from app.config.settings import (
    MAX_SEND_VERIFICATION_ATTEMPTS,
    MOVE_DURATION_SECONDS,
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
from app.safety.validators import GroundingCheckFailure, validate_grounding
from app.vision.grounding import normalize_1000_to_pixels
from app.vision.models import SendSearchResponse
from app.vision.service import VisionRequest, VisionService
from rnd.models.click_execution import VerificationResponseV2
from rnd.models.find_open_email import StepMetrics
from rnd.models.outlook_launch import CallMetrics

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "app" / "vision" / "prompts"
SEND_SEARCH_PROMPT_PATH = PROMPTS_DIR / "send_search_v1.txt"
STATE_CHECK_PROMPT_PATH = Path(__file__).resolve().parents[2] / "rnd" / "prompts" / "state_verification_v2.txt"

# Vision-call stage identifiers (app/fallback/recovery.py structured logging).
STAGE_SEND_GROUNDING = "SEND_GROUNDING"
STAGE_SEND_VERIFICATION = "SEND_VERIFICATION"

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

    def ground_and_click_send(self) -> bool:
        """Grounds the Send control from a FRESH screenshot (never the
        one that verified the draft) and, if valid, clicks it exactly
        once. Reuses call_with_provider_retry() — provider retries repeat
        only the Vision request, never any physical action."""
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

        prompt_text = SEND_SEARCH_PROMPT_PATH.read_text(encoding="utf-8").format(
            width=capture.width, height=capture.height,
        )
        outcome = self.vision.analyze(VisionRequest(
            stage=STAGE_SEND_GROUNDING, screenshot_path=Path(capture.path),
            goal="Locate the Send control", prompt_text=prompt_text, response_model=SendSearchResponse,
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

        metrics = CallMetrics(
            latency_ms=call_metrics.latency_ms, input_tokens=call_metrics.input_tokens,
            output_tokens=call_metrics.output_tokens,
            estimated_cost=estimate_cost(
                call_metrics.provider_name, call_metrics.model, call_metrics.input_tokens, call_metrics.output_tokens,
            ),
        )
        self._accumulate(metrics, self.result.ground_send_metrics)
        self.result.send_grounding_metrics = metrics

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

        self.result.send_grounded_at = datetime.now().isoformat()
        self.result.send_grounding_ms = round((time.monotonic() - grounding_start) * 1000, 1)

        return self._validate_and_click_send(structured, capture.width, capture.height)

    def _validate_and_click_send(self, structured: SendSearchResponse, screen_width: int, screen_height: int) -> bool:
        """Validates the located Send control's bbox against the SAME
        screenshot dimensions that produced it. An invalid bbox is
        always a safe stop — never repaired, clamped, or inferred."""
        bbox = structured.bbox
        if bbox is None or len(bbox) != 4:
            self.result.result = "FAIL"
            self.result.failure_reason = LaunchFailureReason.SEND_GROUNDING_INVALID
            self.result.notes = f"Send control reported visible but had no valid bbox: {bbox!r}."
            return False

        y_min, x_min, y_max, x_max = bbox
        center_x, center_y = (x_min + x_max) / 2, (y_min + y_max) / 2

        validation = validate_grounding(
            raw_x=center_x, raw_y=center_y, box_2d=bbox, confidence=structured.confidence,
            image_width=screen_width, image_height=screen_height,
            confidence_threshold=VISION_CONFIDENCE_THRESHOLD,
            sidebar_max_x_fraction=None,  # Send is never near the left sidebar — heuristic not meaningful here
        )

        self.result.send_grounding_bbox_raw = list(bbox)
        if validation.converted_x is not None:
            px_min, py_min = normalize_1000_to_pixels(x_min, y_min, screen_width, screen_height)
            px_max, py_max = normalize_1000_to_pixels(x_max, y_max, screen_width, screen_height)
            self.result.send_grounding_bbox_pixels = [round(py_min), round(px_min), round(py_max), round(px_max)]
            self.result.send_raw_x, self.result.send_raw_y = center_x, center_y
            self.result.send_converted_x, self.result.send_converted_y = validation.converted_x, validation.converted_y
            self.result.send_coordinate_in_screen_bounds = validation.failure != GroundingCheckFailure.OUT_OF_BOUNDS

        if not validation.valid:
            self.result.result = "FAIL"
            self.result.failure_reason = LaunchFailureReason.SEND_GROUNDING_INVALID
            if validation.notes:
                self.result.notes = validation.notes
            return False

        return self._click_send()

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
        if not is_outlook_foreground(title):
            self.result.result = "FAIL"
            self.result.failure_reason = LaunchFailureReason.OUTLOOK_FOREGROUND_LOST
            self.result.notes = f"Outlook not foreground before Send move; foreground was {title!r}. NO movement, no click."
            return False

        action_start = time.monotonic()
        assert pyautogui.FAILSAFE is True
        pyautogui.moveTo(self.result.send_converted_x, self.result.send_converted_y, duration=MOVE_DURATION_SECONDS)

        if self.check_abort("before_send_click"):
            return False

        title2 = get_foreground_window_title()
        self.result.foreground_before_send_click = title2
        if not is_outlook_foreground(title2):
            self.result.result = "FAIL"
            self.result.failure_reason = LaunchFailureReason.OUTLOOK_FOREGROUND_LOST
            self.result.notes = f"Outlook not foreground before Send click; foreground was {title2!r}. NO CLICK performed."
            return False

        pyautogui.click()  # the one and only Send click for this entire run
        self.result.send_clicked_at = datetime.now().isoformat()
        self.result.send_click_executed = True
        self.result.send_execution_occurred = True
        self.result.mouse_click_count += 1
        self.result.send_click_count = 1
        self.result.send_action_ms = round((time.monotonic() - action_start) * 1000, 1)
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
