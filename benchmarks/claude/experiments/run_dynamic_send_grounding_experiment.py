"""Two-stage, Claude-only, non-physical Send-grounding chain benchmark
(2026-09-06) — research only, no runtime wiring.

Background: run_send_grounding_experiment.py proved CASE B for exact
Send grounding — full-screen and reading-pane-crop bboxes are spatially
unreliable (the reading-pane crop specifically lands right at/just past
the boundary between the primary Send button and its own adjacent
dropdown chevron), while a hand-measured tight COMPOSER_AREA_CROP was
5/5 reliable. That tight crop's bounds were manually pixel-measured
from one screenshot — not usable as a runtime constant (a different
email body length shifts the composer's vertical position).

This script tests whether an equivalent tight crop can instead be
DERIVED DYNAMICALLY, in two Vision stages:

  Stage 1 (COARSE, region-only): given the SAME proven reading-pane
  crop, ask Claude to localize the reply composer's action-bar ROW as
  a whole region (never asked to find Send itself — see the prompt's
  explicit "not one specific button" instruction). This stage's own
  Send-shaped output, if it mentions Send at all, is NEVER used for
  physical action — see module docstring rule in the task this
  script was written for.

  Stage 2 (EXACT, reusing the ALREADY-PROVEN send_grounding_prompt.txt):
  Python deterministically derives a new crop from Stage 1's own bbox
  (remapped to full-screen pixels, then expanded by a fixed, testable
  normalized padding policy, then clamped inside the reading-pane
  crop's own bounds) — never a hand-measured pixel box — and asks
  Claude to find the exact primary Send control within THAT crop.

Three expansion policies (A/B/C, increasingly generous) are compared
using the SAME 5 Stage-1 localizations, so the comparison isolates the
effect of the expansion policy alone.

No Outlook launch, no PyAutoGUI, no physical action, no Gemini
fallback. Uses the same frozen screenshot as run_send_grounding_
experiment.py and reuses its Stage-2 response model/ground truth
rather than duplicating them.
"""

from __future__ import annotations

import csv
import json
import math
import sys
import time
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from app.vision.providers.anthropic_provider import AnthropicProvider  # noqa: E402
from benchmarks.claude.experiments.run_send_grounding_experiment import (  # noqa: E402
    GROUND_TRUTH_CANDIDATES,
    SendGroundingDiagnosticResponse,
    _nearest_candidate,
)

SOURCE_SCREENSHOT = PROJECT_ROOT / "screenshots" / "raw" / "screen_20260906_192232_725.png"
STAGE1_PROMPT_PATH = Path(__file__).resolve().parent / "composer_localization_prompt.txt"
SEND_PROMPT_PATH = Path(__file__).resolve().parent / "send_grounding_prompt.txt"
RESULTS_DIR = Path(__file__).resolve().parent / "send_grounding_results"
OVERLAYS_STAGE1_DIR = RESULTS_DIR / "overlays" / "stage1"
OVERLAYS_STAGE2_DIR = RESULTS_DIR / "overlays" / "stage2"

STAGE1_TRIALS = 5
STAGE1_STAGE = "SEND_COMPOSER_LOCALIZATION_BENCHMARK"
STAGE2_STAGE = "SEND_GROUNDING_DYNAMIC_BENCHMARK"

# Same reading-pane left fraction already proven for REPLY_SEARCH
# (app.config.settings.REPLY_VISION_CROP_LEFT_FRACTION) — reused as a
# literal here to keep this benchmark decoupled from app/ imports, per
# this directory's established convention.
READING_PANE_LEFT_FRACTION = 0.35

# Deterministic, testable expansion policies — Python-side padding
# applied to Stage 1's OWN returned bbox dimensions, never a hardcoded
# pixel box. "More conservative" (C) means MORE generous padding (safer
# — less risk of cropping out a real control), not less.
EXPANSION_POLICIES = {
    "A": {"h_frac": 0.15, "v_frac": 1.50},
    "B": {"h_frac": 0.25, "v_frac": 2.00},
    "C": {"h_frac": 0.35, "v_frac": 2.50},
}


class ComposerActionBarLocalizationResponse(BaseModel):
    composer_visible: bool = False
    action_bar_visible: bool = False
    action_bar_bbox: Optional[list[float]] = None
    composer_bbox: Optional[list[float]] = None
    confidence: float = 0.0
    reason: str = ""


# --- Deterministic coordinate math (benchmark-local, mirrors app.vision.
# crop's proven convention — never a different/incorrect one) ----------

def remap_bbox_normalized_to_full_screen(
    bbox_norm: list[float], crop_box_px: tuple[int, int, int, int], full_w: int, full_h: int,
) -> list[float]:
    left, top, right, bottom = crop_box_px
    crop_w, crop_h = right - left, bottom - top
    y_min, x_min, y_max, x_max = bbox_norm

    def _convert(y: float, x: float) -> tuple[float, float]:
        full_px_x = x / 1000 * crop_w + left
        full_px_y = y / 1000 * crop_h + top
        return full_px_y / full_h * 1000, full_px_x / full_w * 1000

    y_min_f, x_min_f = _convert(y_min, x_min)
    y_max_f, x_max_f = _convert(y_max, x_max)
    return [y_min_f, x_min_f, y_max_f, x_max_f]


def normalized_bbox_to_pixels(bbox_norm: list[float], full_w: int, full_h: int) -> tuple[float, float, float, float]:
    y_min, x_min, y_max, x_max = bbox_norm
    return (y_min / 1000 * full_h, x_min / 1000 * full_w, y_max / 1000 * full_h, x_max / 1000 * full_w)


def derive_stage2_crop_px(
    action_bar_bbox_full_px: tuple[float, float, float, float],
    policy: dict,
    reading_pane_bounds_px: tuple[int, int, int, int],
) -> tuple[int, int, int, int]:
    """Expands Stage 1's own action-bar bbox (already in full-screen
    PIXELS) by policy['h_frac']/policy['v_frac'] of ITS OWN width/height
    — never a fixed pixel amount — then clamps inside the reading-pane
    crop's own bounds. This is the ONLY place the dynamic crop is
    computed; no manually-measured coordinate is ever substituted."""
    y_min, x_min, y_max, x_max = action_bar_bbox_full_px
    width = x_max - x_min
    height = y_max - y_min
    h_pad = policy["h_frac"] * width
    v_pad = policy["v_frac"] * height

    left = x_min - h_pad
    right = x_max + h_pad
    top = y_min - v_pad
    bottom = y_max + v_pad

    rp_left, rp_top, rp_right, rp_bottom = reading_pane_bounds_px
    left = max(rp_left, left)
    top = max(rp_top, top)
    right = min(rp_right, right)
    bottom = min(rp_bottom, bottom)
    return (round(left), round(top), round(right), round(bottom))


def _save_stage1_overlay(image_path: Path, action_bar_bbox_full_px, trial: int) -> None:
    try:
        from PIL import Image, ImageDraw

        with Image.open(image_path) as img:
            img = img.convert("RGB")
            draw = ImageDraw.Draw(img)
            if action_bar_bbox_full_px is not None:
                y_min, x_min, y_max, x_max = action_bar_bbox_full_px
                draw.rectangle([x_min, y_min, x_max, y_max], outline="yellow", width=3)
                draw.text((x_min + 2, max(0, y_min - 14)), "action_bar (stage1)", fill="yellow")
            OVERLAYS_STAGE1_DIR.mkdir(parents=True, exist_ok=True)
            img.save(OVERLAYS_STAGE1_DIR / f"stage1_trial{trial}_overlay.png")
    except Exception:  # noqa: BLE001 — diagnostic only
        pass


def _save_stage2_overlay(image_path: Path, full_send_bbox_px, label: str) -> None:
    try:
        from PIL import Image, ImageDraw

        with Image.open(image_path) as img:
            img = img.convert("RGB")
            draw = ImageDraw.Draw(img)
            if full_send_bbox_px is not None:
                y_min, x_min, y_max, x_max = full_send_bbox_px
                draw.rectangle([x_min, y_min, x_max, y_max], outline="lime", width=3)
                draw.text((x_min + 2, max(0, y_min - 14)), "send (stage2)", fill="lime")
            OVERLAYS_STAGE2_DIR.mkdir(parents=True, exist_ok=True)
            img.save(OVERLAYS_STAGE2_DIR / f"{label}_overlay.png")
    except Exception:  # noqa: BLE001 — diagnostic only
        pass


def run_stage1_trial(provider: AnthropicProvider, image_path: Path, prompt_text: str, trial: int) -> dict:
    start = time.perf_counter()
    result = provider.analyze_screen(image_path, "Locate the reply composer action bar", prompt_text, stage=STAGE1_STAGE)
    latency_ms = (time.perf_counter() - start) * 1000

    row = {
        "trial": trial, "schema_valid": False, "composer_visible": None, "action_bar_visible": None,
        "action_bar_bbox": None, "confidence": None, "latency_ms": round(latency_ms, 1),
        "output_tokens": result.output_tokens,
    }
    if result.parsed_json is not None:
        try:
            parsed = ComposerActionBarLocalizationResponse.model_validate(result.parsed_json)
            row["schema_valid"] = True
            row["composer_visible"] = parsed.composer_visible
            row["action_bar_visible"] = parsed.action_bar_visible
            row["action_bar_bbox"] = parsed.action_bar_bbox
            row["confidence"] = parsed.confidence
        except Exception:  # noqa: BLE001
            pass

    print(
        f"[stage1] trial={trial} schema_valid={row['schema_valid']} composer_visible={row['composer_visible']} "
        f"action_bar_visible={row['action_bar_visible']} confidence={row['confidence']} "
        f"latency_ms={row['latency_ms']}"
    )
    return row


def run_stage2_trial(
    provider: AnthropicProvider, image_path: Path, prompt_text: str, crop_box_px: tuple[int, int, int, int],
    full_w: int, full_h: int,
) -> dict:
    start = time.perf_counter()
    result = provider.analyze_screen(image_path, "Locate the exact primary Send control", prompt_text, stage=STAGE2_STAGE)
    latency_ms = (time.perf_counter() - start) * 1000

    row = {
        "schema_valid": False, "semantic_correct": False, "bbox_correct": False,
        "full_screen_send_bbox": None, "nearest_candidate": None, "distance": None,
        "latency_ms": round(latency_ms, 1), "output_tokens": result.output_tokens,
    }
    if result.parsed_json is None:
        return row
    try:
        parsed = SendGroundingDiagnosticResponse.model_validate(result.parsed_json)
        row["schema_valid"] = True
    except Exception:  # noqa: BLE001
        return row

    selected = next((c for c in parsed.candidates if c.index == parsed.primary_send_index), None)
    if selected is None:
        return row
    row["semantic_correct"] = selected.control_identity == "send"
    if selected.bbox is not None and len(selected.bbox) == 4:
        full_bbox = remap_bbox_normalized_to_full_screen(selected.bbox, crop_box_px, full_w, full_h)
        row["full_screen_send_bbox"] = [round(v, 1) for v in full_bbox]
        center = ((full_bbox[0] + full_bbox[2]) / 2, (full_bbox[1] + full_bbox[3]) / 2)
        nearest, dist = _nearest_candidate(center)
        row["nearest_candidate"] = nearest
        row["distance"] = round(dist, 1)
        row["bbox_correct"] = GROUND_TRUTH_CANDIDATES[nearest]["is_send"] and dist < 60.0
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
        full_w, full_h = img.size
    print(f"Source screenshot: {SOURCE_SCREENSHOT} ({full_w}x{full_h})")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    # --- Stage 1 input: the SAME proven reading-pane crop ---
    reading_pane_left = round(READING_PANE_LEFT_FRACTION * full_w)
    reading_pane_bounds_px = (reading_pane_left, 0, full_w, full_h)
    with Image.open(SOURCE_SCREENSHOT) as img:
        reading_pane_crop = img.convert("RGB").crop(reading_pane_bounds_px)
        reading_pane_path = RESULTS_DIR / "reading_pane_input.png"
        reading_pane_crop.save(reading_pane_path)
    rp_w, rp_h = reading_pane_bounds_px[2] - reading_pane_bounds_px[0], reading_pane_bounds_px[3] - reading_pane_bounds_px[1]
    print(f"Reading-pane crop bounds (full-screen px): {reading_pane_bounds_px} size={rp_w}x{rp_h}")

    stage1_prompt = STAGE1_PROMPT_PATH.read_text(encoding="utf-8").format(width=rp_w, height=rp_h)
    send_prompt_template = SEND_PROMPT_PATH.read_text(encoding="utf-8")

    # --- Run Stage 1, 5 times ---
    stage1_rows = []
    stage1_full_px_bboxes: list[Optional[tuple[float, float, float, float]]] = []
    for trial in range(1, STAGE1_TRIALS + 1):
        row = run_stage1_trial(provider, reading_pane_path, stage1_prompt, trial)
        stage1_rows.append(row)
        full_px = None
        if row["schema_valid"] and row["action_bar_visible"] and row["action_bar_bbox"] is not None:
            full_norm = remap_bbox_normalized_to_full_screen(row["action_bar_bbox"], reading_pane_bounds_px, full_w, full_h)
            full_px = normalized_bbox_to_pixels(full_norm, full_w, full_h)
            row["action_bar_bbox_full_screen_px"] = [round(v, 1) for v in full_px]
        stage1_full_px_bboxes.append(full_px)
        _save_stage1_overlay(SOURCE_SCREENSHOT, full_px, trial)

    usable_stage1 = sum(1 for b in stage1_full_px_bboxes if b is not None)
    print(f"\nStage 1 usable (action bar localized): {usable_stage1}/{STAGE1_TRIALS}")

    # --- Stage 1 ground-truth scoring (evaluation only) ---
    # send/dropdown/discard pixel centers, from run_send_grounding_experiment.py's
    # own normalized ground truth.
    def _center_px(name: str) -> tuple[float, float]:
        gy, gx = GROUND_TRUTH_CANDIDATES[name]["center"]
        return gy / 1000 * full_h, gx / 1000 * full_w

    send_px, dropdown_px, discard_px = _center_px("send_primary"), _center_px("send_dropdown"), _center_px("discard")

    def _contains(bbox_px, point) -> bool:
        y_min, x_min, y_max, x_max = bbox_px
        py, px = point
        return y_min <= py <= y_max and x_min <= px <= x_max

    for row, full_px in zip(stage1_rows, stage1_full_px_bboxes):
        if full_px is None:
            row["contains_send"] = row["contains_dropdown"] = row["contains_discard"] = False
            continue
        row["contains_send"] = _contains(full_px, send_px)
        row["contains_dropdown"] = _contains(full_px, dropdown_px)
        row["contains_discard"] = _contains(full_px, discard_px)
        print(
            f"[stage1 eval] trial={row['trial']} contains_send={row['contains_send']} "
            f"contains_dropdown={row['contains_dropdown']} contains_discard={row['contains_discard']}"
        )

    # --- Stage 2: for each expansion policy, chain against EACH of the
    # 5 Stage-1 results (skipping any Stage-1 trial that failed) ---
    chain_rows = []
    for policy_name, policy in EXPANSION_POLICIES.items():
        for stage1_row, full_px in zip(stage1_rows, stage1_full_px_bboxes):
            if full_px is None:
                continue
            crop_box_px = derive_stage2_crop_px(full_px, policy, reading_pane_bounds_px)
            crop_w, crop_h = crop_box_px[2] - crop_box_px[0], crop_box_px[3] - crop_box_px[1]
            with Image.open(SOURCE_SCREENSHOT) as img:
                stage2_crop = img.convert("RGB").crop(crop_box_px)
                stage2_path = RESULTS_DIR / f"stage2_policy{policy_name}_trial{stage1_row['trial']}_input.png"
                stage2_crop.save(stage2_path)

            send_prompt = send_prompt_template.format(width=crop_w, height=crop_h)
            stage2_result = run_stage2_trial(provider, stage2_path, send_prompt, crop_box_px, full_w, full_h)

            total_latency = stage1_row["latency_ms"] + stage2_result["latency_ms"]
            chain_row = {
                "policy": policy_name, "stage1_trial": stage1_row["trial"],
                "stage1_bbox": stage1_row.get("action_bar_bbox"),
                "derived_crop_bounds": crop_box_px, "derived_crop_size": f"{crop_w}x{crop_h}",
                "stage2_send_bbox_crop_relative": None,
                "full_screen_send_bbox": stage2_result["full_screen_send_bbox"],
                "semantic_correct": stage2_result["semantic_correct"],
                "bbox_correct": stage2_result["bbox_correct"],
                "nearest_candidate": stage2_result["nearest_candidate"],
                "distance_from_real_send": stage2_result["distance"],
                "stage1_latency_ms": stage1_row["latency_ms"], "stage2_latency_ms": stage2_result["latency_ms"],
                "total_latency_ms": round(total_latency, 1),
            }
            chain_rows.append(chain_row)
            _save_stage2_overlay(
                SOURCE_SCREENSHOT, stage2_result["full_screen_send_bbox"],
                f"policy{policy_name}_trial{stage1_row['trial']}",
            )
            print(
                f"[chain] policy={policy_name} stage1_trial={stage1_row['trial']} crop={crop_box_px} "
                f"size={crop_w}x{crop_h} semantic={stage2_result['semantic_correct']} "
                f"bbox_correct={stage2_result['bbox_correct']} distance={stage2_result['distance']} "
                f"total_latency_ms={round(total_latency, 1)}"
            )

    # --- Summary per policy ---
    print("\n=== POLICY SUMMARY ===")
    policy_summary = {}
    for policy_name in EXPANSION_POLICIES:
        rows = [r for r in chain_rows if r["policy"] == policy_name]
        n = len(rows)
        semantic_ok = sum(1 for r in rows if r["semantic_correct"])
        bbox_ok = sum(1 for r in rows if r["bbox_correct"])
        avg_latency = round(sum(r["total_latency_ms"] for r in rows) / n, 1) if n else None
        nearest_values = [r["nearest_candidate"] for r in rows]
        stability = "stable" if len(set(nearest_values)) <= 1 else f"varies({nearest_values})"
        policy_summary[policy_name] = {
            "n": n, "semantic_correct": semantic_ok, "bbox_correct": bbox_ok,
            "avg_total_latency_ms": avg_latency, "bbox_stability": stability,
            "expansion": EXPANSION_POLICIES[policy_name],
        }
        print(
            f"policy={policy_name} n={n} semantic={semantic_ok}/{n} bbox_correct={bbox_ok}/{n} "
            f"avg_total_latency_ms={avg_latency} stability={stability}"
        )

    (RESULTS_DIR / "dynamic_results.json").write_text(
        json.dumps({
            "source_screenshot": str(SOURCE_SCREENSHOT),
            "reading_pane_bounds_px": reading_pane_bounds_px,
            "stage1_trials": stage1_rows,
            "chain_trials": chain_rows,
            "policy_summary": policy_summary,
        }, indent=2, default=str),
        encoding="utf-8",
    )
    with open(RESULTS_DIR / "dynamic_results.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(chain_rows[0].keys()) if chain_rows else [])
        if chain_rows:
            writer.writeheader()
            writer.writerows(chain_rows)
    print(f"\nWritten to {RESULTS_DIR / 'dynamic_results.json'} and dynamic_results.csv")


if __name__ == "__main__":
    main()
