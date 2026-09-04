"""RND-006B — Safe Click Execution (single controlled click per target).

Extends RND-006A's move-only pipeline with ONE controlled click and
post-click state verification. Click allowlist is
rnd.models.click_execution.CLICK_ALLOWED_TARGETS — email_row, reply,
reply_editor only. Send, Reply All, Forward, Delete, Archive, and any
unrecognized target are structurally rejected before any capture, API
call, or mouse action.

Three human-gated phases, matching RND-006A's pattern:

  1. --execute  Fresh screenshot + foreground check + FRESH Gemini
                grounding call (never reuses an RND-006A coordinate) +
                coordinate conversion/validation. No move, no click.

  2. --click    Re-checks foreground (before move), moves the cursor,
                re-checks foreground AGAIN (immediately before click —
                two separate checks, per spec), then performs exactly
                ONE pyautogui.click() — never a double-click. Captures a
                fresh POST-click screenshot and sends it to Gemini for
                state verification (a separate call from grounding).

  3. --confirm {pass,fail}   Records the human's own visual confirmation
                (authoritative — never overridden by the AI verification
                call) and finalizes the result.

pyautogui.FAILSAFE is left at its default True and never touched.
pyautogui.click() is called at most once per --click invocation, always
preceded by moveTo() in the same call — never a bare click at an
arbitrary location.

Run (from outlook-vision-poc/):
    python rnd/experiments/rnd006b_safe_click_execution.py --target email_row
    python rnd/experiments/rnd006b_safe_click_execution.py --target email_row --execute
    python rnd/experiments/rnd006b_safe_click_execution.py --target email_row --click
    python rnd/experiments/rnd006b_safe_click_execution.py --target email_row --confirm pass
"""

from __future__ import annotations

import argparse
import json
import os
import sys
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
from rnd.models.click_execution import (  # noqa: E402
    CLICK_ALLOWED_TARGETS,
    EXPECTED_STATE_AFTER_CLICK,
    RND006BClickResult,
    VerificationResponse,
    is_click_target_allowed,
)
from rnd.models.grounding import GroundingResponse  # noqa: E402
from rnd.providers.base import VisionProviderError  # noqa: E402
from rnd.providers.gemini_provider import GeminiProvider  # noqa: E402

GROUNDING_PROMPT_PATH = PROJECT_ROOT / "rnd" / "prompts" / "ui_grounding_execution_v1.txt"
VERIFICATION_PROMPT_PATH = PROJECT_ROOT / "rnd" / "prompts" / "state_verification_v1.txt"
GROUNDING_PROMPT_VERSION = "ui_grounding_execution_v1"
VERIFICATION_PROMPT_VERSION = "state_verification_v1"

RESULTS_PATH = PROJECT_ROOT / "results" / "raw" / "rnd006b_safe_click_results.json"
REPORT_PATH = PROJECT_ROOT / "results" / "reports" / "rnd006b_safe_click_summary.md"
RAW_RESPONSES_DIR = PROJECT_ROOT / "results" / "raw" / "provider_responses" / "rnd006b"
PENDING_DIR = PROJECT_ROOT / "results" / "raw" / "rnd006b_pending"
PRICING_PATH = PROJECT_ROOT / "config" / "model_pricing.json"

MAX_ATTEMPTS = 2
MOVE_DURATION_SECONDS = 0.6
REQUIRE_FOREGROUND_SUBSTRING = "outlook"


def pending_path(target: str) -> Path:
    return PENDING_DIR / f"{target}.json"


def save_pending(target: str, result: RND006BClickResult) -> None:
    PENDING_DIR.mkdir(parents=True, exist_ok=True)
    pending_path(target).write_text(result.model_dump_json(indent=2), encoding="utf-8")


def estimate_cost(provider: str, model: str, input_tokens: Optional[int], output_tokens: Optional[int]) -> tuple[Optional[float], str]:
    if not PRICING_PATH.exists():
        return None, "Cost unavailable pending verified pricing configuration."
    pricing = json.loads(PRICING_PATH.read_text(encoding="utf-8"))
    entry = next((p for p in pricing.get("models", []) if p["provider"] == provider and p["model"] == model), None)
    if entry is None:
        return None, f"Cost unavailable pending verified pricing configuration for {provider}/{model}."
    if input_tokens is None or output_tokens is None:
        return None, "Cost unavailable: provider did not return token usage."
    cost = (input_tokens / 1_000_000) * entry["input_rate_per_million_tokens"] + \
           (output_tokens / 1_000_000) * entry["output_rate_per_million_tokens"]
    return round(cost, 6), f"Verified pricing dated {entry['pricing_date']} ({entry['source']})."


def cmd_plan(target: str) -> None:
    print("=== RND-006B Safe Click Execution ===")
    print(f"Target: {target}  ({CLICK_ALLOWED_TARGETS[target]})")
    print(f"Expected state after click: {EXPECTED_STATE_AFTER_CLICK[target]}")
    print("Mode: PLAN ONLY — no screenshot captured, no API call made, no mouse action.")
    print("Next: re-run with --execute after preparing Outlook and getting approval to send a screenshot.")


def cmd_execute(target: str) -> None:
    """Fresh capture -> foreground check -> FRESH Gemini grounding call -> convert -> validate -> save pending. No move, no click."""
    load_dotenv(PROJECT_ROOT / ".env")
    model = os.environ.get("GEMINI_MODEL", "")
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key or not model:
        raise SystemExit("GEMINI_API_KEY / GEMINI_MODEL must be set in .env")

    print("=== RND-006B --execute (grounding only) ===")
    title = get_foreground_window_title()
    print(f"Foreground window: {title!r}")
    if REQUIRE_FOREGROUND_SUBSTRING not in title.lower():
        result = RND006BClickResult(
            test_id=f"RND006B-{target}", target=target,
            foreground_before=title, result="ABORTED", failure_reason="FOREGROUND_MISMATCH",
            notes="Foreground window did not contain 'outlook' at capture time. No screenshot taken, no API call made.",
        )
        save_pending(target, result)
        print("ABORTED: Outlook is not the foreground window. No screenshot taken, no API call made.")
        return

    try:
        capture = capture_screen(DEFAULT_OUTPUT_DIR)
    except ScreenCaptureError as exc:
        print(f"Capture FAILED: {exc}")
        return
    print(f"Captured fresh (pre-click) screenshot: {capture.filename} ({capture.width}x{capture.height})")

    prompt_text = GROUNDING_PROMPT_PATH.read_text(encoding="utf-8").format(
        width=capture.width, height=capture.height, target=CLICK_ALLOWED_TARGETS[target]
    )
    provider = GeminiProvider(
        api_key=api_key, model=model, timeout_seconds=float(os.environ.get("VISION_REQUEST_TIMEOUT_SECONDS", "30"))
    )

    image_path = Path(capture.path)
    attempt_count = 0
    last_error: Optional[str] = None
    call_result = None
    while attempt_count < MAX_ATTEMPTS:
        attempt_count += 1
        try:
            call_result = provider.analyze_screen(image_path, f"Locate {CLICK_ALLOWED_TARGETS[target]}", prompt_text)
            last_error = None
            break
        except VisionProviderError as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            print(f"Attempt {attempt_count} FAILED: {last_error}")

    if call_result is None:
        result = RND006BClickResult(
            test_id=f"RND006B-{target}", target=target,
            pre_screenshot=capture.filename, foreground_before=title,
            vision_calls=attempt_count, result="ERROR", failure_reason="NETWORK_ERROR" if "Network" in (last_error or "") else "MODEL_ERROR",
            notes=last_error or "Unknown provider error",
        )
        save_pending(target, result)
        print("Grounding call failed after retries. See pending file.")
        return

    RAW_RESPONSES_DIR.mkdir(parents=True, exist_ok=True)
    raw_path = RAW_RESPONSES_DIR / f"{target}_grounding_{datetime.now().isoformat().replace(':', '-')}.json"
    raw_path.write_text(
        json.dumps({"provider": "gemini", "model": call_result.model, "raw_text": call_result.raw_text, "parsed_json": call_result.parsed_json}, indent=2),
        encoding="utf-8",
    )

    structured: Optional[GroundingResponse] = None
    try:
        if call_result.parsed_json is not None:
            structured = GroundingResponse.model_validate(call_result.parsed_json)
    except ValidationError as exc:
        print(f"Schema validation FAILED: {exc}")

    estimated_cost, _ = estimate_cost("gemini", call_result.model, call_result.input_tokens, call_result.output_tokens)

    if structured is None:
        result = RND006BClickResult(
            test_id=f"RND006B-{target}", target=target,
            pre_screenshot=capture.filename, foreground_before=title,
            vision_calls=attempt_count, input_tokens=call_result.input_tokens, output_tokens=call_result.output_tokens,
            estimated_cost=estimated_cost, grounding_latency_ms=call_result.latency_ms,
            result="ERROR", failure_reason="GROUNDING_FAILURE",
            notes="Response was not schema-valid — no coordinate to convert or click.",
        )
        save_pending(target, result)
        print("No valid coordinate returned. See pending file.")
        return

    if structured.target.strip().lower() in {"send", "reply all", "reply_all"}:
        result = RND006BClickResult(
            test_id=f"RND006B-{target}", target=target,
            pre_screenshot=capture.filename, foreground_before=title,
            vision_calls=attempt_count, result="ABORTED", failure_reason="OTHER",
            notes=f"Model returned target={structured.target!r}, which is blocked. Aborting per safety rule.",
        )
        save_pending(target, result)
        print(f"ABORTED: model returned a blocked target ({structured.target!r}).")
        return

    screen_w, screen_h = pyautogui.size()
    converted_x, converted_y = normalize_1000_to_pixels(structured.x, structured.y, screen_w, screen_h)
    converted_x, converted_y = round(converted_x), round(converted_y)
    coord_valid = coordinate_in_image_bounds(converted_x, converted_y, screen_w, screen_h)

    result = RND006BClickResult(
        test_id=f"RND006B-{target}", target=target,
        pre_screenshot=capture.filename,
        raw_x=structured.x, raw_y=structured.y,
        converted_x=converted_x, converted_y=converted_y,
        foreground_before=title,
        expected_state=EXPECTED_STATE_AFTER_CLICK[target],
        vision_calls=attempt_count, input_tokens=call_result.input_tokens, output_tokens=call_result.output_tokens,
        estimated_cost=estimated_cost, grounding_latency_ms=call_result.latency_ms,
        result="PENDING" if coord_valid else "ABORTED",
        failure_reason=None if coord_valid else "COORDINATE_INVALID",
        notes="" if coord_valid else "Converted coordinate is outside the real screen bounds.",
    )
    save_pending(target, result)

    print()
    print(f"Raw Gemini coordinate:       ({structured.x}, {structured.y})")
    print(f"Converted pixel coordinate:  ({converted_x}, {converted_y})")
    print(f"Screen resolution:           {screen_w} x {screen_h}")
    print(f"Coordinate valid:            {coord_valid}")
    print(f"Confidence:                  {structured.confidence}")
    print(f"Foreground window:           {title!r}")
    print(f"Expected state after click:  {EXPECTED_STATE_AFTER_CLICK[target]}")
    print(f"Grounding latency:           {call_result.latency_ms:.1f} ms")
    print()
    print("NOT CLICKED YET. Awaiting approval, then re-run with --click.")


def cmd_click(target: str) -> None:
    """Re-validate -> move -> re-check foreground -> ONE click -> post-click screenshot -> verification call."""
    load_dotenv(PROJECT_ROOT / ".env")
    ppath = pending_path(target)
    if not ppath.exists():
        raise SystemExit(f"No pending result for '{target}'. Run --execute first.")
    result = RND006BClickResult.model_validate(json.loads(ppath.read_text(encoding="utf-8")))

    if result.result != "PENDING" or result.converted_x is None or result.converted_y is None:
        raise SystemExit(f"Pending result for '{target}' is not in a clickable state (result={result.result!r}).")

    result.approval_received = True

    print("=== RND-006B --click ===")
    print(f"Target: {target}")
    print(f"Converted coordinate from --execute: ({result.converted_x}, {result.converted_y})")

    # Foreground check #1: before move.
    title_before_move = get_foreground_window_title()
    print(f"Foreground window (before move): {title_before_move!r}")
    if REQUIRE_FOREGROUND_SUBSTRING not in title_before_move.lower():
        result.result = "ABORTED"
        result.failure_reason = "FOREGROUND_MISMATCH"
        result.notes = "Foreground window changed away from Outlook before move. No movement, no click."
        save_pending(target, result)
        print("ABORTED: Outlook is not foreground. No movement, no click performed.")
        return

    screen_w, screen_h = pyautogui.size()
    if not coordinate_in_image_bounds(result.converted_x, result.converted_y, screen_w, screen_h):
        result.result = "ABORTED"
        result.failure_reason = "COORDINATE_INVALID"
        result.notes = f"Converted coordinate outside current screen bounds {screen_w}x{screen_h}."
        save_pending(target, result)
        print("ABORTED: coordinate outside current screen bounds. No movement, no click performed.")
        return

    assert pyautogui.FAILSAFE is True, "Refusing to act: FAILSAFE has been disabled somewhere — this must never happen."
    pyautogui.moveTo(result.converted_x, result.converted_y, duration=MOVE_DURATION_SECONDS)
    result.movement_executed = True
    print(f"Cursor moved to ({result.converted_x}, {result.converted_y}).")

    # Foreground check #2: immediately before click.
    title_before_click = get_foreground_window_title()
    result.foreground_before_click = title_before_click
    print(f"Foreground window (immediately before click): {title_before_click!r}")
    if REQUIRE_FOREGROUND_SUBSTRING not in title_before_click.lower():
        result.result = "ABORTED"
        result.failure_reason = "FOREGROUND_MISMATCH"
        result.notes = "Foreground window changed away from Outlook between move and click. Cursor was moved but NO CLICK was performed."
        save_pending(target, result)
        print("ABORTED: foreground changed after move, before click. NO CLICK performed.")
        return

    pyautogui.click()  # single click only — never called more than once per invocation
    result.click_executed = True
    print("CLICK performed (single click).")

    try:
        post_capture = capture_screen(DEFAULT_OUTPUT_DIR)
        result.post_screenshot = post_capture.filename
        print(f"Post-click screenshot captured: {post_capture.filename}")
    except ScreenCaptureError as exc:
        result.notes += f" Post-click screenshot capture FAILED: {exc}."
        save_pending(target, result)
        print(f"WARNING: post-click screenshot capture failed: {exc}. Verification skipped.")
        return

    # Verification call — separate from grounding.
    model = os.environ.get("GEMINI_MODEL", "")
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if api_key and model:
        provider = GeminiProvider(
            api_key=api_key, model=model, timeout_seconds=float(os.environ.get("VISION_REQUEST_TIMEOUT_SECONDS", "30"))
        )
        verification_prompt = VERIFICATION_PROMPT_PATH.read_text(encoding="utf-8").format(
            action=f"Clicked {CLICK_ALLOWED_TARGETS[target]}", expected_state=result.expected_state
        )
        try:
            v_call = provider.analyze_screen(Path(post_capture.path), "Verify expected state", verification_prompt)
            result.verification_latency_ms = v_call.latency_ms
            result.vision_calls += 1
            result.input_tokens = (result.input_tokens or 0) + (v_call.input_tokens or 0)
            result.output_tokens = (result.output_tokens or 0) + (v_call.output_tokens or 0)
            add_cost, _ = estimate_cost("gemini", v_call.model, v_call.input_tokens, v_call.output_tokens)
            if add_cost is not None:
                result.estimated_cost = round((result.estimated_cost or 0) + add_cost, 6)

            RAW_RESPONSES_DIR.mkdir(parents=True, exist_ok=True)
            vraw_path = RAW_RESPONSES_DIR / f"{target}_verification_{datetime.now().isoformat().replace(':', '-')}.json"
            vraw_path.write_text(
                json.dumps({"provider": "gemini", "model": v_call.model, "raw_text": v_call.raw_text, "parsed_json": v_call.parsed_json}, indent=2),
                encoding="utf-8",
            )

            v_structured: Optional[VerificationResponse] = None
            try:
                if v_call.parsed_json is not None:
                    v_structured = VerificationResponse.model_validate(v_call.parsed_json)
            except ValidationError as exc:
                print(f"Verification schema validation FAILED: {exc}")

            if v_structured:
                result.verification_result = v_structured.verified
                result.detected_state = v_structured.detected_state
                result.verification_confidence = v_structured.confidence
                print(f"AI verification: verified={v_structured.verified} detected_state={v_structured.detected_state!r} confidence={v_structured.confidence}")
            else:
                print("AI verification response was not schema-valid.")
        except VisionProviderError as exc:
            print(f"Verification call FAILED: {exc}")

    save_pending(target, result)
    print()
    print("Click complete. Please visually confirm the post-click screenshot/state, then run:")
    print(f"  python rnd/experiments/rnd006b_safe_click_execution.py --target {target} --confirm pass")
    print("  (or --confirm fail)")


def cmd_confirm(target: str, verdict: str) -> None:
    ppath = pending_path(target)
    if not ppath.exists():
        raise SystemExit(f"No pending result for '{target}'. Run --execute and --click first.")
    result = RND006BClickResult.model_validate(json.loads(ppath.read_text(encoding="utf-8")))

    if not result.click_executed:
        raise SystemExit(f"'{target}' was never clicked (click_executed=False) — nothing to confirm.")

    result.human_verification = verdict == "pass"
    result.result = "PASS" if verdict == "pass" else "FAIL"

    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing = json.loads(RESULTS_PATH.read_text(encoding="utf-8")) if RESULTS_PATH.exists() else {"cases": []}
    existing["cases"] = [c for c in existing["cases"] if c["target"] != target] + [json.loads(result.model_dump_json())]
    existing["timestamp"] = datetime.now().isoformat()
    RESULTS_PATH.write_text(json.dumps(existing, indent=2), encoding="utf-8")

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(_build_report(existing["cases"]), encoding="utf-8")

    ppath.unlink()
    print(f"Recorded {target}: {result.result}")
    print(f"Updated {RESULTS_PATH.relative_to(PROJECT_ROOT)} and {REPORT_PATH.relative_to(PROJECT_ROOT)}")


def _build_report(cases: list[dict]) -> str:
    lines = ["# RND-006B Safe Click Execution Summary — Gemini (gemini-3.6-flash)", ""]
    lines.append("ONE controlled click per target. Send/Reply All/Forward/Delete/Archive never reachable.")
    lines.append("")
    lines.append("| target | raw (x,y) | converted (x,y) | click_executed | expected_state | detected_state | ai_verified | human_verified | result |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for c in cases:
        lines.append(
            f"| {c['target']} | ({c['raw_x']},{c['raw_y']}) | ({c['converted_x']},{c['converted_y']}) | "
            f"{c['click_executed']} | {c['expected_state']} | {c['detected_state']} | "
            f"{c['verification_result']} | {c['human_verification']} | {c['result']} |"
        )
    lines.append("")
    passed = sum(1 for c in cases if c["result"] == "PASS")
    lines.append(f"Safe click PASS: {passed}/{len(cases)}")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="RND-006B safe click execution")
    parser.add_argument("--target", required=True, choices=sorted(CLICK_ALLOWED_TARGETS))
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--click", action="store_true")
    parser.add_argument("--confirm", choices=["pass", "fail"])
    args = parser.parse_args()

    if not is_click_target_allowed(args.target):
        raise SystemExit(f"Target '{args.target}' is not in the click allowlist. Refusing.")

    if args.confirm:
        cmd_confirm(args.target, args.confirm)
    elif args.click:
        cmd_click(args.target)
    elif args.execute:
        cmd_execute(args.target)
    else:
        cmd_plan(args.target)


if __name__ == "__main__":
    main()
