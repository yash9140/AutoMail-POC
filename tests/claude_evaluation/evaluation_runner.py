"""Real-Claude-API visual evaluation CLI.

    python -m tests.claude_evaluation.evaluation_runner
    python -m tests.claude_evaluation.evaluation_runner --stage OUTLOOK_SEARCH
    python -m tests.claude_evaluation.evaluation_runner --case outlook_search_live_001
    python -m tests.claude_evaluation.evaluation_runner --runs 3

Makes REAL calls to the configured Anthropic provider (app.config.
settings.get_provider() — same ANTHROPIC_API_KEY/ANTHROPIC_MODEL every
runtime call uses, real cost, real latency). Every screenshot is a
STATIC saved image already on disk — this module never captures a new
screenshot, moves the mouse, presses a key, or launches/touches
Outlook. See the import-safety self-check immediately below the
imports: this module's own import graph is asserted to contain no
pyautogui/keyboard/physical-action module at all.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from pydantic import ValidationError

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from app.config.settings import get_provider  # noqa: E402
from app.fallback.recovery import call_with_provider_retry  # noqa: E402
from app.metrics.step_metrics import estimate_cost  # noqa: E402
from app.vision.providers.base import VisionProviderError  # noqa: E402

from tests.claude_evaluation.cases import EvalCase, PROJECT_ROOT as CASES_ROOT, get_cases, known_stages  # noqa: E402
from tests.claude_evaluation.metrics import (  # noqa: E402
    consistency_across_runs,
    grounding_metrics,
    latency_summary,
    semantic_pass,
)

# --- Import-safety self-check ------------------------------------------------
# This module (and everything it imports above) must NEVER pull in a
# module capable of a physical mouse/keyboard action. Asserted here,
# once, at import time — not a runtime "trust me" comment.
_FORBIDDEN_MODULE_PREFIXES = (
    "pyautogui", "keyboard", "mouse",
    "app.outlook.launch", "app.outlook.find_email", "app.outlook.read_email",
    "app.outlook.reply", "app.outlook.draft", "app.outlook.send",
    "app.automation.scrolling", "app.safety.foreground",
)
_imported_forbidden = sorted(
    name for name in sys.modules
    if any(name == prefix or name.startswith(prefix + ".") for prefix in _FORBIDDEN_MODULE_PREFIXES)
)
assert not _imported_forbidden, (
    f"Evaluation harness import graph pulled in physical-action-capable module(s): {_imported_forbidden}. "
    "This harness must remain perception-only — fix the import, do not silence this."
)
# ------------------------------------------------------------------------------

_eval_logger = logging.getLogger("claude_evaluation")
if not _eval_logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s [EVAL] %(message)s"))
    _eval_logger.addHandler(_handler)
    _eval_logger.setLevel(logging.INFO)
    _eval_logger.propagate = False

OUTPUT_DIR = PROJECT_ROOT / "evaluation_results"


def _run_case_once(provider, model: str, case: EvalCase, run_index: int) -> dict[str, Any]:
    from PIL import Image

    with Image.open(case.screenshot) as img:
        width, height = img.size

    format_kwargs = {"width": width, "height": height, **case.prompt_format_kwargs}
    prompt_text = case.prompt_path.read_text(encoding="utf-8").format(**format_kwargs)

    _eval_logger.info(
        "EVAL_CALL_START case_id=%s stage=%s provider=%s model=%s run=%s screenshot_size=%sx%s",
        case.case_id, case.stage, provider.provider_name, model, run_index, width, height,
    )

    result: dict[str, Any] = {
        "case_id": case.case_id, "stage": case.stage, "run_index": run_index,
        "screenshot": str(case.screenshot.relative_to(CASES_ROOT)),
        "screenshot_width": width, "screenshot_height": height,
        "provider": provider.provider_name, "model": model,
        "provider_retries": 0, "provider_error": None,
        "schema_valid": False, "parsed_json": None, "validation_error": None,
        "latency_ms": None, "input_tokens": None, "output_tokens": None, "estimated_cost": None,
        "semantic": None, "grounding": None,
    }

    try:
        outcome = call_with_provider_retry(
            lambda: provider.analyze_screen(case.screenshot, case.goal, prompt_text),
            stage=case.stage, provider_name=provider.provider_name,
        )
    except VisionProviderError as exc:
        # call_with_provider_retry already retries VisionProviderError
        # internally; this is a defensive catch only, never expected in
        # normal operation.
        result["provider_error"] = f"{type(exc).__name__}: {exc}"
        _eval_logger.info("EVAL_CALL_END case_id=%s outcome=PROVIDER_ERROR", case.case_id)
        return result

    result["provider_retries"] = outcome.retries_used
    if outcome.result is None:
        result["provider_error"] = outcome.error
        _eval_logger.info("EVAL_CALL_END case_id=%s outcome=PROVIDER_ERROR", case.case_id)
        return result

    call = outcome.result
    result["latency_ms"] = call.latency_ms
    result["input_tokens"] = call.input_tokens
    result["output_tokens"] = call.output_tokens
    result["estimated_cost"] = estimate_cost(provider.provider_name, call.model, call.input_tokens, call.output_tokens)
    result["raw_text_length"] = len(call.raw_text) if call.raw_text else 0

    if call.parsed_json is None:
        result["validation_error"] = "Provider returned no parseable JSON."
        _eval_logger.info("EVAL_CALL_END case_id=%s outcome=MALFORMED_JSON latency_ms=%.1f", case.case_id, call.latency_ms or 0)
        return result

    try:
        case.schema.model_validate(call.parsed_json)
        result["schema_valid"] = True
        result["parsed_json"] = call.parsed_json
    except ValidationError as exc:
        result["validation_error"] = str(exc)
        result["parsed_json"] = call.parsed_json
        _eval_logger.info("EVAL_CALL_END case_id=%s outcome=SCHEMA_INVALID latency_ms=%.1f", case.case_id, call.latency_ms or 0)
        return result

    if case.expected:
        result["semantic"] = {
            "pass": semantic_pass(call.parsed_json, case.expected),
            "expected": case.expected,
            "actual": {k: call.parsed_json.get(k) for k in case.expected},
        }

    if case.expected_bbox is not None:
        predicted_bbox = call.parsed_json.get("bbox")
        g = grounding_metrics(predicted_bbox, case.expected_bbox)
        g["predicted_bbox"] = predicted_bbox
        g["expected_bbox"] = case.expected_bbox
        result["grounding"] = g

    _eval_logger.info(
        "EVAL_CALL_END case_id=%s outcome=OK schema_valid=True latency_ms=%.1f confidence=%s",
        case.case_id, call.latency_ms or 0, (call.parsed_json or {}).get("confidence"),
    )
    return result


def run_evaluation(stage: Optional[str], case_id: Optional[str], runs: int) -> dict[str, Any]:
    cases = get_cases(stage=stage, case_id=case_id)
    if not cases:
        available = known_stages()
        raise SystemExit(
            f"No evaluation cases matched (stage={stage!r}, case_id={case_id!r}). "
            f"Known stages with cases defined: {available}"
        )

    provider, model = get_provider()
    _eval_logger.info("provider=%s model=%s", provider.provider_name, model)

    per_case_results = []
    for case in cases:
        if not case.screenshot.exists():
            _eval_logger.info("EVAL_CASE_SKIPPED case_id=%s reason=screenshot_missing path=%s", case.case_id, case.screenshot)
            per_case_results.append({"case_id": case.case_id, "stage": case.stage, "skipped": "screenshot_missing", "runs": []})
            continue

        run_results = [_run_case_once(provider, model, case, run_index) for run_index in range(1, runs + 1)]
        per_case_results.append({
            "case_id": case.case_id,
            "stage": case.stage,
            "notes": case.notes,
            "runs": run_results,
            "consistency": consistency_across_runs(run_results),
        })

    return {
        "timestamp": datetime.now().isoformat(),
        "provider": provider.provider_name,
        "model": model,
        "args": {"stage": stage, "case": case_id, "runs": runs},
        "cases": per_case_results,
    }


def _stage_summary(evaluation: dict[str, Any]) -> dict[str, Any]:
    by_stage: dict[str, list[dict[str, Any]]] = {}
    for case_result in evaluation["cases"]:
        by_stage.setdefault(case_result["stage"], []).append(case_result)

    summary: dict[str, Any] = {}
    for stage, case_results in by_stage.items():
        all_runs = [r for cr in case_results for r in cr.get("runs", [])]
        semantic_total = [r for r in all_runs if r.get("semantic") is not None]
        semantic_passed = [r for r in semantic_total if r["semantic"]["pass"]]
        grounding_total = [r for r in all_runs if r.get("grounding") is not None]
        grounding_passed = [r for r in grounding_total if r["grounding"]["grounding_pass"]]
        high_quality = [r for r in grounding_total if r["grounding"]["high_quality_grounding"]]
        schema_valid = [r for r in all_runs if r.get("schema_valid")]
        provider_errors = [r for r in all_runs if r.get("provider_error")]
        latencies = [r["latency_ms"] for r in all_runs if r.get("latency_ms") is not None]
        costs = [r["estimated_cost"] for r in all_runs if r.get("estimated_cost") is not None]

        summary[stage] = {
            "total_calls": len(all_runs),
            "schema_valid_rate": f"{len(schema_valid)}/{len(all_runs)}" if all_runs else "0/0",
            "provider_error_rate": f"{len(provider_errors)}/{len(all_runs)}" if all_runs else "0/0",
            "semantic_accuracy": f"{len(semantic_passed)}/{len(semantic_total)}" if semantic_total else "n/a",
            "grounding_pass": f"{len(grounding_passed)}/{len(grounding_total)}" if grounding_total else "n/a",
            "high_quality_grounding": f"{len(high_quality)}/{len(grounding_total)}" if grounding_total else "n/a",
            "latency": latency_summary(latencies),
            "total_cost": round(sum(costs), 6) if costs else None,
            "cost_known_for_calls": len(costs),
        }
    return summary


def _write_markdown(evaluation: dict[str, Any], stage_summary: dict[str, Any], path: Path) -> None:
    lines = [
        f"# Claude evaluation — {evaluation['timestamp']}",
        "",
        f"provider={evaluation['provider']} model={evaluation['model']}",
        "",
    ]
    for stage, s in stage_summary.items():
        lines.append(f"## {stage}")
        lines.append(f"- semantic accuracy: {s['semantic_accuracy']}")
        lines.append(f"- grounding pass: {s['grounding_pass']}")
        lines.append(f"- high-quality grounding: {s['high_quality_grounding']}")
        lines.append(f"- schema valid: {s['schema_valid_rate']}")
        lines.append(f"- provider errors: {s['provider_error_rate']}")
        lat = s["latency"]
        lines.append(f"- latency (ms): min={lat['min_ms']} max={lat['max_ms']} median={lat['median_ms']} avg={lat['avg_ms']}")
        lines.append(f"- total cost: {s['total_cost']} (known for {s['cost_known_for_calls']} calls)")
        lines.append("")

    lines.append("## Per-case detail")
    for case_result in evaluation["cases"]:
        if case_result.get("skipped"):
            lines.append(f"### {case_result['case_id']} — SKIPPED ({case_result['skipped']})")
            continue
        lines.append(f"### {case_result['case_id']} ({case_result['stage']})")
        if case_result.get("notes"):
            lines.append(f"_{case_result['notes']}_")
        for r in case_result["runs"]:
            lines.append(f"- run {r['run_index']}: schema_valid={r['schema_valid']} latency_ms={r['latency_ms']} "
                         f"provider_error={r['provider_error']}")
            if r.get("semantic"):
                lines.append(f"  - semantic pass={r['semantic']['pass']} actual={r['semantic']['actual']}")
            if r.get("grounding"):
                g = r["grounding"]
                lines.append(
                    f"  - predicted_bbox={g['predicted_bbox']} expected_bbox={g['expected_bbox']} "
                    f"iou={g['iou']} overlap%={g['overlap_percentage']} grounding_pass={g['grounding_pass']}"
                )
        consistency = case_result.get("consistency", {})
        if consistency.get("applicable"):
            lines.append(f"  - consistency across {consistency['runs']} runs: "
                         f"identity={consistency['identity_field_consistency']} "
                         f"bbox_pairwise_min_iou={consistency['bbox_pairwise_min_iou']} "
                         f"confidence_spread={consistency['confidence_spread']}")
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Real-Claude-API visual evaluation harness (perception-only, no physical actions).")
    parser.add_argument("--stage", default=None, help="Only run cases for this stage (e.g. OUTLOOK_SEARCH).")
    parser.add_argument("--case", dest="case_id", default=None, help="Only run this one case_id.")
    parser.add_argument("--runs", type=int, default=1, help="Repeat each case this many times (default 1 — API cost matters).")
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR), help="Directory to write JSON/Markdown results into.")
    args = parser.parse_args(argv)

    evaluation = run_evaluation(stage=args.stage, case_id=args.case_id, runs=args.runs)
    stage_summary = _stage_summary(evaluation)
    evaluation["stage_summary"] = stage_summary

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = output_dir / f"claude_eval_{timestamp}.json"
    md_path = output_dir / f"claude_eval_{timestamp}.md"

    json_path.write_text(json.dumps(evaluation, indent=2, default=str), encoding="utf-8")
    _write_markdown(evaluation, stage_summary, md_path)

    print(f"\nResults written to:\n  {json_path}\n  {md_path}\n")
    for stage, s in stage_summary.items():
        print(f"{stage}")
        print(f"  semantic accuracy: {s['semantic_accuracy']}")
        print(f"  grounding pass: {s['grounding_pass']}")
        print(f"  high-quality grounding: {s['high_quality_grounding']}")
        print(f"  median latency: {s['latency']['median_ms']} ms")
        print(f"  total cost: {s['total_cost']}")
        print()


if __name__ == "__main__":
    main()
