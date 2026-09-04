"""RND-002 annotated-preview generator.

Draws the manually-recorded ground-truth bounding boxes from the dataset
manifest onto a *copy* of each captured raw screenshot, for human visual
verification. Writes copies to screenshots/annotated/ — the raw screenshot
under screenshots/raw/ is never modified.

No AI, no OCR, no detection — this only draws boxes/labels that already
exist in the manifest (placed by a human via annotate_ground_truth.py).

Vision AI experiments must always read from screenshots/raw/, never from
screenshots/annotated/, since the annotated copy visually reveals the
answer.

Run (from outlook-vision-poc/):
    python rnd/experiments/generate_annotated_previews.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = PROJECT_ROOT / "test_cases" / "outlook" / "dataset_manifest.json"
RAW_DIR = PROJECT_ROOT / "screenshots" / "raw"
ANNOTATED_DIR = PROJECT_ROOT / "screenshots" / "annotated"

sys.path.insert(0, str(PROJECT_ROOT))
from rnd.models.dataset_manifest import DatasetManifest  # noqa: E402

BOX_COLOR = (255, 0, 0)
LABEL_BG = (255, 0, 0)
LABEL_FG = (255, 255, 255)


def generate() -> list[str]:
    manifest = DatasetManifest.model_validate(json.loads(MANIFEST_PATH.read_text(encoding="utf-8")))
    ANNOTATED_DIR.mkdir(parents=True, exist_ok=True)

    written: list[str] = []
    for case in manifest.cases:
        if case.status != "captured" or not case.filename:
            continue

        raw_path = RAW_DIR / case.filename
        if not raw_path.exists():
            continue

        img = Image.open(raw_path).convert("RGB")
        draw = ImageDraw.Draw(img)

        for target in case.targets:
            b = target.bbox
            draw.rectangle([b.x1, b.y1, b.x2, b.y2], outline=BOX_COLOR, width=3)
            label = target.name
            text_pos = (b.x1, max(0, b.y1 - 16))
            text_bbox = draw.textbbox(text_pos, label)
            draw.rectangle(text_bbox, fill=LABEL_BG)
            draw.text(text_pos, label, fill=LABEL_FG)

        header = f"{case.test_id}  |  {case.expected.state if case.expected else 'unknown'}"
        draw.rectangle([0, 0, draw.textlength(header) + 10, 18], fill=(0, 0, 0))
        draw.text((5, 2), header, fill=(255, 255, 255))

        out_path = ANNOTATED_DIR / f"{case.test_id.lower()}_annotated.png"
        img.save(out_path, "PNG")
        written.append(str(out_path))

    return written


def main() -> None:
    written = generate()
    if not written:
        print("No captured cases with targets found — nothing to annotate yet.")
        return
    print(f"Wrote {len(written)} annotated preview(s):")
    for p in written:
        print(f"  {p}")


if __name__ == "__main__":
    main()
