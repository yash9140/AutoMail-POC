"""RND-005B — Coordinate Convention Confirmation.

ONE fresh, independent Gemini grounding request (not a reuse of any
stored RND-003/RND-005 value) using a prompt that explicitly asks Gemini
to state which coordinate convention its x/y are expressed in, without
ever telling it we expect 0-1000 normalized coordinates (fairness rule —
the prompt only asks it to describe whatever it naturally returns).

Same dry-run / --execute split as every prior live stage. No mouse/
keyboard action of any kind — measurement only, same as RND-005.

Run (from outlook-vision-poc/):
    python rnd/experiments/rnd005b_coordinate_convention.py
    python rnd/experiments/rnd005b_coordinate_convention.py --execute
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from PIL import Image
from pydantic import ValidationError

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from rnd.metrics.coordinate_calibration import normalize_1000_to_pixels  # noqa: E402
from rnd.metrics.grounding import classify_grounding_result, coordinate_in_image_bounds  # noqa: E402
from rnd.models.dataset_manifest import BoundingBox  # noqa: E402
from rnd.models.grounding import CoordinateConventionResponse  # noqa: E402
from rnd.providers.base import VisionProviderError  # noqa: E402
from rnd.providers.gemini_provider import GeminiProvider  # noqa: E402

MANIFEST_PATH = PROJECT_ROOT / "test_cases" / "outlook" / "dataset_manifest.json"
RAW_DIR = PROJECT_ROOT / "screenshots" / "raw"
ANNOTATED_DIR = PROJECT_ROOT / "screenshots" / "annotated"
PROMPT_PATH = PROJECT_ROOT / "rnd" / "prompts" / "ui_grounding_coordinate_convention_v1.txt"
RESULTS_PATH = PROJECT_ROOT / "results" / "raw" / "rnd005b_coordinate_convention_confirmation.json"
RAW_RESPONSES_DIR = PROJECT_ROOT / "results" / "raw" / "provider_responses" / "rnd005b"
PRICING_PATH = PROJECT_ROOT / "config" / "model_pricing.json"

PROMPT_VERSION = "ui_grounding_coordinate_convention_v1"
MAX_ATTEMPTS = 2
TEST_ID = "OUTLOOK-003"
TARGET_NAME = "Reply"


def load_case() -> dict:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    case = next(c for c in manifest["cases"] if c["test_id"] == TEST_ID)
    target = next(t for t in case["targets"] if t["name"] == TARGET_NAME)
    return {"case": case, "bbox": target["bbox"]}


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


def main() -> None:
    parser = argparse.ArgumentParser(description="RND-005B coordinate convention confirmation")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()

    load_dotenv(PROJECT_ROOT / ".env")
    model = os.environ.get("GEMINI_MODEL", "")
    api_key = os.environ.get("GEMINI_API_KEY", "")

    loaded = load_case()
    case = loaded["case"]
    bbox = BoundingBox(**loaded["bbox"])
    image_path = resolve_image_path(case["filename"])
    with Image.open(image_path) as img:
        width, height = img.size

    print("=== RND-005B Coordinate Convention Confirmation ===")
    print("Provider: gemini")
    print(f"Model:    {model or '(not set)'}")
    print(f"Test ID:  {TEST_ID}")
    print(f"Target:   {TARGET_NAME}")
    print(f"Image:    {image_path.relative_to(PROJECT_ROOT)}  ({width}x{height})")
    print(f"Goal:     Locate {TARGET_NAME} and declare coordinate convention")
    print(f"Prompt:   {PROMPT_VERSION}")

    if not args.execute:
        print()
        print("DRY RUN — no API call made, nothing sent anywhere. Re-run with --execute after explicit approval.")
        return

    if not api_key:
        raise SystemExit("GEMINI_API_KEY is not set.")
    if not model:
        raise SystemExit("GEMINI_MODEL is not set.")

    timeout_seconds = float(os.environ.get("VISION_REQUEST_TIMEOUT_SECONDS", "30"))
    provider = GeminiProvider(api_key=api_key, model=model, timeout_seconds=timeout_seconds)
    prompt_text = PROMPT_PATH.read_text(encoding="utf-8").format(width=width, height=height, target=TARGET_NAME)

    attempt_count = 0
    last_error: Optional[str] = None
    call_result = None
    while attempt_count < MAX_ATTEMPTS:
        attempt_count += 1
        try:
            call_result = provider.analyze_screen(image_path, f"Locate {TARGET_NAME} and declare coordinate convention", prompt_text)
            last_error = None
            break
        except VisionProviderError as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            print(f"Attempt {attempt_count} FAILED: {last_error}")

    timestamp = datetime.now().isoformat()

    if call_result is None:
        result = {
            "experiment_id": "RND-005B",
            "timestamp": timestamp,
            "test_id": TEST_ID,
            "target": TARGET_NAME,
            "provider": "gemini",
            "model": model,
            "prompt_version": PROMPT_VERSION,
            "image_filename": case["filename"],
            "image_width": width,
            "image_height": height,
            "bbox": loaded["bbox"],
            "attempt_count": attempt_count,
            "request_success": False,
            "schema_valid": False,
            "error": last_error,
        }
        RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        RESULTS_PATH.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"Request FAILED after {attempt_count} attempt(s). See {RESULTS_PATH.relative_to(PROJECT_ROOT)}.")
        return

    RAW_RESPONSES_DIR.mkdir(parents=True, exist_ok=True)
    raw_path = RAW_RESPONSES_DIR / f"{TEST_ID}_{TARGET_NAME}.json"
    raw_path.write_text(
        json.dumps(
            {"provider": "gemini", "model": call_result.model, "raw_text": call_result.raw_text, "parsed_json": call_result.parsed_json},
            indent=2,
        ),
        encoding="utf-8",
    )

    schema_valid = False
    structured: Optional[CoordinateConventionResponse] = None
    schema_error: Optional[str] = None
    if call_result.parsed_json is not None:
        try:
            structured = CoordinateConventionResponse.model_validate(call_result.parsed_json)
            schema_valid = True
        except ValidationError as exc:
            schema_error = str(exc)
    else:
        schema_error = "Response body was not valid JSON."

    estimated_cost, cost_note = estimate_cost("gemini", call_result.model, call_result.input_tokens, call_result.output_tokens)

    result: dict = {
        "experiment_id": "RND-005B",
        "timestamp": timestamp,
        "test_id": TEST_ID,
        "target": TARGET_NAME,
        "provider": "gemini",
        "model": call_result.model,
        "prompt_version": PROMPT_VERSION,
        "image_filename": case["filename"],
        "image_width": width,
        "image_height": height,
        "bbox": loaded["bbox"],
        "attempt_count": attempt_count,
        "request_success": True,
        "schema_valid": schema_valid,
        "error": schema_error if not schema_valid else None,
        "latency_ms": call_result.latency_ms,
        "input_tokens": call_result.input_tokens,
        "output_tokens": call_result.output_tokens,
        "estimated_cost": estimated_cost,
        "cost_note": cost_note,
        "raw_response_reference": str(raw_path.relative_to(PROJECT_ROOT)),
    }

    if structured:
        raw_x, raw_y = structured.x, structured.y
        result["raw_prediction"] = {"x": raw_x, "y": raw_y}
        result["declared_coordinate_system"] = structured.coordinate_system
        result["declared_coordinate_range_x"] = structured.coordinate_range_x
        result["declared_coordinate_range_y"] = structured.coordinate_range_y
        result["confidence"] = structured.confidence
        result["reason"] = structured.reason

        native_in_bounds = coordinate_in_image_bounds(round(raw_x), round(raw_y), width, height)
        native_result = classify_grounding_result(round(raw_x), round(raw_y), bbox)
        result["native_pixel_interpretation"] = {
            "x": raw_x, "y": raw_y, "in_image_bounds": native_in_bounds, "result": native_result,
        }

        norm_x, norm_y = normalize_1000_to_pixels(raw_x, raw_y, width, height)
        norm_result = classify_grounding_result(round(norm_x), round(norm_y), bbox)
        result["normalized_1000_interpretation"] = {
            "converted_x": round(norm_x, 2), "converted_y": round(norm_y, 2), "result": norm_result,
        }

    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(json.dumps(result, indent=2), encoding="utf-8")

    print()
    print(f"Schema valid: {schema_valid}")
    if structured:
        print(f"Raw prediction: ({structured.x}, {structured.y})")
        print(f"Declared coordinate system: {structured.coordinate_system!r}")
        print(f"Native pixel interpretation: {result['native_pixel_interpretation']['result']}")
        print(f"Normalized 0-1000 interpretation: {result['normalized_1000_interpretation']['result']}")
    print(f"Latency: {call_result.latency_ms:.1f} ms")
    print(f"Result written to {RESULTS_PATH.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
