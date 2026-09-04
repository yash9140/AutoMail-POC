"""Marks a dataset case "captured" with no bounding-box targets.

For states where the manifest doesn't call for a target annotation (e.g.
OUTLOOK-002 only needs the state/subject/Reply-visibility recorded as
notes, not a bounding box — OUTLOOK-003 is where Reply itself gets
annotated). Reuses the exact same save_case() write path as the
annotation tool, so a captured case always has the same shape regardless
of which tool wrote it.

Run (from outlook-vision-poc/):
    python rnd/experiments/mark_case_captured.py --test-id OUTLOOK-002 --screenshot screenshots/raw/<file>.png
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "rnd" / "experiments"))
from annotate_ground_truth import load_manifest, save_case  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Mark a dataset case captured with no targets")
    parser.add_argument("--test-id", required=True)
    parser.add_argument("--screenshot", required=True)
    args = parser.parse_args()

    screenshot_path = Path(args.screenshot)
    if not screenshot_path.exists():
        print(f"Screenshot not found: {screenshot_path}")
        raise SystemExit(1)

    manifest = load_manifest()
    if not any(c["test_id"] == args.test_id for c in manifest["cases"]):
        print(f"test_id '{args.test_id}' not found in manifest.")
        raise SystemExit(1)

    with Image.open(screenshot_path) as img:
        width, height = img.size

    save_case(args.test_id, screenshot_path, width, height, [])
    print(f"{args.test_id} marked captured (0 targets), filename={screenshot_path.name}, {width}x{height}")


if __name__ == "__main__":
    main()
