"""RND-007A — Controlled Text Entry.

Determines whether the POC can safely type a fixed, known sentence into
the already-focused Outlook reply editor (RND-006B proved the editor can
be safely clicked/focused) and whether the expected text can be verified
afterward. No contextual AI-generated reply content, no Send, no full
workflow — text entry mechanics only.

Typing correctness (`typing_result`) and verification correctness
(`vision_verification`, `human_verification`) are recorded as separate
fields, never collapsed into one number — same philosophy as RND-006B.

Safety, unchanged in spirit from every prior live stage:
  - Outlook foreground check before typing
  - explicit human approval required before typing (obtained in chat,
    same as every prior --execute/--click gate)
  - pyautogui.FAILSAFE left at its default True, never touched
  - typing uses pyautogui.write() only — no clipboard, no paste
  - Enter, Ctrl+Enter, and Alt+S are never sent anywhere in this module
    (grep-verified by tests/test_text_entry.py, same technique used to
    prove the mouse-click function was unreachable in RND-006A)
  - Send remains structurally unreachable — no click/hotkey path to it
    exists in this file at all

Run (from outlook-vision-poc/):
    python rnd/experiments/rnd007a_controlled_text_entry.py
    python rnd/experiments/rnd007a_controlled_text_entry.py --type
    python rnd/experiments/rnd007a_controlled_text_entry.py --confirm pass
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
from rnd.models.text_entry import FIXED_TEST_TEXT, RND007ACaseResult, TextEntryVerificationResponse  # noqa: E402
from rnd.providers.base import VisionProviderError  # noqa: E402
from rnd.providers.gemini_provider import GeminiProvider  # noqa: E402

VERIFICATION_PROMPT_PATH = PROJECT_ROOT / "rnd" / "prompts" / "text_entry_verification_v1.txt"
VERIFICATION_PROMPT_VERSION = "text_entry_verification_v1"

RESULTS_PATH = PROJECT_ROOT / "results" / "raw" / "rnd007a_controlled_text_entry_results.json"
REPORT_PATH = PROJECT_ROOT / "results" / "reports" / "rnd007a_controlled_text_entry_summary.md"
RAW_RESPONSES_DIR = PROJECT_ROOT / "results" / "raw" / "provider_responses" / "rnd007a"
PENDING_PATH = PROJECT_ROOT / "results" / "raw" / "rnd007a_pending.json"
PRICING_PATH = PROJECT_ROOT / "config" / "model_pricing.json"

REQUIRE_FOREGROUND_SUBSTRING = "outlook"
STABILIZATION_DELAY_SECONDS = 1.5
TYPE_INTERVAL_SECONDS = 0.03


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


def save_pending(result: RND007ACaseResult) -> None:
    PENDING_PATH.parent.mkdir(parents=True, exist_ok=True)
    PENDING_PATH.write_text(result.model_dump_json(indent=2), encoding="utf-8")


def cmd_plan() -> None:
    print("=== RND-007A Controlled Text Entry ===")
    print("Target: reply_editor (must already be open/focused — precondition, not re-clicked here)")
    print(f"Text to type: {FIXED_TEST_TEXT!r}")
    print("Mode: PLAN ONLY — no screenshot captured, no typing, no API call.")
    print("Next: re-run with --type after preparing Outlook and getting approval.")


def cmd_type() -> None:
    """Foreground check -> type (single pyautogui.write call) -> wait -> post-screenshot -> AI verification. No Enter/Ctrl+Enter/Alt+S/Send anywhere."""
    load_dotenv(PROJECT_ROOT / ".env")

    print("=== RND-007A --type ===")
    title = get_foreground_window_title()
    print(f"Foreground window: {title!r}")

    result = RND007ACaseResult(foreground_before=title, expected_text=FIXED_TEST_TEXT)

    if REQUIRE_FOREGROUND_SUBSTRING not in title.lower():
        result.typing_result = "ABORTED"
        result.result = "ABORTED"
        result.failure_reason = "FOREGROUND_MISMATCH"
        result.notes = "Foreground window did not contain 'outlook'. No typing performed."
        save_pending(result)
        print("ABORTED: Outlook is not the foreground window. No typing performed.")
        return

    try:
        pre_capture = capture_screen(DEFAULT_OUTPUT_DIR)
        result.pre_screenshot = pre_capture.filename
        print(f"Pre-typing screenshot: {pre_capture.filename}")
    except ScreenCaptureError as exc:
        print(f"Pre-typing capture FAILED: {exc}")
        result.typing_result = "ERROR"
        result.result = "ERROR"
        result.failure_reason = "OTHER"
        result.notes = str(exc)
        save_pending(result)
        return

    result.approval_received = True

    assert pyautogui.FAILSAFE is True, "Refusing to type: FAILSAFE has been disabled somewhere — this must never happen."

    # The ONLY keyboard action in this entire module. No Enter, no
    # Ctrl+Enter, no Alt+S, no Send — verified by test_text_entry.py's
    # source inspection, same technique as RND-006A's click-unreachability test.
    pyautogui.write(FIXED_TEST_TEXT, interval=TYPE_INTERVAL_SECONDS)
    result.typed_text = FIXED_TEST_TEXT
    result.text_entry_executed = True
    result.typing_result = "PASS"
    save_pending(result)  # persist the typing record immediately — a later verification-step
                           # failure must never lose the fact that typing itself already succeeded
    print(f"Typed (single write call, no Enter/Ctrl+Enter/Alt+S sent): {FIXED_TEST_TEXT!r}")

    print(f"Waiting {STABILIZATION_DELAY_SECONDS}s before verification screenshot...")
    time.sleep(STABILIZATION_DELAY_SECONDS)

    try:
        post_capture = capture_screen(DEFAULT_OUTPUT_DIR)
        result.post_screenshot = post_capture.filename
        print(f"Post-typing screenshot: {post_capture.filename}")
    except ScreenCaptureError as exc:
        result.notes = f"Post-typing screenshot capture FAILED: {exc}. Vision verification skipped."
        save_pending(result)
        print(f"WARNING: {result.notes}")
        return

    model = os.environ.get("GEMINI_MODEL", "")
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if api_key and model:
        provider = GeminiProvider(
            api_key=api_key, model=model, timeout_seconds=float(os.environ.get("VISION_REQUEST_TIMEOUT_SECONDS", "30"))
        )
        prompt_text = VERIFICATION_PROMPT_PATH.read_text(encoding="utf-8").format(expected_text=FIXED_TEST_TEXT)
        try:
            v_call = provider.analyze_screen(Path(post_capture.path), "Verify typed text", prompt_text)
            result.verification_latency_ms = v_call.latency_ms
            result.vision_calls += 1
            result.input_tokens = v_call.input_tokens
            result.output_tokens = v_call.output_tokens
            result.estimated_cost, _ = estimate_cost("gemini", v_call.model, v_call.input_tokens, v_call.output_tokens)

            RAW_RESPONSES_DIR.mkdir(parents=True, exist_ok=True)
            raw_path = RAW_RESPONSES_DIR / f"reply_editor_verify_{datetime.now().isoformat().replace(':', '-')}.json"
            raw_path.write_text(
                json.dumps({"provider": "gemini", "model": v_call.model, "raw_text": v_call.raw_text, "parsed_json": v_call.parsed_json}, indent=2),
                encoding="utf-8",
            )

            structured: Optional[TextEntryVerificationResponse] = None
            try:
                if v_call.parsed_json is not None:
                    structured = TextEntryVerificationResponse.model_validate(v_call.parsed_json)
            except ValidationError as exc:
                print(f"Verification schema validation FAILED: {exc}")

            if structured:
                result.vision_verification = structured.verified
                result.detected_text = structured.detected_text
                result.exact_match = structured.detected_text.strip() == FIXED_TEST_TEXT.strip()
                result.verification_confidence = structured.confidence
                result.verification_reason = structured.reason
                print(f"AI verification: verified={structured.verified} detected_text={structured.detected_text!r} confidence={structured.confidence}")
            else:
                print("AI verification response was not schema-valid.")
        except VisionProviderError as exc:
            print(f"Verification call FAILED: {exc}. Human confirmation will be authoritative regardless.")
    else:
        print("No Gemini credentials configured — skipping AI verification; human confirmation will be authoritative.")

    save_pending(result)
    print()
    print("Typing + verification complete. Please visually confirm, then run:")
    print("  python rnd/experiments/rnd007a_controlled_text_entry.py --confirm pass")
    print("  (or --confirm fail)")


def cmd_confirm(verdict: str) -> None:
    if not PENDING_PATH.exists():
        raise SystemExit("No pending result. Run --type first.")
    result = RND007ACaseResult.model_validate(json.loads(PENDING_PATH.read_text(encoding="utf-8")))

    if not result.text_entry_executed:
        raise SystemExit("Text was never typed — nothing to confirm.")

    result.human_verification = verdict == "pass"
    result.result = "PASS" if result.human_verification else "FAIL"

    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing = json.loads(RESULTS_PATH.read_text(encoding="utf-8")) if RESULTS_PATH.exists() else {"cases": []}
    existing["cases"] = [result.model_dump(mode="json")]  # single fixed-text test — one case
    existing["timestamp"] = datetime.now().isoformat()
    RESULTS_PATH.write_text(json.dumps(existing, indent=2), encoding="utf-8")

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(_build_report(result), encoding="utf-8")

    PENDING_PATH.unlink()
    print(f"Recorded: typing_result={result.typing_result} vision_verification={result.vision_verification} human_verification={result.human_verification} overall={result.result}")
    print(f"Updated {RESULTS_PATH.relative_to(PROJECT_ROOT)} and {REPORT_PATH.relative_to(PROJECT_ROOT)}")


def _build_report(result: RND007ACaseResult) -> str:
    lines = ["# RND-007A Controlled Text Entry Summary — Gemini (gemini-3.6-flash)", ""]
    lines.append(f"Expected text: {result.expected_text!r}")
    lines.append("")
    lines.append(f"- typing_result: {result.typing_result}")
    lines.append(f"- vision_verification: {result.vision_verification}")
    lines.append(f"- detected_text: {result.detected_text!r}")
    lines.append(f"- exact_match: {result.exact_match}")
    lines.append(f"- human_verification: {result.human_verification}")
    lines.append(f"- overall result: {result.result}")
    lines.append("")
    lines.append(f"Verification latency: {result.verification_latency_ms} ms")
    lines.append(f"Vision calls: {result.vision_calls}")
    lines.append(f"Estimated cost: {result.estimated_cost}")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="RND-007A controlled text entry")
    parser.add_argument("--type", action="store_true")
    parser.add_argument("--confirm", choices=["pass", "fail"])
    args = parser.parse_args()

    if args.confirm:
        cmd_confirm(args.confirm)
    elif args.type:
        cmd_type()
    else:
        cmd_plan()


if __name__ == "__main__":
    main()
