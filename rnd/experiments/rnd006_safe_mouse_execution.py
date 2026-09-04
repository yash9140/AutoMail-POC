"""RND-006A — Safe Mouse Execution (MOVE ONLY, no clicking).

Tests whether Gemini's grounding coordinates, after the RND-005A/B-verified
normalized-0-1000-to-pixel conversion, can safely drive the real Windows
mouse cursor to the correct Outlook control. This stage NEVER clicks —
`pyautogui.click` is not imported, called, or reachable anywhere in this
file. `pyautogui.FAILSAFE` is left at its default (True) and never touched.

Three explicit, human-gated phases, run as separate invocations so each
one can be reviewed before the next happens:

  1. --execute   Captures a FRESH screenshot (with a foreground-window
                 safety check — aborts if Outlook isn't active), sends it
                 to Gemini (ui_grounding_execution_v1, single target, no
                 planning), converts the returned coordinate via
                 rnd/metrics/coordinate_calibration.py, validates it's
                 within the real screen bounds, and saves a "pending move"
                 state file. Does NOT move the mouse.

  2. --move      Re-checks the foreground window right before acting
                 (state may be stale), re-validates the coordinate against
                 the current screen size, then performs ONE
                 pyautogui.moveTo(...) — never a click. Prints a prompt
                 asking the human to visually confirm cursor placement.

  3. --confirm {pass,fail}   Records the human's visual verification and
                 finalizes the result in the shared results file.

Only targets in rnd.models.mouse_execution.ALLOWED_MOVE_TARGETS
(email_row, reply, reply_editor) are accepted — "send" and everything
else is structurally rejected before any capture or API call happens.

Run (from outlook-vision-poc/):
    python rnd/experiments/rnd006_safe_mouse_execution.py --target email_row
    python rnd/experiments/rnd006_safe_mouse_execution.py --target email_row --execute
    python rnd/experiments/rnd006_safe_mouse_execution.py --target email_row --move
    python rnd/experiments/rnd006_safe_mouse_execution.py --target email_row --confirm pass
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
from rnd.models.grounding import GroundingResponse  # noqa: E402
from rnd.models.mouse_execution import ALLOWED_MOVE_TARGETS, RND006MoveResult, is_target_allowed  # noqa: E402
from rnd.providers.base import VisionProviderError  # noqa: E402
from rnd.providers.gemini_provider import GeminiProvider  # noqa: E402

PROMPT_PATH = PROJECT_ROOT / "rnd" / "prompts" / "ui_grounding_execution_v1.txt"
PROMPT_VERSION = "ui_grounding_execution_v1"
RESULTS_PATH = PROJECT_ROOT / "results" / "raw" / "rnd006_safe_mouse_execution_results.json"
REPORT_PATH = PROJECT_ROOT / "results" / "reports" / "rnd006_safe_mouse_execution_summary.md"
RAW_RESPONSES_DIR = PROJECT_ROOT / "results" / "raw" / "provider_responses" / "rnd006"
PENDING_DIR = PROJECT_ROOT / "results" / "raw" / "rnd006_pending"
PRICING_PATH = PROJECT_ROOT / "config" / "model_pricing.json"

MAX_ATTEMPTS = 2
MOVE_DURATION_SECONDS = 0.6
REQUIRE_FOREGROUND_SUBSTRING = "outlook"


def pending_path(target: str) -> Path:
    return PENDING_DIR / f"{target}.json"


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
    print("=== RND-006A Safe Mouse Execution (MOVE ONLY) ===")
    print(f"Target: {target}  ({ALLOWED_MOVE_TARGETS[target]})")
    print("Mode: PLAN ONLY — no screenshot captured, no API call made, no mouse movement.")
    print("Next: re-run with --execute after preparing Outlook and getting approval to send a screenshot.")


def cmd_execute(target: str) -> None:
    """Fresh capture -> foreground check -> Gemini call -> convert -> validate -> save pending. No move."""
    load_dotenv(PROJECT_ROOT / ".env")
    model = os.environ.get("GEMINI_MODEL", "")
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key or not model:
        raise SystemExit("GEMINI_API_KEY / GEMINI_MODEL must be set in .env")

    print("=== RND-006A --execute ===")
    title = get_foreground_window_title()
    print(f"Foreground window: {title!r}")
    foreground_ok = REQUIRE_FOREGROUND_SUBSTRING in title.lower()
    if not foreground_ok:
        result = RND006MoveResult(
            test_id=f"RND006-{target}",
            target=target,
            foreground_window=title,
            foreground_check_passed=False,
            result="ABORTED",
            notes="Foreground window did not contain 'outlook' at capture time. No screenshot taken, no API call made.",
        )
        _save_pending(target, result)
        print("ABORTED: Outlook is not the foreground window. No screenshot taken, no API call made.")
        return

    try:
        capture = capture_screen(DEFAULT_OUTPUT_DIR)
    except ScreenCaptureError as exc:
        print(f"Capture FAILED: {exc}")
        return
    print(f"Captured fresh screenshot: {capture.filename} ({capture.width}x{capture.height})")

    prompt_text = PROMPT_PATH.read_text(encoding="utf-8").format(
        width=capture.width, height=capture.height, target=ALLOWED_MOVE_TARGETS[target]
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
            call_result = provider.analyze_screen(image_path, f"Locate {ALLOWED_MOVE_TARGETS[target]}", prompt_text)
            last_error = None
            break
        except VisionProviderError as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            print(f"Attempt {attempt_count} FAILED: {last_error}")

    if call_result is None:
        result = RND006MoveResult(
            test_id=f"RND006-{target}",
            target=target,
            foreground_window=title,
            foreground_check_passed=True,
            screenshot_filename=capture.filename,
            vision_calls=attempt_count,
            result="ERROR",
            notes=last_error or "Unknown provider error",
        )
        _save_pending(target, result)
        print("API call failed after retries. See pending file for details.")
        return

    RAW_RESPONSES_DIR.mkdir(parents=True, exist_ok=True)
    raw_path = RAW_RESPONSES_DIR / f"{target}_{datetime.now().isoformat().replace(':', '-')}.json"
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

    estimated_cost, cost_note = estimate_cost("gemini", call_result.model, call_result.input_tokens, call_result.output_tokens)

    if structured is None:
        result = RND006MoveResult(
            test_id=f"RND006-{target}",
            target=target,
            foreground_window=title,
            foreground_check_passed=True,
            screenshot_filename=capture.filename,
            vision_calls=attempt_count,
            latency_ms=call_result.latency_ms,
            input_tokens=call_result.input_tokens,
            output_tokens=call_result.output_tokens,
            estimated_cost=estimated_cost,
            raw_response_reference=str(raw_path.relative_to(PROJECT_ROOT)),
            result="ERROR",
            notes="Response was not schema-valid — no coordinate to convert or move to.",
        )
        _save_pending(target, result)
        print("No valid coordinate returned. See pending file.")
        return

    screen_w, screen_h = pyautogui.size()
    converted_x, converted_y = normalize_1000_to_pixels(structured.x, structured.y, screen_w, screen_h)
    converted_x, converted_y = round(converted_x), round(converted_y)
    coord_valid = coordinate_in_image_bounds(converted_x, converted_y, screen_w, screen_h)

    result = RND006MoveResult(
        test_id=f"RND006-{target}",
        target=target,
        raw_x=structured.x,
        raw_y=structured.y,
        converted_x=converted_x,
        converted_y=converted_y,
        screen_width=screen_w,
        screen_height=screen_h,
        foreground_window=title,
        foreground_check_passed=True,
        coordinate_valid=coord_valid,
        vision_calls=attempt_count,
        latency_ms=call_result.latency_ms,
        input_tokens=call_result.input_tokens,
        output_tokens=call_result.output_tokens,
        estimated_cost=estimated_cost,
        screenshot_filename=capture.filename,
        raw_response_reference=str(raw_path.relative_to(PROJECT_ROOT)),
        result="PENDING" if coord_valid else "ABORTED",
        notes="" if coord_valid else "Converted coordinate is outside the real screen bounds — movement will be refused.",
    )
    _save_pending(target, result)

    print()
    print(f"Raw Gemini coordinate:       ({structured.x}, {structured.y})")
    print(f"Converted pixel coordinate:  ({converted_x}, {converted_y})")
    print(f"Screen resolution:           {screen_w} x {screen_h}")
    print(f"Coordinate valid:            {coord_valid}")
    print(f"Confidence:                  {structured.confidence}")
    print(f"Foreground window:           {title!r}")
    print(f"Latency:                     {call_result.latency_ms:.1f} ms")
    print()
    print("NOT MOVED YET. Awaiting approval, then re-run with --move.")


def _save_pending(target: str, result: RND006MoveResult) -> None:
    PENDING_DIR.mkdir(parents=True, exist_ok=True)
    pending_path(target).write_text(result.model_dump_json(indent=2), encoding="utf-8")


def cmd_move(target: str) -> None:
    """Reads the pending state saved by --execute, re-validates everything
    fresh (foreground + coordinate bounds), then performs ONE moveTo. Never
    clicks.
    """
    ppath = pending_path(target)
    if not ppath.exists():
        raise SystemExit(f"No pending result for '{target}'. Run --execute first.")
    result = RND006MoveResult.model_validate(json.loads(ppath.read_text(encoding="utf-8")))

    if result.result != "PENDING" or result.converted_x is None or result.converted_y is None:
        raise SystemExit(f"Pending result for '{target}' is not in a movable state (result={result.result!r}).")

    print("=== RND-006A --move ===")
    print(f"Target: {target}")
    print(f"Converted coordinate from --execute: ({result.converted_x}, {result.converted_y})")

    # Re-check foreground fresh — time has passed since --execute.
    title = get_foreground_window_title()
    print(f"Foreground window (re-checked now): {title!r}")
    if REQUIRE_FOREGROUND_SUBSTRING not in title.lower():
        result.foreground_check_passed = False
        result.foreground_window = title
        result.result = "ABORTED"
        result.notes = "Foreground window changed away from Outlook between --execute and --move. Movement refused."
        _save_pending(target, result)
        print("ABORTED: Outlook is no longer the foreground window. No movement performed.")
        return

    # Re-validate against the current real screen size, not the stored one.
    screen_w, screen_h = pyautogui.size()
    coord_valid = coordinate_in_image_bounds(result.converted_x, result.converted_y, screen_w, screen_h)
    if not coord_valid:
        result.result = "ABORTED"
        result.notes = f"Converted coordinate ({result.converted_x},{result.converted_y}) is outside current screen bounds {screen_w}x{screen_h}. Movement refused."
        _save_pending(target, result)
        print("ABORTED: coordinate outside current screen bounds. No movement performed.")
        return

    print(f"pyautogui.FAILSAFE = {pyautogui.FAILSAFE}  (must be True — never disabled by this project)")
    assert pyautogui.FAILSAFE is True, "Refusing to move: FAILSAFE has been disabled somewhere — this must never happen."

    pyautogui.moveTo(result.converted_x, result.converted_y, duration=MOVE_DURATION_SECONDS)
    result.movement_executed = True
    result.foreground_window = title
    result.foreground_check_passed = True
    result.screen_width, result.screen_height = screen_w, screen_h
    _save_pending(target, result)

    print(f"Cursor moved to ({result.converted_x}, {result.converted_y}). NO CLICK was performed.")
    print("Please visually confirm the cursor position, then run:")
    print(f"  python rnd/experiments/rnd006_safe_mouse_execution.py --target {target} --confirm pass")
    print("  (or --confirm fail)")


def cmd_confirm(target: str, verdict: str) -> None:
    ppath = pending_path(target)
    if not ppath.exists():
        raise SystemExit(f"No pending result for '{target}'. Run --execute and --move first.")
    result = RND006MoveResult.model_validate(json.loads(ppath.read_text(encoding="utf-8")))

    if not result.movement_executed:
        raise SystemExit(f"'{target}' was never moved (movement_executed=False) — nothing to confirm.")

    result.human_verified = verdict == "pass"
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
    lines = ["# RND-006A Safe Mouse Execution Summary — Gemini (gemini-3.6-flash)", ""]
    lines.append("MOVE ONLY. No click was performed at any point in this stage.")
    lines.append("")
    lines.append("| target | raw (x,y) | converted (x,y) | coordinate_valid | foreground_ok | movement_executed | human_verified | result |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for c in cases:
        lines.append(
            f"| {c['target']} | ({c['raw_x']},{c['raw_y']}) | ({c['converted_x']},{c['converted_y']}) | "
            f"{c['coordinate_valid']} | {c['foreground_check_passed']} | {c['movement_executed']} | "
            f"{c['human_verified']} | {c['result']} |"
        )
    lines.append("")
    passed = sum(1 for c in cases if c["result"] == "PASS")
    lines.append(f"Move-only PASS: {passed}/{len(cases)}")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="RND-006A safe mouse execution (move only)")
    parser.add_argument("--target", required=True, choices=sorted(ALLOWED_MOVE_TARGETS))
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--move", action="store_true")
    parser.add_argument("--confirm", choices=["pass", "fail"])
    args = parser.parse_args()

    if not is_target_allowed(args.target):
        raise SystemExit(f"Target '{args.target}' is not in the allowed move-target list. Refusing.")

    if args.confirm:
        cmd_confirm(args.target, args.confirm)
    elif args.move:
        cmd_move(args.target)
    elif args.execute:
        cmd_execute(args.target)
    else:
        cmd_plan(args.target)


if __name__ == "__main__":
    main()
