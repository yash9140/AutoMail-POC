"""Post-hoc corrected analysis for run_dynamic_send_grounding_experiment.py's
dynamic_results.json (2026-09-06).

IMPORTANT CORRECTION this script exists to make: the experiment's own
live "bbox_correct" scoring used nearest-ground-truth-CENTER-distance
with a 60-normalized-unit threshold — visual inspection of the
regenerated Stage-2 overlays (benchmarks/claude/experiments/
send_grounding_results/overlays/stage2/) showed several "bbox_correct=
True" trials actually placed their click-center point just OUTSIDE the
real Send button's own pixel bounds (e.g. landing in the gap between
Send and the "..." toolbar button above it) — the 60-unit threshold was
too generous given how close together Send/its dropdown/Discard/the
"..." button all are in this UI. This exactly mirrors the earlier
TARGET_EMAIL_SEARCH row-confusion benchmark's own corrective-analysis
precedent: an automated metric can look like "100%/5-of-5 success" while
visual inspection reveals it isn't measuring the thing that actually
matters (whether the click would land on the real control).

This script re-scores every chain trial against the real Send button's
own measured pixel bounds (evaluation-only, from the same gridded-crop
pixel inspection as the original benchmark — never runtime truth),
checking whether the reported bbox's own CENTER POINT falls inside those
bounds (with a small +/-N px tolerance to absorb this benchmark's own
hand-measurement imprecision, not a weakening of the real criterion) —
never by nearest-candidate distance alone.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

RESULTS_DIR = Path(__file__).resolve().parent / "send_grounding_results"

FULL_WIDTH, FULL_HEIGHT = 1920, 1080
# Evaluation-only, directly pixel-measured Send button bounds (gridded-
# crop inspection of screen_20260906_192232_725.png) — (x_min, y_min,
# x_max, y_max), never runtime truth.
SEND_BUTTON_PX = (800, 1030, 892, 1073)


def click_center_inside_send_button(full_screen_bbox_norm: list[float], tolerance_px: int) -> bool:
    if not full_screen_bbox_norm:
        return False
    y_min, x_min, y_max, x_max = full_screen_bbox_norm
    center_x = (x_min + x_max) / 2 / 1000 * FULL_WIDTH
    center_y = (y_min + y_max) / 2 / 1000 * FULL_HEIGHT
    sx_min, sy_min, sx_max, sy_max = SEND_BUTTON_PX
    return (sx_min - tolerance_px) <= center_x <= (sx_max + tolerance_px) and \
           (sy_min - tolerance_px) <= center_y <= (sy_max + tolerance_px)


def main() -> None:
    data = json.loads((RESULTS_DIR / "dynamic_results.json").read_text(encoding="utf-8"))
    trials = data["chain_trials"]

    print(f"{'Policy':<8} {'Trial':>5} {'CenterPx':>16} {'InsideSend(tol=0)':>18} {'InsideSend(tol=5)':>18}")
    corrected_summary: dict[str, dict] = {}
    for row in trials:
        policy = row["policy"]
        bbox = row["full_screen_send_bbox"]
        if bbox:
            y_min, x_min, y_max, x_max = bbox
            cx = round((x_min + x_max) / 2 / 1000 * FULL_WIDTH)
            cy = round((y_min + y_max) / 2 / 1000 * FULL_HEIGHT)
            center_str = f"({cx},{cy})"
        else:
            center_str = "n/a"
        hit0 = click_center_inside_send_button(bbox, tolerance_px=0)
        hit5 = click_center_inside_send_button(bbox, tolerance_px=5)
        print(f"{policy:<8} {row['stage1_trial']:>5} {center_str:>16} {str(hit0):>18} {str(hit5):>18}")

        s = corrected_summary.setdefault(policy, {"n": 0, "hit_tol0": 0, "hit_tol5": 0, "hit_tol10": 0})
        s["n"] += 1
        s["hit_tol0"] += 1 if hit0 else 0
        s["hit_tol5"] += 1 if hit5 else 0
        s["hit_tol10"] += 1 if click_center_inside_send_button(bbox, tolerance_px=10) else 0

    print("\n=== CORRECTED SUMMARY (click-center inside the REAL Send button's own bounds) ===")
    for policy, s in corrected_summary.items():
        print(
            f"policy={policy}: {s['hit_tol0']}/{s['n']} (tol=0px)  "
            f"{s['hit_tol5']}/{s['n']} (tol=5px)  {s['hit_tol10']}/{s['n']} (tol=10px)"
        )

    (RESULTS_DIR / "corrected_summary.json").write_text(
        json.dumps(corrected_summary, indent=2), encoding="utf-8",
    )
    print(f"\nWritten to {RESULTS_DIR / 'corrected_summary.json'}")


if __name__ == "__main__":
    main()
