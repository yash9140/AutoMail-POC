"""Static, Claude-only, non-physical REPLY_SEARCH structured-output
reliability benchmark (2026-09-06).

Background: a live run got the correct target email opened and fully
read (Yash Dhanraj / "Quick Question Regarding Tomorrow"), then the
REPLY_SEARCH Vision call failed: Claude's raw response was not parseable
JSON (VISION_SCHEMA_VALIDATION_FAILED, json_parse_failed=True,
response_char_count=1525), triggering a Gemini fallback that then timed
out (~30.6s), ending the run at TECHNICAL_PROVIDER_ERROR. No Reply
physical action ever occurred.

This script does NOT re-run that live flow. It isolates the ONE
suspect Vision call (REPLY_SEARCH) against the EXACT screenshot from
that failing moment, calling AnthropicProvider.analyze_screen()
directly — Claude only, no Gemini fallback, no Outlook launch, no
PyAutoGUI, no physical action of any kind — 5 times, to see whether the
JSON-parse failure reproduces, and if so, whether app/vision/providers/
anthropic_provider.py's new diagnostics (2026-09-06 — see
ANTHROPIC_RESPONSE_METADATA / ANTHROPIC_JSON_STRUCTURE_DIAGNOSTICS)
show MAX_TOKENS truncation, a markdown-fence/prose-wrapping issue, or
something else. Per that task's own instruction, this script does NOT
raise MAX_TOKENS and does NOT change parser/fence-stripping behavior —
it only observes and records.

Screenshot identification (documented, not asserted with false
certainty): the live run's own log excerpt did not include a capture
filename. screenshots/raw/screen_20260906_151542_414.png (and its two
byte-identical siblings captured at 15:15:47 and 15:16:03 the same
day) was identified by visual inspection as showing exactly the
post-read state the live run described — Yash Dhanraj's "Quick Question
Regarding Tomorrow" open, full body visible, with the Reply/Reply
All/Forward icons and ribbon Reply button visible — the same visual
state prepare_reply_editor() would have captured for its
_check_reply_editor_open() + REPLY_SEARCH attempt 1 (both reuse that
one capture; see app/outlook/reply.py). This is the best available
match, not a log-confirmed one.

Never stores/prints raw email body text or the full raw Claude
response — only the structural/metadata facts also written to
app.vision.providers.anthropic logs.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv  # noqa: E402

from app.vision.providers.anthropic_provider import (  # noqa: E402
    AnthropicProvider,
    _json_structure_diagnostics,
)

SOURCE_SCREENSHOT = PROJECT_ROOT / "screenshots" / "raw" / "screen_20260906_151542_414.png"
PROMPT_PATH = PROJECT_ROOT / "app" / "vision" / "prompts" / "reply_search_v1.txt"
RESULTS_DIR = Path(__file__).resolve().parent / "reply_search_results"
TRIALS = 5
STAGE = "REPLY_SEARCH"


def run_trial(provider: AnthropicProvider, image_path: Path, prompt_text: str, trial: int) -> dict:
    start = time.perf_counter()
    result = provider.analyze_screen(image_path, "Locate the Reply control", prompt_text, stage=STAGE)
    latency_ms = (time.perf_counter() - start) * 1000

    schema_valid = result.parsed_json is not None
    diagnostics = _json_structure_diagnostics(result.raw_text) if not schema_valid else None

    row = {
        "trial": trial,
        "schema_valid": schema_valid,
        "json_parse_failed": not schema_valid,
        "stop_reason": result.stop_reason,
        "output_tokens": result.output_tokens,
        "response_char_count": len(result.raw_text),
        "contains_markdown_fence": diagnostics["contains_markdown_fence"] if diagnostics else None,
        "brace_balance": diagnostics["brace_balance"] if diagnostics else None,
        "json_starts_with_object": diagnostics["json_starts_with_object"] if diagnostics else None,
        "json_ends_with_object": diagnostics["json_ends_with_object"] if diagnostics else None,
        "leading_non_whitespace_char_type": diagnostics["leading_non_whitespace_char_type"] if diagnostics else None,
        "trailing_non_whitespace_after_object": diagnostics["trailing_non_whitespace_after_object"] if diagnostics else None,
        "latency_ms": round(latency_ms, 1),
    }
    print(
        f"trial={trial} schema_valid={schema_valid} stop_reason={result.stop_reason} "
        f"output_tokens={result.output_tokens} response_char_count={row['response_char_count']} "
        f"latency_ms={row['latency_ms']}"
    )
    return row


def main() -> None:
    if not SOURCE_SCREENSHOT.exists():
        print(f"Source screenshot not found: {SOURCE_SCREENSHOT}")
        print("This benchmark cannot run without it — skipping (per task instructions, "
              "this static test only runs 'if the exact screenshot is available locally').")
        return

    load_dotenv(PROJECT_ROOT / ".env")
    import os

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    model = os.environ.get("ANTHROPIC_MODEL", "")
    if not api_key or not model:
        print("ANTHROPIC_API_KEY / ANTHROPIC_MODEL not set in .env — cannot run live Claude calls.")
        return

    provider = AnthropicProvider(api_key=api_key, model=model, timeout_seconds=60.0)

    from PIL import Image

    with Image.open(SOURCE_SCREENSHOT) as img:
        width, height = img.size
    prompt_text = PROMPT_PATH.read_text(encoding="utf-8").format(width=width, height=height)

    print(f"Source screenshot: {SOURCE_SCREENSHOT} ({width}x{height})")
    print(f"Running {TRIALS} Claude-only REPLY_SEARCH trials (no Gemini fallback, no physical action)...")

    rows = [run_trial(provider, SOURCE_SCREENSHOT, prompt_text, trial) for trial in range(1, TRIALS + 1)]

    schema_valid_count = sum(1 for r in rows if r["schema_valid"])
    print(f"\n=== SUMMARY: schema_valid {schema_valid_count}/{TRIALS} ===")
    for r in rows:
        print(
            f"trial={r['trial']:>2} schema_valid={r['schema_valid']!s:<5} "
            f"json_parse_failed={r['json_parse_failed']!s:<5} stop_reason={r['stop_reason']!s:<10} "
            f"output_tokens={r['output_tokens']!s:<6} response_char_count={r['response_char_count']!s:<6} "
            f"contains_markdown_fence={r['contains_markdown_fence']!s:<6} brace_balance={r['brace_balance']!s:<4} "
            f"latency_ms={r['latency_ms']}"
        )

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (RESULTS_DIR / "results.json").write_text(
        json.dumps({"source_screenshot": str(SOURCE_SCREENSHOT), "trials": rows}, indent=2), encoding="utf-8",
    )
    print(f"\nWritten to {RESULTS_DIR / 'results.json'}")


if __name__ == "__main__":
    main()
