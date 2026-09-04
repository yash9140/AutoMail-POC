"""RND-005A — Coordinate System Calibration.

Local-only analysis. Makes NO new API calls. Reads the exact raw
predictions, bounding boxes, and image filenames already stored in
results/raw/rnd005_ui_grounding_results.json (RND-005's original result —
untouched by this script), re-derives image dimensions from the actual
source PNG files, and tests two interpretations of Gemini's returned
(x, y) values:

    A. Native pixel coordinates (RND-005's original assumption)
    B. Coordinates normalized to Google's documented 0-1000 Gemini
       spatial-output convention (see docs/07A_Coordinate_Calibration.md)

Also re-checks RND-003's independent Reply prediction ((478, 630), from a
completely different, non-grounding-isolated prompt) against the same two
interpretations, since it happens to share the same raw values as RND-005's
Reply case.

Run (from outlook-vision-poc/):
    python rnd/experiments/rnd005a_coordinate_calibration.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from rnd.metrics.coordinate_calibration import normalize_1000_to_pixels  # noqa: E402
from rnd.metrics.grounding import point_in_bbox  # noqa: E402
from rnd.models.dataset_manifest import BoundingBox  # noqa: E402

RND005_RESULTS_PATH = PROJECT_ROOT / "results" / "raw" / "rnd005_ui_grounding_results.json"
RAW_DIR = PROJECT_ROOT / "screenshots" / "raw"
RESULTS_PATH = PROJECT_ROOT / "results" / "raw" / "rnd005a_coordinate_calibration_results.json"
REPORT_PATH = PROJECT_ROOT / "results" / "reports" / "rnd005a_coordinate_calibration_summary.md"

# RND-003's independent prediction for the same OUTLOOK-003/Reply target,
# from a completely different (non-grounding-isolated) prompt. Recorded
# here as a constant for cross-checking only — this is the exact value
# already documented in docs/05_First_Vision_Provider_Integration.md and
# results/raw/rnd003_first_provider_result.json; not re-derived, not altered.
RND003_REPLY_PREDICTION = {"test_id": "RND-003-OUTLOOK-003", "target": "Reply", "raw_x": 478, "raw_y": 630}


def analyze_case(case: dict) -> dict:
    bbox = BoundingBox(**case["bbox"])
    with Image.open(RAW_DIR / case["image_filename"]) as img:
        actual_width, actual_height = img.size

    raw_x, raw_y = case["predicted_x"], case["predicted_y"]

    native_result = "PASS" if point_in_bbox(raw_x, raw_y, bbox) else "FAIL"

    norm_x, norm_y = normalize_1000_to_pixels(raw_x, raw_y, actual_width, actual_height)
    norm_x_r, norm_y_r = round(norm_x), round(norm_y)
    normalized_result = "PASS" if point_in_bbox(norm_x_r, norm_y_r, bbox) else "FAIL"

    return {
        "test_id": case["test_id"],
        "target": case["target"],
        "image_filename": case["image_filename"],
        "image_width_from_source_file": actual_width,
        "image_height_from_source_file": actual_height,
        "bbox": case["bbox"],
        "raw_prediction": {"x": raw_x, "y": raw_y},
        "native_pixel_interpretation": {"x": raw_x, "y": raw_y, "result": native_result},
        "normalized_1000_interpretation": {
            "converted_x": round(norm_x, 2),
            "converted_y": round(norm_y, 2),
            "converted_x_rounded": norm_x_r,
            "converted_y_rounded": norm_y_r,
            "result": normalized_result,
        },
    }


def build_report(rows: list[dict], rnd003_row: dict) -> str:
    native_pass = sum(1 for r in rows if r["native_pixel_interpretation"]["result"] == "PASS")
    normalized_pass = sum(1 for r in rows if r["normalized_1000_interpretation"]["result"] == "PASS")

    lines = ["# RND-005A Coordinate System Calibration — Summary", ""]
    lines.append(
        "Local-only re-analysis of RND-005's existing raw predictions. No new "
        "API calls were made. RND-005's original result file "
        "(results/raw/rnd005_ui_grounding_results.json) is unmodified."
    )
    lines.append("")
    lines.append("| Target | Raw | Native Pixel Result | Normalized Converted | Normalized Result |")
    lines.append("|---|---|---|---|---|")
    for r in rows:
        n = r["normalized_1000_interpretation"]
        lines.append(
            f"| {r['target']} | ({r['raw_prediction']['x']},{r['raw_prediction']['y']}) | "
            f"{r['native_pixel_interpretation']['result']} | "
            f"({n['converted_x']},{n['converted_y']}) | {n['result']} |"
        )
    lines.append("")
    lines.append(f"Native interpretation: {native_pass}/4")
    lines.append(f"Normalized 0-1000 interpretation: {normalized_pass}/4")
    lines.append("")

    n3 = rnd003_row["normalized_1000_interpretation"]
    lines.append("## RND-003 independent Reply prediction, cross-checked")
    lines.append(
        f"- Raw (from RND-003, different prompt): ({rnd003_row['raw_prediction']['x']},{rnd003_row['raw_prediction']['y']})"
    )
    lines.append(f"- Native pixel result: {rnd003_row['native_pixel_interpretation']['result']}")
    lines.append(f"- Normalized converted: ({n3['converted_x']},{n3['converted_y']}) -> {n3['result']}")
    lines.append("")
    lines.append(
        "This value is identical to RND-005's own Reply raw prediction, so this "
        "check is not statistically independent evidence — it is reported for "
        "completeness and traceability, not as a second data point."
    )

    return "\n".join(lines) + "\n"


def main() -> None:
    rnd005_data = json.loads(RND005_RESULTS_PATH.read_text(encoding="utf-8"))
    rows = [analyze_case(case) for case in rnd005_data["cases"]]

    reply_case = next(c for c in rnd005_data["cases"] if c["target"] == "Reply")
    rnd003_pseudo_case = {
        "test_id": RND003_REPLY_PREDICTION["test_id"],
        "target": RND003_REPLY_PREDICTION["target"],
        "image_filename": reply_case["image_filename"],
        "bbox": reply_case["bbox"],
        "predicted_x": RND003_REPLY_PREDICTION["raw_x"],
        "predicted_y": RND003_REPLY_PREDICTION["raw_y"],
    }
    rnd003_row = analyze_case(rnd003_pseudo_case)

    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(
        json.dumps(
            {
                "experiment_id": "RND-005A",
                "source_file": "results/raw/rnd005_ui_grounding_results.json",
                "source_file_modified": False,
                "new_api_calls_made": 0,
                "cases": rows,
                "rnd003_cross_check": rnd003_row,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(build_report(rows, rnd003_row), encoding="utf-8")

    print(build_report(rows, rnd003_row))
    print(f"Results written to {RESULTS_PATH.relative_to(PROJECT_ROOT)}")
    print(f"Report written to {REPORT_PATH.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
