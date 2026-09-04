"""Structured-output parser format experiment (2026-09-03).

    python -m benchmarks.claude.experiments.run_parser_format_experiment

Investigates the production AnthropicProvider's JSON-parsing fragility
first observed with Strategy C (Claude prefacing the JSON with visible
chain-of-thought reasoning, which the provider's leading-fence-only
stripper can't recover). Compares:

  A. the ORIGINAL component_grounding_v1.txt prompt (the one that broke)
  B. a stronger anti-prose variant (component_grounding_strict_v1.txt)

against the SAME real screenshots, classifying each raw response into
one of four formats and measuring whether the PRODUCTION-equivalent
parse (i.e. exactly what app/vision/providers/anthropic_provider.py's
analyze_screen() itself already computed as `parsed_json`) succeeds.
This does not change app/vision/providers/anthropic_provider.py itself
— it only measures, so a production change can be justified by
evidence rather than guessed.
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

from benchmarks.claude.cases import EvalCase, get_cases  # noqa: E402
from benchmarks.claude.experiments.schemas import ComponentGroundingResponse  # noqa: E402
from benchmarks.claude.experiments.strategies import COMPONENT_GROUNDING_PROMPT_PATH, EXPERIMENTS_DIR  # noqa: E402

# --- Import-safety self-check ------------------------------------------------
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
    f"Parser-format harness import graph pulled in physical-action-capable module(s): {_imported_forbidden}. "
    "This harness must remain perception-only — fix the import, do not silence this."
)
# ------------------------------------------------------------------------------

import logging  # noqa: E402

_eval_logger = logging.getLogger("claude_evaluation.parser_format")
if not _eval_logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s [FMT] %(message)s"))
    _eval_logger.addHandler(_handler)
    _eval_logger.setLevel(logging.INFO)
    _eval_logger.propagate = False

OUTPUT_DIR = PROJECT_ROOT / "benchmarks" / "results" / "experiments"
STRICT_PROMPT_PATH = EXPERIMENTS_DIR / "prompts" / "component_grounding_strict_v1.txt"

PROMPT_VARIANTS = {
    "A_current_prompt": COMPONENT_GROUNDING_PROMPT_PATH,
    "B_strict_no_prose_instruction": STRICT_PROMPT_PATH,
}


def classify_response_format(raw_text: Optional[str]) -> str:
    """Mirrors the PRODUCTION parser's exact logic (app/vision/providers/
    anthropic_provider.py::analyze_screen) step by step, so the
    classification and the "would production succeed" question use
    identical rules — never a looser/eval-only interpretation."""
    stripped = (raw_text or "").strip()
    if not stripped:
        return "empty"

    try:
        json.loads(stripped)
        return "raw_json_only"
    except (ValueError, TypeError):
        pass

    if stripped.startswith("```"):
        candidate = stripped.strip("`")
        if candidate.startswith("json"):
            candidate = candidate[4:]
        candidate = candidate.strip()
        try:
            json.loads(candidate)
            return "fenced_json_leading"
        except (ValueError, TypeError):
            pass

    if "{" in stripped and "}" in stripped:
        return "prose_plus_json"
    return "no_json_found"


def _production_parsed_json(raw_text: Optional[str]) -> Optional[dict]:
    """Reproduces app/vision/providers/anthropic_provider.py's own
    parsing exactly (not imported directly, to keep this experiment
    fully decoupled from any accidental production import — but the
    logic below is a byte-for-byte match, verified against that file)."""
    text_to_parse = (raw_text or "").strip()
    if text_to_parse.startswith("```"):
        text_to_parse = text_to_parse.strip("`")
        if text_to_parse.startswith("json"):
            text_to_parse = text_to_parse[4:]
        text_to_parse = text_to_parse.strip()
    try:
        return json.loads(text_to_parse)
    except (ValueError, TypeError):
        return None


def _run_case_once(provider, model: str, variant_name: str, prompt_path: Path, case: EvalCase, run_index: int) -> dict[str, Any]:
    from PIL import Image

    with Image.open(case.screenshot) as img:
        width, height = img.size
    prompt_text = prompt_path.read_text(encoding="utf-8").format(width=width, height=height)

    _eval_logger.info("FMT_CALL_START variant=%s case_id=%s run=%s", variant_name, case.case_id, run_index)

    result: dict[str, Any] = {
        "variant": variant_name, "case_id": case.case_id, "run_index": run_index,
        "provider_error": None, "raw_text": None, "raw_text_length": None,
        "format_class": None, "production_parse_succeeds": None, "schema_valid": None,
        "latency_ms": None, "input_tokens": None, "output_tokens": None, "estimated_cost": None,
    }

    outcome = call_with_provider_retry(
        lambda: provider.analyze_screen(case.screenshot, "Independently locate the Outlook icon, label, and sublabel", prompt_text),
        stage=f"EXPERIMENT_FORMAT_{variant_name}", provider_name=provider.provider_name,
    )
    if outcome.result is None:
        result["provider_error"] = outcome.error
        _eval_logger.info("FMT_CALL_END variant=%s case_id=%s outcome=PROVIDER_ERROR", variant_name, case.case_id)
        return result

    call = outcome.result
    result["latency_ms"] = call.latency_ms
    result["input_tokens"] = call.input_tokens
    result["output_tokens"] = call.output_tokens
    result["estimated_cost"] = estimate_cost(provider.provider_name, call.model, call.input_tokens, call.output_tokens)
    result["raw_text"] = call.raw_text
    result["raw_text_length"] = len(call.raw_text) if call.raw_text else 0

    result["format_class"] = classify_response_format(call.raw_text)
    production_parsed = _production_parsed_json(call.raw_text)
    result["production_parse_succeeds"] = production_parsed is not None
    # Sanity cross-check: the provider's OWN parsed_json (call.parsed_json)
    # should agree with our reproduced logic above.
    assert (call.parsed_json is not None) == (production_parsed is not None), (
        "Reproduced production parsing logic disagreed with the real provider's own parsed_json — "
        "this experiment's classification would be unreliable; fix _production_parsed_json() to match "
        "app/vision/providers/anthropic_provider.py exactly."
    )

    if production_parsed is not None:
        try:
            ComponentGroundingResponse.model_validate(production_parsed)
            result["schema_valid"] = True
        except ValidationError:
            result["schema_valid"] = False
    else:
        result["schema_valid"] = False

    _eval_logger.info(
        "FMT_CALL_END variant=%s case_id=%s format=%s production_parse_succeeds=%s schema_valid=%s",
        variant_name, case.case_id, result["format_class"], result["production_parse_succeeds"], result["schema_valid"],
    )
    return result


def run_format_experiment(extra_runs_case_id: str, extra_runs: int) -> dict[str, Any]:
    cases = get_cases(stage="OUTLOOK_SEARCH")
    if not cases:
        raise SystemExit("No OUTLOOK_SEARCH cases defined in benchmarks/claude/cases.py")

    provider, model = get_provider()
    _eval_logger.info("provider=%s model=%s", provider.provider_name, model)

    variant_results: dict[str, list[dict[str, Any]]] = {}
    for variant_name, prompt_path in PROMPT_VARIANTS.items():
        results = []
        for case in cases:
            runs_for_case = extra_runs if case.case_id == extra_runs_case_id else 1
            for run_index in range(1, runs_for_case + 1):
                results.append(_run_case_once(provider, model, variant_name, prompt_path, case, run_index))
        variant_results[variant_name] = results

    summary = {}
    for variant_name, results in variant_results.items():
        total = len(results)
        by_format: dict[str, int] = {}
        for r in results:
            fmt = r.get("format_class") or "provider_error"
            by_format[fmt] = by_format.get(fmt, 0) + 1
        production_ok = [r for r in results if r.get("production_parse_succeeds")]
        schema_ok = [r for r in results if r.get("schema_valid")]
        latencies = [r["latency_ms"] for r in results if r.get("latency_ms") is not None]
        summary[variant_name] = {
            "total_calls": total,
            "format_breakdown": by_format,
            "raw_json_only_rate": f"{by_format.get('raw_json_only', 0)}/{total}",
            "fenced_json_rate": f"{by_format.get('fenced_json_leading', 0)}/{total}",
            "prose_plus_json_rate": f"{by_format.get('prose_plus_json', 0)}/{total}",
            "no_json_found_rate": f"{by_format.get('no_json_found', 0)}/{total}",
            "production_parse_success_rate": f"{len(production_ok)}/{total}" if total else "0/0",
            "schema_validation_success_rate": f"{len(schema_ok)}/{total}" if total else "0/0",
            "median_latency_ms": round(statistics.median(latencies), 1) if latencies else None,
        }

    return {
        "timestamp": datetime.now().isoformat(),
        "provider": provider.provider_name,
        "model": model,
        "results": variant_results,
        "summary": summary,
    }


def _write_markdown(evaluation: dict[str, Any], path: Path) -> None:
    lines = [
        f"# Structured-output parser format experiment — {evaluation['timestamp']}",
        "",
        f"provider={evaluation['provider']} model={evaluation['model']}",
        "",
        "| Variant | Raw-JSON-only | Fenced-JSON | Prose+JSON | No-JSON | Production Parse Success | Schema Valid | Median Latency (ms) |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for name, s in evaluation["summary"].items():
        lines.append(
            f"| {name} | {s['raw_json_only_rate']} | {s['fenced_json_rate']} | {s['prose_plus_json_rate']} | "
            f"{s['no_json_found_rate']} | {s['production_parse_success_rate']} | {s['schema_validation_success_rate']} | "
            f"{s['median_latency_ms']} |"
        )
    lines.append("")

    lines.append("## Per-call detail")
    for name, results in evaluation["results"].items():
        lines.append(f"### {name}")
        for r in results:
            lines.append(
                f"- {r['case_id']} run {r['run_index']}: format={r['format_class']} "
                f"production_parse_succeeds={r['production_parse_succeeds']} schema_valid={r['schema_valid']} "
                f"latency_ms={r['latency_ms']} raw_text_length={r['raw_text_length']}"
            )
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Compare structured-output format reliability across prompt strictness (perception-only).")
    parser.add_argument("--extra-runs-case", default=None)
    parser.add_argument("--extra-runs", type=int, default=3)
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    args = parser.parse_args(argv)

    extra_runs_case_id = args.extra_runs_case
    if extra_runs_case_id is None:
        cases = get_cases(stage="OUTLOOK_SEARCH")
        extra_runs_case_id = cases[0].case_id if cases else ""

    evaluation = run_format_experiment(extra_runs_case_id, args.extra_runs)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = output_dir / f"parser_format_{timestamp}.json"
    md_path = output_dir / f"parser_format_{timestamp}.md"

    json_path.write_text(json.dumps(evaluation, indent=2, default=str), encoding="utf-8")
    _write_markdown(evaluation, md_path)

    print(f"\nResults written to:\n  {json_path}\n  {md_path}\n")
    for name, s in evaluation["summary"].items():
        print(f"{name}")
        print(f"  format breakdown: {s['format_breakdown']}")
        print(f"  production parse success: {s['production_parse_success_rate']}")
        print(f"  schema valid: {s['schema_validation_success_rate']}")
        print()


if __name__ == "__main__":
    main()
