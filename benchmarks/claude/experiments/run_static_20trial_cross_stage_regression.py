"""20-trial static production-path robustness benchmark for the
cross-stage consistency safety gate (2026-09-06 follow-up).

The two-stage architecture's own 5-trial static regression (run_static_
production_path_regression.py) found Stage 2 spatially correct in 4/5
trials; 5 trials are too few to trust for an irreversible action. This
script runs 20 Claude-only, non-physical chains against the SAME frozen
evidence screenshot, calling the ACTUAL runtime methods (app.outlook.
send.SendSteps._locate_send_composer_action_bar/_ground_exact_send/
_check_send_identity/_validate_bbox_and_cross_stage/_refine_send_
grounding) — but deliberately STOPS one call short of _click_send()/
_validate_and_click_send(), so no pyautogui import or mock is needed at
all: the methods this script calls never touch the mouse.

The PRIMARY safety metric (per task instruction) is not "how often did
grounding produce an actionable result" but WRONG_ACTIONABLE_SEND_BBOX
— the count of trials where the final candidate became actionable
(cross-stage accepted) AND that bbox is NOT actually within the real
Send button's own measured bounds. A safe stop is an acceptable outcome
here; a wrong actionable bbox is not.
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
TRIALS = 20

# Same evaluation-only, directly pixel-measured Send button bounds used
# by analyze_dynamic_send_grounding_results.py / run_static_production_
# path_regression.py — (x_min, y_min, x_max, y_max).
SEND_BUTTON_PX = (800, 1030, 892, 1073)
BBOX_TOLERANCE_PX = 5


@dataclass
class _FakeCapture:
    path: str
    width: int
    height: int


def _bbox_correct_gt(bbox_full_screen_norm, full_w: int, full_h: int) -> bool:
    if not bbox_full_screen_norm:
        return False
    y_min, x_min, y_max, x_max = bbox_full_screen_norm
    center_x = (x_min + x_max) / 2 / 1000 * full_w
    center_y = (y_min + y_max) / 2 / 1000 * full_h
    sx_min, sy_min, sx_max, sy_max = SEND_BUTTON_PX
    return (sx_min - BBOX_TOLERANCE_PX) <= center_x <= (sx_max + BBOX_TOLERANCE_PX) and \
           (sy_min - BBOX_TOLERANCE_PX) <= center_y <= (sy_max + BBOX_TOLERANCE_PX)


def _identity_is_send(structured) -> bool:
    identity = (structured.control_identity or "").strip().lower()
    return bool(structured.send_visible and identity == "send")


def run_one_trial(trial_num: int, api_key: str, model: str, full_w: int, full_h: int) -> dict:
    primary = AnthropicProvider(api_key=api_key, model=model, timeout_seconds=60.0)
    vision = VisionService(primary, fallback=None, default_max_retries=0)
    steps = SendFlowSteps(AbortController(), vision, model, send_approval_granted=True)
    capture = _FakeCapture(path=str(SOURCE_SCREENSHOT), width=full_w, height=full_h)

    row = {
        "trial": trial_num, "stage1_usable": False, "initial_stage2_semantic": False,
        "initial_stage2_bbox_correct": False, "cross_stage_valid": None,
        "refinement_triggered": False, "refined_stage2_bbox_correct": None,
        "final_actionable": False, "final_actionable_bbox_correct": False, "safe_stopped": True,
    }
    start = time.perf_counter()

    stage1_bbox = steps._locate_send_composer_action_bar(capture)
    row["stage1_usable"] = stage1_bbox is not None
    if stage1_bbox is None:
        row["total_latency_ms"] = round((time.perf_counter() - start) * 1000, 1)
        return row

    initial_structured = steps._ground_exact_send(capture, stage1_bbox)
    if initial_structured is None:
        row["total_latency_ms"] = round((time.perf_counter() - start) * 1000, 1)
        return row

    identity_ok = _identity_is_send(initial_structured)
    row["initial_stage2_semantic"] = identity_ok
    if not identity_ok:
        row["total_latency_ms"] = round((time.perf_counter() - start) * 1000, 1)
        return row

    row["initial_stage2_bbox_correct"] = _bbox_correct_gt(initial_structured.bbox, full_w, full_h)

    accepted = steps._validate_bbox_and_cross_stage(initial_structured, full_w, full_h, stage1_bbox)
    row["cross_stage_valid"] = accepted
    if accepted is None:  # structurally invalid bbox — terminal, no refinement
        row["total_latency_ms"] = round((time.perf_counter() - start) * 1000, 1)
        return row

    if accepted:
        row["final_actionable"] = True
        row["final_actionable_bbox_correct"] = row["initial_stage2_bbox_correct"]
        row["safe_stopped"] = False
        row["total_latency_ms"] = round((time.perf_counter() - start) * 1000, 1)
        return row

    # Cross-stage rejected the initial candidate — one bounded refinement.
    row["refinement_triggered"] = True
    dynamic_crop = steps._send_last_dynamic_crop
    refined_structured = steps._refine_send_grounding(dynamic_crop)
    if refined_structured is None:
        row["total_latency_ms"] = round((time.perf_counter() - start) * 1000, 1)
        return row

    refined_identity_ok = _identity_is_send(refined_structured)
    if not refined_identity_ok:
        row["total_latency_ms"] = round((time.perf_counter() - start) * 1000, 1)
        return row

    row["refined_stage2_bbox_correct"] = _bbox_correct_gt(refined_structured.bbox, full_w, full_h)
    refined_accepted = steps._validate_bbox_and_cross_stage(refined_structured, full_w, full_h, stage1_bbox)
    row["cross_stage_valid_refined"] = refined_accepted
    if refined_accepted:
        row["final_actionable"] = True
        row["final_actionable_bbox_correct"] = row["refined_stage2_bbox_correct"]
        row["safe_stopped"] = False

    row["total_latency_ms"] = round((time.perf_counter() - start) * 1000, 1)
    return row


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
    rows = []
    for trial_num in range(1, TRIALS + 1):
        row = run_one_trial(trial_num, api_key, model, full_w, full_h)
        rows.append(row)
        print(
            f"[trial {row['trial']:>2}] stage1_usable={row['stage1_usable']} "
            f"cross_stage_valid={row['cross_stage_valid']} refine={row['refinement_triggered']} "
            f"final_actionable={row['final_actionable']} final_bbox_correct={row['final_actionable_bbox_correct']} "
            f"safe_stopped={row['safe_stopped']} latency_ms={row['total_latency_ms']}"
        )

    n = len(rows)
    correct_actionable = sum(1 for r in rows if r["final_actionable"] and r["final_actionable_bbox_correct"])
    wrong_actionable = sum(1 for r in rows if r["final_actionable"] and not r["final_actionable_bbox_correct"])
    safe_stops = sum(1 for r in rows if r["safe_stopped"])
    refinements = sum(1 for r in rows if r["refinement_triggered"])

    print("\n=== 20-TRIAL CROSS-STAGE SAFETY REGRESSION SUMMARY ===")
    print(f"Correct actionable: {correct_actionable}/{n}")
    print(f"Safe stops: {safe_stops}/{n}")
    print(f"WRONG actionable (final_actionable=True but bbox NOT in real Send bounds): {wrong_actionable}/{n}")
    print(f"Refinement triggered: {refinements}/{n}")

    (RESULTS_DIR / "static_20trial_cross_stage_regression.json").write_text(
        json.dumps({
            "source_screenshot": str(SOURCE_SCREENSHOT), "trials": rows,
            "correct_actionable": f"{correct_actionable}/{n}", "safe_stops": f"{safe_stops}/{n}",
            "wrong_actionable": f"{wrong_actionable}/{n}", "refinements_triggered": f"{refinements}/{n}",
        }, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"\nWritten to {RESULTS_DIR / 'static_20trial_cross_stage_regression.json'}")


if __name__ == "__main__":
    main()
