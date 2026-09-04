"""RND-006B Attempt 2 — Safe Click Execution with Stabilized Verification.

Repeats RND-006B's controlled-click tests with every existing safety gate
unchanged (fresh grounding, normalized coordinate conversion, bounds
validation, dual foreground checks, single click only, FAILSAFE=True,
Send/unsafe-target blocking) — the only change is what happens AFTER the
click: a stabilization delay before the first verification screenshot,
and up to one additional wait+screenshot+re-verify cycle if the first
verification reports the expected state was not detected.

This tests two SEPARATE questions, never conflated:
  (a) Did Outlook need more time to render before verification could see
      the true state? (UI_STABILIZATION_DELAY_REQUIRED)
  (b) Is Gemini's verification itself unreliable even once the UI is
      stable? (VISION_VERIFICATION_FALSE_NEGATIVE)

The retry is ALWAYS wait -> new screenshot -> new verification call.
It is NEVER another click. `pyautogui.click` is called at most once per
--click invocation, identical to Attempt 1.

Preserves RND-006B Attempt 1's result files untouched — writes to its own
results/raw/rnd006b_attempt2_safe_click_results.json.

Run (from outlook-vision-poc/):
    python rnd/experiments/rnd006b_attempt2_safe_click_execution.py --target email_row
    python rnd/experiments/rnd006b_attempt2_safe_click_execution.py --target email_row --execute
    python rnd/experiments/rnd006b_attempt2_safe_click_execution.py --target email_row --click
    python rnd/experiments/rnd006b_attempt2_safe_click_execution.py --target email_row --confirm pass
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
from rnd.metrics.click_verification import classify_verification_outcome, compute_click_result  # noqa: E402
from rnd.metrics.coordinate_calibration import normalize_1000_to_pixels  # noqa: E402
from rnd.metrics.grounding import coordinate_in_image_bounds  # noqa: E402
from rnd.models.click_execution import (  # noqa: E402
    CLICK_ALLOWED_TARGETS,
    EXPECTED_VERIFICATION_STATEMENT_V2,
    RND006BAttempt2Result,
    VerificationAttempt,
    VerificationResponseV2,
    is_click_target_allowed,
)
from rnd.models.grounding import GroundingResponse  # noqa: E402
from rnd.providers.base import VisionProviderError  # noqa: E402
from rnd.providers.gemini_provider import GeminiProvider  # noqa: E402

GROUNDING_PROMPT_PATH = PROJECT_ROOT / "rnd" / "prompts" / "ui_grounding_execution_v1.txt"
VERIFICATION_PROMPT_PATH = PROJECT_ROOT / "rnd" / "prompts" / "state_verification_v2.txt"
GROUNDING_PROMPT_VERSION = "ui_grounding_execution_v1"
VERIFICATION_PROMPT_VERSION = "state_verification_v2"

RESULTS_PATH = PROJECT_ROOT / "results" / "raw" / "rnd006b_attempt2_safe_click_results.json"
REPORT_PATH = PROJECT_ROOT / "results" / "reports" / "rnd006b_attempt2_safe_click_summary.md"
RAW_RESPONSES_DIR = PROJECT_ROOT / "results" / "raw" / "provider_responses" / "rnd006b_attempt2"
PENDING_DIR = PROJECT_ROOT / "results" / "raw" / "rnd006b_attempt2_pending"
PRICING_PATH = PROJECT_ROOT / "config" / "model_pricing.json"

MAX_ATTEMPTS = 2
MOVE_DURATION_SECONDS = 0.6
REQUIRE_FOREGROUND_SUBSTRING = "outlook"
FIRST_STABILIZATION_DELAY_SECONDS = 1.5
SECOND_STABILIZATION_DELAY_SECONDS = 1.0
MAX_VERIFICATION_ATTEMPTS = 2


def pending_path(target: str) -> Path:
    return PENDING_DIR / f"{target}.json"


def save_pending(target: str, result: RND006BAttempt2Result) -> None:
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


def run_verification_cycle(
    provider: GeminiProvider, target: str, action: str, expected_state: str,
    delay_seconds: float, attempt_number: int, click_time: Optional[float] = None,
) -> VerificationAttempt:
    """wait -> fresh screenshot -> Gemini verification call. Never a click."""
    time.sleep(delay_seconds)

    capture = capture_screen(DEFAULT_OUTPUT_DIR)
    screenshot_timestamp = datetime.now().isoformat()
    time_since_click_ms = (time.perf_counter() - click_time) * 1000 if click_time is not None else None

    prompt_text = VERIFICATION_PROMPT_PATH.read_text(encoding="utf-8").format(action=action, expected_state=expected_state)

    attempt = VerificationAttempt(
        attempt_number=attempt_number,
        delay_seconds=delay_seconds,
        screenshot_timestamp=screenshot_timestamp,
        screenshot_filename=capture.filename,
        time_since_click_ms=time_since_click_ms,
    )

    try:
        call_result = provider.analyze_screen(Path(capture.path), "Verify expected state", prompt_text)
    except VisionProviderError as exc:
        attempt.error = f"{type(exc).__name__}: {exc}"
        return attempt

    attempt.latency_ms = call_result.latency_ms
    attempt.input_tokens = call_result.input_tokens
    attempt.output_tokens = call_result.output_tokens
    cost, _ = estimate_cost("gemini", call_result.model, call_result.input_tokens, call_result.output_tokens)
    attempt.estimated_cost = cost

    RAW_RESPONSES_DIR.mkdir(parents=True, exist_ok=True)
    raw_path = RAW_RESPONSES_DIR / f"{target}_verify_attempt{attempt_number}_{screenshot_timestamp.replace(':', '-')}.json"
    raw_path.write_text(
        json.dumps({"provider": "gemini", "model": call_result.model, "raw_text": call_result.raw_text, "parsed_json": call_result.parsed_json}, indent=2),
        encoding="utf-8",
    )

    try:
        if call_result.parsed_json is not None:
            structured = VerificationResponseV2.model_validate(call_result.parsed_json)
            attempt.schema_valid = True
            attempt.verified = structured.verified
            attempt.detected_state = structured.detected_state
            attempt.confidence = structured.confidence
            attempt.visual_evidence = structured.visual_evidence
            attempt.reason = structured.reason
        else:
            attempt.error = "Response body was not valid JSON."
    except ValidationError as exc:
        attempt.error = str(exc)

    return attempt


def run_stabilized_verification(provider: GeminiProvider, target: str, action: str, expected_state: str, click_time: Optional[float] = None) -> dict:
    """attempt 1 (1.5s delay); if not verified, attempt 2 (additional 1.0s
    delay). Maximum 2 attempts, never more. Never clicks.
    """
    attempt_1 = run_verification_cycle(
        provider, target, action, expected_state,
        delay_seconds=FIRST_STABILIZATION_DELAY_SECONDS, attempt_number=1, click_time=click_time,
    )

    if attempt_1.verified:
        return {"attempt_1": attempt_1, "attempt_2": None}

    attempt_2 = run_verification_cycle(
        provider, target, action, expected_state,
        delay_seconds=SECOND_STABILIZATION_DELAY_SECONDS, attempt_number=2, click_time=click_time,
    )
    return {"attempt_1": attempt_1, "attempt_2": attempt_2}


def cmd_plan(target: str) -> None:
    print("=== RND-006B Attempt 2 — Stabilized Verification ===")
    print(f"Target: {target}  ({CLICK_ALLOWED_TARGETS[target]})")
    print(f"Expected verification state: {EXPECTED_VERIFICATION_STATEMENT_V2[target]}")
    print(f"Stabilization: {FIRST_STABILIZATION_DELAY_SECONDS}s before attempt 1; "
          f"+{SECOND_STABILIZATION_DELAY_SECONDS}s before attempt 2 if needed (max {MAX_VERIFICATION_ATTEMPTS} attempts, never a re-click)")
    print("Mode: PLAN ONLY — no screenshot captured, no API call made, no mouse action.")


def cmd_execute(target: str) -> None:
    load_dotenv(PROJECT_ROOT / ".env")
    model = os.environ.get("GEMINI_MODEL", "")
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key or not model:
        raise SystemExit("GEMINI_API_KEY / GEMINI_MODEL must be set in .env")

    print("=== RND-006B Attempt 2 --execute (grounding only) ===")
    title = get_foreground_window_title()
    print(f"Foreground window: {title!r}")
    if REQUIRE_FOREGROUND_SUBSTRING not in title.lower():
        result = RND006BAttempt2Result(
            test_id=f"RND006B-A2-{target}", target=target,
            foreground_before=title, click_result="ABORTED", failure_reason="FOREGROUND_MISMATCH",
            notes="Foreground window did not contain 'outlook' at capture time.",
        )
        save_pending(target, result)
        print("ABORTED: Outlook is not the foreground window.")
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
        result = RND006BAttempt2Result(
            test_id=f"RND006B-A2-{target}", target=target,
            pre_screenshot=capture.filename, foreground_before=title,
            vision_calls=attempt_count, click_result="ERROR",
            failure_reason="NETWORK_ERROR" if "Network" in (last_error or "") else "MODEL_ERROR",
            notes=last_error or "Unknown provider error",
        )
        save_pending(target, result)
        print("Grounding call failed after retries.")
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

    if structured is None:
        result = RND006BAttempt2Result(
            test_id=f"RND006B-A2-{target}", target=target,
            pre_screenshot=capture.filename, foreground_before=title,
            vision_calls=attempt_count, grounding_latency_ms=call_result.latency_ms,
            click_result="ERROR", failure_reason="GROUNDING_FAILURE",
            notes="Response was not schema-valid.",
        )
        save_pending(target, result)
        print("No valid coordinate returned.")
        return

    if structured.target.strip().lower() in {"send", "reply all", "reply_all"}:
        result = RND006BAttempt2Result(
            test_id=f"RND006B-A2-{target}", target=target,
            pre_screenshot=capture.filename, foreground_before=title,
            vision_calls=attempt_count, click_result="ABORTED",
            notes=f"Model returned blocked target={structured.target!r}. Aborting.",
        )
        save_pending(target, result)
        print(f"ABORTED: model returned a blocked target ({structured.target!r}).")
        return

    screen_w, screen_h = pyautogui.size()
    converted_x, converted_y = normalize_1000_to_pixels(structured.x, structured.y, screen_w, screen_h)
    converted_x, converted_y = round(converted_x), round(converted_y)
    coord_valid = coordinate_in_image_bounds(converted_x, converted_y, screen_w, screen_h)

    result = RND006BAttempt2Result(
        test_id=f"RND006B-A2-{target}", target=target,
        pre_screenshot=capture.filename,
        raw_x=structured.x, raw_y=structured.y,
        converted_x=converted_x, converted_y=converted_y,
        foreground_before=title,
        expected_state=EXPECTED_VERIFICATION_STATEMENT_V2[target],
        vision_calls=attempt_count, grounding_latency_ms=call_result.latency_ms,
        total_input_tokens=call_result.input_tokens, total_output_tokens=call_result.output_tokens,
        total_estimated_cost=estimate_cost("gemini", call_result.model, call_result.input_tokens, call_result.output_tokens)[0],
        click_result="PENDING" if coord_valid else "ABORTED",
        failure_reason=None if coord_valid else "COORDINATE_INVALID",
    )
    save_pending(target, result)

    print()
    print(f"Raw Gemini coordinate:       ({structured.x}, {structured.y})")
    print(f"Converted pixel coordinate:  ({converted_x}, {converted_y})")
    print(f"Coordinate valid:            {coord_valid}")
    print(f"Confidence:                  {structured.confidence}")
    print(f"Foreground window:           {title!r}")
    print(f"Expected verification state: {EXPECTED_VERIFICATION_STATEMENT_V2[target]}")
    print()
    print("NOT CLICKED YET. Awaiting approval, then re-run with --click.")


def cmd_click(target: str) -> None:
    load_dotenv(PROJECT_ROOT / ".env")
    ppath = pending_path(target)
    if not ppath.exists():
        raise SystemExit(f"No pending result for '{target}'. Run --execute first.")
    result = RND006BAttempt2Result.model_validate(json.loads(ppath.read_text(encoding="utf-8")))

    if result.click_result != "PENDING" or result.converted_x is None or result.converted_y is None:
        raise SystemExit(f"Pending result for '{target}' is not in a clickable state (click_result={result.click_result!r}).")

    result.approval_received = True

    print("=== RND-006B Attempt 2 --click ===")
    print(f"Target: {target}")
    print(f"Converted coordinate: ({result.converted_x}, {result.converted_y})")

    title_before_move = get_foreground_window_title()
    print(f"Foreground window (before move): {title_before_move!r}")
    if REQUIRE_FOREGROUND_SUBSTRING not in title_before_move.lower():
        result.click_result = "ABORTED"
        result.failure_reason = "FOREGROUND_MISMATCH"
        result.notes = "Foreground changed away from Outlook before move."
        save_pending(target, result)
        print("ABORTED: Outlook is not foreground. No movement, no click.")
        return

    screen_w, screen_h = pyautogui.size()
    if not coordinate_in_image_bounds(result.converted_x, result.converted_y, screen_w, screen_h):
        result.click_result = "ABORTED"
        result.failure_reason = "COORDINATE_INVALID"
        save_pending(target, result)
        print("ABORTED: coordinate outside current screen bounds.")
        return

    assert pyautogui.FAILSAFE is True, "Refusing to act: FAILSAFE disabled."
    pyautogui.moveTo(result.converted_x, result.converted_y, duration=MOVE_DURATION_SECONDS)
    result.movement_executed = True
    print(f"Cursor moved to ({result.converted_x}, {result.converted_y}).")

    title_before_click = get_foreground_window_title()
    result.foreground_before_click = title_before_click
    print(f"Foreground window (immediately before click): {title_before_click!r}")
    if REQUIRE_FOREGROUND_SUBSTRING not in title_before_click.lower():
        result.click_result = "ABORTED"
        result.failure_reason = "FOREGROUND_MISMATCH"
        result.notes = "Foreground changed between move and click. Cursor moved but NO CLICK performed."
        save_pending(target, result)
        print("ABORTED: foreground changed after move, before click. NO CLICK performed.")
        return

    click_time = time.perf_counter()
    pyautogui.click()  # single click only
    result.click_executed = True
    result.click_timestamp = datetime.now().isoformat()
    print("CLICK performed (single click).")

    model = os.environ.get("GEMINI_MODEL", "")
    api_key = os.environ.get("GEMINI_API_KEY", "")
    provider = GeminiProvider(
        api_key=api_key, model=model, timeout_seconds=float(os.environ.get("VISION_REQUEST_TIMEOUT_SECONDS", "30"))
    )
    action_desc = f"Clicked {CLICK_ALLOWED_TARGETS[target]}"

    print(f"Waiting {FIRST_STABILIZATION_DELAY_SECONDS}s stabilization delay before first verification...")
    attempts = run_stabilized_verification(provider, target, action_desc, result.expected_state, click_time=click_time)

    result.verification_attempt_1 = attempts["attempt_1"]
    result.verification_attempt_2 = attempts["attempt_2"]

    a1_verified = attempts["attempt_1"].verified
    a2_verified = attempts["attempt_2"].verified if attempts["attempt_2"] else None
    result.ai_verification_final = bool(a1_verified) or bool(a2_verified)

    result.vision_calls += 1 if attempts["attempt_1"] else 0
    result.vision_calls += 1 if attempts["attempt_2"] else 0
    for a in (attempts["attempt_1"], attempts["attempt_2"]):
        if a:
            result.total_input_tokens = (result.total_input_tokens or 0) + (a.input_tokens or 0)
            result.total_output_tokens = (result.total_output_tokens or 0) + (a.output_tokens or 0)
            if a.estimated_cost is not None:
                result.total_estimated_cost = round((result.total_estimated_cost or 0) + a.estimated_cost, 6)

    save_pending(target, result)

    print()
    print(f"Verification attempt 1: verified={a1_verified} detected_state={attempts['attempt_1'].detected_state!r} confidence={attempts['attempt_1'].confidence}")
    if attempts["attempt_2"]:
        print(f"Verification attempt 2: verified={a2_verified} detected_state={attempts['attempt_2'].detected_state!r} confidence={attempts['attempt_2'].confidence}")
    print()
    print("Click + verification complete. Please visually confirm, then run:")
    print(f"  python rnd/experiments/rnd006b_attempt2_safe_click_execution.py --target {target} --confirm pass")
    print("  (or --confirm fail)")


def cmd_confirm(target: str, verdict: str) -> None:
    ppath = pending_path(target)
    if not ppath.exists():
        raise SystemExit(f"No pending result for '{target}'. Run --execute and --click first.")
    result = RND006BAttempt2Result.model_validate(json.loads(ppath.read_text(encoding="utf-8")))

    if not result.click_executed:
        raise SystemExit(f"'{target}' was never clicked — nothing to confirm.")

    human_verified = verdict == "pass"
    result.human_verification = human_verified
    result.click_result = compute_click_result(human_verified)

    a1_verified = result.verification_attempt_1.verified if result.verification_attempt_1 else None
    a2_verified = result.verification_attempt_2.verified if result.verification_attempt_2 else None
    a1_had_error = bool(result.verification_attempt_1.error) if result.verification_attempt_1 else False
    result.verification_classification = classify_verification_outcome(
        a1_verified, a2_verified, human_verified, attempt1_had_error=a1_had_error
    )

    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing = json.loads(RESULTS_PATH.read_text(encoding="utf-8")) if RESULTS_PATH.exists() else {"cases": []}
    existing["cases"] = [c for c in existing["cases"] if c["target"] != target] + [json.loads(result.model_dump_json())]
    existing["timestamp"] = datetime.now().isoformat()
    RESULTS_PATH.write_text(json.dumps(existing, indent=2), encoding="utf-8")

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(_build_report(existing["cases"]), encoding="utf-8")

    ppath.unlink()
    print(f"Recorded {target}: click_result={result.click_result} verification_classification={result.verification_classification}")
    print(f"Updated {RESULTS_PATH.relative_to(PROJECT_ROOT)} and {REPORT_PATH.relative_to(PROJECT_ROOT)}")


def _build_report(cases: list[dict]) -> str:
    lines = ["# RND-006B Attempt 2 — Stabilized Verification Summary — Gemini (gemini-3.6-flash)", ""]
    lines.append("ONE controlled click per target. Verification retries are wait+screenshot+re-verify only — never a re-click.")
    lines.append("")
    lines.append("| target | converted (x,y) | click | attempt1 verified | attempt2 verified | human | click_result | classification |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for c in cases:
        a1 = c["verification_attempt_1"]["verified"] if c.get("verification_attempt_1") else None
        a2 = c["verification_attempt_2"]["verified"] if c.get("verification_attempt_2") else None
        lines.append(
            f"| {c['target']} | ({c['converted_x']},{c['converted_y']}) | {c['click_executed']} | "
            f"{a1} | {a2} | {c['human_verification']} | {c['click_result']} | {c['verification_classification']} |"
        )
    lines.append("")
    passed = sum(1 for c in cases if c["click_result"] == "PASS")
    lines.append(f"Click PASS: {passed}/{len(cases)}")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="RND-006B Attempt 2: safe click with stabilized verification")
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
