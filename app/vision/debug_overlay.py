"""Diagnostic-only grounding overlay artifacts.

Draws the bbox Vision returned and the calculated click point onto a
COPY of the originating screenshot and saves it under debug/ — for
human visual review during live debugging ONLY. This module is never
imported by any decision path: the runtime click always uses the
validated coordinates computed in app/outlook/*.py directly, never
anything derived from or dependent on this file. Disabled by default;
enabled only via the OUTLOOK_GROUNDING_DEBUG=1 environment variable
(see app/outlook/launch.py).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEBUG_OUTPUT_DIR = PROJECT_ROOT / "debug"


def save_grounding_debug_artifact(
    image_path: Path,
    bbox_pixels: Optional[list[float]],
    click_point: Optional[tuple[int, int]],
    label: str = "outlook_search_grounding",
    overlay_text: Optional[str] = None,
    boundary_lines: Optional[list[tuple[int, str, str]]] = None,
    valid: Optional[bool] = None,
) -> Optional[Path]:
    """Saves a copy of image_path with bbox_pixels ([y_min, x_min, y_max,
    x_max], native pixels) drawn as a rectangle and click_point (native
    pixels) drawn as a marker. overlay_text (e.g. "target_type=desktop_app
    visible_label='Outlook' confidence=0.97"), if given, is drawn as a
    small text label near the top of the image — purely for a human
    reviewer's convenience.

    boundary_lines (added 2026-09-06, optional, backward compatible —
    existing callers passing nothing are unaffected): a list of
    (x_pixel, color, label) vertical reference lines — e.g. the sidebar
    boundary and the message-list-right plausibility boundary used by
    app.safety.validators.validate_email_row_bbox() — drawn purely for a
    human reviewer's spatial context, never read back by any decision
    path. valid, if given, colors the bbox rectangle green (True) or red
    (False) instead of the default red, so an accepted vs. rejected
    candidate is visually obvious at a glance.

    Returns the saved path, or None if drawing/saving failed (never
    raises — a diagnostic artifact must never be able to break the real
    grounding flow that called it)."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return None

    try:
        with Image.open(image_path) as img:
            img = img.convert("RGB")
            draw = ImageDraw.Draw(img)

            if boundary_lines:
                for line_x, line_color, line_label in boundary_lines:
                    draw.line([line_x, 0, line_x, img.height], fill=line_color, width=2)
                    draw.text((line_x + 2, img.height - 14), line_label, fill=line_color)

            if bbox_pixels is not None and len(bbox_pixels) == 4:
                y_min, x_min, y_max, x_max = bbox_pixels
                bbox_color = "red" if valid is False else ("lime" if valid is True else "red")
                draw.rectangle([x_min, y_min, x_max, y_max], outline=bbox_color, width=3)

            if click_point is not None:
                cx, cy = click_point
                radius = 6
                draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], outline="lime", width=3)
                draw.line([cx - radius, cy, cx + radius, cy], fill="lime", width=2)
                draw.line([cx, cy - radius, cx, cy + radius], fill="lime", width=2)

            if overlay_text:
                draw.rectangle([0, 0, min(img.width, 8 + 8 * len(overlay_text)), 20], fill="black")
                draw.text((4, 2), overlay_text, fill="yellow")

            DEBUG_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
            out_path = DEBUG_OUTPUT_DIR / f"{label}_{timestamp}.png"
            img.save(out_path, "PNG")
            return out_path
    except Exception:  # noqa: BLE001 — a diagnostic artifact must never raise into the caller
        return None
