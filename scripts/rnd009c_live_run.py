"""RND-009C bounded live-run driver.

Reuses app/playbook/find_open_email_steps.py::FindOpenEmailSteps — the
exact same step logic the real QThread worker (app/workers/
find_open_email_worker.py) runs for the GUI. Bounded session approval:
one upfront yes covers the entire chained sequence from Windows key
through email-open verification, with no mid-run pause (RND-009B's
first live attempt already proved why a mid-run pause is unsafe here).

Usage:
    python scripts/rnd009c_live_run.py --run-bounded
    python scripts/rnd009c_live_run.py --finalize [--label NAME]
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.outlook.find_email import FindOpenEmailSteps  # noqa: E402
from app.outlook.launch import _provider  # noqa: E402
from app.safety.abort_controller import AbortController  # noqa: E402
from rnd.models.find_open_email import RND009CResult  # noqa: E402

PENDING_PATH = PROJECT_ROOT / "results" / "raw" / "rnd009c_pending.json"


def _save(steps: FindOpenEmailSteps) -> None:
    PENDING_PATH.parent.mkdir(parents=True, exist_ok=True)
    PENDING_PATH.write_text(steps.result.model_dump_json(indent=2), encoding="utf-8")


def cmd_run_bounded() -> None:
    print("=== RND-009C --run-bounded (no further approval will be requested) ===")
    steps = FindOpenEmailSteps(AbortController(), *_provider())
    print(f"Target subject: {steps.result.target_subject!r}")
    print(f"Target sender:  {steps.result.target_sender!r}")
    print()

    steps.start_session()  # pre-RND-009D hardening: total-elapsed timer starts here

    ok = steps.run_launch_and_readiness()
    if ok:
        print("Phase 1-2 (launch + readiness): PASS")
        ok = steps.find_target_email()
    if ok:
        print("Phase 3a (find target email): PASS")
        ok = steps.click_target_email()
    if ok:
        print("Phase 3b (click target email): PASS")
        ok = steps.verify_email_opened()
    if ok:
        print("Phase 4 (verify correct email opened): PASS")

    steps.finalize_session()  # called exactly once, on every terminal outcome
    _save(steps)

    r = steps.result
    print()
    print(f"Overall result: {r.result}")
    print(f"Total E2E wall-clock elapsed: {r.total_elapsed_ms} ms (session_start={r.session_start}, session_end={r.session_end})")
    print(f"Total Vision latency (subset of the above): {r.total_latency_ms} ms")
    if r.failure_reason:
        print(f"Failure reason: {r.failure_reason}")
        print(f"Notes: {r.notes}")
    print()
    print(f"Outlook grounding: raw=({r.outlook_grounding_raw_x},{r.outlook_grounding_raw_y}) converted=({r.outlook_converted_x},{r.outlook_converted_y})")
    print(f"Outlook launch duration: {r.outlook_launch_duration_ms} ms")
    for a in r.readiness_attempts:
        print(f"Readiness attempt {a.attempt_number}: ready_for_interaction={a.ready_for_interaction} splash_screen_visible={a.splash_screen_visible} detected_state={a.detected_state!r}")
    if r.email_grounding:
        print(f"Email grounding: target_visible={r.email_grounding.target_visible} matched_subject={r.email_grounding.matched_subject!r} matched_sender={r.email_grounding.matched_sender!r}")
        print(f"Email raw=({r.email_raw_x},{r.email_raw_y}) converted=({r.email_converted_x},{r.email_converted_y})")
    print(f"Email click executed: {r.email_open_click_executed} at {r.email_open_click_timestamp}")
    for a in r.email_open_verification_attempts:
        print(f"Email-open verification attempt {a.attempt_number}: email_open={a.email_open} subject_match={a.subject_match} sender_match={a.sender_match} body_visible={a.body_visible}")
    print()
    print(f"Vision calls: {r.total_vision_calls}, tokens in/out: {r.total_input_tokens}/{r.total_output_tokens}, cost: ${r.total_estimated_cost}, latency: {r.total_latency_ms}ms")
    print(f"Mouse clicks: {r.mouse_click_count}, keyboard actions: {r.keyboard_action_count}, send_click_count: {r.send_click_count}")
    print()
    print("Run --finalize [--label NAME] to write results/raw + results/reports.")


def cmd_finalize(label: str = "") -> None:
    if not PENDING_PATH.exists():
        print("No pending RND-009C run to finalize. Run --run-bounded first.")
        return
    result = RND009CResult.model_validate(json.loads(PENDING_PATH.read_text(encoding="utf-8")))

    suffix = f"_{label}" if label else ""
    results_path = PROJECT_ROOT / "results" / "raw" / f"rnd009c_find_open_email_results{suffix}.json"
    report_path = PROJECT_ROOT / "results" / "reports" / f"rnd009c_find_open_email_summary{suffix}.md"

    results_path.parent.mkdir(parents=True, exist_ok=True)
    results_path.write_text(
        json.dumps({"timestamp": datetime.now().isoformat(), "case": result.model_dump(mode="json")}, indent=2),
        encoding="utf-8",
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(_build_report(result), encoding="utf-8")
    PENDING_PATH.unlink()
    print(f"Finalized. Overall result: {result.result}")
    print(f"Written: {results_path.relative_to(PROJECT_ROOT)}, {report_path.relative_to(PROJECT_ROOT)}")


def _build_report(result: RND009CResult) -> str:
    lines = ["# RND-009C Find + Open Email Playbook Summary — Gemini (gemini-3.6-flash)", ""]
    lines.append(f"Overall result: {result.result}")
    lines.append(f"Failure reason: {result.failure_reason}")
    lines.append(f"Target subject: {result.target_subject}")
    lines.append(f"Target sender: {result.target_sender}")
    lines.append("")
    lines.append("## Timing")
    lines.append(f"- session_start: {result.session_start}")
    lines.append(f"- session_end: {result.session_end}")
    lines.append(f"- **Total E2E wall-clock elapsed: {result.total_elapsed_ms} ms** (waits, UI interaction, provider calls, mouse/keyboard, stabilization delays, verification — everything)")
    lines.append(f"- Total Vision latency (subset of the above, API call time only): {result.total_latency_ms} ms")
    lines.append("")
    lines.append("## Phase 1-2: Launch + Readiness")
    lines.append(f"- outlook_launch_duration_ms: {result.outlook_launch_duration_ms}")
    lines.append(f"- ready_for_interaction: {result.ready_for_interaction}")
    for a in result.readiness_attempts:
        lines.append(f"  - attempt {a.attempt_number}: ready_for_interaction={a.ready_for_interaction} splash_screen_visible={a.splash_screen_visible}")
    lines.append("")
    lines.append("## Phase 3: Find Target Email")
    if result.email_grounding:
        lines.append(f"- target_visible: {result.email_grounding.target_visible}")
        lines.append(f"- matched_subject: {result.email_grounding.matched_subject!r}")
        lines.append(f"- matched_sender: {result.email_grounding.matched_sender!r}")
    lines.append(f"- raw: ({result.email_raw_x}, {result.email_raw_y})")
    lines.append(f"- converted: ({result.email_converted_x}, {result.email_converted_y})")
    lines.append(f"- email_open_click_executed: {result.email_open_click_executed}")
    lines.append("")
    lines.append("## Phase 4: Verify Email Opened")
    for a in result.email_open_verification_attempts:
        lines.append(
            f"- attempt {a.attempt_number}: email_open={a.email_open} subject_match={a.subject_match} "
            f"sender_match={a.sender_match} body_visible={a.body_visible} "
            f"subject_detected={a.subject_detected!r} sender_detected={a.sender_detected!r}"
        )
    lines.append("")
    lines.append("## Step-level metrics")
    for name, m in (
        ("LAUNCH_OUTLOOK", result.launch_outlook_metrics),
        ("VERIFY_OUTLOOK_READY", result.verify_outlook_ready_metrics),
        ("FIND_EMAIL", result.find_email_metrics),
        ("VERIFY_EMAIL_OPENED", result.verify_email_opened_metrics),
    ):
        lines.append(f"- {name}: calls={m.vision_calls} tokens_in={m.input_tokens} tokens_out={m.output_tokens} cost=${m.estimated_cost} latency={m.latency_ms}ms")
    lines.append("")
    lines.append(f"Total vision calls: {result.total_vision_calls}")
    lines.append(f"Total input tokens: {result.total_input_tokens}")
    lines.append(f"Total output tokens: {result.total_output_tokens}")
    lines.append(f"Total estimated cost: ${result.total_estimated_cost}")
    lines.append(f"Total latency: {result.total_latency_ms} ms")
    lines.append(f"Mouse clicks: {result.mouse_click_count}, keyboard actions: {result.keyboard_action_count}")
    lines.append(f"Human interventions: {result.human_interventions}, safety aborts: {result.safety_aborts}")
    lines.append(f"send_click_count: {result.send_click_count}")
    return "\n".join(lines) + "\n"


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="RND-009C bounded live-run driver")
    parser.add_argument("--run-bounded", action="store_true")
    parser.add_argument("--finalize", action="store_true")
    parser.add_argument("--label", default="")
    args = parser.parse_args()

    if args.finalize:
        cmd_finalize(args.label)
    elif args.run_bounded:
        cmd_run_bounded()
    else:
        print("No command given. Use --run-bounded (after the one upfront approval) or --finalize.")


if __name__ == "__main__":
    main()
