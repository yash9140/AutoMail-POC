"""Unit tests for the RND-002 dataset schema and validator.

Uses only synthetic fixtures (temp manifests, tiny generated PNGs) — no
real Outlook screenshots are required to run this suite, per the RND-002
requirement that unit tests not depend on manual capture having happened.
"""

import json
import sys
from pathlib import Path

import pytest
from PIL import Image
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rnd.experiments.validate_outlook_dataset import validate  # noqa: E402
from rnd.models.dataset_manifest import BoundingBox, DatasetManifest  # noqa: E402


def test_valid_bounding_box_passes():
    box = BoundingBox(x1=10, y1=10, x2=100, y2=50)
    assert box.x2 > box.x1
    assert box.y2 > box.y1


def test_bounding_box_x1_not_less_than_x2_fails():
    with pytest.raises(ValidationError):
        BoundingBox(x1=100, y1=10, x2=100, y2=50)


def test_bounding_box_y1_not_less_than_y2_fails():
    with pytest.raises(ValidationError):
        BoundingBox(x1=10, y1=50, x2=100, y2=10)


def test_duplicate_test_id_fails():
    with pytest.raises(ValidationError):
        DatasetManifest.model_validate({
            "dataset_id": "x",
            "created": "2026-01-01",
            "cases": [
                {"test_id": "OUTLOOK-001", "status": "planned"},
                {"test_id": "OUTLOOK-001", "status": "planned"},
            ],
        })


def test_captured_case_without_filename_fails():
    with pytest.raises(ValidationError):
        DatasetManifest.model_validate({
            "dataset_id": "x",
            "created": "2026-01-01",
            "cases": [{"test_id": "OUTLOOK-001", "status": "captured"}],
        })


def test_invalid_state_fails():
    with pytest.raises(ValidationError):
        DatasetManifest.model_validate({
            "dataset_id": "x",
            "created": "2026-01-01",
            "cases": [{
                "test_id": "OUTLOOK-001",
                "status": "planned",
                "expected": {"application": "Microsoft Outlook", "state": "not_a_real_state"},
            }],
        })


def _write_manifest(tmp_path: Path, cases: list[dict]) -> Path:
    manifest_path = tmp_path / "dataset_manifest.json"
    manifest_path.write_text(json.dumps({
        "dataset_id": "test",
        "created": "2026-01-01",
        "cases": cases,
    }), encoding="utf-8")
    return manifest_path


def test_validator_fails_on_missing_screenshot_file(tmp_path):
    manifest_path = _write_manifest(tmp_path, [{
        "test_id": "OUTLOOK-001",
        "filename": "does_not_exist.png",
        "status": "captured",
        "screen": {"width": 100, "height": 100, "windows_scaling_percent": 100},
    }])
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    annotated_dir = tmp_path / "annotated"
    annotated_dir.mkdir()

    result = validate(manifest_path=manifest_path, raw_dir=raw_dir, annotated_dir=annotated_dir)
    assert result["manifest_parses"] is True
    assert result["overall_pass"] is False
    assert result["cases"][0]["checks"]["file_exists"] is False


def test_validator_fails_on_dimension_mismatch(tmp_path):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    annotated_dir = tmp_path / "annotated"
    annotated_dir.mkdir()

    img_path = raw_dir / "shot.png"
    Image.new("RGB", (50, 50), color="white").save(img_path)

    manifest_path = _write_manifest(tmp_path, [{
        "test_id": "OUTLOOK-001",
        "filename": "shot.png",
        "status": "captured",
        # Recorded dimensions deliberately wrong vs the actual 50x50 PNG.
        "screen": {"width": 200, "height": 200, "windows_scaling_percent": 100},
    }])

    result = validate(manifest_path=manifest_path, raw_dir=raw_dir, annotated_dir=annotated_dir)
    assert result["overall_pass"] is False
    assert result["cases"][0]["checks"]["dimensions_match"] is False


def test_validator_passes_on_well_formed_captured_case(tmp_path):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    annotated_dir = tmp_path / "annotated"
    annotated_dir.mkdir()

    img_path = raw_dir / "shot.png"
    Image.new("RGB", (200, 150), color="white").save(img_path)

    manifest_path = _write_manifest(tmp_path, [{
        "test_id": "OUTLOOK-003",
        "filename": "shot.png",
        "status": "captured",
        "screen": {"width": 200, "height": 150, "windows_scaling_percent": 100},
        "targets": [{"name": "Reply", "type": "button", "bbox": {"x1": 10, "y1": 10, "x2": 60, "y2": 40}}],
    }])

    result = validate(manifest_path=manifest_path, raw_dir=raw_dir, annotated_dir=annotated_dir)
    assert result["overall_pass"] is True
    assert result["cases"][0]["checks"]["bounding_boxes"] == [{"target": "Reply", "inside_image_bounds": True}]


def test_validator_fails_on_bbox_outside_image_bounds(tmp_path):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    annotated_dir = tmp_path / "annotated"
    annotated_dir.mkdir()

    img_path = raw_dir / "shot.png"
    Image.new("RGB", (100, 100), color="white").save(img_path)

    manifest_path = _write_manifest(tmp_path, [{
        "test_id": "OUTLOOK-003",
        "filename": "shot.png",
        "status": "captured",
        "screen": {"width": 100, "height": 100, "windows_scaling_percent": 100},
        "targets": [{"name": "Reply", "type": "button", "bbox": {"x1": 10, "y1": 10, "x2": 500, "y2": 40}}],
    }])

    result = validate(manifest_path=manifest_path, raw_dir=raw_dir, annotated_dir=annotated_dir)
    assert result["overall_pass"] is False
    assert result["cases"][0]["checks"]["bounding_boxes"][0]["inside_image_bounds"] is False
