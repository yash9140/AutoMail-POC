"""RND-007B — Contextual Reply Generation + Draft Entry.

Determines whether Gemini can understand an opened Outlook email, draft
an appropriate reply, type it into the reply editor, and have that draft
verified — stopping BEFORE Send. Five metrics are kept separate, never
collapsed: (A) email understanding, (B) reply generation quality,
(C) text-entry execution, (D) vision draft verification, (E) human
verification.

Gated flow, one command per stage, each requiring the previous stage's
explicit human approval before it will run:

    --understand              fresh screenshot + foreground check + Gemini email understanding
    --approve-understanding {pass,fail}
    --generate                Gemini reply generation, using the approved understanding
    --approve-draft {pass,fail}
    --type                    checks whether the reply editor is already open (no unnecessary
                               re-click, per the RND-004 "click Reply" bias finding); clicks
                               Reply only if not already open; types the approved draft ONCE
    --verify                  wait 1.5s, fresh screenshot, Gemini draft verification
    --confirm {pass,fail}     records human final confirmation (authoritative over AI)
    --quality --relevant {pass,fail} --accurate ... --complete {pass,fail}
                               records human PASS/FAIL per reply-quality criterion, finalizes
    --finalize                finalizes a non-PASS attempt (FAIL/ABORTED/ERROR) without
                               requiring reply-quality scoring

    --attempt {1,2}           suffixes result/report/pending files so retries never
                               overwrite a prior attempt (used from RND-007B Attempt 2 on)
    --retry N                 within an attempt, further suffixes files by retry number
                               (e.g. --attempt 2 --retry 1 for "Attempt 2 Retry 1")
    --seed-from-attempt {1,2} carries the already-approved email understanding + draft
                               forward from a finalized attempt into a fresh pending session

Send is never clicked, never enabled, and no send-hotkey (Ctrl+Enter,
Alt+S) is sent anywhere in this module. The Enter key is pressed ONLY as
the explicit line-break mechanism inside the multiline typing loop
(RND-007B Attempt 2 correction) — never as a submit/shortcut key.

Run (from outlook-vision-poc/):
    python rnd/experiments/rnd007b_contextual_reply.py --understand
    python rnd/experiments/rnd007b_contextual_reply.py --approve-understanding pass
    python rnd/experiments/rnd007b_contextual_reply.py --generate
    python rnd/experiments/rnd007b_contextual_reply.py --approve-draft pass
    python rnd/experiments/rnd007b_contextual_reply.py --type
    python rnd/experiments/rnd007b_contextual_reply.py --verify
    python rnd/experiments/rnd007b_contextual_reply.py --confirm pass
    python rnd/experiments/rnd007b_contextual_reply.py --quality --relevant pass --accurate pass --no-hallucination pass --professional pass --concise pass --complete pass
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
from rnd.metrics.grounding import coordinate_in_image_bounds  # noqa: E402
from rnd.models.click_execution import CLICK_ALLOWED_TARGETS, VerificationResponseV2  # noqa: E402
from rnd.models.contextual_reply import (  # noqa: E402
    CallMetrics,
    DraftVerificationResponse,
    EmailUnderstandingResponse,
    RND007BResult,
    ReplyGenerationResponse,
)
from rnd.models.grounding import GroundingResponse  # noqa: E402
from rnd.providers.base import VisionProviderError  # noqa: E402
from rnd.providers.gemini_provider import GeminiProvider  # noqa: E402

PROMPTS_DIR = PROJECT_ROOT / "rnd" / "prompts"
EMAIL_UNDERSTANDING_PROMPT_PATH = PROMPTS_DIR / "email_understanding_v1.txt"
REPLY_GENERATION_PROMPT_PATH = PROMPTS_DIR / "reply_generation_v1.txt"
DRAFT_VERIFICATION_PROMPT_PATH = PROMPTS_DIR / "draft_verification_v1.txt"
GROUNDING_PROMPT_PATH = PROMPTS_DIR / "ui_grounding_execution_v1.txt"
STATE_CHECK_PROMPT_PATH = PROMPTS_DIR / "state_verification_v2.txt"

RESULTS_PATH = PROJECT_ROOT / "results" / "raw" / "rnd007b_contextual_reply_results.json"
REPORT_PATH = PROJECT_ROOT / "results" / "reports" / "rnd007b_contextual_reply_summary.md"
RAW_RESPONSES_DIR = PROJECT_ROOT / "results" / "raw" / "provider_responses" / "rnd007b"
PENDING_PATH = PROJECT_ROOT / "results" / "raw" / "rnd007b_pending.json"
PRICING_PATH = PROJECT_ROOT / "config" / "model_pricing.json"

REQUIRE_FOREGROUND_SUBSTRING = "outlook"
STABILIZATION_DELAY_SECONDS = 1.5
TYPE_INTERVAL_SECONDS = 0.03
MOVE_DURATION_SECONDS = 0.6
FOCUS_SETTLE_DELAY_SECONDS = 0.6
REPLY_EDITOR_EXPECTED_STATE = "The reply composer/editor is visibly open and ready for text entry."


def _resolve_paths(attempt: Optional[str], retry: Optional[int] = None) -> None:
    """Suffix RESULTS_PATH/REPORT_PATH/PENDING_PATH by attempt (and, within
    an attempt, by retry) so retries — e.g. RND-007B Attempt 1, Attempt 2,
    Attempt 2 Retry 1 — never overwrite each other's evidence.
    """
    global PENDING_PATH, RESULTS_PATH, REPORT_PATH
    if not attempt:
        return
    suffix = f"_attempt{attempt}"
    if retry:
        suffix += f"_retry{retry}"
    PENDING_PATH = PROJECT_ROOT / "results" / "raw" / f"rnd007b_pending{suffix}.json"
    RESULTS_PATH = PROJECT_ROOT / "results" / "raw" / f"rnd007b_contextual_reply_results{suffix}.json"
    REPORT_PATH = PROJECT_ROOT / "results" / "reports" / f"rnd007b_contextual_reply_summary{suffix}.md"


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


def _accumulate(result: RND007BResult, metrics: CallMetrics) -> None:
    """Every real provider call must go through this — it is the single
    place totals are updated, so no call site can silently stay invisible
    from the final R&D totals (a gap found and fixed for
    _check_reply_editor_open's call — see its own docstring)."""
    result.total_vision_calls += 1
    result.total_input_tokens += metrics.input_tokens or 0
    result.total_output_tokens += metrics.output_tokens or 0
    result.total_estimated_cost = round(result.total_estimated_cost + (metrics.estimated_cost or 0), 6)
    result.total_latency_ms = round(result.total_latency_ms + (metrics.latency_ms or 0), 3)


def save_pending(result: RND007BResult) -> None:
    PENDING_PATH.parent.mkdir(parents=True, exist_ok=True)
    PENDING_PATH.write_text(result.model_dump_json(indent=2), encoding="utf-8")


def load_pending() -> RND007BResult:
    if not PENDING_PATH.exists():
        raise SystemExit("No pending RND-007B session. Start with --understand.")
    return RND007BResult.model_validate(json.loads(PENDING_PATH.read_text(encoding="utf-8")))


def _save_raw_response(stage: str, model: str, raw_text: str, parsed_json) -> Path:
    RAW_RESPONSES_DIR.mkdir(parents=True, exist_ok=True)
    path = RAW_RESPONSES_DIR / f"{stage}_{datetime.now().isoformat().replace(':', '-')}.json"
    path.write_text(
        json.dumps({"provider": "gemini", "model": model, "raw_text": raw_text, "parsed_json": parsed_json}, indent=2),
        encoding="utf-8",
    )
    return path


def cmd_plan() -> None:
    print("=== RND-007B Contextual Reply Generation + Draft Entry ===")
    print("Mode: PLAN ONLY. Gated flow:")
    print("  --understand -> --approve-understanding -> --generate -> --approve-draft -> --type -> --verify -> --confirm -> --quality")
    print("Send is never reachable at any point in this module.")


def cmd_understand() -> None:
    provider, model = _provider()
    print("=== RND-007B --understand ===")
    title = get_foreground_window_title()
    print(f"Foreground window: {title!r}")

    result = RND007BResult(foreground_before=title)
    if REQUIRE_FOREGROUND_SUBSTRING not in title.lower():
        result.result = "ABORTED"
        result.failure_reason = "FOREGROUND_MISMATCH"
        result.notes = "Foreground window did not contain 'outlook'."
        save_pending(result)
        print("ABORTED: Outlook is not the foreground window.")
        return

    try:
        capture = capture_screen(DEFAULT_OUTPUT_DIR)
    except ScreenCaptureError as exc:
        print(f"Capture FAILED: {exc}")
        return
    result.pre_screenshot = capture.filename
    print(f"Captured screenshot: {capture.filename}")

    prompt_text = EMAIL_UNDERSTANDING_PROMPT_PATH.read_text(encoding="utf-8")
    try:
        call = provider.analyze_screen(Path(capture.path), "Understand the open email", prompt_text)
    except VisionProviderError as exc:
        result.result = "ERROR"
        result.failure_reason = "MODEL_ERROR" if "Network" not in str(exc) else "NETWORK_ERROR"
        result.notes = str(exc)
        save_pending(result)
        print(f"Email-understanding call FAILED: {exc}")
        return

    raw_path = _save_raw_response("understanding", call.model, call.raw_text, call.parsed_json)
    metrics = CallMetrics(
        latency_ms=call.latency_ms, input_tokens=call.input_tokens, output_tokens=call.output_tokens,
        estimated_cost=estimate_cost("gemini", call.model, call.input_tokens, call.output_tokens),
    )
    _accumulate(result, metrics)
    result.email_understanding_metrics = metrics

    try:
        structured = EmailUnderstandingResponse.model_validate(call.parsed_json) if call.parsed_json else None
    except ValidationError as exc:
        print(f"Schema validation FAILED: {exc}")
        structured = None

    if structured is None:
        result.result = "ERROR"
        result.failure_reason = "MODEL_ERROR"
        result.notes = f"Email understanding response was not schema-valid. See {raw_path}."
        save_pending(result)
        print("No valid understanding returned.")
        return

    result.email_understanding = structured
    save_pending(result)

    print()
    print(f"Email summary:     {structured.email_summary}")
    print(f"Sender intent:     {structured.sender_intent}")
    print(f"Requires reply:    {structured.requires_reply}")
    print(f"Requested action:  {structured.requested_action}")
    print(f"Important points:  {structured.important_points}")
    print(f"Confidence:        {structured.confidence}")
    print()
    print("Awaiting human review. Run --approve-understanding pass|fail.")


def cmd_approve_understanding(verdict: str) -> None:
    result = load_pending()
    if result.email_understanding is None:
        raise SystemExit("No email understanding to approve — run --understand first.")
    result.human_understanding_approved = verdict == "pass"
    if verdict != "pass":
        result.result = "ABORTED"
        result.failure_reason = "OTHER"
        result.notes = "Human rejected email understanding. Reply generation was not attempted."
    save_pending(result)
    print(f"Understanding approval recorded: {result.human_understanding_approved}")
    if not result.human_understanding_approved:
        print("STOPPED — understanding was rejected. No reply will be generated.")


def cmd_generate() -> None:
    result = load_pending()
    if not result.human_understanding_approved:
        raise SystemExit("Email understanding is not approved. Run --approve-understanding pass first.")
    if not result.pre_screenshot:
        raise SystemExit("No screenshot on record — internal state error.")

    provider, model = _provider()
    print("=== RND-007B --generate ===")

    image_path = DEFAULT_OUTPUT_DIR / result.pre_screenshot
    u = result.email_understanding
    prompt_text = REPLY_GENERATION_PROMPT_PATH.read_text(encoding="utf-8").format(
        email_summary=u.email_summary, sender_intent=u.sender_intent,
        requested_action=u.requested_action, important_points=u.important_points,
    )
    try:
        call = provider.analyze_screen(image_path, "Draft a reply", prompt_text)
    except VisionProviderError as exc:
        result.result = "ERROR"
        result.failure_reason = "MODEL_ERROR" if "Network" not in str(exc) else "NETWORK_ERROR"
        result.notes = str(exc)
        save_pending(result)
        print(f"Reply generation call FAILED: {exc}")
        return

    raw_path = _save_raw_response("generation", call.model, call.raw_text, call.parsed_json)
    metrics = CallMetrics(
        latency_ms=call.latency_ms, input_tokens=call.input_tokens, output_tokens=call.output_tokens,
        estimated_cost=estimate_cost("gemini", call.model, call.input_tokens, call.output_tokens),
    )
    _accumulate(result, metrics)
    result.reply_generation_metrics = metrics

    try:
        structured = ReplyGenerationResponse.model_validate(call.parsed_json) if call.parsed_json else None
    except ValidationError as exc:
        print(f"Schema validation FAILED: {exc}")
        structured = None

    if structured is None:
        result.result = "ERROR"
        result.failure_reason = "MODEL_ERROR"
        result.notes = f"Reply generation response was not schema-valid. See {raw_path}."
        save_pending(result)
        print("No valid draft returned.")
        return

    result.reply_generation = structured
    save_pending(result)

    print()
    print("Generated draft (exactly as it will be typed):")
    print("-" * 60)
    print(structured.draft_reply)
    print("-" * 60)
    print(f"Reasoning: {structured.reasoning_summary}")
    print(f"Confidence: {structured.confidence}")
    print()
    print("Awaiting human review. Run --approve-draft pass|fail.")


def cmd_approve_draft(verdict: str) -> None:
    result = load_pending()
    if result.reply_generation is None:
        raise SystemExit("No draft to approve — run --generate first.")
    result.human_draft_approved = verdict == "pass"
    if verdict != "pass":
        result.result = "ABORTED"
        result.failure_reason = "OTHER"
        result.notes = "Human rejected the generated draft. Typing was not attempted."
    save_pending(result)
    print(f"Draft approval recorded: {result.human_draft_approved}")
    if not result.human_draft_approved:
        print("STOPPED — draft was rejected. Nothing was typed.")


def _check_reply_editor_open(provider: GeminiProvider, image_path: Path, result: RND007BResult) -> bool:
    """Every real provider call must contribute to the running totals —
    this helper makes its own Gemini call and, before this fix, was the
    one call site in the module whose cost/tokens/latency never reached
    total_vision_calls/total_input_tokens/total_output_tokens/
    total_estimated_cost (a documented undercount discovered while
    writing up RND-007B). Accumulated here now, same as every other call.
    """
    prompt_text = STATE_CHECK_PROMPT_PATH.read_text(encoding="utf-8").format(
        action="(state check only, no action performed)", expected_state=REPLY_EDITOR_EXPECTED_STATE
    )
    call = provider.analyze_screen(image_path, "Check whether reply editor is open", prompt_text)
    metrics = CallMetrics(
        latency_ms=call.latency_ms, input_tokens=call.input_tokens, output_tokens=call.output_tokens,
        estimated_cost=estimate_cost("gemini", call.model, call.input_tokens, call.output_tokens),
    )
    result.reply_editor_state_check_metrics = metrics
    _accumulate(result, metrics)
    if call.parsed_json is None:
        return False
    try:
        structured = VerificationResponseV2.model_validate(call.parsed_json)
    except ValidationError:
        return False
    return structured.verified


def cmd_type() -> None:
    result = load_pending()
    if not result.human_draft_approved:
        raise SystemExit("Draft is not approved. Run --approve-draft pass first.")

    provider, model = _provider()
    print("=== RND-007B --type ===")

    title = get_foreground_window_title()
    print(f"Foreground window: {title!r}")
    if REQUIRE_FOREGROUND_SUBSTRING not in title.lower():
        result.typing_result = "ABORTED"
        result.result = "ABORTED"
        result.failure_reason = "FOREGROUND_MISMATCH"
        result.notes = "Foreground changed away from Outlook before typing."
        save_pending(result)
        print("ABORTED: Outlook is not foreground. No click, no typing.")
        return

    try:
        state_capture = capture_screen(DEFAULT_OUTPUT_DIR)
    except ScreenCaptureError as exc:
        print(f"Capture FAILED: {exc}")
        return

    already_open = _check_reply_editor_open(provider, Path(state_capture.path), result)
    print(f"Reply editor already open: {already_open}")

    if already_open:
        result.reply_editor_state_before_typing = "already_open"
        print("Skipping Reply click — editor already open (avoids the RND-004 'repeated click Reply' bias).")
    else:
        result.reply_editor_state_before_typing = "clicked_reply"
        print("Reply editor not open — grounding + clicking Reply once.")

        prompt_text = GROUNDING_PROMPT_PATH.read_text(encoding="utf-8").format(
            width=state_capture.width, height=state_capture.height, target=CLICK_ALLOWED_TARGETS["reply"]
        )
        try:
            g_call = provider.analyze_screen(Path(state_capture.path), "Locate the Reply button", prompt_text)
        except VisionProviderError as exc:
            result.typing_result = "ERROR"
            result.result = "ERROR"
            result.notes = f"Reply-grounding call failed: {exc}"
            save_pending(result)
            print(f"Grounding FAILED: {exc}")
            return

        g_metrics = CallMetrics(
            latency_ms=g_call.latency_ms, input_tokens=g_call.input_tokens, output_tokens=g_call.output_tokens,
            estimated_cost=estimate_cost("gemini", g_call.model, g_call.input_tokens, g_call.output_tokens),
        )
        _accumulate(result, g_metrics)
        result.grounding_metrics = g_metrics

        try:
            g_structured = GroundingResponse.model_validate(g_call.parsed_json) if g_call.parsed_json else None
        except ValidationError:
            g_structured = None
        if g_structured is None or g_structured.target.strip().lower() in {"send", "reply all", "reply_all"}:
            result.typing_result = "ABORTED"
            result.result = "ABORTED"
            result.notes = "Grounding failed or returned a blocked target."
            save_pending(result)
            print("ABORTED: grounding failed or returned a blocked target.")
            return

        screen_w, screen_h = pyautogui.size()
        cx, cy = normalize_1000_to_pixels(g_structured.x, g_structured.y, screen_w, screen_h)
        cx, cy = round(cx), round(cy)
        if not coordinate_in_image_bounds(cx, cy, screen_w, screen_h):
            result.typing_result = "ABORTED"
            result.result = "ABORTED"
            result.notes = "Converted Reply coordinate outside screen bounds."
            save_pending(result)
            print("ABORTED: coordinate outside screen bounds.")
            return

        title2 = get_foreground_window_title()
        if REQUIRE_FOREGROUND_SUBSTRING not in title2.lower():
            result.typing_result = "ABORTED"
            result.result = "ABORTED"
            result.failure_reason = "FOREGROUND_MISMATCH"
            save_pending(result)
            print("ABORTED: foreground changed before move.")
            return

        assert pyautogui.FAILSAFE is True
        pyautogui.moveTo(cx, cy, duration=MOVE_DURATION_SECONDS)

        title3 = get_foreground_window_title()
        if REQUIRE_FOREGROUND_SUBSTRING not in title3.lower():
            result.typing_result = "ABORTED"
            result.result = "ABORTED"
            result.failure_reason = "FOREGROUND_MISMATCH"
            result.notes = "Foreground changed between move and click. Cursor moved but NO CLICK performed."
            save_pending(result)
            print("ABORTED: foreground changed after move. NO CLICK performed.")
            return

        pyautogui.click()  # single click only
        print(f"Clicked Reply at ({cx}, {cy}).")
        time.sleep(STABILIZATION_DELAY_SECONDS)

    # Let focus settle before typing, whether we just clicked or the editor
    # was already open (Attempt 1 root-cause hypothesis: typing could begin
    # before focus had actually landed in the body).
    time.sleep(FOCUS_SETTLE_DELAY_SECONDS)

    # Foreground re-check immediately before typing.
    title4 = get_foreground_window_title()
    if REQUIRE_FOREGROUND_SUBSTRING not in title4.lower():
        result.typing_result = "ABORTED"
        result.result = "ABORTED"
        result.failure_reason = "FOREGROUND_MISMATCH"
        result.notes = "Foreground changed before typing."
        save_pending(result)
        print("ABORTED: foreground changed before typing. Nothing typed.")
        return

    assert pyautogui.FAILSAFE is True, "Refusing to type: FAILSAFE disabled."
    draft = result.reply_generation.draft_reply
    # Corrected method (RND-007B Attempt 2): the typing function never
    # receives a string with an embedded "\n" — each line is typed as its
    # own segment and line breaks are inserted as explicit Enter-key
    # presses. This replaces Attempt 1's single call typing the full
    # multiline text at once, which lost all but the final line in the
    # Outlook reply editor.
    #
    # Pre-RND-008 hardening: foreground is re-checked before every
    # segment write AND before every explicit Enter press, not just once
    # before typing begins — RND-007B Attempt 2 showed focus can drift
    # mid-session. On drift, abort immediately (no further keys, no
    # auto-refocus, no automatic retry) and record exactly how much of
    # the draft had already landed.
    segments = draft.split("\n")
    for i, segment in enumerate(segments):
        if segment:
            title_seg = get_foreground_window_title()
            if REQUIRE_FOREGROUND_SUBSTRING not in title_seg.lower():
                result.typing_result = "ABORTED"
                result.result = "ABORTED"
                result.failure_reason = "FOREGROUND_CHANGED_DURING_TYPING"
                result.segments_typed_before_abort = i
                result.segment_index_aborted_at = i
                result.abort_stage = "before_write"
                result.typed_text = "\n".join(segments[:i])
                result.notes = (
                    f"Foreground changed to {title_seg!r} before segment {i} of {len(segments)} could be typed. "
                    f"{i} segment(s) had already been typed. No further keys sent."
                )
                save_pending(result)
                print(f"ABORTED: foreground changed before segment {i}. {i} segment(s) already typed. No further keys sent.")
                return
            pyautogui.write(segment, interval=TYPE_INTERVAL_SECONDS)
        if i < len(segments) - 1:
            title_enter = get_foreground_window_title()
            if REQUIRE_FOREGROUND_SUBSTRING not in title_enter.lower():
                result.typing_result = "ABORTED"
                result.result = "ABORTED"
                result.failure_reason = "FOREGROUND_CHANGED_DURING_TYPING"
                result.segments_typed_before_abort = i + 1
                result.segment_index_aborted_at = i
                result.abort_stage = "before_enter"
                result.typed_text = "\n".join(segments[: i + 1])
                result.notes = (
                    f"Foreground changed to {title_enter!r} before the Enter press following segment {i}. "
                    f"{i + 1} segment(s) had already been typed (text present, line break not yet inserted). "
                    "No further keys sent."
                )
                save_pending(result)
                print(f"ABORTED: foreground changed before Enter after segment {i}. No further keys sent.")
                return
            pyautogui.press("enter")
    result.typed_text = draft
    result.text_entry_executed = True
    result.typing_result = "PASS"
    save_pending(result)  # persist immediately, before verification
    print(f"Typed draft as {len(segments)} segment(s) with explicit Enter key presses (no Ctrl+Enter/Alt+S/Send sent): {draft[:60]!r}...")
    print()
    print("Typing complete. Run --verify next.")


def cmd_verify() -> None:
    result = load_pending()
    if not result.text_entry_executed:
        raise SystemExit("Nothing was typed yet. Run --type first.")

    print(f"=== RND-007B --verify (waiting {STABILIZATION_DELAY_SECONDS}s) ===")
    time.sleep(STABILIZATION_DELAY_SECONDS)

    # Foreground re-check before capture. Verify performs no mouse/keyboard
    # action, but a screenshot of the wrong window (e.g. the IDE, if focus
    # moved away between typing and verification) produces meaningless —
    # and misleading — verification data, so it must abort rather than
    # silently capture whatever is actually on screen.
    title = get_foreground_window_title()
    print(f"Foreground window: {title!r}")
    if REQUIRE_FOREGROUND_SUBSTRING not in title.lower():
        result.result = "ABORTED"
        result.failure_reason = "FOREGROUND_MISMATCH"
        result.notes = "Verification aborted: Outlook was not foreground at capture time. No screenshot captured, no vision call made."
        save_pending(result)
        print("ABORTED: Outlook is not foreground. No verification screenshot captured, no vision call made.")
        print("Switch back to Outlook, then retry --verify.")
        return

    provider, model = _provider()

    try:
        capture = capture_screen(DEFAULT_OUTPUT_DIR)
    except ScreenCaptureError as exc:
        print(f"Post-typing capture FAILED: {exc}")
        return
    result.post_screenshot = capture.filename

    expected_draft = result.reply_generation.draft_reply
    result.expected_draft = expected_draft
    prompt_text = DRAFT_VERIFICATION_PROMPT_PATH.read_text(encoding="utf-8").format(expected_draft=expected_draft)
    try:
        call = provider.analyze_screen(Path(capture.path), "Verify typed draft", prompt_text)
    except VisionProviderError as exc:
        print(f"Verification call FAILED: {exc}. Human confirmation will be authoritative regardless.")
        save_pending(result)
        return

    raw_path = _save_raw_response("verification", call.model, call.raw_text, call.parsed_json)
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
        result.exact_match = structured.detected_draft.strip() == expected_draft.strip()
        result.semantic_match = structured.semantic_match
        result.vision_verification = structured.semantic_match
        print(f"AI verification: semantic_match={structured.semantic_match} exact_match={result.exact_match} confidence={structured.confidence}")
        print(f"Detected draft: {structured.detected_draft!r}")

    save_pending(result)
    print()
    print("Verification complete. Please visually confirm, then run --confirm pass|fail.")


def cmd_confirm(verdict: str) -> None:
    result = load_pending()
    if not result.text_entry_executed:
        raise SystemExit("Nothing was typed — nothing to confirm.")

    result.human_verification = verdict == "pass"
    result.result = "PASS" if result.human_verification else "FAIL"
    save_pending(result)
    print(f"Human verification recorded: {result.human_verification}. Overall (pending quality scores): {result.result}")
    print("Run --quality with the six criteria flags to finalize.")


def _write_final(result: RND007BResult) -> None:
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(
        json.dumps({"timestamp": datetime.now().isoformat(), "case": result.model_dump(mode="json")}, indent=2),
        encoding="utf-8",
    )
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(_build_report(result), encoding="utf-8")

    PENDING_PATH.unlink()
    print(f"Finalized. Overall result: {result.result}")
    print(f"Written: {RESULTS_PATH.relative_to(PROJECT_ROOT)}, {REPORT_PATH.relative_to(PROJECT_ROOT)}")


def cmd_quality(scores: dict[str, str]) -> None:
    result = load_pending()
    if result.human_verification is None:
        raise SystemExit("Run --confirm first.")

    for field, value in scores.items():
        setattr(result.reply_quality, field, value == "pass")

    _write_final(result)


def cmd_finalize() -> None:
    """Finalize a non-PASS attempt (FAIL/ABORTED/ERROR) without requiring
    reply-quality scoring — scoring the quality of a draft's content is not
    meaningful when the text-entry execution itself failed. A PASS result
    must go through --quality instead.
    """
    result = load_pending()
    if result.human_verification is None:
        raise SystemExit("Run --confirm first.")
    if result.result == "PASS":
        raise SystemExit("Result is PASS — use --quality to finalize (reply-quality scoring required).")
    _write_final(result)


def cmd_seed_from(source_attempt: str) -> None:
    """Carry the human-approved email understanding + draft forward from a
    finalized prior attempt into a fresh pending session, resetting only
    the execution/verification fields so a retry re-runs text entry rather
    than re-approving already-approved content.
    """
    source_path = PROJECT_ROOT / "results" / "raw" / f"rnd007b_contextual_reply_results_attempt{source_attempt}.json"
    if not source_path.exists():
        raise SystemExit(f"Attempt {source_attempt} results not found at {source_path}")
    prior = RND007BResult.model_validate(json.loads(source_path.read_text(encoding="utf-8"))["case"])
    if not prior.human_understanding_approved or not prior.human_draft_approved:
        raise SystemExit(f"Attempt {source_attempt} does not have an approved understanding + draft to carry forward.")

    seeded = RND007BResult(
        email_subject=prior.email_subject,
        email_body_summary_human=prior.email_body_summary_human,
        privacy_confirmed=prior.privacy_confirmed,
        pre_screenshot=prior.pre_screenshot,
        foreground_before=prior.foreground_before,
        email_understanding=prior.email_understanding,
        email_understanding_metrics=prior.email_understanding_metrics,
        human_understanding_approved=prior.human_understanding_approved,
        reply_generation=prior.reply_generation,
        reply_generation_metrics=prior.reply_generation_metrics,
        human_draft_approved=prior.human_draft_approved,
        notes=f"Seeded from RND-007B Attempt {source_attempt}: approved email understanding + draft carried "
              "forward unchanged; typing/verification fields reset for a fresh text-entry attempt.",
    )
    save_pending(seeded)
    print(f"Seeded session from {source_path.relative_to(PROJECT_ROOT)}.")
    print(f"Carried forward: human_understanding_approved={seeded.human_understanding_approved}, "
          f"human_draft_approved={seeded.human_draft_approved}")
    print()
    print(f"Approved draft to be typed (unchanged from Attempt {source_attempt}):")
    print("-" * 60)
    print(seeded.reply_generation.draft_reply)
    print("-" * 60)
    print()
    print("Do NOT run --type until the reply editor is confirmed empty and ready.")


def _build_report(result: RND007BResult) -> str:
    q = result.reply_quality
    lines = ["# RND-007B Contextual Reply Generation Summary — Gemini (gemini-3.6-flash)", ""]
    lines.append(f"Overall result: {result.result}")
    lines.append("")
    lines.append("## Separate metrics")
    lines.append(f"- A. Email understanding confidence: {result.email_understanding.confidence if result.email_understanding else None}")
    lines.append(f"- B. Reply generation confidence: {result.reply_generation.confidence if result.reply_generation else None}")
    lines.append(f"- C. Text-entry execution: {result.typing_result}")
    lines.append(f"- D. Vision draft verification (semantic_match): {result.semantic_match}")
    lines.append(f"- E. Human verification: {result.human_verification}")
    lines.append("")
    lines.append("## Reply quality (human-scored)")
    lines.append(f"- relevant: {q.relevant}")
    lines.append(f"- accurate: {q.accurate}")
    lines.append(f"- no_hallucination: {q.no_hallucination}")
    lines.append(f"- professional: {q.professional}")
    lines.append(f"- concise: {q.concise}")
    lines.append(f"- complete: {q.complete}")
    lines.append("")
    lines.append(f"Total vision calls: {result.total_vision_calls}")
    lines.append(f"Total input tokens: {result.total_input_tokens}")
    lines.append(f"Total output tokens: {result.total_output_tokens}")
    lines.append(f"Total estimated cost: ${result.total_estimated_cost}")
    lines.append(f"Total latency: {result.total_latency_ms} ms")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="RND-007B contextual reply generation + draft entry")
    parser.add_argument("--attempt", choices=["1", "2"], help="Suffix result/report/pending files by attempt number.")
    parser.add_argument("--retry", type=int, help="Within an attempt, suffix files by retry number (e.g. Attempt 2 Retry 1).")
    parser.add_argument("--seed-from-attempt", choices=["1", "2"], help="Carry the approved understanding+draft forward from a finalized attempt.")
    parser.add_argument("--understand", action="store_true")
    parser.add_argument("--approve-understanding", choices=["pass", "fail"])
    parser.add_argument("--generate", action="store_true")
    parser.add_argument("--approve-draft", choices=["pass", "fail"])
    parser.add_argument("--type", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--confirm", choices=["pass", "fail"])
    parser.add_argument("--finalize", action="store_true")
    parser.add_argument("--quality", action="store_true")
    parser.add_argument("--relevant", choices=["pass", "fail"])
    parser.add_argument("--accurate", choices=["pass", "fail"])
    parser.add_argument("--no-hallucination", choices=["pass", "fail"], dest="no_hallucination")
    parser.add_argument("--professional", choices=["pass", "fail"])
    parser.add_argument("--concise", choices=["pass", "fail"])
    parser.add_argument("--complete", choices=["pass", "fail"])
    args = parser.parse_args()

    _resolve_paths(args.attempt, args.retry)

    if args.quality:
        required = ["relevant", "accurate", "no_hallucination", "professional", "concise", "complete"]
        scores = {f: getattr(args, f) for f in required}
        if any(v is None for v in scores.values()):
            raise SystemExit(f"--quality requires all six criteria flags: {required}")
        cmd_quality(scores)
    elif args.finalize:
        cmd_finalize()
    elif args.confirm:
        cmd_confirm(args.confirm)
    elif args.verify:
        cmd_verify()
    elif args.type:
        cmd_type()
    elif args.approve_draft:
        cmd_approve_draft(args.approve_draft)
    elif args.generate:
        cmd_generate()
    elif args.approve_understanding:
        cmd_approve_understanding(args.approve_understanding)
    elif args.understand:
        cmd_understand()
    elif args.seed_from_attempt:
        cmd_seed_from(args.seed_from_attempt)
    else:
        cmd_plan()


if __name__ == "__main__":
    main()
