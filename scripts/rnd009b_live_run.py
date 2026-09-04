"""RND-009B bounded live-run driver.

Reuses app/playbook/outlook_launch_steps.py::OutlookLaunchSteps — the
exact same step logic the real QThread worker (app/workers/
outlook_launch_worker.py) runs for the GUI.

BOUNDED SESSION APPROVAL: a single upfront approval covers the entire
press-key-through-verify sequence. There is deliberately no mid-run
approval pause — an earlier multi-gate design (separate --capture-
search / --ground-search / --approve-target / --open-outlook
invocations) failed its first live attempt precisely because leaving
the automation context to show intermediate results for approval
stole foreground away from Windows Search, invalidating the state
being tested. Safety during the uninterrupted run is enforced entirely
by OutlookLaunchSteps' own validation gates (confidence threshold,
bbox check, repeated search-state checks, abort-flag checks), not by a
human answering a question mid-flight.

Usage:
    python scripts/rnd009b_live_run.py --run-bounded
        Runs the complete sequence in one process, printing a full
        report only once the desktop state is no longer time-sensitive
        (i.e. after success/failure/abort). Requires the caller to have
        already obtained the one bounded approval before running this.

    python scripts/rnd009b_live_run.py --finalize
        Writes results/raw + results/reports from the last run's
        pending state (kept as a separate step so a failed run's
        evidence can be reviewed before deciding whether to finalize
        it as-is or retry fresh).
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.outlook.launch import OutlookLaunchResult, OutlookLaunchSteps, _provider  # noqa: E402
from app.safety.abort_controller import AbortController  # noqa: E402

PENDING_PATH = PROJECT_ROOT / "results" / "raw" / "rnd009b_pending.json"
RESULTS_PATH = PROJECT_ROOT / "results" / "raw" / "rnd009b_outlook_launch_results.json"
REPORT_PATH = PROJECT_ROOT / "results" / "reports" / "rnd009b_outlook_launch_summary.md"


def _save(steps: OutlookLaunchSteps) -> None:
    PENDING_PATH.parent.mkdir(parents=True, exist_ok=True)
    PENDING_PATH.write_text(steps.result.model_dump_json(indent=2), encoding="utf-8")


def cmd_run_bounded() -> None:
    """Steps 1-22 of the approved bounded flow, uninterrupted. Human
    approval for the whole sequence is assumed already granted (this
    command is only ever run after that one upfront yes/no) — it is
    recorded as True immediately once grounding validation passes, with
    no pause in between. If any validation gate fails, the run stops
    itself (no click) and this prints the failure only at the end."""
    print("=== RND-009B --run-bounded (no further approval will be requested) ===")
    steps = OutlookLaunchSteps(AbortController(), *_provider())

    ok = steps.press_windows_key()
    ok = ok and steps.type_search_query("Outlook")
    capture = steps.capture_search_screenshot() if ok else None
    ok = ok and capture is not None
    ok = ok and steps.ground_search_result(capture)

    if ok:
        # Bounded session approval, granted once before this run started,
        # applies here automatically — no mid-run question.
        steps.record_human_approval(True)
        ok = steps.click_outlook_result()

    ok = ok and steps.poll_for_outlook_foreground()
    ok = ok and steps.enforce_maximized()
    ok = ok and steps.verify_outlook_readiness()

    _save(steps)

    # Only now — after Outlook is either verified ready, or the run has
    # definitively stopped — is the desktop state no longer time-sensitive,
    # so only now do we print the full report.
    r = steps.result
    print()
    print(f"Overall result: {r.result}")
    if r.failure_reason:
        print(f"Failure reason: {r.failure_reason}")
        print(f"Notes: {r.notes}")
    print()
    print(f"Windows key: {r.windows_key_timestamp}")
    print(f"Search typed: {r.search_query_typed_timestamp}")
    print(f"Search screenshot: {r.search_screenshot}")
    if r.grounding:
        print(f"Grounding: label={r.grounding.result_label!r} raw=({r.raw_x},{r.raw_y}) converted=({r.converted_x},{r.converted_y}) confidence={r.grounding.confidence}")
    print(f"Click executed: {r.outlook_launch_click_executed} at {r.outlook_click_timestamp}")
    for a in r.readiness_attempts:
        print(f"Readiness attempt {a.attempt_number} (waited {a.delay_seconds}s): splash_screen_visible={a.splash_screen_visible} ready_for_interaction={a.ready_for_interaction} detected_state={a.detected_state!r} confidence={a.confidence}")
    print(f"Launch duration: {r.outlook_launch_duration_ms} ms, foreground_verified: {r.foreground_verified}")
    print(f"Maximize required: {r.maximize_required}, executed: {r.maximize_executed}, duration: {r.maximize_duration_ms} ms")
    print(f"Outlook ready at: {r.outlook_ready_at} ({r.outlook_ready_ms} ms since launch start)")
    print(f"Provider retries: {r.provider_retries}")
    if r.launch_verification:
        print(f"Vision verification: outlook_visible={r.launch_verification.outlook_visible} confidence={r.launch_verification.confidence}")
    print()
    print(f"Vision calls: {r.total_vision_calls}, tokens in/out: {r.total_input_tokens}/{r.total_output_tokens}, cost: ${r.total_estimated_cost}, latency: {r.total_latency_ms}ms")
    print(f"Mouse clicks: {r.mouse_click_count}, keyboard actions: {r.keyboard_action_count}, send_click_count: {r.send_click_count}")
    print()
    print("Run --finalize to write results/raw + results/reports.")


def cmd_check_readiness() -> None:
    """Standalone readiness check against whatever Outlook window is
    ALREADY open right now — does not press the Windows key, does not
    type, does not click anything. Used to validate the readiness-
    hardening logic live without repeating the launch sequence."""
    print("=== RND-009B --check-readiness (no launch actions, verification only) ===")
    steps = OutlookLaunchSteps(AbortController(), *_provider())
    steps.result.foreground_verified = True  # standalone check assumes Outlook is already foreground

    ok = steps.enforce_maximized()
    ok = ok and steps.verify_outlook_readiness()

    r = steps.result
    print()
    print(f"Overall result: {r.result}")
    if r.failure_reason:
        print(f"Failure reason: {r.failure_reason}")
        print(f"Notes: {r.notes}")
    for a in r.readiness_attempts:
        print(f"Attempt {a.attempt_number} (waited {a.delay_seconds}s): outlook_visible={a.outlook_visible} "
              f"splash_screen_visible={a.splash_screen_visible} ready_for_interaction={a.ready_for_interaction} "
              f"detected_state={a.detected_state!r} confidence={a.confidence} screenshot={a.screenshot}")
    print()
    print(f"Vision calls: {r.total_vision_calls}, cost: ${r.total_estimated_cost}")


def cmd_finalize(label: str = "") -> None:
    if not PENDING_PATH.exists():
        print("No pending RND-009B run to finalize. Run --run-bounded first.")
        return
    result = OutlookLaunchResult.model_validate(json.loads(PENDING_PATH.read_text(encoding="utf-8")))

    suffix = f"_{label}" if label else ""
    results_path = PROJECT_ROOT / "results" / "raw" / f"rnd009b_outlook_launch_results{suffix}.json"
    report_path = PROJECT_ROOT / "results" / "reports" / f"rnd009b_outlook_launch_summary{suffix}.md"

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


def _build_report(result: OutlookLaunchResult) -> str:
    lines = ["# RND-009B Outlook Launch Playbook Summary — Gemini (gemini-3.6-flash)", ""]
    lines.append(f"Overall result: {result.result}")
    lines.append(f"Failure reason: {result.failure_reason}")
    lines.append("")
    lines.append("## Windows Search")
    lines.append(f"- windows_key_timestamp: {result.windows_key_timestamp}")
    lines.append(f"- search_query_typed_timestamp: {result.search_query_typed_timestamp}")
    lines.append(f"- search_screenshot: {result.search_screenshot}")
    lines.append(f"- foreground_before_search_check: {result.foreground_before_search_check}")
    lines.append("")
    lines.append("## Grounding")
    lines.append(f"- raw: ({result.raw_x}, {result.raw_y})")
    lines.append(f"- converted: ({result.converted_x}, {result.converted_y})")
    lines.append(f"- coordinate_in_screen_bounds: {result.coordinate_in_screen_bounds}")
    lines.append(f"- human_target_approved (bounded session approval): {result.human_target_approved}")
    lines.append("")
    lines.append("## Launch")
    lines.append(f"- outlook_launch_click_executed: {result.outlook_launch_click_executed}")
    lines.append(f"- outlook_click_timestamp: {result.outlook_click_timestamp}")
    lines.append(f"- outlook_launch_duration_ms: {result.outlook_launch_duration_ms}")
    lines.append(f"- foreground_after_launch: {result.foreground_after_launch}")
    lines.append(f"- foreground_verified: {result.foreground_verified}")
    lines.append("")
    lines.append("## Maximize enforcement")
    lines.append(f"- maximize_required: {result.maximize_required}")
    lines.append(f"- maximize_executed: {result.maximize_executed}")
    lines.append(f"- maximize_duration_ms: {result.maximize_duration_ms}")
    lines.append(f"- post_maximize_screenshot: {result.post_maximize_screenshot}")
    lines.append("")
    lines.append("## Readiness (splash screen vs usable UI, hardened)")
    lines.append(f"- ready_for_interaction: {result.ready_for_interaction}")
    for a in result.readiness_attempts:
        lines.append(
            f"- attempt {a.attempt_number} (waited {a.delay_seconds}s): "
            f"outlook_visible={a.outlook_visible} splash_screen_visible={a.splash_screen_visible} "
            f"ready_for_interaction={a.ready_for_interaction} detected_state={a.detected_state!r} "
            f"confidence={a.confidence} screenshot={a.screenshot}"
        )
    lines.append("")
    lines.append(f"outlook_launch_started_at: {result.outlook_launch_started_at}")
    lines.append(f"outlook_ready_at: {result.outlook_ready_at} ({result.outlook_ready_ms} ms since launch start)")
    lines.append(f"provider_retries: {result.provider_retries}")
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

    parser = argparse.ArgumentParser(description="RND-009B bounded live-run driver")
    parser.add_argument("--run-bounded", action="store_true")
    parser.add_argument("--check-readiness", action="store_true")
    parser.add_argument("--finalize", action="store_true")
    parser.add_argument("--label", default="", help="Suffix for the finalized result/report filenames (keeps this run separate from prior attempts).")
    args = parser.parse_args()

    if args.finalize:
        cmd_finalize(args.label)
    elif args.check_readiness:
        cmd_check_readiness()
    elif args.run_bounded:
        cmd_run_bounded()
    else:
        print("No command given. Use --run-bounded (after the one upfront approval), --check-readiness, or --finalize.")


if __name__ == "__main__":
    main()
