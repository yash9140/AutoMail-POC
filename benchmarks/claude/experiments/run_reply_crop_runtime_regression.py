"""Static runtime-crop regression check (2026-09-06) — run AFTER wiring
REPLY_SEARCH's crop into app/outlook/reply.py, to prove the actual
runtime crop utility (app.vision.crop.create_reply_vision_crop) and the
actual calibrated constant (app.config.settings.REPLY_VISION_CROP_LEFT_
FRACTION) reproduce the same result the benchmark predicted — not a
reimplementation of the crop math, the REAL production functions.

Claude only, no Gemini fallback, no Outlook launch, no PyAutoGUI, no
physical action of any kind. Uses the same frozen screenshot and the
same evaluation-only ground truth as run_reply_grounding_experiment.py.
"""

from __future__ import annotations

import math
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402
from typing import Optional  # noqa: E402

from app.config.settings import REPLY_VISION_CROP_LEFT_FRACTION  # noqa: E402
from app.vision.crop import create_reply_vision_crop  # noqa: E402
from app.vision.providers.anthropic_provider import AnthropicProvider  # noqa: E402

SOURCE_SCREENSHOT = PROJECT_ROOT / "screenshots" / "raw" / "screen_20260906_165113_787.png"
PROMPT_PATH = Path(__file__).resolve().parent / "reply_grounding_prompt.txt"
TRIALS = 5
STAGE = "REPLY_SEARCH_RUNTIME_REGRESSION"

GROUND_TRUTH_CANDIDATES: dict[str, dict] = {
    "ribbon_reply_all": {"center": (149.5, 190.1), "is_reply": False},
    "header_reply": {"center": (312.5, 895.5), "is_reply": True},
    "header_reply_all": {"center": (312.5, 916.0), "is_reply": False},
    "header_forward": {"center": (312.5, 937.2), "is_reply": False},
    "bottom_reply": {"center": (650.9, 477.6), "is_reply": True},
    "bottom_forward": {"center": (650.9, 540.9), "is_reply": False},
}


class ReplyCandidate(BaseModel):
    index: int = 0
    visible_label: str = ""
    control_type: str = "other"
    bbox: Optional[list[float]] = None
    confidence: float = 0.0


class ReplyGroundingDiagnosticResponse(BaseModel):
    candidates: list[ReplyCandidate] = Field(default_factory=list)
    exact_reply_index: Optional[int] = None
    reason: str = ""


def _nearest_candidate_distance(center):
    cy, cx = center
    best_name, best_dist = None, math.inf
    for name, info in GROUND_TRUTH_CANDIDATES.items():
        gy, gx = info["center"]
        dist = math.hypot(cy - gy, cx - gx)
        if dist < best_dist:
            best_name, best_dist = name, dist
    return best_name, best_dist


def main() -> None:
    if not SOURCE_SCREENSHOT.exists():
        print(f"Source screenshot not found: {SOURCE_SCREENSHOT}")
        return

    load_dotenv(PROJECT_ROOT / ".env")
    import os

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    model = os.environ.get("ANTHROPIC_MODEL", "")
    if not api_key or not model:
        print("ANTHROPIC_API_KEY / ANTHROPIC_MODEL not set in .env.")
        return

    provider = AnthropicProvider(api_key=api_key, model=model, timeout_seconds=60.0)

    from PIL import Image

    with Image.open(SOURCE_SCREENSHOT) as img:
        full_width, full_height = img.size

    # THE REAL runtime crop utility + THE REAL calibrated constant.
    crop = create_reply_vision_crop(SOURCE_SCREENSHOT, full_width, full_height, REPLY_VISION_CROP_LEFT_FRACTION)
    print(f"Runtime crop: bounds=({crop.left},{crop.top},{crop.right},{crop.bottom}) "
          f"size={crop.width}x{crop.height} left_fraction={REPLY_VISION_CROP_LEFT_FRACTION}")

    prompt_text = PROMPT_PATH.read_text(encoding="utf-8").format(width=crop.width, height=crop.height)

    semantic_ok = 0
    bbox_ok = 0
    for trial in range(1, TRIALS + 1):
        start = time.perf_counter()
        result = provider.analyze_screen(crop.crop_path, "Enumerate reply-related controls", prompt_text, stage=STAGE)
        latency_ms = (time.perf_counter() - start) * 1000

        if result.parsed_json is None:
            print(f"trial={trial} schema_valid=False")
            continue
        parsed = ReplyGroundingDiagnosticResponse.model_validate(result.parsed_json)
        selected = next((c for c in parsed.candidates if c.index == parsed.exact_reply_index), None)
        if selected is None:
            print(f"trial={trial} schema_valid=True exact_reply_index=None")
            continue

        semantic_correct = selected.control_type == "reply"
        semantic_ok += 1 if semantic_correct else 0

        bbox_correct = False
        nearest, dist = None, None
        if selected.bbox is not None and len(selected.bbox) == 4:
            # THE REAL runtime remap function.
            full_bbox = crop.remap_bbox_to_full_screen(selected.bbox)
            center = ((full_bbox[0] + full_bbox[2]) / 2, (full_bbox[1] + full_bbox[3]) / 2)
            nearest, dist = _nearest_candidate_distance(center)
            bbox_correct = GROUND_TRUTH_CANDIDATES[nearest]["is_reply"] and dist < 100.0
            bbox_ok += 1 if bbox_correct else 0

        print(
            f"trial={trial} schema_valid=True semantic_correct={semantic_correct} "
            f"bbox_correct={bbox_correct} nearest={nearest} distance={dist} latency_ms={round(latency_ms, 1)}"
        )

    print(f"\n=== RUNTIME REGRESSION RESULT: semantic={semantic_ok}/{TRIALS} bbox={bbox_ok}/{TRIALS} ===")
    print("Expected (per calibration + benchmark): semantic 5/5, bbox 5/5.")


if __name__ == "__main__":
    main()
