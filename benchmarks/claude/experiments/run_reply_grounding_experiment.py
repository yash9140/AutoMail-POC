"""Static, Claude-only, non-physical Reply-control spatial-grounding
benchmark (2026-09-06).

Live evidence: a run correctly opened the target email, correctly read
it, and Claude's REPLY_SEARCH call returned a schema-valid response with
no Gemini fallback — yet the physical cursor moved to a point that was
NOT the real Reply control, so the Reply editor never opened
(REPLY_EDITOR_NOT_OPEN). This mirrors the earlier TARGET_EMAIL_SEARCH
row-confusion investigation exactly: a semantically-plausible answer
whose BBOX may not be spatially reliable.

This script isolates REPLY_SEARCH's grounding call against the EXACT
screenshot from that failing moment — Claude only, no Gemini fallback,
no Outlook launch, no PyAutoGUI, no physical action of any kind — across
three input variants (FULL_SCREEN / READING_PANE_CROP /
REPLY_REGION_CROP), 5 trials each, to determine whether cropping the
reading pane (mirroring the message-list crop fix that already resolved
TARGET_EMAIL_SEARCH) improves Reply-control bbox grounding.

Screenshot identification: screenshots/raw/screen_20260906_165113_787.png
— byte-identical to two later captures (...165052_439.png and
...165057_435.png as well), i.e. the screen was static across that whole
window, and it is the closest capture strictly BEFORE the live log's
"VISION_CALL_START provider=anthropic stage=REPLY_SEARCH" at
2026-09-06 16:51:20.802 (this file's own timestamp: 16:51:13.787, ~7.0s
before — consistent with prepare_reply_editor()'s own
_check_reply_editor_open() call, on this SAME capture, running first).

Ground-truth candidate positions below are EVALUATION-ONLY, derived by
directly measuring pixel coordinates in THIS one frozen screenshot
(gridded-crop visual inspection) — never used as runtime truth, never
fed back into app/outlook/reply.py. Outlook showed THREE separate
reply-related surfaces simultaneously in this screenshot:
  1. a ribbon "Reply all" button (top toolbar, Respond group)
  2. three small icon-only controls next to the message header
     (reply / reply-all / forward, left to right)
  3. two labeled buttons below the message body ("Reply", "Forward")
Only the header icon's reply and the bottom labeled "Reply" button are
genuine Reply targets; the ribbon button is Reply All, and the other
two are Reply All / Forward respectively.

Never stores/prints raw email body text — only structural/positional
facts, consistent with every other benchmark in this directory.
"""

from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from app.vision.providers.anthropic_provider import AnthropicProvider  # noqa: E402

SOURCE_SCREENSHOT = PROJECT_ROOT / "screenshots" / "raw" / "screen_20260906_165113_787.png"
PROMPT_PATH = Path(__file__).resolve().parent / "reply_grounding_prompt.txt"
RESULTS_DIR = Path(__file__).resolve().parent / "reply_grounding_results"
TRIALS_PER_VARIANT = 5
STAGE = "REPLY_SEARCH_BENCHMARK"

VARIANTS = ("FULL_SCREEN", "READING_PANE_CROP", "REPLY_REGION_CROP")

# --- Evaluation-only ground truth (see module docstring) — normalized
# (y_center, x_center) in FULL-SCREEN 0-1000 space, derived from direct
# pixel measurement of the one frozen screenshot above. ---
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


def _crop_bounds_for_variant(variant: str, width: int, height: int) -> Optional[tuple[int, int, int, int]]:
    """Returns (left, top, right, bottom) native pixel bounds, or None
    for FULL_SCREEN. Evaluation-only crop bounds chosen by visually
    inspecting THIS one frozen screenshot (see module docstring) — never
    used as runtime truth, never fed back into app/outlook/reply.py."""
    if variant == "FULL_SCREEN":
        return None
    if variant == "READING_PANE_CROP":
        # Horizontal crop only, full vertical height retained — same
        # strategy that resolved TARGET_EMAIL_SEARCH's row-bbox grounding.
        # Left bound (755px) is this screenshot's own message-list/
        # reading-pane divider, measured directly (gridded-crop visual
        # inspection), not the unrelated MESSAGE_LIST_RIGHT_MAX_X_FRACTION
        # constant (a different boundary, for a different pane, measured
        # from a different screenshot).
        return (755, 0, width, height)
    if variant == "REPLY_REGION_CROP":
        # Tighter crop around the bottom Reply/Forward button pair, with
        # signature-line context above — deliberately NOT a single-button,
        # no-context crop (the earlier find-email benchmark showed
        # over-tight crops can make grounding worse, not better).
        return (800, 550, 1250, 780)
    raise ValueError(f"Unknown variant: {variant}")


def _remap_bbox_to_full_screen(
    bbox_normalized: list[float], crop_box: Optional[tuple[int, int, int, int]], full_width: int, full_height: int,
) -> list[float]:
    if crop_box is None:
        return list(bbox_normalized)
    left, top, right, bottom = crop_box
    crop_w, crop_h = right - left, bottom - top
    y_min, x_min, y_max, x_max = bbox_normalized

    def _convert(y: float, x: float) -> tuple[float, float]:
        full_px_x = x / 1000 * crop_w + left
        full_px_y = y / 1000 * crop_h + top
        return full_px_y / full_height * 1000, full_px_x / full_width * 1000

    y_min_f, x_min_f = _convert(y_min, x_min)
    y_max_f, x_max_f = _convert(y_max, x_max)
    return [y_min_f, x_min_f, y_max_f, x_max_f]


def _nearest_candidate(center: tuple[float, float]) -> str:
    """Nearest-neighbor classification against GROUND_TRUTH_CANDIDATES —
    used instead of fixed-tolerance bboxes because the real controls
    (Reply / Reply All / Forward) sit close together, so a simple
    "inside an expected box" check would not reliably discriminate them."""
    cy, cx = center
    best_name, best_dist = None, math.inf
    for name, info in GROUND_TRUTH_CANDIDATES.items():
        gy, gx = info["center"]
        dist = math.hypot(cy - gy, cx - gx)
        if dist < best_dist:
            best_name, best_dist = name, dist
    return best_name


def _save_debug_overlay(image_path: Path, candidates: list[dict], exact_reply_index: Optional[int], out_path: Path) -> None:
    try:
        from PIL import Image, ImageDraw

        with Image.open(image_path) as img:
            img = img.convert("RGB")
            draw = ImageDraw.Draw(img)
            width, height = img.size
            for c in candidates:
                bbox = c.get("full_screen_bbox")
                if not bbox or len(bbox) != 4:
                    continue
                y_min, x_min, y_max, x_max = bbox
                px = [x_min / 1000 * width, y_min / 1000 * height, x_max / 1000 * width, y_max / 1000 * height]
                color = "lime" if c["index"] == exact_reply_index else "red"
                draw.rectangle(px, outline=color, width=3)
                label = f"#{c['index']}:{c['control_type']}"
                draw.text((px[0] + 2, max(0, px[1] - 12)), label, fill=color)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            img.save(out_path)
    except Exception:  # noqa: BLE001 — diagnostic only
        pass


def run_trial(
    provider: AnthropicProvider, image_path: Path, prompt_text: str, variant: str, trial: int,
    crop_box: Optional[tuple[int, int, int, int]], full_width: int, full_height: int,
) -> dict:
    start = time.perf_counter()
    result = provider.analyze_screen(image_path, "Enumerate reply-related controls", prompt_text, stage=STAGE)
    latency_ms = (time.perf_counter() - start) * 1000

    schema_valid = False
    parsed: Optional[ReplyGroundingDiagnosticResponse] = None
    if result.parsed_json is not None:
        try:
            parsed = ReplyGroundingDiagnosticResponse.model_validate(result.parsed_json)
            schema_valid = True
        except Exception:  # noqa: BLE001
            schema_valid = False

    row = {
        "variant": variant, "trial": trial, "schema_valid": schema_valid,
        "stop_reason": result.stop_reason, "output_tokens": result.output_tokens,
        "response_char_count": len(result.raw_text), "latency_ms": round(latency_ms, 1),
        "num_candidates": 0, "semantic_correct": False, "bbox_correct": False,
        "nearest_candidate": None, "full_screen_bbox": None,
    }
    candidates_for_overlay = []
    if schema_valid and parsed is not None:
        row["num_candidates"] = len(parsed.candidates)
        selected = None
        for c in parsed.candidates:
            full_bbox = None
            if c.bbox is not None and len(c.bbox) == 4:
                full_bbox = _remap_bbox_to_full_screen(c.bbox, crop_box, full_width, full_height)
            candidates_for_overlay.append({
                "index": c.index, "control_type": c.control_type, "full_screen_bbox": full_bbox,
            })
            if parsed.exact_reply_index is not None and c.index == parsed.exact_reply_index:
                selected = (c, full_bbox)

        if selected is not None:
            candidate, full_bbox = selected
            row["semantic_correct"] = candidate.control_type == "reply"
            if full_bbox is not None:
                center = ((full_bbox[0] + full_bbox[2]) / 2, (full_bbox[1] + full_bbox[3]) / 2)
                nearest = _nearest_candidate(center)
                row["nearest_candidate"] = nearest
                row["bbox_correct"] = GROUND_TRUTH_CANDIDATES[nearest]["is_reply"]
                row["full_screen_bbox"] = [round(v, 1) for v in full_bbox]

    print(
        f"variant={variant:<18} trial={trial} schema_valid={schema_valid} "
        f"num_candidates={row['num_candidates']} semantic_correct={row['semantic_correct']} "
        f"bbox_correct={row['bbox_correct']} nearest={row['nearest_candidate']} latency_ms={row['latency_ms']}"
    )

    if candidates_for_overlay:
        out_path = RESULTS_DIR / f"{variant}_trial{trial}_overlay.png"
        _save_debug_overlay(SOURCE_SCREENSHOT, candidates_for_overlay, parsed.exact_reply_index if parsed else None, out_path)

    return row


def main() -> None:
    if not SOURCE_SCREENSHOT.exists():
        print(f"Source screenshot not found: {SOURCE_SCREENSHOT}")
        return

    load_dotenv(PROJECT_ROOT / ".env")
    import os

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    model = os.environ.get("ANTHROPIC_MODEL", "")
    if not api_key or not model:
        print("ANTHROPIC_API_KEY / ANTHROPIC_MODEL not set in .env — cannot run live Claude calls.")
        return

    provider = AnthropicProvider(api_key=api_key, model=model, timeout_seconds=60.0)

    from PIL import Image

    with Image.open(SOURCE_SCREENSHOT) as img:
        full_width, full_height = img.size
    print(f"Source screenshot: {SOURCE_SCREENSHOT} ({full_width}x{full_height})")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    prompt_template = PROMPT_PATH.read_text(encoding="utf-8")

    all_rows: list[dict] = []
    for variant in VARIANTS:
        crop_box = _crop_bounds_for_variant(variant, full_width, full_height)
        if crop_box is None:
            image_for_call = SOURCE_SCREENSHOT
            call_width, call_height = full_width, full_height
        else:
            left, top, right, bottom = crop_box
            with Image.open(SOURCE_SCREENSHOT) as img:
                cropped = img.convert("RGB").crop((left, top, right, bottom))
                image_for_call = RESULTS_DIR / f"{variant}_input.png"
                cropped.save(image_for_call)
            call_width, call_height = right - left, bottom - top
            print(f"{variant}: crop_bounds={crop_box} crop_size={call_width}x{call_height}")

        prompt_text = prompt_template.format(width=call_width, height=call_height)
        for trial in range(1, TRIALS_PER_VARIANT + 1):
            row = run_trial(provider, image_for_call, prompt_text, variant, trial, crop_box, full_width, full_height)
            all_rows.append(row)

    print("\n=== SUMMARY ===")
    print(f"{'Variant':<20} {'SemanticOK':>10} {'BBoxOK':>8} {'AvgLatencyMs':>14}")
    summary = {}
    for variant in VARIANTS:
        rows = [r for r in all_rows if r["variant"] == variant]
        semantic_ok = sum(1 for r in rows if r["semantic_correct"])
        bbox_ok = sum(1 for r in rows if r["bbox_correct"])
        avg_latency = round(sum(r["latency_ms"] for r in rows) / len(rows), 1)
        nearest_values = [r["nearest_candidate"] for r in rows]
        stability = "stable" if len(set(nearest_values)) <= 1 else f"varies({nearest_values})"
        summary[variant] = {
            "semantic_correct": semantic_ok, "bbox_correct": bbox_ok, "n": len(rows),
            "avg_latency_ms": avg_latency, "bbox_stability": stability,
        }
        print(f"{variant:<20} {semantic_ok}/{len(rows):<8} {bbox_ok}/{len(rows):<6} {avg_latency:>14}")
        print(f"    bbox_stability: {stability}")

    (RESULTS_DIR / "results.json").write_text(
        json.dumps({"source_screenshot": str(SOURCE_SCREENSHOT), "trials": all_rows, "summary": summary}, indent=2),
        encoding="utf-8",
    )
    print(f"\nWritten to {RESULTS_DIR / 'results.json'}")


if __name__ == "__main__":
    main()
