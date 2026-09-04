"""Real-Gemini-API latency benchmark CLI.

    python -m benchmarks.gemini.benchmark_runner

Calls the REAL configured Gemini provider (app.config.settings.
get_provider(), same .env credentials/model every runtime call uses)
against STATIC saved screenshots — real historical captures from
results/raw/phase8_live_result_*.json's referenced screenshots, and
real current captures from today's live demo run. No mouse, no
keyboard, no Outlook launch — see the import-safety self-check below,
identical guarantee to benchmarks/claude/evaluation_runner.py.
"""

from __future__ import annotations

import json
import statistics
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
from app.vision.models import EmailSearchResponse  # noqa: E402
from rnd.models.find_open_email import EmailGroundingResponse  # noqa: E402
from rnd.models.outlook_launch import OutlookLaunchVerificationResponse, WindowsSearchGroundingResponse  # noqa: E402

# Mirrors app.outlook.find_email.TARGET_EMAIL_SUBJECT (a plain string
# constant) WITHOUT importing that module — app.outlook.find_email
# transitively imports app.outlook.launch, which imports pyautogui.
# Duplicating one string here keeps this harness's import graph
# provably free of any physical-action-capable module (see the
# self-check right below).
TARGET_EMAIL_SUBJECT = "Mail for project"

# --- Import-safety self-check (same guarantee as benchmarks/claude) ---
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
    f"Gemini benchmark import graph pulled in physical-action-capable module(s): {_imported_forbidden}. "
    "This harness must remain perception-only — fix the import, do not silence this."
)
# ------------------------------------------------------------------------------

import logging  # noqa: E402

_logger = logging.getLogger("gemini_benchmark")
if not _logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s [BENCH] %(message)s"))
    _logger.addHandler(_handler)
    _logger.setLevel(logging.INFO)
    _logger.propagate = False

SCREENSHOTS_DIR = Path(__file__).resolve().parent / "screenshots"
OUTPUT_DIR = PROJECT_ROOT / "benchmarks" / "results" / "gemini_benchmark"
RND_PROMPTS_DIR = PROJECT_ROOT / "rnd" / "prompts"
APP_PROMPTS_DIR = PROJECT_ROOT / "app" / "vision" / "prompts"

OUTLOOK_SEARCH_PROMPT_PATH = RND_PROMPTS_DIR / "windows_search_grounding_v1.txt"  # current == historical (restored verbatim)
OUTLOOK_READINESS_PROMPT_PATH = RND_PROMPTS_DIR / "outlook_launch_verification_v1.txt"  # unchanged since RND-009B
EMAIL_SEARCH_CURRENT_PROMPT_PATH = APP_PROMPTS_DIR / "email_search_v1.txt"
EMAIL_SEARCH_HISTORICAL_PROMPT_PATH = RND_PROMPTS_DIR / "email_target_grounding_v2.txt"


def _schema_field_count(schema) -> int:
    return len(schema.model_fields)


def _build_case(case_id, stage, screenshot, prompt_path, schema, goal, format_kwargs):
    return {
        "case_id": case_id, "stage": stage, "screenshot": screenshot,
        "prompt_path": prompt_path, "schema": schema, "goal": goal, "format_kwargs": format_kwargs,
    }


def _run_case(provider, model: str, case: dict) -> dict[str, Any]:
    from PIL import Image

    screenshot: Path = case["screenshot"]
    with Image.open(screenshot) as img:
        width, height = img.size
    image_bytes = screenshot.read_bytes()

    prompt_template = case["prompt_path"].read_text(encoding="utf-8")
    prompt_text = prompt_template.format(width=width, height=height, **case["format_kwargs"])

    result: dict[str, Any] = {
        "case_id": case["case_id"], "stage": case["stage"],
        "screenshot": screenshot.name, "screenshot_bytes": len(image_bytes),
        "screenshot_dimensions": f"{width}x{height}",
        "prompt_chars": len(prompt_text), "schema_field_count": _schema_field_count(case["schema"]),
        "provider_error": None, "schema_valid": False, "latency_ms": None,
        "input_tokens": None, "output_tokens": None, "estimated_cost": None,
        "parsed_json": None,
    }

    _logger.info(
        "BENCH_CALL_START case_id=%s prompt_chars=%s image_bytes=%s image_dims=%sx%s",
        case["case_id"], len(prompt_text), len(image_bytes), width, height,
    )

    outcome = call_with_provider_retry(
        lambda: provider.analyze_screen(screenshot, case["goal"], prompt_text),
        stage=f"BENCHMARK_{case['stage']}", provider_name=provider.provider_name,
    )
    if outcome.result is None:
        result["provider_error"] = outcome.error
        _logger.info("BENCH_CALL_END case_id=%s outcome=PROVIDER_ERROR error=%s", case["case_id"], outcome.error)
        return result

    call = outcome.result
    result["latency_ms"] = call.latency_ms
    result["input_tokens"] = call.input_tokens
    result["output_tokens"] = call.output_tokens
    result["estimated_cost"] = estimate_cost(provider.provider_name, call.model, call.input_tokens, call.output_tokens)

    if call.parsed_json is not None:
        try:
            case["schema"].model_validate(call.parsed_json)
            result["schema_valid"] = True
            result["parsed_json"] = call.parsed_json
        except ValidationError as exc:
            result["provider_error"] = f"schema_invalid: {exc}"

    _logger.info(
        "BENCH_CALL_END case_id=%s outcome=OK latency_ms=%.1f schema_valid=%s",
        case["case_id"], call.latency_ms or 0, result["schema_valid"],
    )
    return result


def build_cases() -> list[dict]:
    target_sender = "Yash Dhanraj"
    subject_clause = "(not specified — match on sender alone)"

    return [
        _build_case(
            "outlook_search_current_screenshot", "OUTLOOK_SEARCH",
            SCREENSHOTS_DIR / "outlook_search_current_20260904.png",
            OUTLOOK_SEARCH_PROMPT_PATH, WindowsSearchGroundingResponse,
            "Locate the Outlook search result", {},
        ),
        _build_case(
            "outlook_search_historical_screenshot", "OUTLOOK_SEARCH",
            SCREENSHOTS_DIR / "outlook_search_historical_20260902.png",
            OUTLOOK_SEARCH_PROMPT_PATH, WindowsSearchGroundingResponse,
            "Locate the Outlook search result", {},
        ),
        _build_case(
            "outlook_readiness_historical_screenshot", "OUTLOOK_READINESS",
            SCREENSHOTS_DIR / "outlook_readiness_historical_20260902.png",
            OUTLOOK_READINESS_PROMPT_PATH, OutlookLaunchVerificationResponse,
            "Verify Outlook is ready for interaction", {},
        ),
        _build_case(
            "target_email_search_CURRENT_prompt", "TARGET_EMAIL_SEARCH",
            SCREENSHOTS_DIR / "target_email_search_current_20260904.png",
            EMAIL_SEARCH_CURRENT_PROMPT_PATH, EmailSearchResponse,
            "Search for the target email",
            {"target_sender": target_sender, "target_subject_clause": subject_clause},
        ),
        _build_case(
            "target_email_search_HISTORICAL_prompt", "TARGET_EMAIL_SEARCH",
            SCREENSHOTS_DIR / "target_email_search_current_20260904.png",  # SAME screenshot as above — controlled comparison
            EMAIL_SEARCH_HISTORICAL_PROMPT_PATH, EmailGroundingResponse,
            "Search for the target email",
            {"target_sender": target_sender, "target_subject": TARGET_EMAIL_SUBJECT},
        ),
    ]


def main() -> None:
    provider, model = get_provider()
    _logger.info("provider=%s model=%s", provider.provider_name, model)
    if provider.provider_name != "gemini":
        raise SystemExit(
            f"AI_PROVIDER resolved to {provider.provider_name!r}, not 'gemini'. "
            "Set AI_PROVIDER=gemini in .env before running this benchmark."
        )

    cases = build_cases()
    for case in cases:
        if not case["screenshot"].exists():
            raise SystemExit(f"Missing screenshot: {case['screenshot']}")

    results = [_run_case(provider, model, case) for case in cases]

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = OUTPUT_DIR / f"gemini_benchmark_{timestamp}.json"
    json_path.write_text(json.dumps({"provider": provider.provider_name, "model": model, "results": results}, indent=2, default=str), encoding="utf-8")

    print(f"\nResults written to:\n  {json_path}\n")
    print(f"{'case_id':<40} {'prompt_chars':>12} {'schema_fields':>13} {'image_bytes':>12} {'latency_ms':>11} {'schema_valid':>13}")
    for r in results:
        print(
            f"{r['case_id']:<40} {r['prompt_chars']:>12} {r['schema_field_count']:>13} "
            f"{r['screenshot_bytes']:>12} {str(round(r['latency_ms'], 1)) if r['latency_ms'] else 'ERROR':>11} "
            f"{str(r['schema_valid']):>13}"
        )


if __name__ == "__main__":
    main()
