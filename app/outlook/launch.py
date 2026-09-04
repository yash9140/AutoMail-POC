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
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import pyautogui
from pydantic import ValidationError

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from app.automation.screen_capture import (  # noqa: E402
    DEFAULT_OUTPUT_DIR,
    ScreenCaptureError,
    capture_screen,
    get_environment_info,
)
from app.config.settings import (  # noqa: E402
    MAX_READINESS_ATTEMPTS,
    MAXIMIZE_STABILIZE_WAIT_SECONDS,
    MOVE_DURATION_SECONDS,
    OUTLOOK_LAUNCH_INITIAL_WAIT_SECONDS,
    OUTLOOK_LAUNCH_POLL_INTERVAL_SECONDS,
    OUTLOOK_LAUNCH_TIMEOUT_SECONDS,
    READINESS_INITIAL_WAIT_SECONDS,
    READINESS_RETRY_WAIT_SECONDS,
    SEARCH_TYPE_STABILIZE_SECONDS,
    TYPE_INTERVAL_SECONDS,
    VISION_CONFIDENCE_THRESHOLD,
    WINDOWS_KEY_STABILIZE_SECONDS,
    get_provider,
)
from app.fallback.recovery import call_with_provider_retry  # noqa: E402
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
from app.safety.validators import GroundingCheckFailure, validate_grounding  # noqa: E402
from app.vision.debug_overlay import save_grounding_debug_artifact  # noqa: E402
from app.vision.grounding import coordinate_in_image_bounds, normalize_1000_to_pixels  # noqa: E402
from app.vision.models import OutlookSearchGroundingResponse, OutlookSearchRefineResponse  # noqa: E402
from app.vision.providers.base import VisionProvider  # noqa: E402
from rnd.models.outlook_launch import (  # noqa: E402
    CallMetrics,
    OutlookLaunchVerificationResponse,
    OutlookReadinessAttempt,
    RND009BResult,
    WindowsSearchGroundingResponse,
)

PROMPTS_DIR = PROJECT_ROOT / "rnd" / "prompts"
# Claude (Anthropic) OUTLOOK_SEARCH path — bbox + semantic-classification
# + tightness-self-check + bounded refine-pass (2026-09 R&D). See
# _ground_search_result_claude().
WINDOWS_SEARCH_GROUNDING_PROMPT_PATH = (
    PROJECT_ROOT / "app" / "vision" / "prompts" / "outlook_search_grounding_v1.txt"
)
OUTLOOK_SEARCH_REFINE_PROMPT_PATH = (
    PROJECT_ROOT / "app" / "vision" / "prompts" / "outlook_search_grounding_refine_v1.txt"
)
# Gemini OUTLOOK_SEARCH path — the ORIGINAL, pre-Claude-migration, single
# loose-point contract (rnd/prompts/windows_search_grounding_v1.txt,
# historical and frozen — read directly, not copied, so this is always
# byte-identical to what the original RND-009B flow used). Gemini never
# showed the "Best match" header-vs-row bbox-scoping problem Claude did
# during live testing, so it is intentionally kept simple rather than
# forced through Claude's bbox pipeline. See _ground_search_result_gemini().
WINDOWS_SEARCH_GROUNDING_PROMPT_PATH_GEMINI = PROMPTS_DIR / "windows_search_grounding_v1.txt"
OUTLOOK_LAUNCH_VERIFICATION_PROMPT_PATH = PROMPTS_DIR / "outlook_launch_verification_v1.txt"

# Kept as a module-level alias (same value as VISION_CONFIDENCE_THRESHOLD)
# so existing call sites/tests that reference this name keep working.
GROUNDING_CONFIDENCE_THRESHOLD = VISION_CONFIDENCE_THRESHOLD

# A single clickable search-result row can never legitimately span this
# much of the screenshot in BOTH width and height at once — a
# resolution-independent, layout-independent (normalized 0-1000) sanity
# bound, not a screen-specific threshold. See ground_search_result()'s
# oversized-bbox check for the full reasoning on why this is
# deliberately narrow (only the "whole screen/panel" extreme).
MAX_PLAUSIBLE_RESULT_BBOX_NORMALIZED_SPAN = 950.0

# Vision-call stage identifiers (app/fallback/recovery.py structured
# logging) for this module's calls.
STAGE_OUTLOOK_SEARCH = "OUTLOOK_SEARCH"
# The optional, bounded, SAME-screenshot second pass — a distinct stage
# tag so its own VISION_CALL_*/provider-retry logging is never confused
# with the first pass in diagnostics. See ground_search_result().
STAGE_OUTLOOK_SEARCH_REFINE = "OUTLOOK_SEARCH_REFINE"
STAGE_OUTLOOK_READINESS = "OUTLOOK_READINESS"

# Diagnostic-only, opt-in: set OUTLOOK_GROUNDING_DEBUG=1 to save a
# debug/outlook_search_grounding_<timestamp>.png overlay artifact per
# grounding attempt. Never enabled by default; never a runtime click
# source (see app/vision/debug_overlay.py).
_DEBUG_ARTIFACTS_ENABLED = os.environ.get("OUTLOOK_GROUNDING_DEBUG") == "1"

_grounding_logger = logging.getLogger("app.outlook.launch.grounding")
if not _grounding_logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s [GROUNDING] %(message)s"))
    _grounding_logger.addHandler(_handler)
    _grounding_logger.setLevel(logging.INFO)
    _grounding_logger.propagate = False

# Maps app.safety.validators.GroundingCheckFailure to this module's own
# two pre-existing failure reasons — mirrors the same lookup-table pattern
# every other bbox-grounded stage in app/outlook/ already uses, scoped to
# the reasons this stage already declares (no new reasons introduced
# beyond COORDINATE_SPACE_MISMATCH, which is handled separately before
# validate_grounding() is ever called).
_GROUNDING_FAILURE_TO_REASON = {
    GroundingCheckFailure.INVALID_BBOX: LaunchFailureReason.GROUNDING_INVALID,
    GroundingCheckFailure.DEGENERATE_BBOX: LaunchFailureReason.GROUNDING_INVALID,
    GroundingCheckFailure.OUT_OF_NORMALIZED_RANGE: LaunchFailureReason.GROUNDING_INVALID,
    GroundingCheckFailure.POINT_OUTSIDE_BBOX: LaunchFailureReason.GROUNDING_INVALID,
    GroundingCheckFailure.MISSING_COORDINATES: LaunchFailureReason.GROUNDING_INVALID,
    GroundingCheckFailure.OUT_OF_BOUNDS: LaunchFailureReason.GROUNDING_OUT_OF_BOUNDS,
    GroundingCheckFailure.SIDEBAR_REJECTED: LaunchFailureReason.GROUNDING_INVALID,
    GroundingCheckFailure.LOW_CONFIDENCE: LaunchFailureReason.GROUNDING_INVALID,
}


def _provider() -> tuple[VisionProvider, str]:
    """Kept as a thin alias to app.config.settings.get_provider() — the
    name several existing call sites/tests import. Returns whichever
    provider get_provider() constructs — Anthropic (Claude) or Gemini,
    whichever AI_PROVIDER selects (see app/config/settings.py). This
    module's OWN provider-specific behavior is isolated to the ONE
    dispatch in ground_search_result() — see
    _ground_search_result_claude()/_ground_search_result_gemini()."""
    return get_provider()


class OutlookLaunchResult(RND009BResult):
    """Phase 2 additions, as a subclass rather than editing
    rnd/models/outlook_launch.py directly — that file is historical R&D
    evidence and is never modified. `outlook_window_detected_at`/`_ms`
    are intentionally NOT duplicated here: they're already covered by
    the inherited `outlook_detected_timestamp`/`outlook_launch_duration_ms`
    fields, populated by poll_for_outlook_foreground().

    Two OUTLOOK_SEARCH result shapes now coexist, populated by whichever
    of _ground_search_result_claude()/_ground_search_result_gemini() ran
    (2026-09-04, Gemini demo-restoration — previously, during the
    Claude-only period, `grounding` was left permanently None; that is
    no longer true now that Gemini mode populates it again):
    - `grounding` (inherited from RND009BResult): the historical,
      Gemini-path Optional[WindowsSearchGroundingResponse] — a loose
      (x, y) point. Populated only in Gemini mode.
    - `search_grounding`: the Claude-path Optional[
      OutlookSearchGroundingResponse] — bbox-based. Populated only in
      Claude mode. See that class's docstring in app/vision/models.py
      for the live coordinate-contract fix this was added for."""

    outlook_launch_started_at: Optional[str] = None

    maximize_required: Optional[bool] = None
    maximize_executed: bool = False
    maximize_duration_ms: Optional[float] = None
    post_maximize_screenshot: Optional[str] = None

    outlook_ready_at: Optional[str] = None
    outlook_ready_ms: Optional[float] = None

    provider_retries: int = 0

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
    def __init__(self, abort_controller: AbortController, provider: VisionProvider, model: str) -> None:
        self.abort_controller = abort_controller
        self.provider = provider
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
        """Shared pre-flight (abort + coordinate-space checks — generic,
        provider-independent safety, see below), then dispatches to the
        one provider-specific OUTLOOK_SEARCH grounding strategy for
        whichever provider is actually configured. This dispatch is
        deliberately the ONLY provider-name check in this module (in the
        whole find/read/reply/draft/send pipeline, in fact) — every
        other stage sends the same prompt/schema to whichever provider
        get_provider() constructed, unaware of which one it is. See
        _ground_search_result_claude()/_ground_search_result_gemini()
        for why OUTLOOK_SEARCH specifically needs two strategies."""
        if self.check_abort("before_vision_call"):
            return False

        # Coordinate-SPACE check, independent of anything Vision returns
        # and independent of which provider is active: if the screenshot
        # mss captured and pyautogui's own reported screen size disagree,
        # ANY coordinate computed from the screenshot and later used to
        # move the mouse would be systematically wrong regardless of how
        # correct the grounding itself is. Checked first (cheap, local,
        # no network) so a mismatch fails fast before spending a Vision
        # call, for either provider.
        env_info = get_environment_info()
        self.result.pyautogui_width = env_info.get("pyautogui_width")
        self.result.pyautogui_height = env_info.get("pyautogui_height")
        dimensions_match = env_info.get("dimensions_match")
        self.result.dimensions_match = dimensions_match
        _grounding_logger.info(
            "COORDINATE_SPACE screenshot_size=%sx%s pyautogui_size=%sx%s dimensions_match=%s",
            capture.width, capture.height, env_info.get("pyautogui_width"), env_info.get("pyautogui_height"),
            dimensions_match,
        )
        if dimensions_match is False:
            self.result.result = "FAIL"
            self.result.failure_reason = LaunchFailureReason.COORDINATE_SPACE_MISMATCH
            self.result.notes = (
                f"Screenshot size ({capture.width}x{capture.height}) does not match pyautogui's reported "
                f"screen size ({env_info.get('pyautogui_width')}x{env_info.get('pyautogui_height')}). "
                "Refusing to compute a click point in a mismatched coordinate space."
            )
            return False

        if self.provider.provider_name == "gemini":
            return self._ground_search_result_gemini(capture)
        return self._ground_search_result_claude(capture)

    def _ground_search_result_claude(self, capture) -> bool:
        """Claude (Anthropic) OUTLOOK_SEARCH grounding — bbox + semantic
        classification + tightness-self-check + bounded same-screenshot
        refine-pass, developed during the 2026-09 Claude R&D after live
        testing showed Claude sometimes grounding the "Best match"
        section header instead of the actual Outlook result row.
        Preserved unchanged here; see git-independent history in this
        file's own accumulated docstrings/comments for the full
        reasoning behind each check."""
        prompt_text = WINDOWS_SEARCH_GROUNDING_PROMPT_PATH.read_text(encoding="utf-8").format(
            width=capture.width, height=capture.height
        )
        outcome = call_with_provider_retry(
            lambda: self.provider.analyze_screen(Path(capture.path), "Locate the Outlook search result", prompt_text),
            stage=STAGE_OUTLOOK_SEARCH, provider_name=self.provider.provider_name,
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
        self._accumulate(metrics)
        self.result.grounding_metrics = metrics
        self.result.vision_grounding_latency_ms = call.latency_ms

        if self.check_abort("after_vision_response"):
            return False

        try:
            structured = OutlookSearchGroundingResponse.model_validate(call.parsed_json) if call.parsed_json else None
        except ValidationError:
            structured = None
        if structured is None:
            self.result.result = "ERROR"
            self.result.failure_reason = LaunchFailureReason.TECHNICAL_PROVIDER_ERROR
            self.result.notes = "Grounding response was not schema-valid."
            return False

        self.result.search_grounding = structured
        _grounding_logger.info(
            "OUTLOOK_GROUNDING target_visible=%s target_type=%s visible_label=%r raw_bbox=%r "
            "bbox_order=[y_min,x_min,y_max,x_max] screenshot_size=%sx%s confidence=%s",
            structured.target_visible, structured.target_type, structured.visible_label,
            structured.bbox, capture.width, capture.height, structured.confidence,
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
        # it sees; it never decides whether a click is authorized. Only a
        # result Vision itself classified as the desktop app, AND whose
        # own label text still independently mentions "outlook" (cheap
        # defense-in-depth against a type/label disagreement), is ever
        # eligible to proceed.
        if structured.target_type != "desktop_app":
            self.result.result = "FAIL"
            self.result.failure_reason = LaunchFailureReason.OUTLOOK_RESULT_WRONG_TYPE
            self.result.notes = (
                f"Target was visible but classified as target_type={structured.target_type!r} "
                f"(visible_label={structured.visible_label!r}), not the desktop app — refusing to click."
            )
            return False
        if "outlook" not in structured.visible_label.lower():
            self.result.result = "FAIL"
            self.result.failure_reason = LaunchFailureReason.OUTLOOK_RESULT_NOT_FOUND
            self.result.notes = f"target_type was desktop_app but visible_label {structured.visible_label!r} does not mention Outlook."
            return False
        # Secondary identity signal, independent of visible_label: a
        # section header or container would never have an "App"-style
        # sublabel under it. Only checked when Claude actually reported
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

        bbox = structured.bbox
        if bbox is None or len(bbox) != 4:
            self.result.result = "FAIL"
            self.result.failure_reason = LaunchFailureReason.GROUNDING_INVALID
            self.result.notes = f"Outlook result reported visible but had no valid bbox: {bbox!r}."
            return False

        confidence_for_validation = structured.confidence

        # Bbox-SCOPING self-check (2026-09-03 live evidence: target_type/
        # visible_label/confidence were all correctly reported, but the
        # bbox covered the "Best match" section header above the row
        # instead of the row itself — a distinct bug from target identity,
        # which the checks above already cover). If Claude itself was not
        # confident the bbox is tightly scoped to just the row, run ONE
        # bounded, SAME-screenshot second-pass call asking only for a
        # tighter bbox for the row already identified — no new screenshot,
        # no physical action, never more than this one extra read-only
        # call. If the refine pass still can't produce a confident, valid
        # bbox, this fails closed (OUTLOOK_RESULT_BBOX_NOT_TIGHT) rather
        # than falling back to the bbox already flagged as possibly
        # including the header/container.
        if not structured.bbox_tightly_scoped:
            if self.check_abort("before_refine_vision_call"):
                return False

            refine_prompt = OUTLOOK_SEARCH_REFINE_PROMPT_PATH.read_text(encoding="utf-8").format(
                visible_label=structured.visible_label, visible_sublabel=structured.visible_sublabel,
                width=capture.width, height=capture.height,
            )
            refine_outcome = call_with_provider_retry(
                lambda: self.provider.analyze_screen(
                    Path(capture.path), "Tighten the bbox for the previously identified Outlook result", refine_prompt,
                ),
                stage=STAGE_OUTLOOK_SEARCH_REFINE, provider_name=self.provider.provider_name,
            )
            self.result.provider_retries += refine_outcome.retries_used
            self.result.refine_pass_used = True

            refined = None
            if refine_outcome.result is not None:
                refine_call = refine_outcome.result
                try:
                    refined = OutlookSearchRefineResponse.model_validate(refine_call.parsed_json) \
                        if refine_call.parsed_json else None
                except ValidationError:
                    refined = None
                refine_metrics = CallMetrics(
                    latency_ms=refine_call.latency_ms, input_tokens=refine_call.input_tokens,
                    output_tokens=refine_call.output_tokens,
                    estimated_cost=estimate_cost(
                        self.provider.provider_name, refine_call.model,
                        refine_call.input_tokens, refine_call.output_tokens,
                    ),
                )
                self._accumulate(refine_metrics)

            self.result.search_grounding_refine = refined
            _grounding_logger.info(
                "OUTLOOK_GROUNDING_REFINE target_visible=%s raw_bbox=%r confidence=%s",
                refined.target_visible if refined else None,
                refined.bbox if refined else None,
                refined.confidence if refined else None,
            )

            if refined is None or not refined.target_visible or refined.bbox is None or len(refined.bbox) != 4:
                self.result.result = "FAIL"
                self.result.failure_reason = LaunchFailureReason.OUTLOOK_RESULT_BBOX_NOT_TIGHT
                self.result.notes = (
                    "First-pass bbox was not confidently tightly-scoped to the result row, and the bounded "
                    "second-pass refine call could not confirm a tight bbox either. Refusing to click a bbox "
                    "known to possibly include the section header/container."
                )
                return False

            bbox = refined.bbox
            confidence_for_validation = refined.confidence

        # Generic, resolution-independent sanity check: a single
        # clickable search-result row/tile can never legitimately span
        # nearly the ENTIRE screenshot HEIGHT — a topological fact, true
        # at any resolution/theme/scaling, not a screen-specific
        # threshold. Checked on HEIGHT ALONE, deliberately not width: a
        # legitimate single row commonly spans most of the panel's WIDTH
        # (normal, must not be rejected), but a search flyout always
        # shows more than just one row's worth of vertical space (the
        # search box, tabs, and multiple result rows stacked below each
        # other) — so a bbox whose height alone approaches the whole
        # screenshot's height can never be one row; it must be a column,
        # panel, or the whole results container. This one check catches
        # both the extreme "grounded the whole screen/panel" case and a
        # tall-but-narrow "whole results column" bbox, without penalizing
        # legitimately wide rows.
        #
        # A moderately-oversized container bbox that is NOT tall enough to
        # trip this (e.g. just the search-results group, not a full-height
        # column) is NOT reliably distinguishable from a legitimately
        # large multi-line result tile by geometry alone without inventing
        # a brittle, layout-specific assumption — that case is instead
        # caught by the target_type/visible_label/visible_sublabel
        # semantic checks and the bbox_tightly_scoped self-check above,
        # the prompt's explicit "never the container" instruction, and the
        # human-approval gate before any physical action. Re-applied here
        # even after a refine pass, since the refined bbox is a new claim
        # too.
        y_min, x_min, y_max, x_max = bbox
        if (y_max - y_min) >= MAX_PLAUSIBLE_RESULT_BBOX_NORMALIZED_SPAN:
            self.result.result = "FAIL"
            self.result.failure_reason = LaunchFailureReason.GROUNDING_INVALID
            self.result.notes = (
                f"bbox {bbox!r} spans nearly the entire screenshot height — "
                "not a plausible single result row; refusing to click a container/column-sized region."
            )
            return False

        # bbox is [y_min, x_min, y_max, x_max], 0-1000 normalized — SAME
        # convention as every other bbox in this project (see
        # OutlookSearchGroundingResponse's docstring). The click point is
        # the bbox CENTER, computed by validate_grounding() from the bbox
        # itself — never Claude's own loose x/y (there isn't one anymore),
        # and never trusted without the point-inside-its-own-bbox,
        # normalized-range, degenerate-bbox, and pixel-bounds checks
        # validate_grounding() already applies to every other grounded
        # click target (email row, Reply, Send).
        center_x, center_y = (x_min + x_max) / 2, (y_min + y_max) / 2

        validation = validate_grounding(
            raw_x=center_x, raw_y=center_y, box_2d=bbox, confidence=confidence_for_validation,
            image_width=capture.width, image_height=capture.height,
            confidence_threshold=GROUNDING_CONFIDENCE_THRESHOLD,
            sidebar_max_x_fraction=None,  # the search-result list is not the Outlook sidebar
        )

        self.result.search_grounding_bbox_raw = list(bbox)
        screen_bbox_pixels = None
        click_point = None
        if validation.converted_x is not None:
            px_min, py_min = normalize_1000_to_pixels(x_min, y_min, capture.width, capture.height)
            px_max, py_max = normalize_1000_to_pixels(x_max, y_max, capture.width, capture.height)
            screen_bbox_pixels = [round(py_min), round(px_min), round(py_max), round(px_max)]
            self.result.search_grounding_bbox_pixels = screen_bbox_pixels
            self.result.raw_x, self.result.raw_y = center_x, center_y
            self.result.converted_x, self.result.converted_y = validation.converted_x, validation.converted_y
            click_point = (validation.converted_x, validation.converted_y)
            self.result.coordinate_in_screen_bounds = validation.failure != GroundingCheckFailure.OUT_OF_BOUNDS

        _grounding_logger.info(
            "OUTLOOK_GROUNDING target_visible=%s target_type=%s visible_label=%r visible_sublabel=%r "
            "raw_bbox=%r bbox_order=[y_min,x_min,y_max,x_max] refine_pass_used=%s screenshot_size=%sx%s "
            "screen_bbox=%r click_point=%r confidence=%s valid=%s",
            structured.target_visible, structured.target_type, structured.visible_label,
            structured.visible_sublabel, bbox, self.result.refine_pass_used,
            capture.width, capture.height, screen_bbox_pixels, click_point, confidence_for_validation,
            validation.valid,
        )

        if _DEBUG_ARTIFACTS_ENABLED:
            artifact_path = save_grounding_debug_artifact(
                Path(capture.path), screen_bbox_pixels, click_point, label="outlook_search_grounding",
                overlay_text=(
                    f"target_type={structured.target_type} visible_label={structured.visible_label!r} "
                    f"confidence={confidence_for_validation} refined={self.result.refine_pass_used}"
                ),
            )
            if artifact_path is not None:
                self.result.grounding_debug_artifact = str(artifact_path)

        if not validation.valid:
            self.result.result = "FAIL"
            self.result.failure_reason = _GROUNDING_FAILURE_TO_REASON.get(
                validation.failure, LaunchFailureReason.GROUNDING_INVALID,
            )
            if validation.notes:
                self.result.notes = validation.notes
            return False

        return True  # ready for human approval

    def _ground_search_result_gemini(self, capture) -> bool:
        """Gemini OUTLOOK_SEARCH grounding — the ORIGINAL, pre-Claude-
        migration, single-loose-point flow (2026-09-04, demo-prep
        restoration): a plain (x, y) point per rnd/prompts/
        windows_search_grounding_v1.txt's historical, frozen contract
        (search_visible / outlook_result_visible / result_label / x / y
        / confidence). Reconstructed from that surviving prompt file and
        rnd/models/outlook_launch.py::WindowsSearchGroundingResponse —
        no git history was needed or used. Gemini never showed the
        "Best match" header-vs-row bbox-scoping problem Claude did
        during live testing, so it is intentionally NOT forced through
        Claude's bbox + semantic-classification + refine-pass pipeline
        (_ground_search_result_claude) — same failure-reason vocabulary
        (WINDOWS_SEARCH_NOT_VISIBLE / OUTLOOK_RESULT_NOT_FOUND /
        GROUNDING_INVALID / GROUNDING_OUT_OF_BOUNDS), same
        provider-retry/abort/logging/debug-overlay conventions as every
        other stage — only the response shape and validation are
        simpler, because that simplicity is what actually worked."""
        prompt_text = WINDOWS_SEARCH_GROUNDING_PROMPT_PATH_GEMINI.read_text(encoding="utf-8").format(
            width=capture.width, height=capture.height
        )
        outcome = call_with_provider_retry(
            lambda: self.provider.analyze_screen(Path(capture.path), "Locate the Outlook search result", prompt_text),
            stage=STAGE_OUTLOOK_SEARCH, provider_name=self.provider.provider_name,
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
        self._accumulate(metrics)
        self.result.grounding_metrics = metrics
        self.result.vision_grounding_latency_ms = call.latency_ms

        if self.check_abort("after_vision_response"):
            return False

        try:
            structured = WindowsSearchGroundingResponse.model_validate(call.parsed_json) if call.parsed_json else None
        except ValidationError:
            structured = None
        if structured is None:
            self.result.result = "ERROR"
            self.result.failure_reason = LaunchFailureReason.TECHNICAL_PROVIDER_ERROR
            self.result.notes = "Grounding response was not schema-valid."
            return False

        self.result.grounding = structured

        if not structured.search_visible:
            self.result.result = "FAIL"
            self.result.failure_reason = LaunchFailureReason.WINDOWS_SEARCH_NOT_VISIBLE
            return False
        if not structured.outlook_result_visible or "outlook" not in structured.result_label.lower():
            self.result.result = "FAIL"
            self.result.failure_reason = LaunchFailureReason.OUTLOOK_RESULT_NOT_FOUND
            return False
        if structured.x is None or structured.y is None:
            self.result.result = "FAIL"
            self.result.failure_reason = LaunchFailureReason.GROUNDING_INVALID
            return False

        self.result.raw_x, self.result.raw_y = structured.x, structured.y
        cx, cy = normalize_1000_to_pixels(structured.x, structured.y, capture.width, capture.height)
        cx, cy = round(cx), round(cy)
        self.result.converted_x, self.result.converted_y = cx, cy

        in_bounds = coordinate_in_image_bounds(cx, cy, capture.width, capture.height)
        self.result.coordinate_in_screen_bounds = in_bounds

        _grounding_logger.info(
            "OUTLOOK_GROUNDING(gemini) search_visible=%s outlook_result_visible=%s result_label=%r "
            "raw_point=(%s, %s) screenshot_size=%sx%s click_point=(%s, %s) confidence=%s in_bounds=%s",
            structured.search_visible, structured.outlook_result_visible, structured.result_label,
            structured.x, structured.y, capture.width, capture.height, cx, cy, structured.confidence, in_bounds,
        )

        if _DEBUG_ARTIFACTS_ENABLED:
            artifact_path = save_grounding_debug_artifact(
                Path(capture.path), None, (cx, cy), label="outlook_search_grounding_gemini",
                overlay_text=f"result_label={structured.result_label!r} confidence={structured.confidence}",
            )
            if artifact_path is not None:
                self.result.grounding_debug_artifact = str(artifact_path)

        if not in_bounds:
            self.result.result = "FAIL"
            self.result.failure_reason = LaunchFailureReason.GROUNDING_OUT_OF_BOUNDS
            return False

        if structured.confidence < GROUNDING_CONFIDENCE_THRESHOLD:
            self.result.result = "FAIL"
            self.result.failure_reason = LaunchFailureReason.GROUNDING_INVALID
            self.result.notes = f"Confidence {structured.confidence} below threshold {GROUNDING_CONFIDENCE_THRESHOLD}."
            return False

        return True  # ready for human approval

    def record_human_approval(self, approved: bool) -> bool:
        self.result.human_target_approved = approved
        self.result.human_interventions += 1
        if not approved:
            self.result.result = "ABORTED"
            self.result.failure_reason = LaunchFailureReason.HUMAN_REJECTED_TARGET
        return approved

    def click_outlook_result(self) -> bool:
        if self.check_abort("before_mouse_movement"):
            _grounding_logger.info("OUTLOOK_CLICK_BLOCKED reason=abort_requested")
            return False
        if not self.result.human_target_approved:
            raise RuntimeError("Cannot click: human approval was not recorded as True.")

        # Stale-state protection: the bbox/click point were computed from
        # the ONE screenshot captured in capture_search_screenshot(); no
        # new Vision call happens between grounding and click (per
        # instruction — freshness is enforced by re-checking the *current*
        # foreground state below, not by re-observing with Vision). This
        # elapsed time is diagnostic evidence of how long that screenshot
        # has been trusted for, not a hard gate — the human-approval step
        # in between is an intentional, variable-length pause.
        elapsed_since_capture_ms = (
            round((time.monotonic() - self._search_capture_monotonic) * 1000, 1)
            if self._search_capture_monotonic is not None else None
        )

        title = get_foreground_window_title()
        if not is_search_state_foreground(title):
            self.result.result = "FAIL"
            self.result.failure_reason = LaunchFailureReason.SEARCH_STATE_LOST_BEFORE_CLICK
            self.result.notes = f"Search state lost before move; foreground was {title!r}. NO movement, no click."
            _grounding_logger.info(
                "OUTLOOK_CLICK_BLOCKED reason=search_state_lost_before_move foreground=%r", title,
            )
            return False

        _grounding_logger.info(
            "OUTLOOK_CLICK_PRECHECK foreground_ok=True abort_requested=False click_point=(%s, %s) "
            "elapsed_since_capture_ms=%s",
            self.result.converted_x, self.result.converted_y, elapsed_since_capture_ms,
        )

        assert pyautogui.FAILSAFE is True
        try:
            _grounding_logger.info(
                "OUTLOOK_MOUSE_MOVE_START click_point=(%s, %s)", self.result.converted_x, self.result.converted_y,
            )
            pyautogui.moveTo(self.result.converted_x, self.result.converted_y, duration=MOVE_DURATION_SECONDS)
            _grounding_logger.info("OUTLOOK_MOUSE_MOVE_COMPLETE")
        except Exception as exc:
            _grounding_logger.info("OUTLOOK_CLICK_FAILED exception_type=%s message=%s", type(exc).__name__, exc)
            self.result.result = "ERROR"
            self.result.failure_reason = LaunchFailureReason.PHYSICAL_ACTION_FAILED
            self.result.notes = f"Mouse move failed: {type(exc).__name__}: {exc}"
            return False

        if self.check_abort("before_click"):
            _grounding_logger.info("OUTLOOK_CLICK_BLOCKED reason=abort_requested_after_move")
            return False

        title2 = get_foreground_window_title()
        self.result.foreground_before_click = title2
        if not is_search_state_foreground(title2):
            self.result.result = "FAIL"
            self.result.failure_reason = LaunchFailureReason.SEARCH_STATE_LOST_BEFORE_CLICK
            self.result.notes = f"Search state lost between move and click; foreground was {title2!r}. NO CLICK performed."
            _grounding_logger.info(
                "OUTLOOK_CLICK_BLOCKED reason=search_state_lost_before_click foreground=%r", title2,
            )
            return False

        try:
            _grounding_logger.info(
                "OUTLOOK_CLICK_ABOUT_TO_EXECUTE click_point=(%s, %s)",
                self.result.converted_x, self.result.converted_y,
            )
            pyautogui.click()  # single click only
            _grounding_logger.info("OUTLOOK_CLICK_EXECUTED")
        except Exception as exc:
            _grounding_logger.info("OUTLOOK_CLICK_FAILED exception_type=%s message=%s", type(exc).__name__, exc)
            self.result.result = "ERROR"
            self.result.failure_reason = LaunchFailureReason.PHYSICAL_ACTION_FAILED
            self.result.notes = f"Click failed: {type(exc).__name__}: {exc}"
            return False

        self.result.outlook_click_timestamp = datetime.now().isoformat()
        self.result.outlook_launch_click_executed = True
        self.result.mouse_click_count += 1
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
                return True
            if (time.monotonic() - start) >= OUTLOOK_LAUNCH_TIMEOUT_SECONDS:
                self.result.result = "FAIL"
                self.result.failure_reason = LaunchFailureReason.OUTLOOK_LAUNCH_TIMEOUT
                self.result.foreground_after_launch = title
                self.result.foreground_verified = False
                self.result.notes = f"Outlook foreground not detected within {OUTLOOK_LAUNCH_TIMEOUT_SECONDS}s. Last foreground: {title!r}."
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
            outcome = call_with_provider_retry(
                lambda: self.provider.analyze_screen(Path(capture.path), "Verify Outlook is ready for interaction", prompt_text),
                stage=STAGE_OUTLOOK_READINESS, provider_name=self.provider.provider_name,
            )
            self.result.provider_retries += outcome.retries_used
            if outcome.result is None:
                attempt.error = outcome.error
                self.result.readiness_attempts.append(attempt)
                self.result.result = "ERROR"
                self.result.failure_reason = LaunchFailureReason.TECHNICAL_PROVIDER_ERROR
                self.result.notes = outcome.error
                return False
            call = outcome.result

            metrics = CallMetrics(
                latency_ms=call.latency_ms, input_tokens=call.input_tokens, output_tokens=call.output_tokens,
                estimated_cost=estimate_cost(self.provider.provider_name, call.model, call.input_tokens, call.output_tokens),
            )
            self._accumulate(metrics)
            attempt.metrics = metrics
            if attempt_number == 1:
                self.result.launch_verification_metrics = metrics

            try:
                structured = OutlookLaunchVerificationResponse.model_validate(call.parsed_json) if call.parsed_json else None
            except ValidationError:
                structured = None

            if structured is None:
                attempt.error = "Response was not schema-valid."
                self.result.readiness_attempts.append(attempt)
                self.result.result = "ERROR"
                self.result.failure_reason = LaunchFailureReason.TECHNICAL_PROVIDER_ERROR
                self.result.notes = "Readiness-verification response was not schema-valid."
                return False

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
    # never a separate playbook state — see docs/poc/02_STATE_MACHINE.md) ---

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
