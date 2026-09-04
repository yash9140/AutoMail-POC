"""Real-Claude-API grounding-strategy comparison CLI.

    python -m benchmarks.claude.experiments.run_comparison

Runs all 4 OUTLOOK_SEARCH grounding strategies (A baseline, B
landmark-relative prompt, C component/landmark grounding, D candidate
list) against the same real screenshots via the real Anthropic
provider, computes the same deterministic metrics for each, and writes
a comparison JSON + Markdown table. Perception-only — see the
import-safety self-check below; identical guarantee as
evaluation_runner.py. Does not modify app/outlook/launch.py, the
runtime prompt, or the runtime schema.
"""

from __future__ import annotations

import argparse
import json
import re
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

from benchmarks.claude.cases import EvalCase, get_cases  # noqa: E402
from benchmarks.claude import metrics  # noqa: E402
from benchmarks.claude.experiments.strategies import ALL_STRATEGIES, Strategy  # noqa: E402

# --- Import-safety self-check (same guarantee as evaluation_runner.py) ------
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
    f"Experiment harness import graph pulled in physical-action-capable module(s): {_imported_forbidden}. "
    "This harness must remain perception-only — fix the import, do not silence this."
)
# ------------------------------------------------------------------------------

import logging  # noqa: E402

_eval_logger = logging.getLogger("claude_evaluation.experiments")
if not _eval_logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s [EXP] %(message)s"))
    _eval_logger.addHandler(_handler)
    _eval_logger.setLevel(logging.INFO)
    _eval_logger.propagate = False

OUTPUT_DIR = PROJECT_ROOT / "benchmarks" / "results" / "experiments"


def _extract_json_fallback(raw_text: Optional[str]) -> Optional[dict]:
    """Best-effort JSON recovery for EVALUATION purposes only, used when
    the provider's own (intentionally conservative — see
    app/vision/providers/anthropic_provider.py) leading-fence parser
    returns None. That parser only strips a fence at the very START of
    the response; if Claude prefaces the JSON with visible chain-of-
    thought reasoning (observed with the component-grounding strategy),
    it correctly reports "no parseable JSON" for the RAW production
    parsing path. This fallback searches for a fenced ```json ... ```
    block ANYWHERE in the text, then falls back to the last top-level
    {...} object via brace matching — purely so this evaluation can
    still measure the underlying grounding quality Claude was reasoning
    toward. Never used by, or copied into, the runtime provider; the
    runtime's conservative parsing is intentionally left unchanged."""
    if not raw_text:
        return None
    for match in re.finditer(r"```(?:json)?\s*(\{.*?\})\s*```", raw_text, re.DOTALL):
        try:
            return json.loads(match.group(1))
        except (ValueError, TypeError):
            continue
    start = raw_text.rfind("{")
    if start != -1:
        depth = 0
        for i in range(start, len(raw_text)):
            if raw_text[i] == "{":
                depth += 1
            elif raw_text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(raw_text[start:i + 1])
                    except (ValueError, TypeError):
                        break
    return None


def _run_case_once(provider, model: str, strategy: Strategy, case: EvalCase, run_index: int) -> dict[str, Any]:
    from PIL import Image

    with Image.open(case.screenshot) as img:
        width, height = img.size
    prompt_text = strategy.prompt_path.read_text(encoding="utf-8").format(width=width, height=height)

    _eval_logger.info(
        "EXP_CALL_START strategy=%s case_id=%s run=%s screenshot_size=%sx%s",
        strategy.name, case.case_id, run_index, width, height,
    )

    result: dict[str, Any] = {
        "strategy": strategy.name, "case_id": case.case_id, "run_index": run_index,
        "provider": provider.provider_name, "model": model,
        "provider_retries": 0, "provider_error": None,
        "schema_valid": False, "parsed_json": None, "validation_error": None,
        "latency_ms": None, "input_tokens": None, "output_tokens": None, "estimated_cost": None,
        "predicted_bbox": None, "semantic_actual": None, "semantic_pass": None,
        "grounding": None, "component": None, "candidates_reported": None,
        "raw_text": None, "raw_text_length": None, "json_recovered_via_eval_fallback": False,
    }

    outcome = call_with_provider_retry(
        lambda: provider.analyze_screen(case.screenshot, strategy.goal, prompt_text),
        stage=strategy.stage_tag, provider_name=provider.provider_name,
    )
    result["provider_retries"] = outcome.retries_used
    if outcome.result is None:
        result["provider_error"] = outcome.error
        _eval_logger.info("EXP_CALL_END strategy=%s case_id=%s outcome=PROVIDER_ERROR", strategy.name, case.case_id)
        return result

    call = outcome.result
    result["latency_ms"] = call.latency_ms
    result["input_tokens"] = call.input_tokens
    result["output_tokens"] = call.output_tokens
    result["estimated_cost"] = estimate_cost(provider.provider_name, call.model, call.input_tokens, call.output_tokens)
    result["raw_text"] = call.raw_text
    result["raw_text_length"] = len(call.raw_text) if call.raw_text else 0

    parsed_json = call.parsed_json
    if parsed_json is None:
        # The PROVIDER's own strict/conservative parse failed (recorded
        # above as the authoritative production behavior). For
        # evaluation purposes only, try to recover a JSON object from
        # anywhere in the raw text, so a verbose/chain-of-thought
        # response doesn't hide the underlying grounding quality Claude
        # was reasoning toward. See _extract_json_fallback()'s docstring.
        parsed_json = _extract_json_fallback(call.raw_text)
        result["json_recovered_via_eval_fallback"] = parsed_json is not None

    if parsed_json is None:
        result["validation_error"] = "Provider returned no parseable JSON (eval-only fallback extraction also failed)."
        _eval_logger.info("EXP_CALL_END strategy=%s case_id=%s outcome=MALFORMED_JSON", strategy.name, case.case_id)
        return result

    try:
        strategy.schema.model_validate(parsed_json)
        result["schema_valid"] = True
        result["parsed_json"] = parsed_json
    except ValidationError as exc:
        result["validation_error"] = str(exc)
        result["parsed_json"] = parsed_json
        _eval_logger.info("EXP_CALL_END strategy=%s case_id=%s outcome=SCHEMA_INVALID", strategy.name, case.case_id)
        return result

    extracted = strategy.extract(parsed_json, case)
    result["predicted_bbox"] = extracted.get("predicted_bbox")
    semantic_actual = extracted.get("semantic_actual") or {}
    result["semantic_actual"] = semantic_actual
    result["semantic_pass"] = strategy.semantic_pass_fn(semantic_actual, case)
    if "component" in extracted:
        result["component"] = extracted["component"]
    if "candidates_reported" in extracted:
        result["candidates_reported"] = extracted["candidates_reported"]

    if case.expected_bbox is not None:
        g = metrics.grounding_metrics(result["predicted_bbox"], case.expected_bbox)
        g["predicted_bbox"] = result["predicted_bbox"]
        g["expected_bbox"] = case.expected_bbox
        result["grounding"] = g

    _eval_logger.info(
        "EXP_CALL_END strategy=%s case_id=%s outcome=OK schema_valid=True latency_ms=%.1f semantic_pass=%s",
        strategy.name, case.case_id, call.latency_ms or 0, result["semantic_pass"],
    )
    return result


def _predicted_bbox_consistency(run_results: list[dict[str, Any]]) -> dict[str, Any]:
    """Strategy-agnostic consistency metric — uses each run's already-
    extracted `predicted_bbox` (whichever strategy-specific bbox that
    is: raw bbox for A/B, cluster bbox for C, selected-candidate bbox
    for D) rather than assuming a fixed schema shape."""
    if len(run_results) < 2:
        return {"runs": len(run_results), "applicable": False}

    boxes = [r.get("predicted_bbox") for r in run_results]
    pairwise = []
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            v = metrics.iou(boxes[i], boxes[j])
            if v is not None:
                pairwise.append(v)

    semantic_flags = [r.get("semantic_pass") for r in run_results]
    confidences = [
        (r.get("parsed_json") or {}).get("confidence") for r in run_results
    ]
    confidences = [c for c in confidences if isinstance(c, (int, float))]

    return {
        "runs": len(run_results),
        "applicable": True,
        "semantic_pass_consistent": len(set(semantic_flags)) <= 1,
        "bbox_pairwise_min_iou": round(min(pairwise), 3) if pairwise else None,
        "bbox_pairwise_avg_iou": round(statistics.mean(pairwise), 3) if pairwise else None,
        "confidence_spread": round(max(confidences) - min(confidences), 3) if len(confidences) >= 2 else None,
    }


def _run_strategy(provider, model: str, strategy: Strategy, cases: list[EvalCase], extra_runs_case_id: str, extra_runs: int) -> list[dict[str, Any]]:
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
    grounding_total = [r for r in results if r.get("grounding") is not None]
    grounding_passed = [r for r in grounding_total if r["grounding"]["grounding_pass"]]
    high_quality = [r for r in grounding_total if r["grounding"]["high_quality_grounding"]]
    center_in_target = [r for r in grounding_total if r["grounding"]["predicted_center_inside_expected"]]
    ious = [r["grounding"]["iou"] for r in grounding_total if r["grounding"]["iou"] is not None]
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
        "grounding_pass": f"{len(grounding_passed)}/{len(grounding_total)}" if grounding_total else "n/a",
        "high_quality_grounding": f"{len(high_quality)}/{len(grounding_total)}" if grounding_total else "n/a",
        "center_in_target": f"{len(center_in_target)}/{len(grounding_total)}" if grounding_total else "n/a",
        "mean_iou": round(statistics.mean(ious), 3) if ious else None,
        "median_iou": round(statistics.median(ious), 3) if ious else None,
        "latency": metrics.latency_summary(latencies),
        "total_cost": round(sum(costs), 6) if costs else None,
        "cost_known_for_calls": len(costs),
    }


def _component_summary(results: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    comps = [r["component"] for r in results if r.get("component")]
    if not comps:
        return None

    def rate(key: str) -> str:
        vals = [c[key] for c in comps if c.get(key) is not None]
        return f"{sum(1 for v in vals if v)}/{len(vals)}" if vals else "n/a"

    cluster_ious = [c["cluster_iou_with_expected"] for c in comps if c.get("cluster_iou_with_expected") is not None]
    return {
        "icon_center_in_expected": rate("icon_center_in_expected"),
        "label_center_in_expected": rate("label_center_in_expected"),
        "sublabel_center_in_expected": rate("sublabel_center_in_expected"),
        "cluster_center_in_expected": rate("cluster_center_in_expected"),
        "cluster_iou_mean": round(statistics.mean(cluster_ious), 3) if cluster_ious else None,
    }


def run_comparison(extra_runs_case_id: str, extra_runs: int, strategies: Optional[list[Strategy]] = None) -> dict[str, Any]:
    cases = get_cases(stage="OUTLOOK_SEARCH")
    if not cases:
        raise SystemExit("No OUTLOOK_SEARCH cases defined in benchmarks/claude/cases.py")

    provider, model = get_provider()
    _eval_logger.info("provider=%s model=%s", provider.provider_name, model)

    strategy_results: dict[str, list[dict[str, Any]]] = {}
    strategy_summaries: dict[str, Any] = {}
    strategy_consistency: dict[str, Any] = {}
    strategy_component: dict[str, Any] = {}

    for strategy in (strategies if strategies is not None else ALL_STRATEGIES):
        results = _run_strategy(provider, model, strategy, cases, extra_runs_case_id, extra_runs)
        strategy_results[strategy.name] = results
        strategy_summaries[strategy.name] = _strategy_summary(strategy.name, results)

        repeated = [r for r in results if r["case_id"] == extra_runs_case_id]
        strategy_consistency[strategy.name] = _predicted_bbox_consistency(repeated)

        component_summary = _component_summary(results)
        if component_summary is not None:
            strategy_component[strategy.name] = component_summary

    return {
        "timestamp": datetime.now().isoformat(),
        "provider": provider.provider_name,
        "model": model,
        "extra_runs_case_id": extra_runs_case_id,
        "extra_runs": extra_runs,
        "results": strategy_results,
        "summary": strategy_summaries,
        "consistency": strategy_consistency,
        "component_special_metrics": strategy_component,
    }


def _write_markdown(evaluation: dict[str, Any], path: Path) -> None:
    lines = [
        f"# Grounding-strategy comparison — {evaluation['timestamp']}",
        "",
        f"provider={evaluation['provider']} model={evaluation['model']}",
        f"Repeated-run case: {evaluation['extra_runs_case_id']} x{evaluation['extra_runs']}",
        "",
        "## Comparison table",
        "",
        "| Strategy | Semantic Accuracy | Grounding Pass | High-Quality | Center-in-Target | Mean IoU | Median IoU | Consistency (bbox IoU) | Median Latency (ms) | Total Cost |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name, s in evaluation["summary"].items():
        c = evaluation["consistency"].get(name, {})
        consistency_str = (
            f"{c.get('bbox_pairwise_avg_iou')}" if c.get("applicable") else "n/a"
        )
        lines.append(
            f"| {name} | {s['semantic_accuracy']} | {s['grounding_pass']} | {s['high_quality_grounding']} | "
            f"{s['center_in_target']} | {s['mean_iou']} | {s['median_iou']} | {consistency_str} | "
            f"{s['latency']['median_ms']} | {s['total_cost']} |"
        )
    lines.append("")

    if evaluation["component_special_metrics"]:
        lines.append("## Component/landmark special metrics (Strategy C)")
        for name, cm in evaluation["component_special_metrics"].items():
            lines.append(f"### {name}")
            lines.append(f"- icon center in expected row: {cm['icon_center_in_expected']}")
            lines.append(f"- label center in expected row: {cm['label_center_in_expected']}")
            lines.append(f"- sublabel center in expected row: {cm['sublabel_center_in_expected']}")
            lines.append(f"- cluster (union) center in expected row: {cm['cluster_center_in_expected']}")
            lines.append(f"- cluster IoU with expected row (mean): {cm['cluster_iou_mean']}")
            lines.append("")

    lines.append("## Per-strategy per-case detail")
    for name, results in evaluation["results"].items():
        lines.append(f"### {name}")
        for r in results:
            lines.append(
                f"- {r['case_id']} run {r['run_index']}: schema_valid={r['schema_valid']} "
                f"semantic_pass={r['semantic_pass']} predicted_bbox={r['predicted_bbox']} "
                f"latency_ms={r['latency_ms']} provider_error={r['provider_error']}"
            )
            if r.get("grounding"):
                g = r["grounding"]
                lines.append(
                    f"    grounding: expected_bbox={g['expected_bbox']} iou={g['iou']} "
                    f"overlap%={g['overlap_percentage']} center_in_target={g['predicted_center_inside_expected']}"
                )
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Compare OUTLOOK_SEARCH grounding strategies via the real Anthropic API (perception-only).")
    parser.add_argument("--extra-runs-case", default=None, help="case_id to repeat --extra-runs times (default: first OUTLOOK_SEARCH case).")
    parser.add_argument("--extra-runs", type=int, default=3, help="How many times to run the repeated case (default 3).")
    parser.add_argument(
        "--strategies", default=None,
        help="Comma-separated strategy names to run (default: all). E.g. --strategies C_component_landmark_grounding",
    )
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    args = parser.parse_args(argv)

    extra_runs_case_id = args.extra_runs_case
    if extra_runs_case_id is None:
        cases = get_cases(stage="OUTLOOK_SEARCH")
        extra_runs_case_id = cases[0].case_id if cases else ""

    selected_strategies = None
    if args.strategies:
        wanted = set(args.strategies.split(","))
        selected_strategies = [s for s in ALL_STRATEGIES if s.name in wanted]
        if not selected_strategies:
            raise SystemExit(f"No matching strategies for {wanted!r}. Known: {[s.name for s in ALL_STRATEGIES]}")

    evaluation = run_comparison(extra_runs_case_id, args.extra_runs, strategies=selected_strategies)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = output_dir / f"strategy_comparison_{timestamp}.json"
    md_path = output_dir / f"strategy_comparison_{timestamp}.md"

    json_path.write_text(json.dumps(evaluation, indent=2, default=str), encoding="utf-8")
    _write_markdown(evaluation, md_path)

    print(f"\nResults written to:\n  {json_path}\n  {md_path}\n")
    for name, s in evaluation["summary"].items():
        print(f"{name}")
        print(f"  semantic accuracy: {s['semantic_accuracy']}")
        print(f"  grounding pass: {s['grounding_pass']}")
        print(f"  high-quality grounding: {s['high_quality_grounding']}")
        print(f"  center-in-target: {s['center_in_target']}")
        print(f"  mean IoU: {s['mean_iou']}")
        print(f"  median latency: {s['latency']['median_ms']} ms")
        print()


if __name__ == "__main__":
    main()
