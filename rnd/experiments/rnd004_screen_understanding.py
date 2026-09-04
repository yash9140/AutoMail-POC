"""RND-004 — Vision Screen Understanding Accuracy (Gemini only).

Runs screen_understanding_v2 (coordinate-free) against all 10 required
RND-002 raw screenshots (OUTLOOK-001..010) sequentially, evaluates each
prediction locally against the RND-002 manifest's human-verified ground
truth (rnd/metrics/evaluator.py — no AI involved in grading), and writes:

    results/raw/rnd004_screen_understanding_results.json
    results/reports/rnd004_screen_understanding_summary.md
    results/raw/provider_responses/rnd004/<test_id>.json  (sanitized)

Same dry-run / --execute split as RND-003 and the readiness stage: dry
run only prints what would be sent (provider, model, all 10 test IDs and
filenames) and touches no network; --execute performs the real sequential
run, only after explicit human approval already obtained in conversation.

Run (from outlook-vision-poc/):
    python rnd/experiments/rnd004_screen_understanding.py
    python rnd/experiments/rnd004_screen_understanding.py --execute
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from PIL import Image
from pydantic import ValidationError

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from rnd.metrics.evaluator import evaluate_case  # noqa: E402
from rnd.models.screen_understanding import RND004CaseResult, ScreenUnderstandingResponse  # noqa: E402
from rnd.providers.base import VisionProviderError  # noqa: E402
from rnd.providers.gemini_provider import GeminiProvider  # noqa: E402

MANIFEST_PATH = PROJECT_ROOT / "test_cases" / "outlook" / "dataset_manifest.json"
RAW_DIR = PROJECT_ROOT / "screenshots" / "raw"
ANNOTATED_DIR = PROJECT_ROOT / "screenshots" / "annotated"
PROMPT_PATH = PROJECT_ROOT / "rnd" / "prompts" / "screen_understanding_v2.txt"
RESULTS_PATH = PROJECT_ROOT / "results" / "raw" / "rnd004_screen_understanding_results.json"
REPORT_PATH = PROJECT_ROOT / "results" / "reports" / "rnd004_screen_understanding_summary.md"
RAW_RESPONSES_DIR = PROJECT_ROOT / "results" / "raw" / "provider_responses" / "rnd004"
PRICING_PATH = PROJECT_ROOT / "config" / "model_pricing.json"

PROMPT_VERSION = "screen_understanding_v2"
GOAL = "Reply to the currently opened email."
MAX_ATTEMPTS = 2
REQUIRED_TEST_IDS = [f"OUTLOOK-{i:03d}" for i in range(1, 11)]

# OUTLOOK-002's Reply-visibility ground truth lives only in the RND-002
# manifest's free-text notes (human-confirmed, no bounding box was
# annotated — Reply's bbox belongs to OUTLOOK-003). Recorded here
# explicitly rather than silently left out, since it IS real human-verified
# ground truth, just not structured as a `targets` entry.
EXTRA_EXPECTED_CONTROLS = {
    "OUTLOOK-002": ["Reply"],
}

# OUTLOOK-003's notes explicitly state Reply All was NOT visible/present —
# a rare case where we have positive evidence of absence, usable to detect
# a genuine hallucination if the model claims it anyway.
KNOWN_ABSENT_CONTROLS = {
    "OUTLOOK-003": ["Reply All"],
}


def load_required_cases() -> list[dict]:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    by_id = {c["test_id"]: c for c in manifest["cases"]}
    cases = []
    for test_id in REQUIRED_TEST_IDS:
        case = by_id.get(test_id)
        if case is None:
            raise SystemExit(f"{test_id} not found in manifest")
        if case["status"] != "captured":
            raise SystemExit(f"{test_id} is not captured (status={case['status']}) — cannot run RND-004")
        cases.append(case)
    return cases


def resolve_image_path(filename: str) -> Path:
    path = (RAW_DIR / filename).resolve()
    if RAW_DIR.resolve() not in path.parents:
        raise SystemExit(f"Refusing to use an image outside screenshots/raw/: {path}")
    if ANNOTATED_DIR.resolve() in path.parents:
        raise SystemExit("Refusing to send an annotated screenshot to a Vision provider.")
    if not path.exists():
        raise SystemExit(f"Image not found: {path}")
    return path


def expected_relevant_controls_for(case: dict) -> Optional[list[str]]:
    if case["test_id"] in EXTRA_EXPECTED_CONTROLS:
        return EXTRA_EXPECTED_CONTROLS[case["test_id"]]
    targets = case.get("targets") or []
    if not targets:
        return None
    return [t["name"] for t in targets]


def estimate_cost(
    provider: str, model: str, input_tokens: Optional[int], output_tokens: Optional[int]
) -> tuple[Optional[float], str]:
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


def print_plan(cases: list[dict], model: str) -> None:
    print("=== RND-004 Screen Understanding Accuracy — Gemini ===")
    print("Provider: gemini")
    print(f"Model:    {model or '(not set)'}")
    print(f"Cases:    {len(cases)}")
    for case in cases:
        print(f"  {case['test_id']}  ->  {case['filename']}")
    print(f"Goal:     {GOAL}")
    print(f"Prompt:   {PROMPT_VERSION}")


def run_one_case(provider: GeminiProvider, case: dict, prompt_template: str) -> RND004CaseResult:
    test_id = case["test_id"]
    image_path = resolve_image_path(case["filename"])
    with Image.open(image_path) as img:
        width, height = img.size
    prompt_text = prompt_template.format(goal=GOAL)

    expected_application = case["expected"]["application"]
    expected_state = case["expected"]["state"]
    expected_action = case["expected"].get("recommended_action")
    expected_controls = expected_relevant_controls_for(case)
    known_absent = KNOWN_ABSENT_CONTROLS.get(test_id, [])

    attempt_count = 0
    last_error: Optional[str] = None
    call_result = None
    while attempt_count < MAX_ATTEMPTS:
        attempt_count += 1
        try:
            call_result = provider.analyze_screen(image_path, GOAL, prompt_text)
            last_error = None
            break
        except VisionProviderError as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            print(f"  Attempt {attempt_count} FAILED: {last_error}")

    if call_result is None:
        return RND004CaseResult(
            test_id=test_id,
            provider="gemini",
            model=provider.model,
            prompt_version=PROMPT_VERSION,
            expected_application=expected_application,
            expected_state=expected_state,
            expected_relevant_controls=expected_controls,
            expected_action=expected_action,
            attempt_count=attempt_count,
            schema_valid=False,
            request_success=False,
            failure_types=["PROVIDER_ERROR"] if "Timeout" not in (last_error or "") else ["TIMEOUT"],
            error=last_error,
        )

    RAW_RESPONSES_DIR.mkdir(parents=True, exist_ok=True)
    raw_path = RAW_RESPONSES_DIR / f"{test_id}.json"
    raw_path.write_text(
        json.dumps(
            {"provider": "gemini", "model": call_result.model, "raw_text": call_result.raw_text, "parsed_json": call_result.parsed_json},
            indent=2,
        ),
        encoding="utf-8",
    )

    schema_valid = False
    structured: Optional[ScreenUnderstandingResponse] = None
    schema_error: Optional[str] = None
    if call_result.parsed_json is not None:
        try:
            structured = ScreenUnderstandingResponse.model_validate(call_result.parsed_json)
            schema_valid = True
        except ValidationError as exc:
            schema_error = str(exc)
    else:
        schema_error = "Response body was not valid JSON."

    estimated_cost, cost_note = estimate_cost("gemini", call_result.model, call_result.input_tokens, call_result.output_tokens)

    eval_fields = evaluate_case(
        expected_application=expected_application,
        expected_state=expected_state,
        expected_action=expected_action,
        expected_relevant_controls=expected_controls,
        predicted_application=structured.application if structured else None,
        predicted_state=structured.screen_state if structured else None,
        predicted_relevant_controls=structured.relevant_visible_controls if structured else None,
        predicted_action=structured.recommended_action if structured else None,
        predicted_target=structured.target if structured else None,
        known_absent_controls=known_absent,
    )
    failure_types = list(eval_fields.pop("failure_types"))
    if not schema_valid:
        failure_types.append("INVALID_SCHEMA")

    return RND004CaseResult(
        test_id=test_id,
        provider="gemini",
        model=call_result.model,
        prompt_version=PROMPT_VERSION,
        expected_application=expected_application,
        predicted_application=structured.application if structured else None,
        expected_state=expected_state,
        predicted_state=structured.screen_state if structured else None,
        expected_relevant_controls=expected_controls,
        predicted_relevant_controls=structured.relevant_visible_controls if structured else None,
        expected_action=expected_action,
        predicted_action=structured.recommended_action if structured else None,
        predicted_target=structured.target if structured else None,
        confidence=structured.confidence if structured else None,
        latency_ms=call_result.latency_ms,
        attempt_count=attempt_count,
        input_tokens=call_result.input_tokens,
        output_tokens=call_result.output_tokens,
        estimated_cost=estimated_cost,
        schema_valid=schema_valid,
        request_success=True,
        error=schema_error if not schema_valid else None,
        raw_response_reference=str(raw_path.relative_to(PROJECT_ROOT)),
        **eval_fields,
        failure_types=failure_types,
    )


def build_report(results: list[RND004CaseResult]) -> str:
    n = len(results)
    app_scored = [r for r in results if r.application_correct is not None]
    state_scored = [r for r in results if r.state_correct is not None]
    controls_scored = [r for r in results if r.controls_correct is not None]
    action_scored = [r for r in results if r.action_correct is not None]

    app_correct = sum(1 for r in app_scored if r.application_correct)
    state_correct = sum(1 for r in state_scored if r.state_correct)
    controls_correct = sum(1 for r in controls_scored if r.controls_correct)
    action_correct = sum(1 for r in action_scored if r.action_correct)

    latencies = [r.latency_ms for r in results if r.latency_ms is not None]
    input_tokens_total = sum(r.input_tokens or 0 for r in results if r.input_tokens is not None)
    output_tokens_total = sum(r.output_tokens or 0 for r in results if r.output_tokens is not None)
    costs = [r.estimated_cost for r in results if r.estimated_cost is not None]
    total_cost = sum(costs) if costs else None

    high_conf_wrong = [
        r for r in results
        if r.confidence is not None and r.confidence >= 0.8
        and (r.state_correct is False or r.action_correct is False or r.application_correct is False)
    ]

    lines = ["# RND-004 Screen Understanding Summary — Gemini (gemini-3.6-flash)", ""]
    lines.append(f"Cases evaluated: {n}/10")
    lines.append("")
    lines.append(f"- Application recognition: {app_correct}/{len(app_scored)} = {app_correct/len(app_scored)*100:.0f}%" if app_scored else "- Application recognition: no scorable cases")
    lines.append(f"- Screen-state understanding: {state_correct}/{len(state_scored)} = {state_correct/len(state_scored)*100:.0f}%" if state_scored else "- Screen-state understanding: no scorable cases")
    lines.append(f"- Relevant-control detection: {controls_correct}/{len(controls_scored)} = {controls_correct/len(controls_scored)*100:.0f}% (scored only on cases with ground-truth controls: {', '.join(r.test_id for r in controls_scored)})" if controls_scored else "- Relevant-control detection: no scorable cases")
    lines.append(f"- Correct next action: {action_correct}/{len(action_scored)} = {action_correct/len(action_scored)*100:.0f}% (scored only on cases with an expected action: {', '.join(r.test_id for r in action_scored)})" if action_scored else "- Correct next action: no scorable cases")
    lines.append("")

    lines.append("## Latency")
    if latencies:
        lines.append(f"- min: {min(latencies):.1f} ms")
        lines.append(f"- max: {max(latencies):.1f} ms")
        lines.append(f"- average: {statistics.mean(latencies):.1f} ms")
        lines.append(f"- median (p50): {statistics.median(latencies):.1f} ms")
    else:
        lines.append("- no successful calls to measure")
    lines.append("")

    lines.append("## Usage / Cost")
    lines.append(f"- total input tokens: {input_tokens_total}")
    lines.append(f"- total output tokens: {output_tokens_total}")
    lines.append(f"- total cost: ${total_cost:.6f}" if total_cost is not None else "- total cost: unavailable")
    lines.append(f"- average cost per screenshot: ${total_cost/n:.6f}" if total_cost is not None else "- average cost per screenshot: unavailable")
    lines.append("")

    lines.append("## High-confidence wrong cases")
    if high_conf_wrong:
        for r in high_conf_wrong:
            lines.append(f"- {r.test_id}: confidence={r.confidence}, failure_types={r.failure_types}")
    else:
        lines.append("- none found")
    lines.append("")

    lines.append("## Per-case results")
    lines.append("| test_id | app_correct | state_correct | controls_correct | action_correct | confidence | latency_ms | failure_types |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for r in results:
        lines.append(
            f"| {r.test_id} | {r.application_correct} | {r.state_correct} | {r.controls_correct} | "
            f"{r.action_correct} | {r.confidence} | {r.latency_ms} | {', '.join(r.failure_types) or '-'} |"
        )

    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="RND-004 screen understanding accuracy (Gemini)")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()

    load_dotenv(PROJECT_ROOT / ".env")
    model = os.environ.get("GEMINI_MODEL", "")
    api_key = os.environ.get("GEMINI_API_KEY", "")

    cases = load_required_cases()
    print_plan(cases, model)

    if not args.execute:
        print()
        print("DRY RUN — no API calls made, nothing sent anywhere. Re-run with --execute after explicit approval.")
        return

    if not api_key:
        raise SystemExit("GEMINI_API_KEY is not set.")
    if not model:
        raise SystemExit("GEMINI_MODEL is not set.")

    timeout_seconds = float(os.environ.get("VISION_REQUEST_TIMEOUT_SECONDS", "30"))
    provider = GeminiProvider(api_key=api_key, model=model, timeout_seconds=timeout_seconds)
    prompt_template = PROMPT_PATH.read_text(encoding="utf-8")

    results: list[RND004CaseResult] = []
    for case in cases:
        print()
        print(f"--- {case['test_id']} ---")
        result = run_one_case(provider, case, prompt_template)
        results.append(result)
        print(f"  request_success={result.request_success} schema_valid={result.schema_valid} latency_ms={result.latency_ms}")

    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(
        json.dumps(
            {
                "experiment_id": "RND-004",
                "timestamp": datetime.now().isoformat(),
                "provider": "gemini",
                "model": model,
                "prompt_version": PROMPT_VERSION,
                "cases": [json.loads(r.model_dump_json()) for r in results],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(build_report(results), encoding="utf-8")

    print()
    print(f"Results written to {RESULTS_PATH.relative_to(PROJECT_ROOT)}")
    print(f"Report written to {REPORT_PATH.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
