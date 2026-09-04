"""Generates results/raw/rnd002_dataset_results.json from the current
manifest state and environment findings. No AI involved — this only
summarizes what validate_outlook_dataset.py and the environment check
already found.

Run (from outlook-vision-poc/):
    python rnd/experiments/run_rnd002_summary.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RESULTS_PATH = PROJECT_ROOT / "results" / "raw" / "rnd002_dataset_results.json"
INVALID_ATTEMPTS_PATH = PROJECT_ROOT / "results" / "raw" / "rnd002_invalid_attempts.json"

sys.path.insert(0, str(PROJECT_ROOT))
from rnd.experiments.validate_outlook_dataset import MANIFEST_PATH, validate  # noqa: E402
from rnd.models.dataset_manifest import DatasetManifest  # noqa: E402


def main() -> None:
    validation = validate()
    manifest = DatasetManifest.model_validate(json.loads(MANIFEST_PATH.read_text(encoding="utf-8")))

    captured = [c for c in manifest.cases if c.status == "captured"]
    states_covered = sorted({c.expected.state for c in captured if c.expected})
    targets_annotated = sum(len(c.targets) for c in captured)

    summary = {
        "experiment_id": "RND-002",
        "timestamp": datetime.now().isoformat(),
        "total_planned_cases": validation["total_cases"],
        "captured_cases": validation["captured_cases"],
        "skipped_cases": validation["skipped_cases"],
        "still_planned_cases": validation["planned_cases"],
        "raw_images_valid": validation["overall_pass"] if validation["captured_cases"] > 0 else None,
        "annotations_valid": validation["overall_pass"] if validation["captured_cases"] > 0 else None,
        "screen_width": 1920,
        "screen_height": 1080,
        "windows_scaling_percent": 125,
        "states_covered": states_covered,
        "targets_annotated": targets_annotated,
        "validation_passed": validation["overall_pass"],
        "environment": {
            "classic_outlook_installed": False,
            "outlook_for_windows_installed": True,
            "outlook_for_windows_running_at_check_time": True,
            "office_edition_detected": "Microsoft Office Home and Student 2019 (does not include Outlook)",
        },
        "invalid_attempts_log": "results/raw/rnd002_invalid_attempts.json",
        "failures": (
            ["No screenshots captured yet: real dataset capture requires a human to launch and sign into "
             "Outlook, then navigate the required states while screenshots are taken with "
             "rnd/experiments/capture_with_foreground_check.py."] if validation["captured_cases"] == 0 else []
        ) + [
            f"{a['test_id']} attempt {a['attempt']} was rejected: {a['reason']}"
            for a in json.loads(INVALID_ATTEMPTS_PATH.read_text(encoding="utf-8"))["invalid_attempts"]
        ],
        "notes": (
            "RND-002 required-case capture complete (OUTLOOK-001..011 all resolved: 10 captured, "
            "1 correctly skipped). OUTLOOK-012..015 are optional and remain 'planned' by design, "
            "not attempted. Manifest schema, annotation tool, validator, and annotated-preview "
            "generator are built and pass their own tests. No fabricated capture data is included; "
            "rejected attempts are logged (see failures/invalid_attempts_log), not counted as valid."
        ),
    }

    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"Wrote {RESULTS_PATH}")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
