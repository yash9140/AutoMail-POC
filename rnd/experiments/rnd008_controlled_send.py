"""RND-008 — Controlled Send + Send Verification.

The first stage in this POC that performs a real, externally-visible,
irreversible action: clicking Outlook's Send button. Every gate below
requires the previous gate's explicit, separately-recorded human
approval before it will run (enforced by SystemExit, unit-tested) —
the same philosophy as every prior stage, applied more conservatively
because this is the first stage where a mistake cannot be undone.

Gated flow, one command per stage:

    --show-email                 carries the approved understanding + draft forward
                                  from RND-007B's finalized result; shows sender/subject/
                                  body summary/draft for a safe-test-email approval
    --approve-email {pass,fail}
    --verify-draft                foreground + editor-open check, fresh screenshot,
                                   Gemini draft-verification call (vision_match)
    --confirm-draft {pass,fail}   MANDATORY regardless of vision_match
    --ground-send                 fresh screenshot, Gemini grounding-only call for
                                   "Send" ONLY, normalized-1000 coordinate conversion,
                                   checked against the known Send bounding box —
                                   ABORTS (does not proceed to approval) if the
                                   converted point falls outside that box
    --approve-send {pass,fail}    the FINAL immediate pre-click approval — refuses to
                                   record "pass" if the coordinate is not inside the
                                   known Send bbox
    --execute-send                single controlled click; refuses to run a second
                                   time once send_click_executed is True
    --verify-send                 post-send stabilization wait (2.0s attempt 1, +1.5s
                                   attempt 2) + Gemini send-verification call; max 2
                                   attempts; NEVER re-clicks Send
    --confirm-send {pass,fail}    human confirmation, authoritative; computes the
                                   combined final_classification
    --finalize                    writes the result/report files

Send is clicked at most ONCE per session, only after --approve-send has
recorded "pass" for a coordinate already validated against the known
Send bounding box. No code path in this module can click Send twice,
retry a failed send automatically, or substitute AI verification for
the mandatory human approval gates.

Run (from outlook-vision-poc/):
    python rnd/experiments/rnd008_controlled_send.py --show-email
    python rnd/experiments/rnd008_controlled_send.py --approve-email pass
    python rnd/experiments/rnd008_controlled_send.py --verify-draft
    python rnd/experiments/rnd008_controlled_send.py --confirm-draft pass
    python rnd/experiments/rnd008_controlled_send.py --ground-send
    python rnd/experiments/rnd008_controlled_send.py --approve-send pass
    python rnd/experiments/rnd008_controlled_send.py --execute-send
    python rnd/experiments/rnd008_controlled_send.py --verify-send
    python rnd/experiments/rnd008_controlled_send.py --confirm-send pass
    python rnd/experiments/rnd008_controlled_send.py --finalize
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import pyautogui
from dotenv import load_dotenv
from pydantic import ValidationError

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "rnd" / "capture"))

from screen_capture import DEFAULT_OUTPUT_DIR, ScreenCaptureError, capture_screen  # noqa: E402

from rnd.experiments.capture_with_foreground_check import get_foreground_window_title  # noqa: E402
from rnd.metrics.coordinate_calibration import normalize_1000_to_pixels  # noqa: E402
from rnd.metrics.grounding import coordinate_in_image_bounds, point_in_bbox  # noqa: E402
from rnd.models.click_execution import VerificationResponseV2  # noqa: E402
from rnd.models.contextual_reply import DraftVerificationResponse, RND007BResult  # noqa: E402
from rnd.models.controlled_send import (  # noqa: E402
    CallMetrics,
    PostSendVerificationAttempt,
    RND008Result,
    SendVerificationResponse,
)
from rnd.models.dataset_manifest import BoundingBox  # noqa: E402
from rnd.models.grounding import GroundingResponse  # noqa: E402
from rnd.providers.base import VisionProviderError  # noqa: E402
from rnd.providers.gemini_provider import GeminiProvider  # noqa: E402

PROMPTS_DIR = PROJECT_ROOT / "rnd" / "prompts"
DRAFT_VERIFICATION_PROMPT_PATH = PROMPTS_DIR / "draft_verification_v1.txt"
GROUNDING_PROMPT_PATH = PROMPTS_DIR / "ui_grounding_execution_v1.txt"
STATE_CHECK_PROMPT_PATH = PROMPTS_DIR / "state_verification_v2.txt"
SEND_VERIFICATION_PROMPT_PATH = PROMPTS_DIR / "send_verification_v1.txt"

RESULTS_PATH = PROJECT_ROOT / "results" / "raw" / "rnd008_controlled_send_results.json"
REPORT_PATH = PROJECT_ROOT / "results" / "reports" / "rnd008_controlled_send_summary.md"
RAW_RESPONSES_DIR = PROJECT_ROOT / "results" / "raw" / "provider_responses" / "rnd008"
PENDING_PATH = PROJECT_ROOT / "results" / "raw" / "rnd008_pending.json"
PRICING_PATH = PROJECT_ROOT / "config" / "model_pricing.json"

# Source of the approved understanding + draft this stage reuses (does not
# re-run email understanding or reply generation — those were already
# human-approved in RND-007B).
RND007B_SOURCE_PATH = PROJECT_ROOT / "results" / "raw" / "rnd007b_contextual_reply_results_attempt2_retry1.json"

REQUIRE_FOREGROUND_SUBSTRING = "outlook"
DRAFT_STABILIZATION_DELAY_SECONDS = 1.5
POST_SEND_INITIAL_DELAY_SECONDS = 2.0
POST_SEND_RETRY_DELAY_SECONDS = 1.5
MAX_POST_SEND_VERIFICATION_ATTEMPTS = 2
MOVE_DURATION_SECONDS = 0.6
REPLY_EDITOR_EXPECTED_STATE = "The reply composer/editor is visibly open and ready for text entry."

# The ONLY target this module ever asks Gemini to locate, and the only
# coordinate it will ever move to or click. Deliberately its own
# single-entry constant — not a reuse or widening of RND-006B's
# CLICK_ALLOWED_TARGETS (which explicitly BLOCKS "send") — so no other
# stage's allowlist is silently expanded by this module existing.
SEND_GROUNDING_TARGET_DESCRIPTION = (
    "Send button (the main send action that submits this reply — "
    "NOT the small dropdown/options arrow beside it, and NOT Reply, "
    "Reply All, Forward, Delete, Discard, or Archive)"
)

# Human-annotated, RND-002 (OUTLOOK-005) ground truth — the only bounding
# box a Send click is ever allowed to land inside in this module.
SEND_GROUND_TRUTH_BBOX = BoundingBox(x1=800, y1=1022, x2=902, y2=1060)


def _provider() -> tuple[GeminiProvider, str]:
    load_dotenv(PROJECT_ROOT / ".env")
    model = os.environ.get("GEMINI_MODEL", "")
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key or not model:
        raise SystemExit("GEMINI_API_KEY / GEMINI_MODEL must be set in .env")
    timeout = float(os.environ.get("VISION_REQUEST_TIMEOUT_SECONDS", "30"))
    return GeminiProvider(api_key=api_key, model=model, timeout_seconds=timeout), model


def estimate_cost(provider: str, model: str, input_tokens: Optional[int], output_tokens: Optional[int]) -> Optional[float]:
    if not PRICING_PATH.exists() or input_tokens is None or output_tokens is None:
        return None
    pricing = json.loads(PRICING_PATH.read_text(encoding="utf-8"))
    entry = next((p for p in pricing.get("models", []) if p["provider"] == provider and p["model"] == model), None)
    if entry is None:
        return None
    return round(
        (input_tokens / 1_000_000) * entry["input_rate_per_million_tokens"]
        + (output_tokens / 1_000_000) * entry["output_rate_per_million_tokens"],
        6,
    )


def _accumulate(result: RND008Result, metrics: CallMetrics) -> None:
    """Every real provider call in this module goes through this — no
    helper call site is allowed to stay invisible from the final totals
    (the gap found and fixed in RND-007B's pre-RND-008 hardening)."""
    result.total_vision_calls += 1
    result.total_input_tokens += metrics.input_tokens or 0
    result.total_output_tokens += metrics.output_tokens or 0
    result.total_estimated_cost = round(result.total_estimated_cost + (metrics.estimated_cost or 0), 6)
    result.total_latency_ms = round(result.total_latency_ms + (metrics.latency_ms or 0), 3)


def save_pending(result: RND008Result) -> None:
    PENDING_PATH.parent.mkdir(parents=True, exist_ok=True)
    PENDING_PATH.write_text(result.model_dump_json(indent=2), encoding="utf-8")


def load_pending() -> RND008Result:
    if not PENDING_PATH.exists():
        raise SystemExit("No pending RND-008 session. Start with --show-email.")
    return RND008Result.model_validate(json.loads(PENDING_PATH.read_text(encoding="utf-8")))


def _save_raw_response(stage: str, model: str, raw_text: str, parsed_json) -> Path:
    RAW_RESPONSES_DIR.mkdir(parents=True, exist_ok=True)
    path = RAW_RESPONSES_DIR / f"{stage}_{datetime.now().isoformat().replace(':', '-')}.json"
    path.write_text(
        json.dumps({"provider": "gemini", "model": model, "raw_text": raw_text, "parsed_json": parsed_json}, indent=2),
        encoding="utf-8",
    )
    return path


def cmd_plan() -> None:
    print("=== RND-008 Controlled Send + Send Verification ===")
    print("Mode: PLAN ONLY. Gated flow:")
    print("  --show-email -> --approve-email -> --verify-draft -> --confirm-draft ->")
    print("  --ground-send -> --approve-send -> --execute-send -> --verify-send -> --confirm-send -> --finalize")
    print("Send is clicked at most once, only after the final immediate pre-click approval.")


def cmd_show_email() -> None:
    if not RND007B_SOURCE_PATH.exists():
        raise SystemExit(f"RND-007B source result not found at {RND007B_SOURCE_PATH}")
    prior = RND007BResult.model_validate(json.loads(RND007B_SOURCE_PATH.read_text(encoding="utf-8"))["case"])
    if not prior.human_understanding_approved or not prior.human_draft_approved or prior.result != "PASS":
        raise SystemExit("RND-007B source is not a fully human-approved PASS result — refusing to carry it forward.")

    result = RND008Result(
        # Subject and sender are the same real, previously human-confirmed
        # non-sensitive test email used throughout RND-002/006A/006B/007A/007B
        # ("Mail for project", test_cases/outlook/dataset_manifest.json
        # OUTLOOK-008/010) — not re-derived from a fresh AI call here, since
        # this exact identity has already been established and confirmed
        # multiple times. Sender line is as directly observed in RND-007B's
        # own screenshots (the email's own signature/header).
        email_sender="Yash Dhanraj <yashdhanraj9140@gmail.com> (as shown in the email's own header/signature)",
        email_subject="Mail for project",
        email_body_summary=prior.email_understanding.email_summary if prior.email_understanding else None,
        approved_draft=prior.reply_generation.draft_reply if prior.reply_generation else None,
    )
    save_pending(result)

    print("=== RND-008 --show-email ===")
    print(f"Sender:            {result.email_sender}")
    print(f"Subject:           {result.email_subject}")
    print(f"Body summary:      {result.email_body_summary}")
    print()
    print("Approved reply draft (unchanged, carried forward from RND-007B):")
    print("-" * 60)
    print(result.approved_draft)
    print("-" * 60)
    print()
    print("This is the same real, previously human-confirmed non-sensitive test")
    print("email reused across every prior live stage — no HR/client/confidential/")
    print("production/financial/personal-sensitive content.")
    print()
    print("Is this safe test email approved for the RND-008 send test? yes/no")
    print("Run --approve-email pass|fail to record the answer.")


def cmd_approve_email(verdict: str) -> None:
    result = load_pending()
    if result.approved_draft is None:
        raise SystemExit("No email/draft on record — run --show-email first.")
    result.human_email_approved = verdict == "pass"
    if verdict != "pass":
        result.result = "ABORTED"
        result.failure_reason = "OTHER"
        result.notes = "Human rejected the test email as unsafe for the RND-008 send test."
    save_pending(result)
    print(f"Email approval recorded: {result.human_email_approved}")
    if not result.human_email_approved:
        print("STOPPED — email was rejected. No further RND-008 action will occur.")


def _check_reply_editor_open(provider: GeminiProvider, image_path: Path, result: RND008Result) -> bool:
    prompt_text = STATE_CHECK_PROMPT_PATH.read_text(encoding="utf-8").format(
        action="(state check only, no action performed)", expected_state=REPLY_EDITOR_EXPECTED_STATE
    )
    call = provider.analyze_screen(image_path, "Check whether reply editor is open", prompt_text)
    metrics = CallMetrics(
        latency_ms=call.latency_ms, input_tokens=call.input_tokens, output_tokens=call.output_tokens,
        estimated_cost=estimate_cost("gemini", call.model, call.input_tokens, call.output_tokens),
    )
    _accumulate(result, metrics)
    if call.parsed_json is None:
        return False
    try:
        structured = VerificationResponseV2.model_validate(call.parsed_json)
    except ValidationError:
        return False
    return structured.verified


def cmd_verify_draft() -> None:
    result = load_pending()
    if not result.human_email_approved:
        raise SystemExit("Email is not approved. Run --approve-email pass first.")

    provider, model = _provider()
    print("=== RND-008 --verify-draft ===")

    title = get_foreground_window_title()
    print(f"Foreground window: {title!r}")
    result.foreground_before_capture = title
    if REQUIRE_FOREGROUND_SUBSTRING not in title.lower():
        result.result = "ABORTED"
        result.failure_reason = "FOREGROUND_MISMATCH"
        result.abort_stage = "verify_draft"
        result.notes = "Outlook is not foreground. No draft-verification capture or call made."
        save_pending(result)
        print("STOP: Outlook is not foreground.")
        return

    try:
        capture = capture_screen(DEFAULT_OUTPUT_DIR)
    except ScreenCaptureError as exc:
        print(f"Capture FAILED: {exc}")
        return
    result.pre_send_screenshot = capture.filename

    editor_open = _check_reply_editor_open(provider, Path(capture.path), result)
    print(f"Reply editor open: {editor_open}")
    if not editor_open:
        result.result = "ABORTED"
        result.failure_reason = "OTHER"
        result.abort_stage = "verify_draft"
        result.notes = "Reply editor is not open — the approved draft is not confirmed present. STOP."
        save_pending(result)
        print("STOP: reply editor is not open. Nothing to verify or send.")
        return

    expected_draft = result.approved_draft
    result.expected_draft = expected_draft
    prompt_text = DRAFT_VERIFICATION_PROMPT_PATH.read_text(encoding="utf-8").format(expected_draft=expected_draft)
    try:
        call = provider.analyze_screen(Path(capture.path), "Verify draft before Send", prompt_text)
    except VisionProviderError as exc:
        result.result = "ERROR"
        result.failure_reason = "MODEL_ERROR" if "Network" not in str(exc) else "NETWORK_ERROR"
        result.notes = str(exc)
        save_pending(result)
        print(f"Draft-verification call FAILED: {exc}")
        return

    raw_path = _save_raw_response("draft_verification", call.model, call.raw_text, call.parsed_json)
    metrics = CallMetrics(
        latency_ms=call.latency_ms, input_tokens=call.input_tokens, output_tokens=call.output_tokens,
        estimated_cost=estimate_cost("gemini", call.model, call.input_tokens, call.output_tokens),
    )
    _accumulate(result, metrics)
    result.draft_verification_metrics = metrics

    try:
        structured = DraftVerificationResponse.model_validate(call.parsed_json) if call.parsed_json else None
    except ValidationError as exc:
        print(f"Schema validation FAILED: {exc}. See {raw_path}.")
        structured = None

    if structured:
        result.detected_draft = structured.detected_draft
        result.vision_match = structured.semantic_match
        print(f"Vision match: {structured.semantic_match} (confidence {structured.confidence})")
        print(f"Detected draft: {structured.detected_draft!r}")
        if not structured.semantic_match:
            print()
            print("Vision says the draft looks incomplete/mismatched. This does NOT")
            print("send automatically and does NOT block a human PASS — please inspect")
            print("Outlook directly before answering --confirm-draft.")

    save_pending(result)
    print()
    print("Human approval is mandatory before Send regardless of the Vision result.")
    print("Run --confirm-draft pass|fail.")


def cmd_confirm_draft(verdict: str) -> None:
    result = load_pending()
    if result.expected_draft is None:
        raise SystemExit("No draft verification on record — run --verify-draft first.")
    result.human_draft_confirmed = verdict == "pass"
    if verdict != "pass":
        result.result = "ABORTED"
        result.failure_reason = "OTHER"
        result.abort_stage = "confirm_draft"
        result.notes = "Human did not confirm the draft before Send. STOP."
    save_pending(result)
    print(f"Draft confirmation recorded: {result.human_draft_confirmed}")
    if not result.human_draft_confirmed:
        print("STOPPED — draft was not confirmed. Send grounding will not be attempted.")


def cmd_ground_send() -> None:
    result = load_pending()
    if not result.human_draft_confirmed:
        raise SystemExit("Draft is not confirmed. Run --confirm-draft pass first.")

    provider, model = _provider()
    print("=== RND-008 --ground-send ===")

    title = get_foreground_window_title()
    print(f"Foreground window: {title!r}")
    result.foreground_before_grounding = title
    if REQUIRE_FOREGROUND_SUBSTRING not in title.lower():
        result.result = "ABORTED"
        result.failure_reason = "FOREGROUND_MISMATCH"
        result.abort_stage = "ground_send"
        save_pending(result)
        print("STOP: Outlook is not foreground. No grounding capture made.")
        return

    try:
        capture = capture_screen(DEFAULT_OUTPUT_DIR)
    except ScreenCaptureError as exc:
        print(f"Capture FAILED: {exc}")
        return
    result.grounding_screenshot = capture.filename
    result.screen_width = capture.width
    result.screen_height = capture.height

    prompt_text = GROUNDING_PROMPT_PATH.read_text(encoding="utf-8").format(
        width=capture.width, height=capture.height, target=SEND_GROUNDING_TARGET_DESCRIPTION
    )
    try:
        call = provider.analyze_screen(Path(capture.path), "Locate the Send button only", prompt_text)
    except VisionProviderError as exc:
        result.result = "ERROR"
        result.failure_reason = "MODEL_ERROR" if "Network" not in str(exc) else "NETWORK_ERROR"
        result.notes = str(exc)
        save_pending(result)
        print(f"Grounding call FAILED: {exc}")
        return

    raw_path = _save_raw_response("send_grounding", call.model, call.raw_text, call.parsed_json)
    metrics = CallMetrics(
        latency_ms=call.latency_ms, input_tokens=call.input_tokens, output_tokens=call.output_tokens,
        estimated_cost=estimate_cost("gemini", call.model, call.input_tokens, call.output_tokens),
    )
    _accumulate(result, metrics)
    result.grounding_metrics = metrics

    try:
        structured = GroundingResponse.model_validate(call.parsed_json) if call.parsed_json else None
    except ValidationError as exc:
        print(f"Schema validation FAILED: {exc}. See {raw_path}.")
        structured = None

    blocked = {"reply", "reply all", "reply_all", "forward", "delete", "discard", "archive"}
    if structured is None or structured.target.strip().lower() in blocked:
        result.result = "ABORTED"
        result.failure_reason = "OTHER"
        result.abort_stage = "ground_send"
        result.notes = "Grounding failed or returned a blocked/unexpected target. STOP — no Send target recorded."
        save_pending(result)
        print("ABORTED: grounding failed or returned a blocked target.")
        return

    result.grounding_target_raw = structured.target
    result.raw_x = structured.x
    result.raw_y = structured.y
    result.grounding_confidence = structured.confidence
    result.grounding_reason = structured.reason

    cx, cy = normalize_1000_to_pixels(structured.x, structured.y, capture.width, capture.height)
    cx, cy = round(cx), round(cy)
    result.converted_x = cx
    result.converted_y = cy
    result.ground_truth_bbox = f"({SEND_GROUND_TRUTH_BBOX.x1},{SEND_GROUND_TRUTH_BBOX.y1})-({SEND_GROUND_TRUTH_BBOX.x2},{SEND_GROUND_TRUTH_BBOX.y2})"
    result.coordinate_in_screen_bounds = coordinate_in_image_bounds(cx, cy, capture.width, capture.height)
    result.coordinate_inside_bbox = point_in_bbox(cx, cy, SEND_GROUND_TRUTH_BBOX)

    print(f"Raw Gemini coordinate:       ({structured.x}, {structured.y})  [0-1000 normalized]")
    print(f"Converted pixel coordinate:  ({cx}, {cy})")
    print(f"Send ground-truth bbox:      {result.ground_truth_bbox}")
    print(f"Inside known Send bbox:      {result.coordinate_inside_bbox}")
    print(f"Inside screen bounds:        {result.coordinate_in_screen_bounds}")
    print(f"Foreground window:           {title!r}")
    print(f"Screen resolution:           {capture.width}x{capture.height}")

    if not result.coordinate_inside_bbox or not result.coordinate_in_screen_bounds:
        result.result = "ABORTED"
        result.failure_reason = "OTHER"
        result.abort_stage = "ground_send"
        result.notes = "Converted Send coordinate is outside the known Send bounding box. DO NOT SEND. STOP."
        save_pending(result)
        print()
        print("ABORTED: converted coordinate is outside the known Send bbox. DO NOT SEND.")
        return

    save_pending(result)
    print()
    print("Grounding validated. Awaiting the final immediate pre-click approval.")
    print("Run --approve-send pass|fail.")


def cmd_approve_send(verdict: str) -> None:
    result = load_pending()
    if result.converted_x is None or result.converted_y is None:
        raise SystemExit("No validated Send grounding on record — run --ground-send first.")
    if verdict == "pass" and not result.coordinate_inside_bbox:
        raise SystemExit("Refusing to approve Send: coordinate is outside the known Send bbox.")
    result.human_send_approved = verdict == "pass"
    result.approval_timestamp = datetime.now().isoformat()
    if verdict != "pass":
        result.result = "ABORTED"
        result.failure_reason = "OTHER"
        result.abort_stage = "approve_send"
        result.notes = "Human did not give final approval for the Send click. STOP."
    save_pending(result)
    print(f"Send approval recorded: {result.human_send_approved} at {result.approval_timestamp}")
    if not result.human_send_approved:
        print("STOPPED — Send was not approved. Nothing will be clicked.")


def cmd_execute_send() -> None:
    result = load_pending()
    if not result.human_send_approved:
        raise SystemExit("Send is not approved. Run --approve-send pass first.")
    if result.send_click_executed:
        raise SystemExit("Send has already been executed for this session. A second Send click is never permitted.")

    print("=== RND-008 --execute-send ===")

    title_move = get_foreground_window_title()
    result.foreground_before_move = title_move
    if REQUIRE_FOREGROUND_SUBSTRING not in title_move.lower():
        result.send_click_result = "ABORTED"
        result.result = "ABORTED"
        result.failure_reason = "FOREGROUND_MISMATCH"
        result.abort_stage = "execute_send_before_move"
        result.send_was_executed_at_abort = False
        save_pending(result)
        print("ABORTED: Outlook is not foreground before move. No movement, no click.")
        return

    assert pyautogui.FAILSAFE is True
    pyautogui.moveTo(result.converted_x, result.converted_y, duration=MOVE_DURATION_SECONDS)

    title_click = get_foreground_window_title()
    result.foreground_before_click = title_click
    if REQUIRE_FOREGROUND_SUBSTRING not in title_click.lower():
        result.send_click_result = "ABORTED"
        result.result = "ABORTED"
        result.failure_reason = "FOREGROUND_MISMATCH"
        result.abort_stage = "execute_send_before_click"
        result.send_was_executed_at_abort = False
        result.notes = "Foreground changed between move and click. Cursor moved but NO CLICK performed."
        save_pending(result)
        print("ABORTED: foreground changed after move. NO CLICK performed.")
        return

    pyautogui.click()  # single Send click only — the only click in this module
    result.send_click_executed = True
    result.send_click_timestamp = datetime.now().isoformat()
    result.send_click_result = "PASS"
    save_pending(result)  # persist immediately
    print(f"Send clicked once at ({result.converted_x}, {result.converted_y}) at {result.send_click_timestamp}.")
    print("Run --verify-send next. Send will NEVER be clicked again in this session.")


def cmd_verify_send() -> None:
    result = load_pending()
    if not result.send_click_executed:
        raise SystemExit("Send has not been clicked yet. Run --execute-send first.")
    if len(result.post_send_attempts) >= MAX_POST_SEND_VERIFICATION_ATTEMPTS:
        raise SystemExit(f"Maximum post-send verification attempts ({MAX_POST_SEND_VERIFICATION_ATTEMPTS}) already reached.")

    attempt_number = len(result.post_send_attempts) + 1
    delay = POST_SEND_INITIAL_DELAY_SECONDS if attempt_number == 1 else POST_SEND_RETRY_DELAY_SECONDS
    print(f"=== RND-008 --verify-send (attempt {attempt_number}/{MAX_POST_SEND_VERIFICATION_ATTEMPTS}, waiting {delay}s) ===")
    time.sleep(delay)

    provider, model = _provider()
    try:
        capture = capture_screen(DEFAULT_OUTPUT_DIR)
    except ScreenCaptureError as exc:
        attempt = PostSendVerificationAttempt(
            attempt_number=attempt_number, delay_seconds=delay, timestamp=datetime.now().isoformat(), error=str(exc)
        )
        result.post_send_attempts.append(attempt)
        save_pending(result)
        print(f"Post-send capture FAILED: {exc}")
        return

    prompt_text = SEND_VERIFICATION_PROMPT_PATH.read_text(encoding="utf-8")
    attempt = PostSendVerificationAttempt(
        attempt_number=attempt_number, delay_seconds=delay,
        timestamp=datetime.now().isoformat(), screenshot=capture.filename,
    )
    try:
        call = provider.analyze_screen(Path(capture.path), "Verify the reply was sent", prompt_text)
    except VisionProviderError as exc:
        attempt.error = str(exc)
        result.post_send_attempts.append(attempt)
        save_pending(result)
        print(f"Send-verification call FAILED: {exc}. Human confirmation will be authoritative regardless.")
        return

    raw_path = _save_raw_response("send_verification", call.model, call.raw_text, call.parsed_json)
    metrics = CallMetrics(
        latency_ms=call.latency_ms, input_tokens=call.input_tokens, output_tokens=call.output_tokens,
        estimated_cost=estimate_cost("gemini", call.model, call.input_tokens, call.output_tokens),
    )
    _accumulate(result, metrics)
    attempt.metrics = metrics

    try:
        structured = SendVerificationResponse.model_validate(call.parsed_json) if call.parsed_json else None
    except ValidationError as exc:
        print(f"Schema validation FAILED: {exc}. See {raw_path}.")
        structured = None

    if structured:
        attempt.schema_valid = True
        attempt.verified_sent = structured.verified_sent
        attempt.detected_state = structured.detected_state
        attempt.visual_evidence = structured.visual_evidence
        attempt.confidence = structured.confidence
        attempt.reason = structured.reason
        print(f"verified_sent={structured.verified_sent} confidence={structured.confidence}")
        print(f"detected_state: {structured.detected_state!r}")
        print(f"visual_evidence: {structured.visual_evidence}")

    result.post_send_attempts.append(attempt)

    any_verified = any(a.verified_sent for a in result.post_send_attempts)
    if any_verified:
        result.ai_send_verification = True
        result.ai_send_verification_classification = (
            "AI_VERIFIED_FIRST_ATTEMPT" if result.post_send_attempts[0].verified_sent else "AI_VERIFIED_AFTER_STABILIZATION"
        )
    elif attempt_number >= MAX_POST_SEND_VERIFICATION_ATTEMPTS:
        result.ai_send_verification = False
        result.ai_send_verification_classification = "AI_NOT_VERIFIED"

    save_pending(result)
    print()
    if any_verified:
        print("AI verification PASSED. Run --confirm-send pass|fail for human confirmation.")
    elif attempt_number < MAX_POST_SEND_VERIFICATION_ATTEMPTS:
        print(f"AI did not verify send on attempt {attempt_number}. NOT re-clicking Send.")
        print("Run --verify-send again for the final retry, or proceed directly to --confirm-send.")
    else:
        print("AI did not verify send after the maximum attempts. NOT re-clicking Send.")
        print("Run --confirm-send pass|fail — human confirmation is authoritative.")


def cmd_confirm_send(verdict: str) -> None:
    result = load_pending()
    if not result.send_click_executed:
        raise SystemExit("Send has not been clicked — nothing to confirm.")
    if not result.post_send_attempts:
        raise SystemExit("No post-send verification attempt on record. Run --verify-send at least once first.")

    result.human_send_verification = verdict == "pass"

    if not result.human_send_verification:
        result.final_classification = "SEND_NOT_CONFIRMED"
        result.result = "FAIL"
    elif result.ai_send_verification_classification == "AI_VERIFIED_FIRST_ATTEMPT":
        result.final_classification = "SEND_CONFIRMED"
        result.result = "PASS"
    elif result.ai_send_verification_classification == "AI_VERIFIED_AFTER_STABILIZATION":
        result.final_classification = "SEND_VERIFIED_AFTER_STABILIZATION"
        result.result = "PASS"
    else:
        # ai_send_verification is False/None (never confirmed by Vision),
        # but the human — authoritative — confirmed the send succeeded.
        result.final_classification = "SEND_SUCCEEDED_AI_FALSE_NEGATIVE"
        result.result = "PASS"

    save_pending(result)
    print(f"Human send verification recorded: {result.human_send_verification}")
    print(f"Final classification: {result.final_classification}")
    print("Run --finalize to write the result/report files.")


def cmd_finalize() -> None:
    result = load_pending()
    if result.human_send_verification is None:
        raise SystemExit("Run --confirm-send first.")

    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(
        json.dumps({"timestamp": datetime.now().isoformat(), "case": result.model_dump(mode="json")}, indent=2),
        encoding="utf-8",
    )
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(_build_report(result), encoding="utf-8")

    PENDING_PATH.unlink()
    print(f"Finalized. Overall result: {result.result}  ({result.final_classification})")
    print(f"Written: {RESULTS_PATH.relative_to(PROJECT_ROOT)}, {REPORT_PATH.relative_to(PROJECT_ROOT)}")


def _build_report(result: RND008Result) -> str:
    lines = ["# RND-008 Controlled Send + Send Verification Summary — Gemini (gemini-3.6-flash)", ""]
    lines.append(f"Overall result: {result.result}")
    lines.append(f"Final classification: {result.final_classification}")
    lines.append("")
    lines.append("## Test email")
    lines.append(f"- Sender: {result.email_sender}")
    lines.append(f"- Subject: {result.email_subject}")
    lines.append(f"- Human email approved: {result.human_email_approved}")
    lines.append("")
    lines.append("## Draft verification")
    lines.append(f"- vision_match: {result.vision_match}")
    lines.append(f"- human_draft_confirmed: {result.human_draft_confirmed}")
    lines.append("")
    lines.append("## Send grounding")
    lines.append(f"- raw: ({result.raw_x}, {result.raw_y})")
    lines.append(f"- converted: ({result.converted_x}, {result.converted_y})")
    lines.append(f"- ground-truth bbox: {result.ground_truth_bbox}")
    lines.append(f"- coordinate_inside_bbox: {result.coordinate_inside_bbox}")
    lines.append("")
    lines.append("## Send execution")
    lines.append(f"- human_send_approved: {result.human_send_approved} at {result.approval_timestamp}")
    lines.append(f"- send_click_result: {result.send_click_result}")
    lines.append(f"- send_click_timestamp: {result.send_click_timestamp}")
    lines.append("")
    lines.append("## Post-send verification")
    for a in result.post_send_attempts:
        lines.append(f"- attempt {a.attempt_number}: verified_sent={a.verified_sent} confidence={a.confidence} detected_state={a.detected_state!r}")
    lines.append(f"- ai_send_verification: {result.ai_send_verification} ({result.ai_send_verification_classification})")
    lines.append(f"- human_send_verification: {result.human_send_verification}")
    lines.append("")
    lines.append(f"Total vision calls: {result.total_vision_calls}")
    lines.append(f"Total input tokens: {result.total_input_tokens}")
    lines.append(f"Total output tokens: {result.total_output_tokens}")
    lines.append(f"Total estimated cost: ${result.total_estimated_cost}")
    lines.append(f"Total latency: {result.total_latency_ms} ms")
    if result.abort_stage:
        lines.append("")
        lines.append(f"Abort stage: {result.abort_stage}")
        lines.append(f"Failure reason: {result.failure_reason}")
        lines.append(f"Notes: {result.notes}")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="RND-008 controlled Send + Send verification")
    parser.add_argument("--show-email", action="store_true")
    parser.add_argument("--approve-email", choices=["pass", "fail"])
    parser.add_argument("--verify-draft", action="store_true")
    parser.add_argument("--confirm-draft", choices=["pass", "fail"])
    parser.add_argument("--ground-send", action="store_true")
    parser.add_argument("--approve-send", choices=["pass", "fail"])
    parser.add_argument("--execute-send", action="store_true")
    parser.add_argument("--verify-send", action="store_true")
    parser.add_argument("--confirm-send", choices=["pass", "fail"])
    parser.add_argument("--finalize", action="store_true")
    args = parser.parse_args()

    if args.finalize:
        cmd_finalize()
    elif args.confirm_send:
        cmd_confirm_send(args.confirm_send)
    elif args.verify_send:
        cmd_verify_send()
    elif args.execute_send:
        cmd_execute_send()
    elif args.approve_send:
        cmd_approve_send(args.approve_send)
    elif args.ground_send:
        cmd_ground_send()
    elif args.confirm_draft:
        cmd_confirm_draft(args.confirm_draft)
    elif args.verify_draft:
        cmd_verify_draft()
    elif args.approve_email:
        cmd_approve_email(args.approve_email)
    elif args.show_email:
        cmd_show_email()
    else:
        cmd_plan()


if __name__ == "__main__":
    main()
