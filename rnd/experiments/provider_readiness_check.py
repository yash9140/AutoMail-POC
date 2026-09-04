"""Multi-provider readiness check (pre-RND-004).

Runs the exact same OUTLOOK-003 raw screenshot and goal through whichever
provider is selected (openai or anthropic), using a coordinate-free prompt
(provider_readiness_v1) — this stage only proves the provider is
technically ready (valid key/model, structured response, usage/cost
capture), not grounding accuracy (that's RND-005) or full-dataset
screen-understanding accuracy (that's RND-004).

Same dry-run / --execute split as rnd003_first_vision_call.py: the dry
run never touches the network; --execute performs one real call only
after explicit human approval has already been obtained in conversation.

Run (from outlook-vision-poc/):
    python rnd/experiments/provider_readiness_check.py --provider openai
    python rnd/experiments/provider_readiness_check.py --provider openai --execute
    python rnd/experiments/provider_readiness_check.py --provider anthropic --execute
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

from rnd.models.provider_readiness import ProviderReadinessResult, ReadinessVisionResponse  # noqa: E402
from rnd.providers.base import VisionProviderError  # noqa: E402

MANIFEST_PATH = PROJECT_ROOT / "test_cases" / "outlook" / "dataset_manifest.json"
RAW_DIR = PROJECT_ROOT / "screenshots" / "raw"
ANNOTATED_DIR = PROJECT_ROOT / "screenshots" / "annotated"
PROMPT_PATH = PROJECT_ROOT / "rnd" / "prompts" / "provider_readiness_v1.txt"
RESULTS_DIR = PROJECT_ROOT / "results" / "raw"
PRICING_PATH = PROJECT_ROOT / "config" / "model_pricing.json"

PROMPT_VERSION = "provider_readiness_v1"
MAX_ATTEMPTS = 2

PROVIDER_ENV = {
    "openai": {"key": "OPENAI_API_KEY", "model": "OPENAI_MODEL"},
    "anthropic": {"key": "ANTHROPIC_API_KEY", "model": "ANTHROPIC_MODEL"},
}


def load_case(test_id: str) -> dict:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    case = next((c for c in manifest["cases"] if c["test_id"] == test_id), None)
    if case is None:
        raise SystemExit(f"test_id '{test_id}' not found in manifest")
    if case["status"] != "captured":
        raise SystemExit(f"test_id '{test_id}' is not captured (status={case['status']})")
    return case


def resolve_image_path(filename: str) -> Path:
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


def build_provider(provider_name: str, api_key: str, model: str, timeout_seconds: float):
    if provider_name == "openai":
        from rnd.providers.openai_provider import OpenAIProvider

        return OpenAIProvider(api_key=api_key, model=model, timeout_seconds=timeout_seconds)
    if provider_name == "anthropic":
        from rnd.providers.anthropic_provider import AnthropicProvider

        return AnthropicProvider(api_key=api_key, model=model, timeout_seconds=timeout_seconds)
    raise SystemExit(f"Unknown provider '{provider_name}' — expected 'openai' or 'anthropic'")


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
        return None, f"Cost unavailable pending verified pricing configuration for {provider}/{model}."
    if input_tokens is None or output_tokens is None:
        return None, "Cost unavailable: provider did not return token usage."
    input_cost = (input_tokens / 1_000_000) * entry["input_rate_per_million_tokens"]
    output_cost = (output_tokens / 1_000_000) * entry["output_rate_per_million_tokens"]
    return (
        round(input_cost + output_cost, 6),
        f"Calculated from verified pricing dated {entry['pricing_date']} ({entry['source']}).",
    )


def _save_result(result: ProviderReadinessResult, provider_name: str) -> Path:
    """Writes the "latest" snapshot (for easy reading) AND appends to a
    history file (so an earlier run's result — e.g. a billing failure —
    is never silently erased by a later run's overwrite).
    """
    out_path = RESULTS_DIR / f"rnd_provider_readiness_{provider_name}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")

    history_path = RESULTS_DIR / f"rnd_provider_readiness_{provider_name}_history.json"
    history = json.loads(history_path.read_text(encoding="utf-8")) if history_path.exists() else {"runs": []}
    history["runs"].append(json.loads(result.model_dump_json()))
    history_path.write_text(json.dumps(history, indent=2), encoding="utf-8")

    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Multi-provider readiness check (pre-RND-004)")
    parser.add_argument("--provider", required=True, choices=["openai", "anthropic"])
    parser.add_argument("--test-id", default="OUTLOOK-003")
    parser.add_argument("--goal", default="Reply to the currently opened email.")
    parser.add_argument("--execute", action="store_true", help="Actually call the provider. Omit for a dry run.")
    args = parser.parse_args()

    load_dotenv(PROJECT_ROOT / ".env")

    env_vars = PROVIDER_ENV[args.provider]
    api_key = os.environ.get(env_vars["key"], "")
    model = os.environ.get(env_vars["model"], "")

    case = load_case(args.test_id)
    image_path = resolve_image_path(case["filename"])
    with Image.open(image_path) as img:
        width, height = img.size
    if (width, height) != (case["screen"]["width"], case["screen"]["height"]):
        raise SystemExit("Image dimensions do not match the manifest record — refusing to proceed.")

    prompt_template = PROMPT_PATH.read_text(encoding="utf-8")
    prompt_text = prompt_template.format(goal=args.goal)

    print(f"=== Provider Readiness Check — {args.provider} ===")
    print(f"Provider: {args.provider}")
    print(f"Model:    {model or '(not set)'}")
    print(f"Test ID:  {args.test_id}")
    print(f"Image:    {image_path.relative_to(PROJECT_ROOT)}  ({width}x{height})")
    print(f"Goal:     {args.goal}")
    print(f"Prompt:   {PROMPT_VERSION}")

    if not model:
        print()
        print(f"STOP: {env_vars['model']} is empty in .env. Configuration missing — cannot proceed.")
        return

    timeout_seconds = float(os.environ.get("VISION_REQUEST_TIMEOUT_SECONDS", "30"))
    print(f"Timeout:  {timeout_seconds}s")
    print()

    if not args.execute:
        print("DRY RUN — no API call made, nothing sent anywhere. Re-run with --execute after explicit approval.")
        if not api_key:
            print(f"NOTE: {env_vars['key']} is not set in .env — required before --execute will work.")
        return

    if not api_key:
        raise SystemExit(f"{env_vars['key']} is not set. Configure it in .env before running with --execute.")

    provider = build_provider(args.provider, api_key, model, timeout_seconds)

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
        result = ProviderReadinessResult(
            test_id=args.test_id,
            timestamp=timestamp,
            provider=args.provider,
            model=model,
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
        out_path = _save_result(result, args.provider)
        print(f"Request FAILED after {attempt_count} attempt(s). See {out_path.relative_to(PROJECT_ROOT)}.")
        return

    raw_dir = RESULTS_DIR / "provider_responses" / "readiness" / args.provider
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_response_path = raw_dir / f"{args.test_id}_{timestamp.replace(':', '-')}.json"
    raw_response_path.write_text(
        json.dumps(
            {
                "provider": args.provider,
                "model": call_result.model,
                "raw_text": call_result.raw_text,
                "parsed_json": call_result.parsed_json,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    schema_valid = False
    structured: Optional[ReadinessVisionResponse] = None
    schema_error: Optional[str] = None
    if call_result.parsed_json is not None:
        try:
            structured = ReadinessVisionResponse.model_validate(call_result.parsed_json)
            schema_valid = True
        except ValidationError as exc:
            schema_error = str(exc)
    else:
        schema_error = "Response body was not valid JSON."

    estimated_cost, cost_note = estimate_cost(
        args.provider, call_result.model, call_result.input_tokens, call_result.output_tokens
    )

    result = ProviderReadinessResult(
        test_id=args.test_id,
        timestamp=timestamp,
        provider=args.provider,
        model=call_result.model,
        prompt_version=PROMPT_VERSION,
        image_filename=case["filename"],
        image_width=width,
        image_height=height,
        goal=args.goal,
        application=structured.application if structured else None,
        screen_state=structured.screen_state if structured else None,
        screen_description=structured.screen_description if structured else None,
        relevant_visible_controls=structured.relevant_visible_controls if structured else None,
        recommended_action=structured.recommended_action if structured else None,
        target=structured.target if structured else None,
        confidence=structured.confidence if structured else None,
        reason=structured.reason if structured else None,
        latency_ms=call_result.latency_ms,
        input_tokens=call_result.input_tokens,
        output_tokens=call_result.output_tokens,
        estimated_cost=estimated_cost,
        cost_note=cost_note,
        schema_valid=schema_valid,
        request_success=True,
        attempt_count=attempt_count,
        raw_response_reference=str(raw_response_path.relative_to(PROJECT_ROOT)),
        error=schema_error if not schema_valid else None,
    )
    out_path = _save_result(result, args.provider)

    print(f"Request succeeded on attempt {attempt_count}/{MAX_ATTEMPTS}.")
    print(f"Latency: {call_result.latency_ms:.1f} ms")
    print(f"Schema valid: {schema_valid}")
    if not schema_valid:
        print(f"Schema error: {schema_error}")
    print(f"Result written to {out_path.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
