"""Real-Claude-API interior-point grounding comparison CLI.

    python -m tests.claude_evaluation.experiments.run_point_comparison

Runs P1 (icon center), P2 (label-text center), P3 (visible-cluster
interior point) against the same real screenshots via the real
Anthropic provider. Primary success metric per instruction:
POINT_INSIDE_EXPECTED_ROW — whether the reported point actually falls
inside the hand-measured expected clickable-row bbox. Perception-only
— see the import-safety self-check below, identical guarantee as
evaluation_runner.py/run_comparison.py. Does not modify
app/outlook/launch.py, the runtime prompt, or the runtime schema.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from pydantic import ValidationError

PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT))

from app.config.settings import get_provider  # noqa: E402
from app.fallback.recovery import call_with_provider_retry  # noqa: E402
from app.metrics.step_metrics import estimate_cost  # noqa: E402

from tests.claude_evaluation.cases import EvalCase, get_cases  # noqa: E402
from tests.claude_evaluation import metrics  # noqa: E402
from tests.claude_evaluation.experiments.point_strategies import ALL_POINT_STRATEGIES, POINT_SEMANTIC_KEYS, PointStrategy  # noqa: E402

# --- Import-safety self-check (same guarantee as the other harness modules) -
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
    f"Point-experiment harness import graph pulled in physical-action-capable module(s): {_imported_forbidden}. "
    "This harness must remain perception-only — fix the import, do not silence this."
)
# ------------------------------------------------------------------------------

import logging  # noqa: E402

_eval_logger = logging.getLogger("claude_evaluation.point_experiments")
if not _eval_logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s [PT] %(message)s"))
    _eval_logger.addHandler(_handler)
    _eval_logger.setLevel(logging.INFO)
    _eval_logger.propagate = False

OUTPUT_DIR = PROJECT_ROOT / "evaluation_results" / "experiments"


def _run_case_once(provider, model: str, strategy: PointStrategy, case: EvalCase, run_index: int) -> dict[str, Any]:
    from PIL import Image

    with Image.open(case.screenshot) as img:
        width, height = img.size
    prompt_text = strategy.prompt_path.read_text(encoding="utf-8").format(width=width, height=height)

    _eval_logger.info(
        "PT_CALL_START strategy=%s case_id=%s run=%s screenshot_size=%sx%s",
        strategy.name, case.case_id, run_index, width, height,
    )

    result: dict[str, Any] = {
        "strategy": strategy.name, "case_id": case.case_id, "run_index": run_index,
        "provider": provider.provider_name, "model": model,
        "provider_retries": 0, "provider_error": None,
        "schema_valid": False, "parsed_json": None, "validation_error": None,
        "latency_ms": None, "input_tokens": None, "output_tokens": None, "estimated_cost": None,
        "predicted_point": None, "semantic_actual": None, "semantic_pass": None, "point_grounding": None,
    }

    outcome = call_with_provider_retry(
        lambda: provider.analyze_screen(case.screenshot, strategy.goal, prompt_text),
        stage=strategy.stage_tag, provider_name=provider.provider_name,
    )
    result["provider_retries"] = outcome.retries_used
    if outcome.result is None:
        result["provider_error"] = outcome.error
        _eval_logger.info("PT_CALL_END strategy=%s case_id=%s outcome=PROVIDER_ERROR", strategy.name, case.case_id)
        return result

    call = outcome.result
    result["latency_ms"] = call.latency_ms
    result["input_tokens"] = call.input_tokens
    result["output_tokens"] = call.output_tokens
    result["estimated_cost"] = estimate_cost(provider.provider_name, call.model, call.input_tokens, call.output_tokens)

    if call.parsed_json is None:
        result["validation_error"] = "Provider returned no parseable JSON."
        _eval_logger.info("PT_CALL_END strategy=%s case_id=%s outcome=MALFORMED_JSON", strategy.name, case.case_id)
        return result

    try:
        strategy.schema.model_validate(call.parsed_json)
        result["schema_valid"] = True
        result["parsed_json"] = call.parsed_json
    except ValidationError as exc:
        result["validation_error"] = str(exc)
        result["parsed_json"] = call.parsed_json
        _eval_logger.info("PT_CALL_END strategy=%s case_id=%s outcome=SCHEMA_INVALID", strategy.name, case.case_id)
        return result

    predicted_point = call.parsed_json.get("point")
    result["predicted_point"] = predicted_point
    semantic_actual = {k: call.parsed_json.get(k) for k in POINT_SEMANTIC_KEYS}
    result["semantic_actual"] = semantic_actual
    expected_subset = {k: case.expected[k] for k in POINT_SEMANTIC_KEYS if k in case.expected}
    result["semantic_pass"] = metrics.semantic_pass(semantic_actual, expected_subset)

    if case.expected_bbox is not None:
        result["point_grounding"] = metrics.point_grounding_metrics(predicted_point, case.expected_bbox, width, height)

    _eval_logger.info(
        "PT_CALL_END strategy=%s case_id=%s outcome=OK schema_valid=True latency_ms=%.1f "
        "point_inside_expected_row=%s",
        strategy.name, case.case_id, call.latency_ms or 0,
        (result["point_grounding"] or {}).get("point_inside_expected_row"),
    )
    return result


def _predicted_point_consistency(run_results: list[dict[str, Any]]) -> dict[str, Any]:
    if len(run_results) < 2:
        return {"runs": len(run_results), "applicable": False}

    points = [r.get("predicted_point") for r in run_results if metrics.point_valid(r.get("predicted_point"))]
    pairwise_distances = []
    for i in range(len(points)):
        for j in range(i + 1, len(points)):
            ay, ax = points[i]
            by, bx = points[j]
            pairwise_distances.append(((ay - by) ** 2 + (ax - bx) ** 2) ** 0.5)

    semantic_flags = [r.get("semantic_pass") for r in run_results]
    inside_flags = [
        (r.get("point_grounding") or {}).get("point_inside_expected_row")
        for r in run_results if r.get("point_grounding") is not None
    ]
    confidences = [(r.get("parsed_json") or {}).get("confidence") for r in run_results]
    confidences = [c for c in confidences if isinstance(c, (int, float))]

    return {
        "runs": len(run_results),
        "applicable": True,
        "semantic_pass_consistent": len(set(semantic_flags)) <= 1,
        "point_inside_expected_row_consistent": len(set(inside_flags)) <= 1 if inside_flags else None,
        "pairwise_normalized_distance_min": round(min(pairwise_distances), 2) if pairwise_distances else None,
        "pairwise_normalized_distance_max": round(max(pairwise_distances), 2) if pairwise_distances else None,
        "confidence_spread": round(max(confidences) - min(confidences), 3) if len(confidences) >= 2 else None,
    }


def _run_strategy(provider, model: str, strategy: PointStrategy, cases: list[EvalCase], extra_runs_case_id: str, extra_runs: int) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for case in cases:
        runs_for_case = extra_runs if case.case_id == extra_runs_case_id else 1
        for run_index in range(1, runs_for_case + 1):
            results.append(_run_case_once(provider, model, strategy, case, run_index))
    return results


def _strategy_summary(strategy_name: str, results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(results)
    semantic_total = [r for r in results if r.get("semantic_pass") is not None]
    semantic_passed = [r for r in semantic_total if r["semantic_pass"]]
    grounding_total = [r for r in results if r.get("point_grounding") is not None]
    inside_total = [r for r in grounding_total if r["point_grounding"]["point_inside_expected_row"] is not None]
    inside_passed = [r for r in inside_total if r["point_grounding"]["point_inside_expected_row"]]
    norm_distances = [
        r["point_grounding"]["normalized_distance_to_row_center"] for r in grounding_total
        if r["point_grounding"]["normalized_distance_to_row_center"] is not None
    ]
    pixel_distances = [
        r["point_grounding"]["pixel_distance_to_row_center"] for r in grounding_total
        if r["point_grounding"]["pixel_distance_to_row_center"] is not None
    ]
    latencies = [r["latency_ms"] for r in results if r.get("latency_ms") is not None]
    schema_valid = [r for r in results if r.get("schema_valid")]
    provider_errors = [r for r in results if r.get("provider_error")]
    costs = [r["estimated_cost"] for r in results if r.get("estimated_cost") is not None]

    return {
        "strategy": strategy_name,
        "total_calls": total,
        "schema_valid_rate": f"{len(schema_valid)}/{total}" if total else "0/0",
        "provider_error_rate": f"{len(provider_errors)}/{total}" if total else "0/0",
        "semantic_accuracy": f"{len(semantic_passed)}/{len(semantic_total)}" if semantic_total else "n/a",
        "point_in_target": f"{len(inside_passed)}/{len(inside_total)}" if inside_total else "n/a",
        "mean_normalized_distance": round(statistics.mean(norm_distances), 2) if norm_distances else None,
        "median_normalized_distance": round(statistics.median(norm_distances), 2) if norm_distances else None,
        "mean_pixel_distance": round(statistics.mean(pixel_distances), 1) if pixel_distances else None,
        "median_pixel_distance": round(statistics.median(pixel_distances), 1) if pixel_distances else None,
        "latency": metrics.latency_summary(latencies),
        "total_cost": round(sum(costs), 6) if costs else None,
        "cost_known_for_calls": len(costs),
    }


def run_point_comparison(extra_runs_case_id: str, extra_runs: int, strategies: Optional[list[PointStrategy]] = None) -> dict[str, Any]:
    cases = get_cases(stage="OUTLOOK_SEARCH")
    if not cases:
        raise SystemExit("No OUTLOOK_SEARCH cases defined in tests/claude_evaluation/cases.py")

    provider, model = get_provider()
    _eval_logger.info("provider=%s model=%s", provider.provider_name, model)

    strategy_results: dict[str, list[dict[str, Any]]] = {}
    strategy_summaries: dict[str, Any] = {}
    strategy_consistency: dict[str, Any] = {}

    for strategy in (strategies if strategies is not None else ALL_POINT_STRATEGIES):
        results = _run_strategy(provider, model, strategy, cases, extra_runs_case_id, extra_runs)
        strategy_results[strategy.name] = results
        strategy_summaries[strategy.name] = _strategy_summary(strategy.name, results)

        repeated = [r for r in results if r["case_id"] == extra_runs_case_id]
        strategy_consistency[strategy.name] = _predicted_point_consistency(repeated)

    return {
        "timestamp": datetime.now().isoformat(),
        "provider": provider.provider_name,
        "model": model,
        "extra_runs_case_id": extra_runs_case_id,
        "extra_runs": extra_runs,
        "results": strategy_results,
        "summary": strategy_summaries,
        "consistency": strategy_consistency,
    }


def _write_markdown(evaluation: dict[str, Any], path: Path) -> None:
    lines = [
        f"# Interior-point grounding comparison — {evaluation['timestamp']}",
        "",
        f"provider={evaluation['provider']} model={evaluation['model']}",
        f"Repeated-run case: {evaluation['extra_runs_case_id']} x{evaluation['extra_runs']}",
        "",
        "## Comparison table",
        "",
        "| Strategy | Semantic Accuracy | Point-In-Target | Mean Norm. Distance | Mean Pixel Distance | Consistency | Median Latency (ms) | Total Cost |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for name, s in evaluation["summary"].items():
        c = evaluation["consistency"].get(name, {})
        consistency_str = (
            f"max_dist={c.get('pairwise_normalized_distance_max')}" if c.get("applicable") else "n/a"
        )
        lines.append(
            f"| {name} | {s['semantic_accuracy']} | {s['point_in_target']} | {s['mean_normalized_distance']} | "
            f"{s['mean_pixel_distance']} | {consistency_str} | {s['latency']['median_ms']} | {s['total_cost']} |"
        )
    lines.append("")

    lines.append("## Per-strategy per-case detail")
    for name, results in evaluation["results"].items():
        lines.append(f"### {name}")
        for r in results:
            lines.append(
                f"- {r['case_id']} run {r['run_index']}: schema_valid={r['schema_valid']} "
                f"semantic_pass={r['semantic_pass']} predicted_point={r['predicted_point']} "
                f"latency_ms={r['latency_ms']} provider_error={r['provider_error']}"
            )
            if r.get("point_grounding"):
                g = r["point_grounding"]
                lines.append(
                    f"    point_inside_expected_row={g['point_inside_expected_row']} "
                    f"normalized_distance={g['normalized_distance_to_row_center']} "
                    f"pixel_distance={g['pixel_distance_to_row_center']}"
                )
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Compare interior-point OUTLOOK_SEARCH grounding strategies via the real Anthropic API (perception-only).")
    parser.add_argument("--extra-runs-case", default=None)
    parser.add_argument("--extra-runs", type=int, default=3)
    parser.add_argument("--strategies", default=None, help="Comma-separated strategy names (default: all P1/P2/P3).")
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    args = parser.parse_args(argv)

    extra_runs_case_id = args.extra_runs_case
    if extra_runs_case_id is None:
        cases = get_cases(stage="OUTLOOK_SEARCH")
        extra_runs_case_id = cases[0].case_id if cases else ""

    selected_strategies = None
    if args.strategies:
        wanted = set(args.strategies.split(","))
        selected_strategies = [s for s in ALL_POINT_STRATEGIES if s.name in wanted]
        if not selected_strategies:
            raise SystemExit(f"No matching strategies for {wanted!r}. Known: {[s.name for s in ALL_POINT_STRATEGIES]}")

    evaluation = run_point_comparison(extra_runs_case_id, args.extra_runs, strategies=selected_strategies)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = output_dir / f"point_comparison_{timestamp}.json"
    md_path = output_dir / f"point_comparison_{timestamp}.md"

    json_path.write_text(json.dumps(evaluation, indent=2, default=str), encoding="utf-8")
    _write_markdown(evaluation, md_path)

    print(f"\nResults written to:\n  {json_path}\n  {md_path}\n")
    for name, s in evaluation["summary"].items():
        print(f"{name}")
        print(f"  semantic accuracy: {s['semantic_accuracy']}")
        print(f"  point-in-target: {s['point_in_target']}")
        print(f"  mean normalized distance: {s['mean_normalized_distance']}")
        print(f"  mean pixel distance: {s['mean_pixel_distance']}")
        print(f"  median latency: {s['latency']['median_ms']} ms")
        print()


if __name__ == "__main__":
    main()
