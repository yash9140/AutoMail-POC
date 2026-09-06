"""Static, Claude-only, non-physical Send-control spatial-grounding
benchmark (2026-09-06).

Live evidence: the full Reply flow (find email -> read -> reply ->
draft -> type -> verify -> DRAFT_READY -> WAITING_FOR_SEND_APPROVAL)
completed correctly, SEND_GROUNDING returned a schema-valid response
with no Gemini fallback, and the cursor visibly moved — but it did NOT
land on the real Send button, and the run ended SEND_VERIFICATION_
UNCERTAIN. This mirrors the earlier TARGET_EMAIL_SEARCH and REPLY_
SEARCH investigations exactly: a semantically-plausible answer whose
BBOX may not be spatially reliable.

This script isolates SEND_GROUNDING's grounding call against the EXACT
screenshot from that live moment — Claude only, no Gemini fallback, no
Outlook launch, no PyAutoGUI, no physical action of any kind — across
three input variants (FULL_SCREEN / READING_PANE_CROP / COMPOSER_AREA_
CROP), 5 trials each, to determine whether cropping (mirroring the
message-list crop and reply-region crop fixes that already resolved
TARGET_EMAIL_SEARCH and REPLY_SEARCH) improves Send-control bbox
grounding.

Screenshot identification: screenshots/raw/screen_20260906_192232_725.png
— the closest capture strictly BEFORE the live log's "VISION_CALL_START
provider=anthropic stage=SEND_GROUNDING" at 2026-09-06 19:22:32.829
(this file's own timestamp: 19:22:32.725, ~0.1s before).

Ground-truth candidate positions below are EVALUATION-ONLY, derived by
directly measuring pixel coordinates in THIS one frozen screenshot
(gridded-crop visual inspection) — never used as runtime truth, never
fed back into app/outlook/send.py. The composer toolbar shows the
primary Send button (icon + "Send" text), a small attached dropdown
chevron immediately to its right, and a separate "Discard" button
further right — three closely-spaced candidates.

Never stores/prints raw email/draft body text — only structural/
positional facts, consistent with every other benchmark in this
directory.
"""

from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from app.vision.providers.anthropic_provider import AnthropicProvider  # noqa: E402

SOURCE_SCREENSHOT = PROJECT_ROOT / "screenshots" / "raw" / "screen_20260906_192232_725.png"
PROMPT_PATH = Path(__file__).resolve().parent / "send_grounding_prompt.txt"
RESULTS_DIR = Path(__file__).resolve().parent / "send_grounding_results"
TRIALS_PER_VARIANT = 5
STAGE = "SEND_GROUNDING_BENCHMARK"

VARIANTS = ("FULL_SCREEN", "READING_PANE_CROP", "COMPOSER_AREA_CROP")

# --- Evaluation-only ground truth (see module docstring) — normalized
# (y_center, x_center) in FULL-SCREEN 0-1000 space, derived from direct
# pixel measurement of the one frozen screenshot above. ---
GROUND_TRUTH_CANDIDATES: dict[str, dict] = {
    "send_primary": {"center": (973.6, 440.6), "is_send": True},
    "send_dropdown": {"center": (973.6, 473.4), "is_send": False},
    "discard": {"center": (973.6, 539.3), "is_send": False},
}


class SendCandidate(BaseModel):
    index: int = 0
    visible_label: str = ""
    control_identity: str = "other"
    control_type: str = ""
    bbox: Optional[list[float]] = None
    confidence: float = 0.0


class SendGroundingDiagnosticResponse(BaseModel):
    candidates: list[SendCandidate] = Field(default_factory=list)
    primary_send_index: Optional[int] = None
    reason: str = ""


def _crop_bounds_for_variant(variant: str, width: int, height: int) -> Optional[tuple[int, int, int, int]]:
    """Returns (left, top, right, bottom) native pixel bounds, or None
    for FULL_SCREEN. Evaluation-only crop bounds — never used as runtime
    truth, never fed back into app/outlook/send.py."""
    if variant == "FULL_SCREEN":
        return None
    if variant == "READING_PANE_CROP":
        # Reuses the SAME evidence-based left fraction already proven
        # for REPLY_SEARCH (app.config.settings.REPLY_VISION_CROP_LEFT_
        # FRACTION = 0.35) — horizontal crop only, full height retained.
        return (round(0.35 * width), 0, width, height)
    if variant == "COMPOSER_AREA_CROP":
        # Tighter crop around the composer's own toolbar row, with draft
        # text above for context — deliberately NOT isolating only the
        # Send button (Send dropdown and Discard both remain visible).
        return (780, 900, 1150, height)
    raise ValueError(f"Unknown variant: {variant}")


def _remap_bbox_to_full_screen(
    bbox_normalized: list[float], crop_box: Optional[tuple[int, int, int, int]], full_width: int, full_height: int,
) -> list[float]:
    if crop_box is None:
        return list(bbox_normalized)
    left, top, right, bottom = crop_box
    crop_w, crop_h = right - left, bottom - top
    y_min, x_min, y_max, x_max = bbox_normalized

    def _convert(y: float, x: float) -> tuple[float, float]:
        full_px_x = x / 1000 * crop_w + left
        full_px_y = y / 1000 * crop_h + top
        return full_px_y / full_height * 1000, full_px_x / full_width * 1000

    y_min_f, x_min_f = _convert(y_min, x_min)
    y_max_f, x_max_f = _convert(y_max, x_max)
    return [y_min_f, x_min_f, y_max_f, x_max_f]


def _nearest_candidate(center: tuple[float, float]) -> tuple[str, float]:
    """Nearest-neighbor classification against GROUND_TRUTH_CANDIDATES —
    used instead of fixed-tolerance boxes because Send / its dropdown /
    Discard sit close together, so a simple "inside an expected box"
    check would not reliably discriminate them."""
    cy, cx = center
    best_name, best_dist = None, math.inf
    for name, info in GROUND_TRUTH_CANDIDATES.items():
        gy, gx = info["center"]
        dist = math.hypot(cy - gy, cx - gx)
        if dist < best_dist:
            best_name, best_dist = name, dist
    return best_name, best_dist


def _save_debug_overlay(image_path: Path, candidates: list[dict], primary_send_index: Optional[int], out_path: Path) -> None:
    try:
        from PIL import Image, ImageDraw

        with Image.open(image_path) as img:
            img = img.convert("RGB")
            draw = ImageDraw.Draw(img)
            width, height = img.size
            for c in candidates:
                bbox = c.get("full_screen_bbox")
                if not bbox or len(bbox) != 4:
                    continue
                y_min, x_min, y_max, x_max = bbox
                px = [x_min / 1000 * width, y_min / 1000 * height, x_max / 1000 * width, y_max / 1000 * height]
                color = "lime" if c["index"] == primary_send_index else "red"
                draw.rectangle(px, outline=color, width=3)
                label = f"#{c['index']}:{c['control_identity']}"
                draw.text((px[0] + 2, max(0, px[1] - 12)), label, fill=color)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            img.save(out_path)
    except Exception:  # noqa: BLE001 — diagnostic only
        pass


def run_trial(
    provider: AnthropicProvider, image_path: Path, prompt_text: str, variant: str, trial: int,
    crop_box: Optional[tuple[int, int, int, int]], full_width: int, full_height: int,
) -> dict:
    start = time.perf_counter()
    result = provider.analyze_screen(image_path, "Enumerate composer action controls", prompt_text, stage=STAGE)
    latency_ms = (time.perf_counter() - start) * 1000

    schema_valid = False
    parsed: Optional[SendGroundingDiagnosticResponse] = None
    if result.parsed_json is not None:
        try:
            parsed = SendGroundingDiagnosticResponse.model_validate(result.parsed_json)
            schema_valid = True
        except Exception:  # noqa: BLE001
            schema_valid = False

    row = {
        "variant": variant, "trial": trial, "schema_valid": schema_valid,
        "stop_reason": result.stop_reason, "output_tokens": result.output_tokens,
        "response_char_count": len(result.raw_text), "latency_ms": round(latency_ms, 1),
        "num_candidates": 0, "semantic_send_correct": False, "bbox_correct": False,
        "nearest_candidate": None, "distance": None, "full_screen_bbox": None,
    }
    candidates_for_overlay = []
    if schema_valid and parsed is not None:
        row["num_candidates"] = len(parsed.candidates)
        selected = None
        for c in parsed.candidates:
            full_bbox = None
            if c.bbox is not None and len(c.bbox) == 4:
                full_bbox = _remap_bbox_to_full_screen(c.bbox, crop_box, full_width, full_height)
            candidates_for_overlay.append({
                "index": c.index, "control_identity": c.control_identity, "full_screen_bbox": full_bbox,
            })
            if parsed.primary_send_index is not None and c.index == parsed.primary_send_index:
                selected = (c, full_bbox)

        if selected is not None:
            candidate, full_bbox = selected
            row["semantic_send_correct"] = candidate.control_identity == "send"
            if full_bbox is not None:
                center = ((full_bbox[0] + full_bbox[2]) / 2, (full_bbox[1] + full_bbox[3]) / 2)
                nearest, dist = _nearest_candidate(center)
                row["nearest_candidate"] = nearest
                row["distance"] = round(dist, 1)
                row["bbox_correct"] = GROUND_TRUTH_CANDIDATES[nearest]["is_send"] and dist < 60.0
                row["full_screen_bbox"] = [round(v, 1) for v in full_bbox]

    print(
        f"variant={variant:<18} trial={trial} schema_valid={schema_valid} "
        f"num_candidates={row['num_candidates']} semantic_correct={row['semantic_send_correct']} "
        f"bbox_correct={row['bbox_correct']} nearest={row['nearest_candidate']} distance={row['distance']} "
        f"latency_ms={row['latency_ms']}"
    )

    if candidates_for_overlay:
        out_path = RESULTS_DIR / f"{variant}_trial{trial}_overlay.png"
        _save_debug_overlay(SOURCE_SCREENSHOT, candidates_for_overlay, parsed.primary_send_index if parsed else None, out_path)

    return row


def main() -> None:
    if not SOURCE_SCREENSHOT.exists():
        print(f"Source screenshot not found: {SOURCE_SCREENSHOT}")
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
        full_width, full_height = img.size
    print(f"Source screenshot: {SOURCE_SCREENSHOT} ({full_width}x{full_height})")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    prompt_template = PROMPT_PATH.read_text(encoding="utf-8")

    all_rows: list[dict] = []
    for variant in VARIANTS:
        crop_box = _crop_bounds_for_variant(variant, full_width, full_height)
        if crop_box is None:
            image_for_call = SOURCE_SCREENSHOT
            call_width, call_height = full_width, full_height
        else:
            left, top, right, bottom = crop_box
            with Image.open(SOURCE_SCREENSHOT) as img:
                cropped = img.convert("RGB").crop((left, top, right, bottom))
                image_for_call = RESULTS_DIR / f"{variant}_input.png"
                cropped.save(image_for_call)
            call_width, call_height = right - left, bottom - top
            print(f"{variant}: crop_bounds={crop_box} crop_size={call_width}x{call_height}")

        prompt_text = prompt_template.format(width=call_width, height=call_height)
        for trial in range(1, TRIALS_PER_VARIANT + 1):
            row = run_trial(provider, image_for_call, prompt_text, variant, trial, crop_box, full_width, full_height)
            all_rows.append(row)

    print("\n=== SUMMARY ===")
    print(f"{'Variant':<20} {'SemanticOK':>10} {'BBoxOK':>8} {'AvgLatencyMs':>14}")
    summary = {}
    for variant in VARIANTS:
        rows = [r for r in all_rows if r["variant"] == variant]
        semantic_ok = sum(1 for r in rows if r["semantic_send_correct"])
        bbox_ok = sum(1 for r in rows if r["bbox_correct"])
        avg_latency = round(sum(r["latency_ms"] for r in rows) / len(rows), 1)
        nearest_values = [r["nearest_candidate"] for r in rows]
        stability = "stable" if len(set(nearest_values)) <= 1 else f"varies({nearest_values})"
        summary[variant] = {
            "semantic_correct": semantic_ok, "bbox_correct": bbox_ok, "n": len(rows),
            "avg_latency_ms": avg_latency, "bbox_stability": stability,
        }
        print(f"{variant:<20} {semantic_ok}/{len(rows):<8} {bbox_ok}/{len(rows):<6} {avg_latency:>14}")
        print(f"    bbox_stability: {stability}")

    (RESULTS_DIR / "results.json").write_text(
        json.dumps({"source_screenshot": str(SOURCE_SCREENSHOT), "trials": all_rows, "summary": summary}, indent=2),
        encoding="utf-8",
    )
    print(f"\nWritten to {RESULTS_DIR / 'results.json'}")


if __name__ == "__main__":
    main()
