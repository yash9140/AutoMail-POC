"""Static production-path regression for the runtime two-stage
SEND_GROUNDING implementation (2026-09-06).

Unlike run_dynamic_send_grounding_experiment.py (which reimplements the
crop/remap math locally to keep the benchmarks/ directory decoupled from
app/ imports), this script deliberately imports and calls the ACTUAL
runtime functions now wired into app/outlook/send.py and app/vision/
crop.py:

  - app.outlook.send.SendSteps._locate_send_composer_action_bar()  (Stage 1)
  - app.outlook.send.SendSteps._ground_exact_send()                (Stage 2)
  - app.vision.crop.create_horizontal_vision_crop() / create_rectangular_
    vision_crop() / derive_send_composer_crop_bounds_px()

against the SAME frozen evidence screenshot used by both prior
benchmarks, using a REAL SendFlowSteps instance (constructed exactly as
app/workers/send_worker.py constructs it) and a REAL VisionService(
AnthropicProvider, fallback=None) — Claude only, no Gemini fallback, no
PyAutoGUI, no physical action of any kind. This proves the runtime
wiring reproduces the benchmarked two-stage chain's own results, not
just that the isolated unit math is correct (already covered by
tests/test_send_two_stage_grounding.py).

Scoring reuses analyze_dynamic_send_grounding_results.py's corrected,
rigorous method: does the reported Send bbox's own CENTER POINT fall
inside the REAL Send button's own measured pixel bounds
(SEND_BUTTON_PX) — never the original experiment's misleading
nearest-ground-truth-CENTER-distance metric.
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv  # noqa: E402

from app.outlook.send import SendFlowSteps  # noqa: E402
from app.safety.abort_controller import AbortController  # noqa: E402
from app.vision.providers.anthropic_provider import AnthropicProvider  # noqa: E402
from app.vision.service import VisionService  # noqa: E402

SOURCE_SCREENSHOT = PROJECT_ROOT / "screenshots" / "raw" / "screen_20260906_192232_725.png"
RESULTS_DIR = Path(__file__).resolve().parent / "send_grounding_results"
TRIALS = 5

# Same evaluation-only, directly pixel-measured Send button bounds used
# by analyze_dynamic_send_grounding_results.py — (x_min, y_min, x_max, y_max).
SEND_BUTTON_PX = (800, 1030, 892, 1073)
BBOX_TOLERANCE_PX = 5


@dataclass
class _FakeCapture:
    """Minimal stand-in for app.automation.screen_capture's real capture
    result — the two grounding methods only ever read .path/.width/
    .height off it, never call any of its other methods."""

    path: str
    width: int
    height: int


def _bbox_center_inside_send_button(full_screen_bbox_norm, full_w: int, full_h: int, tolerance_px: int) -> bool:
    if not full_screen_bbox_norm:
        return False
    y_min, x_min, y_max, x_max = full_screen_bbox_norm
    center_x = (x_min + x_max) / 2 / 1000 * full_w
    center_y = (y_min + y_max) / 2 / 1000 * full_h
    sx_min, sy_min, sx_max, sy_max = SEND_BUTTON_PX
    return (sx_min - tolerance_px) <= center_x <= (sx_max + tolerance_px) and \
           (sy_min - tolerance_px) <= center_y <= (sy_max + tolerance_px)


def main() -> None:
    if not SOURCE_SCREENSHOT.exists():
        print(f"Source screenshot not found: {SOURCE_SCREENSHOT}")
        return

    load_dotenv(PROJECT_ROOT / ".env")
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    model = os.environ.get("ANTHROPIC_MODEL", "")
    if not api_key or not model:
        print("ANTHROPIC_API_KEY / ANTHROPIC_MODEL not set in .env - cannot run live Claude calls.")
        return

    from PIL import Image

    with Image.open(SOURCE_SCREENSHOT) as img:
        full_w, full_h = img.size
    print(f"Source screenshot: {SOURCE_SCREENSHOT} ({full_w}x{full_h})")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    trial_rows = []
    stage1_usable = 0
    stage2_semantic_ok = 0
    stage2_bbox_ok = 0

    for trial in range(1, TRIALS + 1):
        # A fresh SendFlowSteps per trial: production code caches the
        # reading-pane crop per-instance, keyed by screenshot path — a
        # fresh instance proves each trial recomputes Stage 1 rather
        # than accidentally reusing a stale cached crop across trials.
        primary = AnthropicProvider(api_key=api_key, model=model, timeout_seconds=60.0)
        vision = VisionService(primary, fallback=None, default_max_retries=0)
        steps = SendFlowSteps(AbortController(), vision, model, send_approval_granted=True)
        capture = _FakeCapture(path=str(SOURCE_SCREENSHOT), width=full_w, height=full_h)

        start = time.perf_counter()
        stage1_bbox = steps._locate_send_composer_action_bar(capture)
        row = {
            "trial": trial, "stage1_bbox_full_px": [round(v, 1) for v in stage1_bbox] if stage1_bbox else None,
            "stage1_usable": stage1_bbox is not None,
        }
        if stage1_bbox is not None:
            stage1_usable += 1
            structured = steps._ground_exact_send(capture, stage1_bbox)
            row["dynamic_crop_bounds"] = steps.result.send_dynamic_crop_bounds
            if structured is not None:
                row["stage2_send_visible"] = structured.send_visible
                row["stage2_control_identity"] = structured.control_identity
                # Mirrors the EXACT identity check app.outlook.send's own
                # _validate_and_click_send() performs at runtime
                # (app/outlook/send.py: identity = structured.
                # control_identity.strip().lower()) - never a stricter
                # literal comparison invented only for this script.
                identity = structured.control_identity.strip().lower()
                row["stage2_semantic_correct"] = bool(structured.send_visible and identity == "send")
                row["remapped_bbox_full_screen_norm"] = structured.bbox
                if row["stage2_semantic_correct"]:
                    stage2_semantic_ok += 1
                bbox_ok = _bbox_center_inside_send_button(structured.bbox, full_w, full_h, BBOX_TOLERANCE_PX)
                row["stage2_bbox_correct"] = bbox_ok
                if bbox_ok:
                    stage2_bbox_ok += 1
            else:
                row["stage2_send_visible"] = None
                row["stage2_semantic_correct"] = False
                row["stage2_bbox_correct"] = False
                row["failure_reason"] = str(steps.result.failure_reason)
        else:
            row["stage2_semantic_correct"] = False
            row["stage2_bbox_correct"] = False
            row["failure_reason"] = str(steps.result.failure_reason)
        total_latency_ms = (time.perf_counter() - start) * 1000
        row["total_latency_ms"] = round(total_latency_ms, 1)
        trial_rows.append(row)
        print(
            f"[trial {trial}] stage1_usable={row['stage1_usable']} "
            f"stage2_semantic={row.get('stage2_semantic_correct')} "
            f"stage2_bbox={row.get('stage2_bbox_correct')} "
            f"total_latency_ms={row['total_latency_ms']}"
        )

    print("\n=== STATIC PRODUCTION-PATH REGRESSION SUMMARY ===")
    print(f"Stage 1 usable: {stage1_usable}/{TRIALS}")
    print(f"Stage 2 semantic correct: {stage2_semantic_ok}/{TRIALS}")
    print(f"Stage 2 bbox correct (real Send-button-bounds containment, tol={BBOX_TOLERANCE_PX}px): {stage2_bbox_ok}/{TRIALS}")

    (RESULTS_DIR / "static_production_path_regression.json").write_text(
        json.dumps({
            "source_screenshot": str(SOURCE_SCREENSHOT), "trials": trial_rows,
            "stage1_usable": f"{stage1_usable}/{TRIALS}", "stage2_semantic_correct": f"{stage2_semantic_ok}/{TRIALS}",
            "stage2_bbox_correct": f"{stage2_bbox_ok}/{TRIALS}",
        }, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"\nWritten to {RESULTS_DIR / 'static_production_path_regression.json'}")


if __name__ == "__main__":
    main()
