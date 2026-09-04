"""RND-002 dataset validation.

Validates the Outlook screenshot dataset manifest (test_cases/outlook/dataset_manifest.json)
and the raw screenshots it references, using only local file/schema checks —
no AI involved.

Checks performed:
    - Manifest parses against the DatasetManifest schema (unique test IDs,
      valid state/action vocabulary, well-formed bounding boxes, etc. — see
      rnd/models/dataset_manifest.py)
    - Every "captured" case's screenshot file exists under screenshots/raw/
    - Every such screenshot opens successfully with Pillow
    - Image dimensions match the manifest's recorded width/height
    - Every target bounding box lies fully inside the image boundaries
    - The referenced file lives under screenshots/raw/, never screenshots/annotated/

Run (from outlook-vision-poc/):
    python rnd/experiments/validate_outlook_dataset.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from PIL import Image
from pydantic import ValidationError

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = PROJECT_ROOT / "test_cases" / "outlook" / "dataset_manifest.json"
RAW_DIR = PROJECT_ROOT / "screenshots" / "raw"
ANNOTATED_DIR = PROJECT_ROOT / "screenshots" / "annotated"

sys.path.insert(0, str(PROJECT_ROOT))
from rnd.models.dataset_manifest import DatasetManifest  # noqa: E402


def validate(manifest_path: Path = MANIFEST_PATH, raw_dir: Path = RAW_DIR, annotated_dir: Path = ANNOTATED_DIR) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []

    if not manifest_path.exists():
        return {
            "manifest_parses": False,
            "cases": [],
            "overall_pass": False,
            "error": f"manifest not found at {manifest_path}",
        }

    raw_json = json.loads(manifest_path.read_text(encoding="utf-8"))

    try:
        manifest = DatasetManifest.model_validate(raw_json)
        manifest_parses = True
        schema_error = None
    except ValidationError as exc:
        manifest_parses = False
        schema_error = str(exc)
        manifest = None

    if not manifest_parses:
        return {
            "manifest_parses": False,
            "schema_error": schema_error,
            "cases": [],
            "overall_pass": False,
        }

    for case in manifest.cases:
        case_result: dict[str, Any] = {"test_id": case.test_id, "status": case.status, "checks": {}, "pass": True}

        if case.status != "captured":
            case_result["checks"]["skipped_validation"] = f"status is '{case.status}', file checks not applicable"
            findings.append(case_result)
            continue

        file_path = raw_dir / case.filename
        exists = file_path.exists()
        case_result["checks"]["file_exists"] = exists
        if not exists:
            case_result["pass"] = False
            findings.append(case_result)
            continue

        under_raw = raw_dir.resolve() in file_path.resolve().parents
        under_annotated = annotated_dir.resolve() in file_path.resolve().parents
        case_result["checks"]["is_under_raw_not_annotated"] = under_raw and not under_annotated
        if not (under_raw and not under_annotated):
            case_result["pass"] = False

        try:
            with Image.open(file_path) as img:
                img.verify()
            with Image.open(file_path) as img:
                width, height = img.size
            case_result["checks"]["image_opens"] = True
        except Exception as exc:
            case_result["checks"]["image_opens"] = False
            case_result["checks"]["image_open_error"] = str(exc)
            case_result["pass"] = False
            findings.append(case_result)
            continue

        dims_match = case.screen is not None and width == case.screen.width and height == case.screen.height
        case_result["checks"]["dimensions_match"] = dims_match
        case_result["checks"]["recorded_dimensions"] = (
            [case.screen.width, case.screen.height] if case.screen else None
        )
        case_result["checks"]["actual_dimensions"] = [width, height]
        if not dims_match:
            case_result["pass"] = False

        bbox_checks = []
        for target in case.targets:
            b = target.bbox
            inside = (0 <= b.x1 < width) and (0 <= b.x2 <= width) and (0 <= b.y1 < height) and (0 <= b.y2 <= height)
            bbox_checks.append({"target": target.name, "inside_image_bounds": inside})
            if not inside:
                case_result["pass"] = False
        case_result["checks"]["bounding_boxes"] = bbox_checks

        findings.append(case_result)

    overall_pass = all(f["pass"] for f in findings)
    return {
        "manifest_parses": True,
        "total_cases": len(manifest.cases),
        "captured_cases": sum(1 for c in manifest.cases if c.status == "captured"),
        "planned_cases": sum(1 for c in manifest.cases if c.status == "planned"),
        "skipped_cases": sum(1 for c in manifest.cases if c.status == "skipped"),
        "cases": findings,
        "overall_pass": overall_pass,
    }


def main() -> None:
    result = validate()
    print(json.dumps(result, indent=2))
    print()
    if not result["manifest_parses"]:
        print(f"MANIFEST SCHEMA VALIDATION FAILED: {result.get('schema_error') or result.get('error')}")
        raise SystemExit(1)

    print(
        f"Cases: {result['total_cases']} total | "
        f"{result['captured_cases']} captured | "
        f"{result['planned_cases']} planned | "
        f"{result['skipped_cases']} skipped"
    )
    print(f"Overall dataset validation: {'PASS' if result['overall_pass'] else 'FAIL'}")
    if not result["overall_pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
