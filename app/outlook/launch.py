"""Outlook launch — pure step logic, no Qt dependency.

Moved from app/playbook/outlook_launch_steps.py (RND-009B) for the
final POC runtime — behavior-preserving move: same method bodies, same
constants (now sourced from app/config/settings.py instead of local
module-level values), same result model (rnd.models.outlook_launch
kept as-is per the "no new behavior" scope of this phase; a leaner
final-POC result schema is a later cleanup pass once every phase that
touches it has landed).

Architecture rule enforced throughout: the playbook (caller) decides
what happens next; Vision only reports what it sees; PyAutoGUI only
executes the one action it's told to; every abort checkpoint is checked
via AbortController before the corresponding action, never after.
"""

from __future__ import annotations

import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import pyautogui

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from app.automation.screen_capture import (  # noqa: E402
    DEFAULT_OUTPUT_DIR,
    ScreenCaptureError,
    capture_screen,
)
from app.config.settings import (  # noqa: E402
    MAX_READINESS_ATTEMPTS,
    MAXIMIZE_STABILIZE_WAIT_SECONDS,
    OUTLOOK_LAUNCH_INITIAL_WAIT_SECONDS,
    OUTLOOK_LAUNCH_POLL_INTERVAL_SECONDS,
    OUTLOOK_LAUNCH_TIMEOUT_SECONDS,
    READINESS_INITIAL_WAIT_SECONDS,
    READINESS_RETRY_WAIT_SECONDS,
    SEARCH_TYPE_STABILIZE_SECONDS,
    TYPE_INTERVAL_SECONDS,
    VISION_CONFIDENCE_THRESHOLD,
    WINDOWS_KEY_STABILIZE_SECONDS,
)
from app.metrics.step_metrics import estimate_cost  # noqa: E402
from app.playbook.failure_reasons import LaunchFailureReason  # noqa: E402
from app.safety.abort_controller import AbortController  # noqa: E402
from app.safety.foreground import (  # noqa: E402
    get_foreground_hwnd,
    get_foreground_window_title,
    is_maximized,
    is_outlook_foreground,
    is_search_state_foreground,
    maximize,
)
from app.vision.models import OutlookSearchGroundingResponse, OutlookSearchRefineResponse  # noqa: E402
from app.vision.service import VisionRequest, VisionService, get_vision_service  # noqa: E402
from rnd.models.outlook_launch import (  # noqa: E402
    CallMetrics,
    OutlookLaunchVerificationResponse,
    OutlookReadinessAttempt,
    RND009BResult,
)

PROMPTS_DIR = PROJECT_ROOT / "rnd" / "prompts"
# OUTLOOK_SEARCH — semantic verification ONLY (2026-09-06 keyboard-
# activation fix; see ground_search_result()'s docstring). Used
# identically for both providers (primary or fallback). bbox is still
# in the response schema/logged for diagnostics but never drives a
# physical action for this stage anymore — see activate_outlook_result().
# The historical Gemini-only loose-point prompt (rnd/prompts/
# windows_search_grounding_v1.txt) and the historical bbox-refine prompt
# (app/vision/prompts/outlook_search_grounding_refine_v1.txt) are left
# in place, frozen, simply no longer referenced here.
WINDOWS_SEARCH_GROUNDING_PROMPT_PATH = (
    PROJECT_ROOT / "app" / "vision" / "prompts" / "outlook_search_grounding_v1.txt"
)
OUTLOOK_LAUNCH_VERIFICATION_PROMPT_PATH = PROMPTS_DIR / "outlook_launch_verification_v1.txt"

# Kept as a module-level alias (same value as VISION_CONFIDENCE_THRESHOLD)
# so existing call sites/tests that reference this name keep working.
GROUNDING_CONFIDENCE_THRESHOLD = VISION_CONFIDENCE_THRESHOLD

# Vision-call stage identifiers (app/fallback/recovery.py structured
# logging) for this module's calls.
STAGE_OUTLOOK_SEARCH = "OUTLOOK_SEARCH"
STAGE_OUTLOOK_READINESS = "OUTLOOK_READINESS"

_grounding_logger = logging.getLogger("app.outlook.launch.grounding")
if not _grounding_logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s [GROUNDING] %(message)s"))
    _grounding_logger.addHandler(_handler)
    _grounding_logger.setLevel(logging.INFO)
    _grounding_logger.propagate = False


def _provider() -> tuple[VisionService, str]:
    """Kept as a thin alias to app.vision.service.get_vision_service()
    (2026-09-05, Claude-primary/Gemini-fallback architecture) — the name
    several existing call sites/tests import. Returns the VisionService
    composed from whichever provider(s) PRIMARY_VISION_PROVIDER/
    FALLBACK_VISION_PROVIDER (or the legacy AI_PROVIDER, for backward
    compatibility) select. Every OUTLOOK_SEARCH/OUTLOOK_READINESS call in
    this module goes through this ONE VisionService — there is no
    provider-name branch anywhere in this module; ground_search_result()
    uses the exact same semantic-verification contract regardless of
    which provider (primary or fallback) actually answers."""
    return get_vision_service()


class OutlookLaunchResult(RND009BResult):
    """Phase 2 additions, as a subclass rather than editing
    rnd/models/outlook_launch.py directly — that file is historical R&D
    evidence and is never modified. `outlook_window_detected_at`/`_ms`
    are intentionally NOT duplicated here: they're already covered by
    the inherited `outlook_detected_timestamp`/`outlook_launch_duration_ms`
    fields, populated by poll_for_outlook_foreground().

    `grounding` (inherited from RND009BResult, the historical Gemini-only
    loose-point Optional[WindowsSearchGroundingResponse]) is never
    populated by the live dispatch as of the 2026-09-05 provider-
    architecture unification — kept only for backward compatibility.
    `search_grounding` (Optional[OutlookSearchGroundingResponse], bbox-
    based) is the one OUTLOOK_SEARCH result shape used for BOTH
    providers, populated by ground_search_result(). As of the 2026-09-06
    keyboard-activation fix its bbox is diagnostic-only — see that
    method's docstring — so `converted_x`/`converted_y`/
    `search_grounding_bbox_pixels`/`raw_x`/`raw_y` below are likewise
    never populated anymore; they remain declared for backward
    compatibility only."""

    outlook_launch_started_at: Optional[str] = None

    maximize_required: Optional[bool] = None
    maximize_executed: bool = False
    maximize_duration_ms: Optional[float] = None
    post_maximize_screenshot: Optional[str] = None

    outlook_ready_at: Optional[str] = None
    outlook_ready_ms: Optional[float] = None

    provider_retries: int = 0
    # Count of Vision calls in this run resolved by the FALLBACK provider
    # rather than the primary (2026-09-05 Claude-primary/Gemini-fallback
    # architecture) — 0 whenever no fallback is configured or the primary
    # never technically failed.
    fallback_uses: int = 0

    # --- Live coordinate-contract fix (2026-09-02) ---
    search_grounding: Optional[OutlookSearchGroundingResponse] = None
    search_grounding_bbox_raw: Optional[list[float]] = None
    search_grounding_bbox_pixels: Optional[list[float]] = None
    pyautogui_width: Optional[int] = None
    pyautogui_height: Optional[int] = None
    dimensions_match: Optional[bool] = None
    grounding_debug_artifact: Optional[str] = None

    # --- Bbox-scoping hardening (2026-09-03) ---
    search_grounding_refine: Optional[OutlookSearchRefineResponse] = None
    refine_pass_used: bool = False


class OutlookLaunchSteps:
    def __init__(self, abort_controller: AbortController, vision: VisionService, model: str) -> None:
        self.abort_controller = abort_controller
        self.vision = vision
        self.model = model
        self.result = OutlookLaunchResult()
        self._launch_start_monotonic: Optional[float] = None
        self._search_capture_monotonic: Optional[float] = None

    def _accumulate(self, metrics: CallMetrics) -> None:
        self.result.total_vision_calls += 1
        self.result.total_input_tokens += metrics.input_tokens or 0
        self.result.total_output_tokens += metrics.output_tokens or 0
        self.result.total_estimated_cost = round(self.result.total_estimated_cost + (metrics.estimated_cost or 0), 6)
        self.result.total_latency_ms = round(self.result.total_latency_ms + (metrics.latency_ms or 0), 3)

    def check_abort(self, stage: str) -> bool:
        if self.abort_controller.is_abort_requested():
            self.result.result = "ABORTED"
            self.result.failure_reason = LaunchFailureReason.USER_ABORTED
            self.result.safety_aborts += 1
            self.result.notes = f"Abort requested at stage: {stage}."
            return True
        return False

    # --- Windows Search ---

    def press_windows_key(self) -> bool:
        if self.check_abort("before_windows_key"):
            return False
        self._launch_start_monotonic = time.monotonic()
        self.result.outlook_launch_started_at = datetime.now().isoformat()
        assert pyautogui.FAILSAFE is True
        pyautogui.press("win")
        self.result.windows_key_timestamp = datetime.now().isoformat()
        self.result.keyboard_action_count += 1
        time.sleep(WINDOWS_KEY_STABILIZE_SECONDS)
        return True

    def type_search_query(self, query: str = "Outlook") -> bool:
        if self.check_abort("before_typing"):
            return False
        assert pyautogui.FAILSAFE is True
        pyautogui.write(query, interval=TYPE_INTERVAL_SECONDS)  # never presses Enter
        self.result.search_query_typed_timestamp = datetime.now().isoformat()
        self.result.keyboard_action_count += 1
        time.sleep(SEARCH_TYPE_STABILIZE_SECONDS)
        return True

    def capture_search_screenshot(self):
        if self.check_abort("before_screenshot"):
            return None
        title = get_foreground_window_title()
        self.result.foreground_before_search_check = title
        if not is_search_state_foreground(title):
            self.result.result = "FAIL"
            self.result.failure_reason = LaunchFailureReason.WINDOWS_SEARCH_NOT_VISIBLE
            self.result.notes = f"Foreground window {title!r} does not match the expected Windows Search state."
            return None
        try:
            capture = capture_screen(DEFAULT_OUTPUT_DIR)
        except ScreenCaptureError as exc:
            self.result.result = "ERROR"
            self.result.failure_reason = LaunchFailureReason.TECHNICAL_PROVIDER_ERROR
            self.result.notes = f"Capture failed: {exc}"
            return None
        self.result.search_capture_timestamp = datetime.now().isoformat()
        self._search_capture_monotonic = time.monotonic()
        self.result.search_screenshot = capture.filename
        self.result.screen_width = capture.width
        self.result.screen_height = capture.height
        return capture

    def ground_search_result(self, capture) -> bool:
        """OUTLOOK_SEARCH — Vision-based SEMANTIC verification only
        (2026-09-06 keyboard-activation fix). Used identically regardless
        of which provider (primary or fallback) answers the
        self.vision.analyze() call below — there is still no
        provider-name branch anywhere in this method.

        History: a live Claude-primary run reproduced this project's
        long-standing finding that Claude's semantic recognition for this
        stage is reliable (target_visible/target_type/visible_label all
        correct) but its BBOX is not — the resulting click landed above
        the actual result and Outlook never got foreground
        (OUTLOOK_FOREGROUND_VERIFICATION_FAILED). This used to be
        "solved" with a bbox-tightness self-check + bounded same-
        screenshot refine pass + oversized-bbox geometry check +
        validate_grounding() pixel conversion, all in service of
        producing an accurate CLICK point. None of that made the bbox
        reliable enough in practice, and building a more elaborate
        Claude-specific bbox pipeline would have meant exactly the kind
        of second, provider-specific Outlook flow this architecture
        exists to avoid.

        Instead, physical activation for THIS stage no longer depends on
        ANY Vision-reported coordinate at all — see
        activate_outlook_result() below, which presses Enter (the same
        keyboard action a human uses to open Windows Search's own top
        result) once semantic verification passes. Vision's bbox is
        still accepted in the response schema (OutlookSearchGroundingResponse)
        and logged for diagnostics, but it is never converted to pixels
        and never drives a physical action for this stage — every other
        bbox-grounded stage (email row, Reply, Send) is completely
        unaffected and still uses validate_grounding()/click execution
        exactly as before."""
        if self.check_abort("before_vision_call"):
            return False

        prompt_text = WINDOWS_SEARCH_GROUNDING_PROMPT_PATH.read_text(encoding="utf-8").format(
            width=capture.width, height=capture.height
        )
        outcome = self.vision.analyze(VisionRequest(
            stage=STAGE_OUTLOOK_SEARCH, screenshot_path=Path(capture.path),
            goal="Verify the Outlook search result is present", prompt_text=prompt_text,
            response_model=OutlookSearchGroundingResponse,
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
        self._accumulate(metrics)
        self.result.grounding_metrics = metrics
        self.result.vision_grounding_latency_ms = call_metrics.latency_ms

        if self.check_abort("after_vision_response"):
            return False

        self.result.search_grounding = structured
        # raw_bbox is logged for diagnostics ONLY — never converted to
        # pixels, never used to move a mouse, for this stage.
        _grounding_logger.info(
            "OUTLOOK_SEARCH_RESULT target_visible=%s target_type=%s visible_label=%r visible_sublabel=%r "
            "raw_bbox=%r confidence=%s provider_used=%s",
            structured.target_visible, structured.target_type, structured.visible_label,
            structured.visible_sublabel, structured.bbox, structured.confidence, outcome.provider_used,
        )

        if not structured.search_visible:
            self.result.result = "FAIL"
            self.result.failure_reason = LaunchFailureReason.WINDOWS_SEARCH_NOT_VISIBLE
            return False
        if not structured.target_visible:
            self.result.result = "FAIL"
            self.result.failure_reason = LaunchFailureReason.OUTLOOK_RESULT_NOT_FOUND
            return False
        # Deterministic, code-side policy — Vision only ever REPORTS what
        # it sees; it never decides whether activation is authorized.
        # Only a result Vision itself classified as the desktop app, AND
        # whose own label text still independently mentions "outlook"
        # (cheap defense-in-depth against a type/label disagreement), is
        # ever eligible to proceed.
        if structured.target_type != "desktop_app":
            self.result.result = "FAIL"
            self.result.failure_reason = LaunchFailureReason.OUTLOOK_RESULT_WRONG_TYPE
            self.result.notes = (
                f"Target was visible but classified as target_type={structured.target_type!r} "
                f"(visible_label={structured.visible_label!r}), not the desktop app — refusing to activate."
            )
            return False
        if "outlook" not in structured.visible_label.lower():
            self.result.result = "FAIL"
            self.result.failure_reason = LaunchFailureReason.OUTLOOK_RESULT_NOT_FOUND
            self.result.notes = f"target_type was desktop_app but visible_label {structured.visible_label!r} does not mention Outlook."
            return False
        # Secondary identity signal, independent of visible_label: a
        # section header or container would never have an "App"-style
        # sublabel under it. Only checked when Vision actually reported
        # one — an empty sublabel is not itself disqualifying (Vision may
        # legitimately not see one), but a REPORTED sublabel that doesn't
        # look like an app indicator contradicts the desktop_app claim.
        if structured.visible_sublabel and "app" not in structured.visible_sublabel.lower():
            self.result.result = "FAIL"
            self.result.failure_reason = LaunchFailureReason.OUTLOOK_RESULT_SUBLABEL_MISMATCH
            self.result.notes = (
                f"target_type was desktop_app but visible_sublabel {structured.visible_sublabel!r} "
                "does not indicate an app result — identity signals disagree."
            )
            return False
        if structured.confidence < GROUNDING_CONFIDENCE_THRESHOLD:
            self.result.result = "FAIL"
            self.result.failure_reason = LaunchFailureReason.GROUNDING_INVALID
            self.result.notes = f"Confidence {structured.confidence} below threshold {GROUNDING_CONFIDENCE_THRESHOLD}."
            return False

        return True  # ready for human approval / activation

    def record_human_approval(self, approved: bool) -> bool:
        self.result.human_target_approved = approved
        self.result.human_interventions += 1
        if not approved:
            self.result.result = "ABORTED"
            self.result.failure_reason = LaunchFailureReason.HUMAN_REJECTED_TARGET
        return approved

    def activate_outlook_result(self) -> bool:
        """Deterministic keyboard activation (2026-09-06 — replaces the
        old coordinate-based click). Vision's job for OUTLOOK_SEARCH ends
        at ground_search_result()'s semantic verification above; this
        method performs exactly ONE Enter key press (pyautogui) — the
        same user-equivalent action a human uses to open Windows
        Search's own top result — never dependent on a Vision-reported
        coordinate, and identical regardless of which provider produced
        the semantic verification. No mouse movement, no click, no
        fixed/hardcoded x/y, no direct executable launch, no COM/API/Graph."""
        if self.check_abort("before_activation"):
            _grounding_logger.info("OUTLOOK_ACTIVATION_BLOCKED reason=abort_requested")
            return False
        if not self.result.human_target_approved:
            raise RuntimeError("Cannot activate: human approval was not recorded as True.")

        title = get_foreground_window_title()
        if not is_search_state_foreground(title):
            self.result.result = "FAIL"
            self.result.failure_reason = LaunchFailureReason.SEARCH_STATE_LOST_BEFORE_CLICK
            self.result.notes = f"Search state lost before activation; foreground was {title!r}. NO key press."
            _grounding_logger.info(
                "OUTLOOK_ACTIVATION_BLOCKED reason=search_state_lost foreground=%r", title,
            )
            return False

        _grounding_logger.info(
            "OUTLOOK_ACTIVATION_PRECHECK semantic_valid=True foreground_ok=True abort_requested=False",
        )

        assert pyautogui.FAILSAFE is True
        try:
            _grounding_logger.info("OUTLOOK_ENTER_ABOUT_TO_EXECUTE")
            pyautogui.press("enter")  # single key press only
            _grounding_logger.info("OUTLOOK_ENTER_EXECUTED")
        except Exception as exc:
            _grounding_logger.info("OUTLOOK_ACTIVATION_FAILED exception_type=%s message=%s", type(exc).__name__, exc)
            self.result.result = "ERROR"
            self.result.failure_reason = LaunchFailureReason.PHYSICAL_ACTION_FAILED
            self.result.notes = f"Enter key press failed: {type(exc).__name__}: {exc}"
            return False

        self.result.outlook_click_timestamp = datetime.now().isoformat()
        self.result.outlook_launch_click_executed = True
        self.result.keyboard_action_count += 1  # keyboard action, not a mouse click
        return True

    def poll_for_outlook_foreground(self) -> bool:
        """Bounded polling using lightweight foreground/title checks only —
        no Vision call per poll tick, per instruction."""
        start = time.monotonic()
        time.sleep(OUTLOOK_LAUNCH_INITIAL_WAIT_SECONDS)
        while True:
            if self.check_abort("during_launch_waiting"):
                return False
            title = get_foreground_window_title()
            if is_outlook_foreground(title):
                self.result.foreground_after_launch = title
                self.result.foreground_verified = True
                self.result.outlook_detected_timestamp = datetime.now().isoformat()
                self.result.outlook_launch_duration_ms = round((time.monotonic() - start) * 1000, 1)
                _grounding_logger.info("POST_ACTIVATION_OUTLOOK_VERIFICATION success=True")
                return True
            if (time.monotonic() - start) >= OUTLOOK_LAUNCH_TIMEOUT_SECONDS:
                self.result.result = "FAIL"
                self.result.failure_reason = LaunchFailureReason.OUTLOOK_LAUNCH_TIMEOUT
                self.result.foreground_after_launch = title
                self.result.foreground_verified = False
                self.result.notes = f"Outlook foreground not detected within {OUTLOOK_LAUNCH_TIMEOUT_SECONDS}s. Last foreground: {title!r}."
                _grounding_logger.info("POST_ACTIVATION_OUTLOOK_VERIFICATION success=False")
                return False
            time.sleep(OUTLOOK_LAUNCH_POLL_INTERVAL_SECONDS)

    def verify_outlook_readiness(self) -> bool:
        """A splash/loading screen is never treated as ready, however
        confidently Vision reports outlook_visible for it. Up to
        MAX_READINESS_ATTEMPTS attempts, each with its own wait and its
        own fresh screenshot + Vision call, each recorded separately in
        result.readiness_attempts. Exhausting all attempts still loading
        classifies OUTLOOK_READY_TIMEOUT — a genuine failure, not a PASS
        with an asterisk."""
        if not self.result.foreground_verified:
            self.result.result = "FAIL"
            self.result.failure_reason = LaunchFailureReason.OUTLOOK_FOREGROUND_VERIFICATION_FAILED
            return False

        for attempt_number in range(1, MAX_READINESS_ATTEMPTS + 1):
            if self.check_abort("before_final_verification"):
                return False

            delay = READINESS_INITIAL_WAIT_SECONDS if attempt_number == 1 else READINESS_RETRY_WAIT_SECONDS
            time.sleep(delay)

            if self.check_abort("before_final_verification"):
                return False

            title = get_foreground_window_title()
            if not is_outlook_foreground(title):
                self.result.result = "FAIL"
                self.result.failure_reason = LaunchFailureReason.OUTLOOK_FOREGROUND_VERIFICATION_FAILED
                self.result.notes = f"Outlook lost foreground during readiness verification (attempt {attempt_number}); foreground was {title!r}."
                return False

            attempt = OutlookReadinessAttempt(
                attempt_number=attempt_number, delay_seconds=delay, timestamp=datetime.now().isoformat()
            )
            try:
                capture = capture_screen(DEFAULT_OUTPUT_DIR)
            except ScreenCaptureError as exc:
                attempt.error = str(exc)
                self.result.readiness_attempts.append(attempt)
                self.result.result = "ERROR"
                self.result.failure_reason = LaunchFailureReason.TECHNICAL_PROVIDER_ERROR
                self.result.notes = f"Readiness capture failed: {exc}"
                return False
            attempt.screenshot = capture.filename

            prompt_text = OUTLOOK_LAUNCH_VERIFICATION_PROMPT_PATH.read_text(encoding="utf-8")
            outcome = self.vision.analyze(VisionRequest(
                stage=STAGE_OUTLOOK_READINESS, screenshot_path=Path(capture.path),
                goal="Verify Outlook is ready for interaction", prompt_text=prompt_text,
                response_model=OutlookLaunchVerificationResponse,
            ))
            self.result.provider_retries += outcome.primary_retries + outcome.fallback_retries
            self.result.fallback_uses += 1 if outcome.fallback_used else 0
            if outcome.parsed is None:
                attempt.error = outcome.error
                self.result.readiness_attempts.append(attempt)
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
            self._accumulate(metrics)
            attempt.metrics = metrics
            if attempt_number == 1:
                self.result.launch_verification_metrics = metrics

            attempt.schema_valid = True
            attempt.outlook_visible = structured.outlook_visible
            attempt.splash_screen_visible = structured.splash_screen_visible
            attempt.ready_for_interaction = structured.ready_for_interaction
            attempt.detected_state = structured.detected_state
            attempt.confidence = structured.confidence
            attempt.reason = structured.reason
            self.result.readiness_attempts.append(attempt)
            self.result.launch_verification = structured

            if structured.ready_for_interaction and not structured.splash_screen_visible:
                self.result.ready_for_interaction = True
                self.result.result = "PASS"
                self.result.outlook_ready_at = datetime.now().isoformat()
                if self._launch_start_monotonic is not None:
                    self.result.outlook_ready_ms = round((time.monotonic() - self._launch_start_monotonic) * 1000, 1)
                return True

            # Still loading (or not visible at all) — no click, no retry of
            # the LAUNCH itself, just another readiness look if attempts remain.

        self.result.ready_for_interaction = False
        self.result.result = "FAIL"
        self.result.failure_reason = LaunchFailureReason.OUTLOOK_READY_TIMEOUT
        self.result.notes = f"Outlook still showed a splash/loading screen after {MAX_READINESS_ATTEMPTS} readiness attempts."
        return False

    # --- Phase 2: maximize enforcement (sub-step of reaching OUTLOOK_READY,
    # never a separate playbook state — see docs/architecture/02_STATE_MACHINE.md) ---

    def enforce_maximized(self) -> bool:
        """Ensures Outlook is maximized before readiness verification
        proceeds. Never uses mouse coordinates — maximize is a pure
        ctypes ShowWindow(SW_MAXIMIZE) call on the current foreground
        window handle. A no-op (no wait, no action) if Outlook is
        already maximized. Foreground is re-checked before the maximize
        check itself, before the maximize action, and again after the
        stabilization wait — if Outlook loses foreground at any point,
        this stops safely (OUTLOOK_FOREGROUND_LOST) rather than
        auto-refocusing or continuing on another application."""
        if self.check_abort("before_maximize_check"):
            return False

        title = get_foreground_window_title()
        if not is_outlook_foreground(title):
            self.result.result = "FAIL"
            self.result.failure_reason = LaunchFailureReason.OUTLOOK_FOREGROUND_LOST
            self.result.notes = f"Outlook not foreground before maximize check; foreground was {title!r}."
            return False

        hwnd = get_foreground_hwnd()
        already_maximized = is_maximized(hwnd)
        self.result.maximize_required = not already_maximized

        if already_maximized:
            self.result.maximize_executed = False
            return True

        if self.check_abort("before_maximize_action"):
            return False

        maximize(hwnd)  # never mouse coordinates
        self.result.maximize_executed = True

        maximize_start = time.monotonic()
        time.sleep(MAXIMIZE_STABILIZE_WAIT_SECONDS)
        self.result.maximize_duration_ms = round((time.monotonic() - maximize_start) * 1000, 1)

        if self.check_abort("after_maximize_stabilization"):
            return False

        title2 = get_foreground_window_title()
        if not is_outlook_foreground(title2):
            self.result.result = "FAIL"
            self.result.failure_reason = LaunchFailureReason.OUTLOOK_FOREGROUND_LOST
            self.result.notes = f"Outlook lost foreground after maximize stabilization; foreground was {title2!r}."
            return False

        try:
            capture = capture_screen(DEFAULT_OUTPUT_DIR)
        except ScreenCaptureError as exc:
            self.result.result = "ERROR"
            self.result.failure_reason = LaunchFailureReason.TECHNICAL_PROVIDER_ERROR
            self.result.notes = f"Post-maximize capture failed: {exc}"
            return False
        self.result.post_maximize_screenshot = capture.filename

        return True
