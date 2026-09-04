"""RND-009D bounded live-run driver.

Reuses app/playbook/reply_draft_steps.py::ReplyDraftSteps — the exact
same step logic the real QThread worker (app/workers/
reply_draft_worker.py) runs for the GUI. Bounded session approval: one
upfront yes covers the entire chained sequence from Windows key through
draft verification, with no mid-run pause. Stops at DRAFT_READY — no
Send code path exists anywhere in this chain.

Usage:
    python scripts/rnd009d_live_run.py --run-bounded
    python scripts/rnd009d_live_run.py --finalize [--label NAME]
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.outlook.launch import _provider  # noqa: E402
from app.outlook.draft import ReplyDraftSteps  # noqa: E402
from app.safety.abort_controller import AbortController  # noqa: E402
from rnd.models.reply_draft import RND009DResult  # noqa: E402

PENDING_PATH = PROJECT_ROOT / "results" / "raw" / "rnd009d_pending.json"


def _save(steps: ReplyDraftSteps) -> None:
    PENDING_PATH.parent.mkdir(parents=True, exist_ok=True)
    PENDING_PATH.write_text(steps.result.model_dump_json(indent=2), encoding="utf-8")


def cmd_run_bounded(subject: str = "", sender: str = "") -> None:
    print("=== RND-009D --run-bounded (no further approval will be requested) ===")
    steps = ReplyDraftSteps(AbortController(), *_provider())
    if subject:
        steps.find_open.result.target_subject = subject
        steps.result.target_subject = subject
    if sender:
        steps.find_open.result.target_sender = sender
        steps.result.target_sender = sender
    print(f"Target subject: {steps.result.target_subject!r}")
    print(f"Target sender:  {steps.result.target_sender!r}")
    print()

    steps.start_session()

    ok = steps.run_find_and_open_email()
    if ok:
        print("Phase 1-4 (find + open email): PASS")
        ok = steps.understand_email()
    if ok:
        print("Phase 5 (email understanding): PASS")
        ok = steps.prepare_reply_editor()
    if ok:
        print("Phase 6-7 (prepare reply editor): PASS")
        ok = steps.verify_reply_editor()
    if ok:
        print("Phase 8 (reply editor verified): PASS")
        ok = steps.generate_draft()
    if ok:
        print("Phase 9-10 (draft generated + quality gate): PASS")
        ok = steps.type_draft()
    if ok:
        print("Phase 11 (typed): PASS")
        ok = steps.verify_draft()
    if ok:
        print("Phase 12-14 (draft verified): PASS")

    steps.finalize_session()
    _save(steps)

    r = steps.result
    print()
    print(f"Overall result: {r.result}")
    print(f"Total E2E wall-clock elapsed: {r.total_elapsed_ms} ms")
    print(f"Total Vision latency (subset): {r.total_latency_ms} ms")
    if r.failure_reason:
        print(f"Failure reason: {r.failure_reason}")
        print(f"Notes: {r.notes}")
    print()
    if r.email_understanding_summary:
        print(f"Email summary: {r.email_understanding_summary}")
        print(f"Requires reply: {r.requires_reply}")
    print(f"Reply editor already open: {r.reply_editor_already_open}")
    print(f"Reply grounding: raw=({r.reply_raw_x},{r.reply_raw_y}) converted=({r.reply_converted_x},{r.reply_converted_y})")
    print(f"Reply click executed: {r.reply_click_executed}")
    print(f"Reply editor verified: {r.reply_editor_verified}")
    if r.draft_reply:
        print()
        print("Generated draft:")
        print("-" * 60)
        print(r.draft_reply)
        print("-" * 60)
    print(f"Typing result: {r.typing_result}")
    print(f"Detected draft: {r.detected_draft!r}")
    print(f"exact_match={r.exact_match} semantic_match={r.semantic_match} reply_editor_open={r.draft_verification_reply_editor_open}")
    print()
    print(f"Vision calls: {r.total_vision_calls}, tokens in/out: {r.total_input_tokens}/{r.total_output_tokens}, cost: ${r.total_estimated_cost}")
    print(f"Mouse clicks: {r.mouse_click_count}, keyboard actions: {r.keyboard_action_count}, send_click_count: {r.send_click_count}")
    print()
    print("Run --finalize [--label NAME] to write results/raw + results/reports.")


def cmd_finalize(label: str = "") -> None:
    if not PENDING_PATH.exists():
        print("No pending RND-009D run to finalize. Run --run-bounded first.")
        return
    result = RND009DResult.model_validate(json.loads(PENDING_PATH.read_text(encoding="utf-8")))

    suffix = f"_{label}" if label else ""
    results_path = PROJECT_ROOT / "results" / "raw" / f"rnd009d_reply_draft_results{suffix}.json"
    report_path = PROJECT_ROOT / "results" / "reports" / f"rnd009d_reply_draft_summary{suffix}.md"

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


def _build_report(result: RND009DResult) -> str:
    lines = ["# RND-009D Reply + Draft Playbook Summary — Gemini (gemini-3.6-flash)", ""]
    lines.append(f"Overall result: {result.result}")
    lines.append(f"Failure reason: {result.failure_reason}")
    lines.append(f"Target subject: {result.target_subject}")
    lines.append(f"Target sender: {result.target_sender}")
    lines.append("")
    lines.append("## Timing")
    lines.append(f"- session_start: {result.session_start}")
    lines.append(f"- session_end: {result.session_end}")
    lines.append(f"- **Total E2E wall-clock elapsed: {result.total_elapsed_ms} ms**")
    lines.append(f"- Total Vision latency (subset): {result.total_latency_ms} ms")
    lines.append("")
    lines.append("## Email Understanding")
    lines.append(f"- summary: {result.email_understanding_summary}")
    lines.append(f"- sender_intent: {result.email_understanding_sender_intent}")
    lines.append(f"- requires_reply: {result.requires_reply}")
    lines.append("")
    lines.append("## Reply Editor")
    lines.append(f"- already_open: {result.reply_editor_already_open}")
    lines.append(f"- raw: ({result.reply_raw_x}, {result.reply_raw_y})")
    lines.append(f"- converted: ({result.reply_converted_x}, {result.reply_converted_y})")
    lines.append(f"- click_executed: {result.reply_click_executed}")
    lines.append(f"- verified: {result.reply_editor_verified}")
    lines.append("")
    lines.append("## Draft Generation")
    lines.append("```")
    lines.append(result.draft_reply or "")
    lines.append("```")
    lines.append(f"- reasoning: {result.draft_reasoning_summary}")
    lines.append(f"- quality_passed: {result.draft_quality_passed} ({result.draft_quality_notes})")
    lines.append("")
    lines.append("## Typing")
    lines.append(f"- typing_result: {result.typing_result}")
    lines.append(f"- typed_text matches draft: {result.typed_text == result.draft_reply}")
    lines.append("")
    lines.append("## Draft Verification")
    lines.append(f"- detected_draft: {result.detected_draft!r}")
    lines.append(f"- exact_match: {result.exact_match}")
    lines.append(f"- semantic_match: {result.semantic_match}")
    lines.append(f"- reply_editor_open: {result.draft_verification_reply_editor_open}")
    lines.append("")
    lines.append("## Human Final Review")
    lines.append(f"- human_draft_confirmed: {result.human_draft_confirmed}")
    lines.append("")
    lines.append("## Step-level metrics")
    for name, m in (
        ("LAUNCH_OUTLOOK", result.launch_outlook_metrics),
        ("VERIFY_OUTLOOK_READY", result.verify_outlook_ready_metrics),
        ("FIND_EMAIL", result.find_email_metrics),
        ("VERIFY_EMAIL_OPEN", result.verify_email_opened_metrics),
        ("UNDERSTAND_EMAIL", result.understand_email_metrics),
        ("GROUND_REPLY", result.ground_reply_metrics),
        ("VERIFY_REPLY_EDITOR", result.verify_reply_editor_metrics),
        ("GENERATE_DRAFT", result.generate_draft_metrics),
        ("VERIFY_DRAFT", result.verify_draft_metrics),
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

    parser = argparse.ArgumentParser(description="RND-009D bounded live-run driver")
    parser.add_argument("--run-bounded", action="store_true")
    parser.add_argument("--finalize", action="store_true")
    parser.add_argument("--label", default="")
    parser.add_argument("--subject", default="", help="Override target email subject (default: 'Mail for project')")
    parser.add_argument("--sender", default="", help="Override target email sender (default: 'Yash')")
    args = parser.parse_args()

    if args.finalize:
        cmd_finalize(args.label)
    elif args.run_bounded:
        cmd_run_bounded(args.subject, args.sender)
    else:
        print("No command given. Use --run-bounded (after the one upfront approval) or --finalize.")


if __name__ == "__main__":
    main()
