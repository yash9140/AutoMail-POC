"""Static Claude-only calibration of the REPLY_SEARCH Vision-crop LEFT
FRACTION (2026-09-06), run BEFORE any runtime wiring.

The prior reply_grounding benchmark (run_reply_grounding_experiment.py)
proved a full-height, right-side crop fixes REPLY_SEARCH's full-screen
bbox-grounding failure — but its READING_PANE_CROP left edge (755px)
was a HAND-MEASURED pixel from that one screenshot, never meant to
become a runtime constant. This script tests a small set of NORMALIZED
left-fraction candidates (0.35 / 0.38 / 0.40 of screen width) against
the same frozen screenshot, scoring actual bbox spatial correctness
(not just semantic labeling), to pick an evidence-based
REPLY_VISION_CROP_LEFT_FRACTION — preferring the WIDEST crop (smallest
left fraction) that still grounds reliably, since more context is safer
and a narrower crop risks cutting off a real Reply control on some
other email layout.

Claude only, no Gemini fallback, no Outlook launch, no PyAutoGUI, no
physical action of any kind.
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

SOURCE_SCREENSHOT = PROJECT_ROOT / "screenshots" / "raw" / "screen_20260906_165113_787.png"
PROMPT_PATH = Path(__file__).resolve().parent / "reply_grounding_prompt.txt"
RESULTS_DIR = Path(__file__).resolve().parent / "reply_crop_calibration_results"
TRIALS_PER_CANDIDATE = 3
STAGE = "REPLY_SEARCH_CALIBRATION"

LEFT_FRACTION_CANDIDATES = (0.35, 0.38, 0.40)

# Same evaluation-only ground truth as run_reply_grounding_experiment.py —
# normalized (y_center, x_center) in FULL-SCREEN 0-1000 space, directly
# measured from this one frozen screenshot.
GROUND_TRUTH_CANDIDATES: dict[str, dict] = {
    "ribbon_reply_all": {"center": (149.5, 190.1), "is_reply": False},
    "header_reply": {"center": (312.5, 895.5), "is_reply": True},
    "header_reply_all": {"center": (312.5, 916.0), "is_reply": False},
    "header_forward": {"center": (312.5, 937.2), "is_reply": False},
    "bottom_reply": {"center": (650.9, 477.6), "is_reply": True},
    "bottom_forward": {"center": (650.9, 540.9), "is_reply": False},
}


class ReplyCandidate(BaseModel):
    index: int = 0
    visible_label: str = ""
    control_type: str = "other"
    bbox: Optional[list[float]] = None
    confidence: float = 0.0


class ReplyGroundingDiagnosticResponse(BaseModel):
    candidates: list[ReplyCandidate] = Field(default_factory=list)
    exact_reply_index: Optional[int] = None
    reason: str = ""


def _remap_bbox_to_full_screen(bbox_normalized, left_px, crop_width, full_width, full_height):
    y_min, x_min, y_max, x_max = bbox_normalized

    def _convert(y, x):
        full_px_x = x / 1000 * crop_width + left_px
        full_px_y = y / 1000 * full_height  # crop_height == full_height (top=0)
        return full_px_y / full_height * 1000, full_px_x / full_width * 1000

    y_min_f, x_min_f = _convert(y_min, x_min)
    y_max_f, x_max_f = _convert(y_max, x_max)
    return [y_min_f, x_min_f, y_max_f, x_max_f]


def _nearest_candidate_distance(center):
    cy, cx = center
    best_name, best_dist = None, math.inf
    for name, info in GROUND_TRUTH_CANDIDATES.items():
        gy, gx = info["center"]
        dist = math.hypot(cy - gy, cx - gx)
        if dist < best_dist:
            best_name, best_dist = name, dist
    return best_name, best_dist


def run_trial(provider, image_path, prompt_text, left_px, crop_width, full_width, full_height, candidate_label, trial):
    start = time.perf_counter()
    result = provider.analyze_screen(image_path, "Enumerate reply-related controls", prompt_text, stage=STAGE)
    latency_ms = (time.perf_counter() - start) * 1000

    schema_valid = False
    parsed = None
    if result.parsed_json is not None:
        try:
            parsed = ReplyGroundingDiagnosticResponse.model_validate(result.parsed_json)
            schema_valid = True
        except Exception:  # noqa: BLE001
            schema_valid = False

    row = {
        "left_fraction": candidate_label, "trial": trial, "schema_valid": schema_valid,
        "latency_ms": round(latency_ms, 1), "semantic_correct": False, "bbox_correct": False,
        "nearest_candidate": None, "distance": None,
    }
    if schema_valid and parsed is not None and parsed.exact_reply_index is not None:
        selected = next((c for c in parsed.candidates if c.index == parsed.exact_reply_index), None)
        if selected is not None:
            row["semantic_correct"] = selected.control_type == "reply"
            if selected.bbox is not None and len(selected.bbox) == 4:
                full_bbox = _remap_bbox_to_full_screen(selected.bbox, left_px, crop_width, full_width, full_height)
                center = ((full_bbox[0] + full_bbox[2]) / 2, (full_bbox[1] + full_bbox[3]) / 2)
                nearest, dist = _nearest_candidate_distance(center)
                row["nearest_candidate"] = nearest
                row["distance"] = round(dist, 1)
                row["bbox_correct"] = GROUND_TRUTH_CANDIDATES[nearest]["is_reply"] and dist < 100.0

    print(
        f"left_fraction={candidate_label} trial={trial} schema_valid={schema_valid} "
        f"semantic_correct={row['semantic_correct']} bbox_correct={row['bbox_correct']} "
        f"nearest={row['nearest_candidate']} distance={row['distance']} latency_ms={row['latency_ms']}"
    )
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

    all_rows = []
    for fraction in LEFT_FRACTION_CANDIDATES:
        left_px = round(fraction * full_width)
        crop_width = full_width - left_px
        with Image.open(SOURCE_SCREENSHOT) as img:
            cropped = img.convert("RGB").crop((left_px, 0, full_width, full_height))
            image_for_call = RESULTS_DIR / f"crop_{fraction}.png"
            cropped.save(image_for_call)
        print(f"\n--- left_fraction={fraction} left_px={left_px} crop_size={crop_width}x{full_height} ---")

        prompt_text = prompt_template.format(width=crop_width, height=full_height)
        for trial in range(1, TRIALS_PER_CANDIDATE + 1):
            row = run_trial(
                provider, image_for_call, prompt_text, left_px, crop_width, full_width, full_height, fraction, trial,
            )
            all_rows.append(row)

    print("\n=== CALIBRATION SUMMARY ===")
    summary = {}
    for fraction in LEFT_FRACTION_CANDIDATES:
        rows = [r for r in all_rows if r["left_fraction"] == fraction]
        semantic_ok = sum(1 for r in rows if r["semantic_correct"])
        bbox_ok = sum(1 for r in rows if r["bbox_correct"])
        avg_latency = round(sum(r["latency_ms"] for r in rows) / len(rows), 1)
        distances = [r["distance"] for r in rows if r["distance"] is not None]
        summary[str(fraction)] = {
            "semantic_correct": f"{semantic_ok}/{len(rows)}", "bbox_correct": f"{bbox_ok}/{len(rows)}",
            "avg_latency_ms": avg_latency, "distances": distances,
        }
        print(f"left_fraction={fraction}: semantic={semantic_ok}/{len(rows)} bbox={bbox_ok}/{len(rows)} "
              f"avg_latency_ms={avg_latency} distances={distances}")

    (RESULTS_DIR / "results.json").write_text(
        json.dumps({"source_screenshot": str(SOURCE_SCREENSHOT), "trials": all_rows, "summary": summary}, indent=2),
        encoding="utf-8",
    )
    print(f"\nWritten to {RESULTS_DIR / 'results.json'}")


if __name__ == "__main__":
    main()
