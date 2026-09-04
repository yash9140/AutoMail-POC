"""RND-003 — first Vision provider integration experiment runner.

Loads exactly one raw Outlook screenshot (OUTLOOK-003 by default) from
screenshots/raw/ (never screenshots/annotated/ — that would leak ground
truth), builds the screen_understanding_v1 prompt, and either:

  - dry-run (default): validates configuration and prints exactly what
    WOULD be sent (provider, model, test case, image, goal) without
    calling the API or spending anything. Use this to get explicit
    human sign-off before sending a real screenshot to a third party.

  - --execute: performs the real API call, validates the response
    against the expected schema, records full technical metadata
    (latency, tokens, cost if verified pricing is configured), and
    writes results/raw/rnd003_first_provider_result.json.

Run (from outlook-vision-poc/):
    python rnd/experiments/rnd003_first_vision_call.py --test-id OUTLOOK-003
    python rnd/experiments/rnd003_first_vision_call.py --test-id OUTLOOK-003 --execute
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

from rnd.models.vision_result import (  # noqa: E402
    StructuredVisionResponse,
    VisionResult,
    validate_coordinates_in_bounds,
)
from rnd.providers.base import VisionProviderError  # noqa: E402
from rnd.providers.gemini_provider import GeminiProvider  # noqa: E402

MANIFEST_PATH = PROJECT_ROOT / "test_cases" / "outlook" / "dataset_manifest.json"
RAW_DIR = PROJECT_ROOT / "screenshots" / "raw"
ANNOTATED_DIR = PROJECT_ROOT / "screenshots" / "annotated"
PROMPT_PATH = PROJECT_ROOT / "rnd" / "prompts" / "screen_understanding_v1.txt"
RESULT_PATH = PROJECT_ROOT / "results" / "raw" / "rnd003_first_provider_result.json"
RAW_RESPONSES_DIR = PROJECT_ROOT / "results" / "raw" / "provider_responses"
PRICING_PATH = PROJECT_ROOT / "config" / "model_pricing.json"

PROMPT_VERSION = "screen_understanding_v1"
MAX_ATTEMPTS = 2  # total attempts including the first try — small and measurable per RND-003 spec


def load_case(test_id: str) -> dict:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    case = next((c for c in manifest["cases"] if c["test_id"] == test_id), None)
    if case is None:
        raise SystemExit(f"test_id '{test_id}' not found in manifest")
    if case["status"] != "captured":
        raise SystemExit(f"test_id '{test_id}' is not captured (status={case['status']})")
    return case


def resolve_image_path(filename: str) -> Path:
    """Enforce: only screenshots/raw/, never screenshots/annotated/."""
    path = (RAW_DIR / filename).resolve()
    if RAW_DIR.resolve() not in path.parents:
        raise SystemExit(f"Refusing to use an image outside screenshots/raw/: {path}")
    if ANNOTATED_DIR.resolve() in path.parents:
        raise SystemExit(
            "Refusing to send an annotated screenshot to a Vision provider — "
            "it visually contains the ground-truth answer."
        )
    if not path.exists():
        raise SystemExit(f"Image not found: {path}")
    return path


def build_prompt(goal: str, width: int, height: int) -> str:
    template = PROMPT_PATH.read_text(encoding="utf-8")
    return template.format(goal=goal, width=width, height=height)


def estimate_cost(
    provider: str, model: str, input_tokens: Optional[int], output_tokens: Optional[int]
) -> tuple[Optional[float], str]:
    if not PRICING_PATH.exists():
        return None, "Cost unavailable pending verified pricing configuration (config/model_pricing.json not found)."
    pricing = json.loads(PRICING_PATH.read_text(encoding="utf-8"))
    entry = next(
        (p for p in pricing.get("models", []) if p["provider"] == provider and p["model"] == model), None
    )
    if entry is None:
        return None, f"Cost unavailable: no verified pricing entry for {provider}/{model} in config/model_pricing.json."
    if input_tokens is None or output_tokens is None:
        return None, "Cost unavailable: provider did not return token usage."
    input_cost = (input_tokens / 1_000_000) * entry["input_rate_per_million_tokens"]
    output_cost = (output_tokens / 1_000_000) * entry["output_rate_per_million_tokens"]
    return (
        round(input_cost + output_cost, 6),
        f"Calculated from verified pricing dated {entry['pricing_date']} ({entry['source']}).",
    )


def _save_result(result: VisionResult) -> None:
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULT_PATH.write_text(result.model_dump_json(indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="RND-003 first Vision provider integration")
    parser.add_argument("--test-id", default="OUTLOOK-003")
    parser.add_argument("--goal", default="Reply to the currently opened email.")
    parser.add_argument("--execute", action="store_true", help="Actually call the Vision provider. Omit for a dry run.")
    args = parser.parse_args()

    load_dotenv(PROJECT_ROOT / ".env")

    case = load_case(args.test_id)
    image_path = resolve_image_path(case["filename"])
    with Image.open(image_path) as img:
        width, height = img.size
    if (width, height) != (case["screen"]["width"], case["screen"]["height"]):
        raise SystemExit("Image dimensions do not match the manifest record — refusing to proceed.")

    prompt_text = build_prompt(args.goal, width, height)

    gemini_api_key = os.environ.get("GEMINI_API_KEY", "")
    gemini_model = os.environ.get("GEMINI_MODEL", "")

    print("=== RND-003 First Vision Provider Integration ===")
    print("Provider: gemini")
    print(f"Model:    {gemini_model or '(not set)'}")
    print(f"Test ID:  {args.test_id}")
    print(f"Image:    {image_path.relative_to(PROJECT_ROOT)}  ({width}x{height})")
    print(f"Goal:     {args.goal}")
    print(f"Prompt:   {PROMPT_VERSION}")
    print(f"Timeout:  {os.environ.get('VISION_REQUEST_TIMEOUT_SECONDS', '30 (default)')}s")
    print()

    if not args.execute:
        print("DRY RUN — no API call made, nothing sent anywhere. Re-run with --execute after explicit approval.")
        if not gemini_api_key:
            print("NOTE: GEMINI_API_KEY is not set in .env — required before --execute will work.")
        if not gemini_model:
            print("NOTE: GEMINI_MODEL is not set in .env — required before --execute will work.")
        return

    if not gemini_api_key:
        raise SystemExit("GEMINI_API_KEY is not set. Configure it in .env before running with --execute.")
    if not gemini_model:
        raise SystemExit("GEMINI_MODEL is not set. Configure it in .env before running with --execute.")

    timeout_seconds = float(os.environ.get("VISION_REQUEST_TIMEOUT_SECONDS", "30"))
    provider = GeminiProvider(api_key=gemini_api_key, model=gemini_model, timeout_seconds=timeout_seconds)

    attempt_count = 0
    last_error: Optional[str] = None
    call_result = None
    while attempt_count < MAX_ATTEMPTS:
        attempt_count += 1
        try:
            call_result = provider.analyze_screen(image_path, args.goal, prompt_text)
            last_error = None
            break
        except VisionProviderError as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            print(f"Attempt {attempt_count} FAILED: {last_error}")

    timestamp = datetime.now().isoformat()

    if call_result is None:
        result = VisionResult(
            experiment_id="RND-003",
            test_id=args.test_id,
            timestamp=timestamp,
            provider="gemini",
            model=gemini_model,
            prompt_version=PROMPT_VERSION,
            image_filename=case["filename"],
            image_width=width,
            image_height=height,
            goal=args.goal,
            schema_valid=False,
            request_success=False,
            attempt_count=attempt_count,
            error=last_error,
        )
        _save_result(result)
        print(f"Request FAILED after {attempt_count} attempt(s). See {RESULT_PATH.relative_to(PROJECT_ROOT)}.")
        return

    RAW_RESPONSES_DIR.mkdir(parents=True, exist_ok=True)
    raw_response_filename = f"rnd003_{args.test_id}_{timestamp.replace(':', '-')}.json"
    raw_response_path = RAW_RESPONSES_DIR / raw_response_filename
    raw_response_path.write_text(
        json.dumps(
            {
                "provider": "gemini",
                "model": call_result.model,
                "raw_text": call_result.raw_text,
                "parsed_json": call_result.parsed_json,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    schema_valid = False
    structured: Optional[StructuredVisionResponse] = None
    schema_error: Optional[str] = None
    if call_result.parsed_json is not None:
        try:
            structured = StructuredVisionResponse.model_validate(call_result.parsed_json)
            schema_valid = True
        except ValidationError as exc:
            schema_error = str(exc)
    else:
        schema_error = "Response body was not valid JSON."

    coords_in_bounds: Optional[bool] = None
    if structured is not None:
        coords_in_bounds = validate_coordinates_in_bounds(
            structured.recommended_action.x, structured.recommended_action.y, width, height
        )

    estimated_cost, cost_note = estimate_cost(
        "gemini", call_result.model, call_result.input_tokens, call_result.output_tokens
    )

    result = VisionResult(
        experiment_id="RND-003",
        test_id=args.test_id,
        timestamp=timestamp,
        provider="gemini",
        model=call_result.model,
        prompt_version=PROMPT_VERSION,
        image_filename=case["filename"],
        image_width=width,
        image_height=height,
        goal=args.goal,
        application=structured.application if structured else None,
        screen_state=structured.screen_state if structured else None,
        screen_description=structured.screen_description if structured else None,
        visible_controls=structured.visible_controls if structured else None,
        action=structured.recommended_action.action if structured else None,
        target=structured.recommended_action.target if structured else None,
        x=structured.recommended_action.x if structured else None,
        y=structured.recommended_action.y if structured else None,
        confidence=structured.confidence if structured else None,
        reason=structured.reason if structured else None,
        latency_ms=call_result.latency_ms,
        input_tokens=call_result.input_tokens,
        output_tokens=call_result.output_tokens,
        estimated_cost=estimated_cost,
        cost_note=cost_note,
        schema_valid=schema_valid,
        coordinates_in_bounds=coords_in_bounds,
        request_success=True,
        attempt_count=attempt_count,
        raw_response_reference=str(raw_response_path.relative_to(PROJECT_ROOT)),
        error=schema_error if not schema_valid else None,
    )
    _save_result(result)

    print(f"Request succeeded on attempt {attempt_count}/{MAX_ATTEMPTS}.")
    print(f"Latency: {call_result.latency_ms:.1f} ms")
    print(f"Schema valid: {schema_valid}")
    if not schema_valid:
        print(f"Schema error: {schema_error}")
    print(f"Result written to {RESULT_PATH.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
