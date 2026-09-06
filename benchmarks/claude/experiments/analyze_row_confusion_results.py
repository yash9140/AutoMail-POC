"""Post-hoc analysis for run_row_confusion_experiment.py's results.json
(2026-09-06).

IMPORTANT CORRECTION this script exists to make: the experiment's own
live printed summary ("target_row_correct") only checked whether the
TEXT visible_subject Claude assigned to its own self-reported
matching_row_index started with the target prefix. Visual inspection
of the saved debug overlays (row_confusion_results/*_overlay.png)
showed that metric was misleading — in several trials the DRAWN BBOX
for that row sat over a visually DIFFERENT row than the one whose
subject text was reported, even though the text label was "correct".
This script adds the metric that actually matters for the live bug
under investigation: does the reported bbox's own vertical CENTER fall
within the TRUE target row's vertical span?

Ground truth row bounds below are EVALUATION-ONLY, derived by sampling
pixel colors along a fixed x-column through the avatar-icon circles of
the ONE frozen screenshot this experiment uses (screen_20260906_
143533_116.png) to find each row's vertical center, then using the
observed ~100px uniform row spacing. Never used as runtime truth, never
fed back into app/outlook/*.py — see the parent experiment's own
docstring for that constraint.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

RESULTS_DIR = Path(__file__).resolve().parent / "row_confusion_results"

# Normalized (0-1000) [y_min, y_max] row spans, full-screen space —
# see module docstring for derivation.
GROUND_TRUTH_ROW_SPANS = {
    "Demo of live run": (356, 448),
    "Quick Question Regarding...": (452, 544),  # the actual live target
    "Important Organization-W...": (544, 637),
    "Testing Outlook Desktop L...": (637, 730),
}
TARGET_ROW_SPAN = GROUND_TRUTH_ROW_SPANS["Quick Question Regarding..."]
TOLERANCE = 20  # normalized units (~2% of screen height) — generous rounding slack only


def bbox_spatially_correct(full_screen_bbox, expected_span=TARGET_ROW_SPAN, tolerance=TOLERANCE) -> bool | None:
    if not full_screen_bbox:
        return None
    y_min, _x_min, y_max, _x_max = full_screen_bbox
    center = (y_min + y_max) / 2
    lo, hi = expected_span
    return (lo - tolerance) <= center <= (hi + tolerance)


def main() -> None:
    data = json.loads((RESULTS_DIR / "results.json").read_text(encoding="utf-8"))
    trials = data["trials"]

    print(f"{'Variant':<25} {'Trial':>5} {'YashRows':>8} {'SubjOrderOK':>11} "
          f"{'TextLabelOK':>11} {'BBoxSpatiallyOK':>15} {'Latency(ms)':>12}")
    corrected_summary = {}
    for variant, variant_trials in trials.items():
        spatial_hits = 0
        for i, t in enumerate(variant_trials, 1):
            spatial_ok = bbox_spatially_correct(t.get("full_screen_bbox"))
            if spatial_ok:
                spatial_hits += 1
            subj_order_ok = t["num_yash_rows"] == 4  # all 4 real Yash rows enumerated
            print(f"{variant:<25} {i:>5} {t['num_yash_rows']:>8} {str(subj_order_ok):>11} "
                  f"{str(t['target_row_correct']):>11} {str(spatial_ok):>15} {t['latency_ms']:>12}")
        corrected_summary[variant] = {
            "text_label_correct": sum(1 for t in variant_trials if t["target_row_correct"]),
            "bbox_spatially_correct": spatial_hits,
            "n": len(variant_trials),
            "avg_latency_ms": round(sum(t["latency_ms"] for t in variant_trials) / len(variant_trials), 1),
        }

    print("\n=== CORRECTED SUMMARY (bbox spatial accuracy, not just text label) ===")
    for variant, s in corrected_summary.items():
        print(f"{variant}: text_label_correct={s['text_label_correct']}/{s['n']} | "
              f"bbox_spatially_correct={s['bbox_spatially_correct']}/{s['n']} | "
              f"avg_latency_ms={s['avg_latency_ms']}")

    (RESULTS_DIR / "corrected_summary.json").write_text(
        json.dumps(corrected_summary, indent=2), encoding="utf-8",
    )
    print(f"\nWritten to {RESULTS_DIR / 'corrected_summary.json'}")


if __name__ == "__main__":
    main()
