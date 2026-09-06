"""Static, Claude-only visual-resolution / row-reading experiment
(2026-09-06).

Live evidence showed Claude's TARGET_EMAIL_SEARCH, TARGET_EMAIL_ROW_
IDENTITY_REFINE, and TARGET_EMAIL_ROW_BBOX_REFINE calls all agreeing
with EACH OTHER on a bbox — yet a freshness-guard check proved the
message-list pixels were unchanged between grounding and the physical
click (difference_score=0.00), and the row physically at that bbox was
"Demo of live run", not the claimed "Quick Question Regarding...".
This rules out screenshot staleness — the remaining hypothesis is that
Claude itself misreads which row shows which subject at full-screen
resolution, when several rows share a sender.

This script tests that hypothesis directly and ONLY: it re-runs a
diagnostic, anti-bias, enumerate-every-row prompt against THREE static
crops of the EXACT failing screenshot (never a newly captured one),
5 trials each, Claude only (no Gemini fallback, no retries beyond what
a single provider call already does internally at the SDK level).

NO physical actions. NO Outlook launch. NO PyAutoGUI. Nothing in this
script touches app/outlook/*.py or any runtime automation path — it
calls AnthropicProvider.analyze_screen() directly, exactly like a real
call site would, but against static, already-saved images.

Run with:
    .venv/Scripts/python -m benchmarks.claude.experiments.run_row_confusion_experiment
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402
from pydantic import BaseModel, Field, ValidationError  # noqa: E402

from app.vision.providers.anthropic_provider import AnthropicProvider  # noqa: E402

load_dotenv(PROJECT_ROOT / ".env")

# --- Frozen evidence: the EXACT screenshot that produced the live
# grounding failure under investigation. Captured immediately before
# the TARGET_EMAIL_SEARCH Vision call at 2026-09-06 14:35:33.365 (the
# filename's own embedded timestamp — 14:35:33.116 — and the call-start
# timestamp are ~249ms apart, consistent with capture-then-immediately-
# call sequencing; no other capture in screenshots/raw/ is closer to
# that call). Never modified, never re-captured.
SOURCE_SCREENSHOT = PROJECT_ROOT / "screenshots" / "raw" / "screen_20260906_143533_116.png"

RESULTS_DIR = Path(__file__).resolve().parent / "row_confusion_results"
PROMPT_PATH = Path(__file__).resolve().parent / "row_confusion_prompt.txt"

# Existing, already-approved normalized layout constants — never
# invented pixel coordinates.
LEFT_SIDEBAR_MAX_X_FRACTION = 0.17
MESSAGE_LIST_RIGHT_MAX_X_FRACTION = 0.55

TRIALS_PER_VARIANT = 5
TARGET_SUBJECT_PREFIX = "quick question regarding"  # normalized (casefolded) comparison


class RowObservation(BaseModel):
    row_index: int = 0
    sender: str = ""
    visible_subject: str = ""
    bbox: Optional[list[float]] = None
    confidence: float = 0.0


class RowConfusionDiagnosticResponse(BaseModel):
    rows: list[RowObservation] = Field(default_factory=list)
    target_subject_prefix: str = ""
    matching_row_index: Optional[int] = None
    reason: str = ""


def _normalize(s: str) -> str:
    return " ".join((s or "").strip().split()).casefold()


def _crop_box_for_variant(variant: str, width: int, height: int) -> Optional[tuple[int, int, int, int]]:
    if variant == "FULL_SCREEN":
        return None
    if variant == "MESSAGE_LIST_CROP":
        left = round(LEFT_SIDEBAR_MAX_X_FRACTION * width)
        right = round(MESSAGE_LIST_RIGHT_MAX_X_FRACTION * width)
        return (left, 0, right, height)
    if variant == "TARGET_NEIGHBORHOOD_CROP":
        # Evaluation-only crop bounds, chosen by visually inspecting THIS
        # one frozen screenshot to bracket the visible Yash Dhanraj rows
        # plus one neighbor above/below — never used as runtime truth,
        # never fed back into app/outlook/*.py.
        left = round(LEFT_SIDEBAR_MAX_X_FRACTION * width)
        return (left, 280, 760, 700)
    raise ValueError(variant)


def _remap_bbox_to_full_screen(
    bbox_normalized: Optional[list[float]], crop_box: Optional[tuple[int, int, int, int]],
    full_width: int, full_height: int,
) -> Optional[list[float]]:
    """Deterministic Python remapping — Claude is never asked to do this
    itself. bbox_normalized is [y_min, x_min, y_max, x_max] in 0-1000
    space relative to whichever image (crop or full) was actually sent."""
    if bbox_normalized is None or len(bbox_normalized) != 4:
        return None
    if crop_box is None:
        return bbox_normalized
    crop_left, crop_top, crop_right, crop_bottom = crop_box
    crop_width = crop_right - crop_left
    crop_height = crop_bottom - crop_top
    y_min, x_min, y_max, x_max = bbox_normalized

    def _remap_x(v: float) -> float:
        px = v / 1000 * crop_width + crop_left
        return px / full_width * 1000

    def _remap_y(v: float) -> float:
        px = v / 1000 * crop_height + crop_top
        return px / full_height * 1000

    return [_remap_y(y_min), _remap_x(x_min), _remap_y(y_max), _remap_x(x_max)]


def _save_debug_overlay(image_path: Path, response: Optional[RowConfusionDiagnosticResponse], out_path: Path) -> None:
    try:
        with Image.open(image_path) as img:
            img = img.convert("RGB")
            draw = ImageDraw.Draw(img)
            if response is not None:
                for row in response.rows:
                    if row.bbox and len(row.bbox) == 4:
                        y_min, x_min, y_max, x_max = row.bbox
                        px_min, py_min = x_min / 1000 * img.width, y_min / 1000 * img.height
                        px_max, py_max = x_max / 1000 * img.width, y_max / 1000 * img.height
                        is_match = response.matching_row_index == row.row_index
                        color = "lime" if is_match else "red"
                        draw.rectangle([px_min, py_min, px_max, py_max], outline=color, width=3)
                        label = f"#{row.row_index}: {row.visible_subject[:24]!r}"
                        draw.text((px_min + 2, max(0, py_min - 14)), label, fill=color)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            img.save(out_path, "PNG")
    except Exception as exc:  # noqa: BLE001 — diagnostic only
        print(f"    (debug overlay failed: {exc})")


def run_trial(provider: AnthropicProvider, image_path: Path, prompt_text: str, width: int, height: int) -> dict:
    start = time.perf_counter()
    error: Optional[str] = None
    parsed: Optional[RowConfusionDiagnosticResponse] = None
    call = None
    try:
        call = provider.analyze_screen(image_path, "Diagnostic row-reading experiment", prompt_text)
    except Exception as exc:  # noqa: BLE001 — record and continue, never crash the whole experiment
        error = f"{type(exc).__name__}: {exc}"
    latency_ms = round((time.perf_counter() - start) * 1000, 1)

    response_char_count = len(call.raw_text) if call is not None and call.raw_text else 0
    schema_valid = False
    if call is not None and call.parsed_json:
        try:
            parsed = RowConfusionDiagnosticResponse.model_validate(call.parsed_json)
            schema_valid = True
        except ValidationError as exc:
            error = f"ValidationError: {exc}"

    yash_rows = [r for r in (parsed.rows if parsed else []) if "yash dhanraj" in _normalize(r.sender)]
    subjects_in_order = [r.visible_subject for r in (parsed.rows if parsed else [])]
    matching_row = None
    target_row_correct = False
    if parsed is not None and parsed.matching_row_index is not None:
        matching_row = next((r for r in parsed.rows if r.row_index == parsed.matching_row_index), None)
        if matching_row is not None:
            target_row_correct = _normalize(matching_row.visible_subject).startswith(TARGET_SUBJECT_PREFIX)

    return {
        "schema_valid": schema_valid,
        "error": error,
        "num_yash_rows": len(yash_rows),
        "subjects_in_order": subjects_in_order,
        "matching_row_index": parsed.matching_row_index if parsed else None,
        "target_row_correct": target_row_correct,
        "matching_row_bbox": matching_row.bbox if matching_row else None,
        "matching_row_confidence": matching_row.confidence if matching_row else None,
        "latency_ms": latency_ms,
        "response_char_count": response_char_count,
        "parsed": parsed,
    }


def main() -> None:
    model = os.environ.get("ANTHROPIC_MODEL", "")
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key or not model:
        raise SystemExit("ANTHROPIC_API_KEY / ANTHROPIC_MODEL must be set in .env to run this experiment.")
    provider = AnthropicProvider(api_key=api_key, model=model, timeout_seconds=60.0)

    if not SOURCE_SCREENSHOT.exists():
        raise SystemExit(f"Source screenshot not found: {SOURCE_SCREENSHOT}")

    with Image.open(SOURCE_SCREENSHOT) as src:
        full_width, full_height = src.size

        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        variant_image_paths: dict[str, Path] = {}
        variant_crop_boxes: dict[str, Optional[tuple[int, int, int, int]]] = {}
        for variant in ("FULL_SCREEN", "MESSAGE_LIST_CROP", "TARGET_NEIGHBORHOOD_CROP"):
            crop_box = _crop_box_for_variant(variant, full_width, full_height)
            variant_crop_boxes[variant] = crop_box
            out_path = RESULTS_DIR / f"{variant}.png"
            if crop_box is None:
                src.convert("RGB").save(out_path, "PNG")
            else:
                src.crop(crop_box).convert("RGB").save(out_path, "PNG")
            variant_image_paths[variant] = out_path

    prompt_template = PROMPT_PATH.read_text(encoding="utf-8")

    all_results: dict[str, list[dict]] = {}
    for variant, image_path in variant_image_paths.items():
        with Image.open(image_path) as im:
            variant_width, variant_height = im.size
        prompt_text = prompt_template.format(width=variant_width, height=variant_height)

        print(f"\n=== {variant} ({variant_width}x{variant_height}) ===")
        trials = []
        for trial_num in range(1, TRIALS_PER_VARIANT + 1):
            result = run_trial(provider, image_path, prompt_text, variant_width, variant_height)
            crop_box = variant_crop_boxes[variant]
            full_screen_bbox = _remap_bbox_to_full_screen(
                result["matching_row_bbox"], crop_box, full_width, full_height,
            )
            result["full_screen_bbox"] = full_screen_bbox
            trials.append(result)

            print(
                f"  trial {trial_num}: schema_valid={result['schema_valid']} "
                f"yash_rows={result['num_yash_rows']} matching_row={result['matching_row_index']} "
                f"target_row_correct={result['target_row_correct']} latency_ms={result['latency_ms']} "
                f"chars={result['response_char_count']} error={result['error']}"
            )
            if result["parsed"] is not None:
                overlay_path = RESULTS_DIR / f"{variant}_trial{trial_num}_overlay.png"
                _save_debug_overlay(image_path, result["parsed"], overlay_path)

        all_results[variant] = trials

    # --- Summary ---
    print("\n\n=== SUMMARY ===")
    summary = {}
    for variant, trials in all_results.items():
        correct = sum(1 for t in trials if t["target_row_correct"])
        avg_latency = round(sum(t["latency_ms"] for t in trials) / len(trials), 1)
        matching_rows = [t["matching_row_index"] for t in trials]
        stable = len(set(matching_rows)) <= 1
        summary[variant] = {
            "correct_target_row": f"{correct}/{len(trials)}",
            "avg_latency_ms": avg_latency,
            "matching_row_indices_across_trials": matching_rows,
            "grounding_stable": stable,
        }
        print(f"{variant}: correct target row {correct}/{len(trials)} | avg_latency_ms={avg_latency} | "
              f"matching_row_indices={matching_rows} | stable={stable}")

    # Persist raw results (minus full parsed objects, to avoid storing
    # any incidental email body text) as JSON for the report.
    serializable = {
        variant: [
            {k: v for k, v in t.items() if k != "parsed"}
            for t in trials
        ]
        for variant, trials in all_results.items()
    }
    (RESULTS_DIR / "results.json").write_text(json.dumps({
        "summary": summary, "trials": serializable,
    }, indent=2, default=str), encoding="utf-8")
    print(f"\nFull results written to {RESULTS_DIR / 'results.json'}")


if __name__ == "__main__":
    main()
