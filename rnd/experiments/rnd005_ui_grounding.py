"""RND-005 — UI Grounding Accuracy (Gemini only).

Answers: given a raw Outlook screenshot and a target control name, can
Gemini return a coordinate that falls inside the human-annotated
clickable target area? Isolated from screen understanding / next-action
reasoning — ui_grounding_v1 asks only for a location.

Uses only the 4 RND-002 targets with real human-verified bounding boxes:
OUTLOOK-001/email_row, OUTLOOK-003/Reply, OUTLOOK-004/reply_editor,
OUTLOOK-005/Send. N=4 — not a broad benchmark, stated explicitly
everywhere this result is reported.

Same dry-run / --execute split as every prior stage. No clicking, no
mouse movement — measurement only.

Run (from outlook-vision-poc/):
    python rnd/experiments/rnd005_ui_grounding.py
    python rnd/experiments/rnd005_ui_grounding.py --execute
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from PIL import Image, ImageDraw
from pydantic import ValidationError

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from rnd.metrics.grounding import (  # noqa: E402
    aggregate_grounding,
    classify_grounding_result,
    coordinate_in_image_bounds,
    distance_to_bbox_center_px,
    distance_to_bbox_px,
    is_high_confidence_failure,
)
from rnd.models.dataset_manifest import BoundingBox  # noqa: E402
from rnd.models.grounding import GroundingResponse, RND005CaseResult  # noqa: E402
from rnd.providers.base import VisionProviderError  # noqa: E402
from rnd.providers.gemini_provider import GeminiProvider  # noqa: E402

MANIFEST_PATH = PROJECT_ROOT / "test_cases" / "outlook" / "dataset_manifest.json"
RAW_DIR = PROJECT_ROOT / "screenshots" / "raw"
ANNOTATED_DIR = PROJECT_ROOT / "screenshots" / "annotated"
RND005_ANNOTATED_DIR = ANNOTATED_DIR / "rnd005"
PROMPT_PATH = PROJECT_ROOT / "rnd" / "prompts" / "ui_grounding_v1.txt"
RESULTS_PATH = PROJECT_ROOT / "results" / "raw" / "rnd005_ui_grounding_results.json"
REPORT_PATH = PROJECT_ROOT / "results" / "reports" / "rnd005_ui_grounding_summary.md"
RAW_RESPONSES_DIR = PROJECT_ROOT / "results" / "raw" / "provider_responses" / "rnd005"
PRICING_PATH = PROJECT_ROOT / "config" / "model_pricing.json"

PROMPT_VERSION = "ui_grounding_v1"
MAX_ATTEMPTS = 2
REQUIRED_TARGETS = [
    ("OUTLOOK-001", "email_row"),
    ("OUTLOOK-003", "Reply"),
    ("OUTLOOK-004", "reply_editor"),
    ("OUTLOOK-005", "Send"),
]


def load_target_cases() -> list[dict]:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    by_id = {c["test_id"]: c for c in manifest["cases"]}
    out = []
    for test_id, target_name in REQUIRED_TARGETS:
        case = by_id.get(test_id)
        if case is None:
            raise SystemExit(f"{test_id} not found in manifest")
        if case["status"] != "captured":
            raise SystemExit(f"{test_id} is not captured — cannot run RND-005")
        target = next((t for t in case["targets"] if t["name"] == target_name), None)
        if target is None:
            raise SystemExit(f"{test_id} has no annotated target named '{target_name}' in the manifest")
        out.append({"case": case, "target_name": target_name, "bbox": target["bbox"]})
    return out


def resolve_image_path(filename: str) -> Path:
    path = (RAW_DIR / filename).resolve()
    if RAW_DIR.resolve() not in path.parents:
        raise SystemExit(f"Refusing to use an image outside screenshots/raw/: {path}")
    if ANNOTATED_DIR.resolve() in path.parents:
        raise SystemExit("Refusing to send an annotated screenshot to a Vision provider.")
    if not path.exists():
        raise SystemExit(f"Image not found: {path}")
    return path


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


def generate_annotated_preview(result: RND005CaseResult, raw_image_path: Path) -> Path:
    RND005_ANNOTATED_DIR.mkdir(parents=True, exist_ok=True)
    img = Image.open(raw_image_path).convert("RGB")
    draw = ImageDraw.Draw(img)

    bbox = result.bbox
    draw.rectangle([bbox.x1, bbox.y1, bbox.x2, bbox.y2], outline=(0, 160, 0), width=3)
    label = f"{result.target} (ground truth)"
    draw.rectangle(draw.textbbox((bbox.x1, max(0, bbox.y1 - 20)), label), fill=(0, 160, 0))
    draw.text((bbox.x1, max(0, bbox.y1 - 20)), label, fill=(255, 255, 255))

    if result.predicted_x is not None and result.predicted_y is not None:
        px, py = result.predicted_x, result.predicted_y
        color = (0, 120, 255) if result.grounding_result == "PASS" else (220, 0, 0)
        r = 8
        draw.ellipse([px - r, py - r, px + r, py + r], outline=color, width=3)
        draw.line([px - r - 4, py, px + r + 4, py], fill=color, width=2)
        draw.line([px, py - r - 4, px, py + r + 4], fill=color, width=2)

    header = (
        f"{result.test_id}  |  target={result.target}  |  {result.grounding_result}  |  "
        f"confidence={result.confidence}"
    )
    draw.rectangle([0, 0, draw.textlength(header) + 10, 20], fill=(0, 0, 0))
    draw.text((5, 2), header, fill=(255, 255, 255))

    out_path = RND005_ANNOTATED_DIR / f"{result.test_id.lower()}_{result.target.lower()}_grounding.png"
    img.save(out_path, "PNG")
    return out_path


def run_one_case(provider: GeminiProvider, entry: dict, prompt_template: str) -> tuple[RND005CaseResult, Path]:
    case = entry["case"]
    test_id = case["test_id"]
    target_name = entry["target_name"]
    bbox = BoundingBox(**entry["bbox"])

    image_path = resolve_image_path(case["filename"])
    with Image.open(image_path) as img:
        width, height = img.size

    prompt_text = prompt_template.format(width=width, height=height, target=target_name)

    attempt_count = 0
    last_error: Optional[str] = None
    call_result = None
    while attempt_count < MAX_ATTEMPTS:
        attempt_count += 1
        try:
            call_result = provider.analyze_screen(image_path, f"Locate {target_name}", prompt_text)
            last_error = None
            break
        except VisionProviderError as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            print(f"  Attempt {attempt_count} FAILED: {last_error}")

    common = dict(
        test_id=test_id,
        target=target_name,
        model=provider.model,
        prompt_version=PROMPT_VERSION,
        image_filename=case["filename"],
        image_width=width,
        image_height=height,
        bbox=bbox,
        bbox_width=bbox.x2 - bbox.x1,
        bbox_height=bbox.y2 - bbox.y1,
        attempt_count=attempt_count,
    )

    if call_result is None:
        result = RND005CaseResult(
            **common,
            grounding_result="ERROR",
            schema_valid=False,
            request_success=False,
            error=last_error,
        )
        return result, image_path

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
    structured: Optional[GroundingResponse] = None
    schema_error: Optional[str] = None
    if call_result.parsed_json is not None:
        try:
            structured = GroundingResponse.model_validate(call_result.parsed_json)
            schema_valid = True
        except ValidationError as exc:
            schema_error = str(exc)
    else:
        schema_error = "Response body was not valid JSON."

    predicted_x = structured.x if structured else None
    predicted_y = structured.y if structured else None
    coord_in_bounds = coordinate_in_image_bounds(predicted_x, predicted_y, width, height) if structured else None
    grounding_result = classify_grounding_result(predicted_x, predicted_y, bbox if structured else None)
    coord_inside_target = grounding_result == "PASS" if structured else None

    dist_to_bbox = distance_to_bbox_px(predicted_x, predicted_y, bbox) if structured else None
    dist_to_center = distance_to_bbox_center_px(predicted_x, predicted_y, bbox) if structured else None

    estimated_cost, cost_note = estimate_cost("gemini", call_result.model, call_result.input_tokens, call_result.output_tokens)

    high_conf_fail = is_high_confidence_failure(
        structured.confidence if structured else None, grounding_result
    )

    result = RND005CaseResult(
        **common,
        predicted_x=predicted_x,
        predicted_y=predicted_y,
        coordinate_in_bounds=coord_in_bounds,
        coordinate_inside_target=coord_inside_target,
        grounding_result=grounding_result,
        distance_to_bbox_px=dist_to_bbox,
        distance_to_bbox_center_px=dist_to_center,
        confidence=structured.confidence if structured else None,
        reason=structured.reason if structured else None,
        latency_ms=call_result.latency_ms,
        input_tokens=call_result.input_tokens,
        output_tokens=call_result.output_tokens,
        estimated_cost=estimated_cost,
        cost_note=cost_note,
        schema_valid=schema_valid,
        request_success=True,
        high_confidence_failure=high_conf_fail,
        raw_response_reference=str(raw_path.relative_to(PROJECT_ROOT)),
        error=schema_error if not schema_valid else None,
    )
    return result, image_path


def build_report(results: list[RND005CaseResult]) -> str:
    agg = aggregate_grounding([{"target": r.target, "grounding_result": r.grounding_result} for r in results])
    latencies = [r.latency_ms for r in results if r.latency_ms is not None]
    input_tokens_total = sum(r.input_tokens or 0 for r in results if r.input_tokens is not None)
    output_tokens_total = sum(r.output_tokens or 0 for r in results if r.output_tokens is not None)
    costs = [r.estimated_cost for r in results if r.estimated_cost is not None]
    total_cost = sum(costs) if costs else None
    high_conf_failures = [r for r in results if r.high_confidence_failure]

    lines = ["# RND-005 UI Grounding Summary — Gemini (gemini-3.6-flash)", ""]
    lines.append(
        "**N = 4** — only four RND-002 targets currently have human-verified bounding boxes. "
        "This measures grounding on this POC's known Outlook controls, not general desktop grounding accuracy."
    )
    lines.append("")
    lines.append("## Per-target result")
    for r in results:
        lines.append(f"- {r.test_id} / {r.target}: **{r.grounding_result}**")
    lines.append("")
    lines.append(f"Overall grounding: {agg['passed']}/{agg['total']} = {agg['percentage']}%  (N = {agg['n']})")
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
    lines.append("")

    lines.append("## High-confidence grounding failures")
    if high_conf_failures:
        for r in high_conf_failures:
            lines.append(f"- {r.test_id}/{r.target}: confidence={r.confidence}, distance_to_bbox={r.distance_to_bbox_px:.1f}px")
    else:
        lines.append("- none found")
    lines.append("")

    lines.append("## Per-case detail")
    lines.append("| test_id | target | bbox | predicted (x,y) | result | confidence | dist_to_bbox_px | dist_to_center_px | latency_ms |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for r in results:
        bbox_str = f"({r.bbox.x1},{r.bbox.y1})-({r.bbox.x2},{r.bbox.y2})"
        pred_str = f"({r.predicted_x},{r.predicted_y})" if r.predicted_x is not None else "-"
        lines.append(
            f"| {r.test_id} | {r.target} | {bbox_str} | {pred_str} | {r.grounding_result} | {r.confidence} | "
            f"{f'{r.distance_to_bbox_px:.1f}' if r.distance_to_bbox_px is not None else '-'} | "
            f"{f'{r.distance_to_bbox_center_px:.1f}' if r.distance_to_bbox_center_px is not None else '-'} | "
            f"{r.latency_ms} |"
        )

    return "\n".join(lines) + "\n"


def print_plan(entries: list[dict], model: str) -> None:
    print("=== RND-005 UI Grounding Accuracy — Gemini ===")
    print("Provider: gemini")
    print(f"Model:    {model or '(not set)'}")
    for e in entries:
        print(f"  {e['case']['test_id']}  target={e['target_name']}  ->  {e['case']['filename']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="RND-005 UI grounding accuracy (Gemini)")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()

    load_dotenv(PROJECT_ROOT / ".env")
    model = os.environ.get("GEMINI_MODEL", "")
    api_key = os.environ.get("GEMINI_API_KEY", "")

    entries = load_target_cases()
    print_plan(entries, model)

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

    results: list[RND005CaseResult] = []
    for entry in entries:
        print()
        print(f"--- {entry['case']['test_id']} / {entry['target_name']} ---")
        result, image_path = run_one_case(provider, entry, prompt_template)
        results.append(result)
        print(f"  grounding_result={result.grounding_result} confidence={result.confidence} latency_ms={result.latency_ms}")
        if result.request_success:
            preview_path = generate_annotated_preview(result, image_path)
            print(f"  annotated preview: {preview_path.relative_to(PROJECT_ROOT)}")

    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(
        json.dumps(
            {
                "experiment_id": "RND-005",
                "timestamp": datetime.now().isoformat(),
                "provider": "gemini",
                "model": model,
                "prompt_version": PROMPT_VERSION,
                "n": len(results),
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
