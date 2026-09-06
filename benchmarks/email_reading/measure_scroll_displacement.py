"""Estimates the ACTUAL visual vertical scroll displacement between
consecutive EMAIL_SECTION_EXTRACTION screenshots from the 2026-09-06
21:45-21:46 live long-email run that safe-stopped as CONTENT_NOT_FULLY_
READ (unresolved_after_no_progress) after 4 sections.

Research/diagnostic only — never imported by runtime code, never
launches Outlook, never performs any physical action. Uses only Pillow
(already a project dependency, same "no new heavy dependency" choice as
app/safety/screen_freshness.py) — no OCR, no numpy, no OpenCV.

Method: crop each screenshot to an approximate reading-pane ROI, build a
1D vertical "row-darkness profile" (sum of (255 - grayscale) per row,
subsampled across columns) for each screenshot, then find the integer
vertical shift s that minimizes the mean squared difference between
profile_a[0 : H-s] and profile_b[s : H]. This is standard 1D signal
cross-correlation/alignment — a projection profile, not OCR — and is
far less sensitive to font-antialiasing noise than a raw full-2D pixel
diff at small shift magnitudes (a first-pass full-2D-diff attempt on
this same data could not resolve the true ~5-8px-per-line shift at all,
since anti-aliasing noise from a 1px-integer redraw is comparable in
magnitude to the true signal; aggregating across hundreds of columns
per row averages that noise out and makes the true shift the clear
minimum).

Two prior methodological mistakes, both corrected here (documented since
they're easy to repeat):
  1. An early attempt included the reading pane's own sticky subject-
     line banner/ribbon (visually confirmed pixel-identical across all
     4 screenshots regardless of body scroll) in the ROI, which biased
     toward a "no shift" result regardless of true body movement — the
     ROI here starts BELOW that banner.
  2. A second attempt searched an unbounded shift range (up to ~90% of
     the ROI height) and found a spurious "best" shift of ~94px —
     roughly 15x the true value. This body text is quasi-periodic
     (paragraphs of similar line height/spacing), so a profile shifted
     by roughly one paragraph's worth of pixels can spuriously
     correlate almost as well as the true, much smaller shift — classic
     aliasing. MAX_SHIFT_SEARCH_PX below is deliberately kept small
     (well under the observed ~90px aliasing period) specifically to
     avoid this trap; a targeted single-line-landmark cross-check (see
     this same directory's investigation notes) confirmed the ~6-7px
     result this bounded search converges to, independently.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from PIL import Image  # noqa: E402

SCREENSHOTS_DIR = PROJECT_ROOT / "screenshots" / "raw"

# The 4 EMAIL_SECTION_EXTRACTION screenshots from the failed live run,
# identified by matching each call's reported start timestamp
# (21:45:41.525 / 21:46:01.846 / 21:46:10.842 / 21:46:20.009) against
# the closest-preceding capture_screen() filename timestamp (a capture
# always completes fractionally before the Vision call it feeds starts).
SECTION_SCREENSHOTS = {
    1: "screen_20260906_214541_424.png",
    2: "screen_20260906_214601_750.png",
    3: "screen_20260906_214610_738.png",
    4: "screen_20260906_214619_916.png",
}

# Approximate reading-pane ROI, as fractions of the full screenshot —
# a generous, diagnostic-only estimate (excludes the left message
# list/nav, a bottom margin, AND the reading pane's own sticky subject-
# line banner/ribbon at the very top — visually confirmed pixel-
# identical across all 4 screenshots regardless of body scroll). NOT a
# claim about any exact runtime boundary. x_min matches the reading-
# pane crop fraction already established elsewhere in this project
# (0.35).
ROI_X_MIN_FRACTION = 0.36
ROI_X_MAX_FRACTION = 0.98
ROI_Y_MIN_FRACTION = 0.30
ROI_Y_MAX_FRACTION = 0.95

COLUMN_SUBSAMPLE_STRIDE = 3  # speed only — every Nth column contributes to each row's darkness sum
# Deliberately bounded well below the ~90px paragraph-line aliasing
# period observed in this email's body text (see module docstring,
# mistake #2) — a search window this narrow cannot lock onto that
# spurious alternative minimum.
MAX_SHIFT_SEARCH_PX = 60


def _roi_px(width: int, height: int) -> tuple[int, int, int, int]:
    return (
        round(ROI_X_MIN_FRACTION * width), round(ROI_Y_MIN_FRACTION * height),
        round(ROI_X_MAX_FRACTION * width), round(ROI_Y_MAX_FRACTION * height),
    )


def _row_darkness_profile(image_path: Path, roi: tuple[int, int, int, int]) -> list[int]:
    with Image.open(image_path) as img:
        crop = img.convert("L").crop(roi)
    width, height = crop.size
    pixels = crop.load()
    profile = []
    for y in range(height):
        total = 0
        for x in range(0, width, COLUMN_SUBSAMPLE_STRIDE):
            total += 255 - pixels[x, y]
        profile.append(total)
    return profile


def estimate_vertical_shift_px(image_a_path: Path, image_b_path: Path) -> dict:
    with Image.open(image_a_path) as img_a:
        width, height = img_a.size
    roi = _roi_px(width, height)
    roi_h = roi[3] - roi[1]

    profile_a = _row_darkness_profile(image_a_path, roi)
    profile_b = _row_darkness_profile(image_b_path, roi)

    best_shift = 0
    best_score = None
    max_shift = min(MAX_SHIFT_SEARCH_PX, round(roi_h * 0.5))
    for shift in range(0, max_shift):
        window_h = roi_h - shift
        if window_h <= 50:
            break
        score = sum((profile_a[i] - profile_b[i + shift]) ** 2 for i in range(window_h)) / window_h
        if best_score is None or score < best_score:
            best_score = score
            best_shift = shift

    return {
        "estimated_vertical_shift_px": best_shift,
        "reading_pane_visible_height_px": roi_h,
        "shift_as_fraction_of_visible_height": round(best_shift / roi_h, 4),
        "alignment_score": round(best_score, 1),
        "roi_px": roi,
    }


# --- Independent cross-check: locate one specific, isolated (non-
# repeating) text line's own top edge in each screenshot directly,
# rather than aligning a whole-ROI profile. Immune to the paragraph-
# line aliasing above by construction (there is only one "Hi Everyone,"
# in the email), at the cost of needing a landmark specific to this one
# frozen screenshot's own content — not a generalizable runtime
# technique, diagnostic cross-check only. ---
LANDMARK_X_MIN, LANDMARK_X_MAX = 850, 1060  # full-screen px column spanning "Hi Everyone,"
LANDMARK_Y_SEARCH_MIN, LANDMARK_Y_SEARCH_MAX = 340, 700
LANDMARK_DARK_THRESHOLD_SUM = 400


def _find_landmark_line_top(image_path: Path) -> int | None:
    with Image.open(image_path) as img:
        pixels = img.convert("L").load()
    for y in range(LANDMARK_Y_SEARCH_MIN, LANDMARK_Y_SEARCH_MAX):
        total = sum(255 - pixels[x, y] for x in range(LANDMARK_X_MIN, LANDMARK_X_MAX))
        if total > LANDMARK_DARK_THRESHOLD_SUM:
            return y
    return None


def main() -> None:
    missing = [name for name in SECTION_SCREENSHOTS.values() if not (SCREENSHOTS_DIR / name).exists()]
    if missing:
        print(f"Missing screenshots: {missing}")
        return

    print("=== SECTION SCREENSHOTS ===")
    for idx, name in SECTION_SCREENSHOTS.items():
        path = SCREENSHOTS_DIR / name
        with Image.open(path) as img:
            dims = img.size
        stamp = name.replace("screen_", "").replace(".png", "")
        date_part, time_part, ms_part = stamp.split("_")
        readable = f"{date_part[:4]}-{date_part[4:6]}-{date_part[6:]} {time_part[:2]}:{time_part[2:4]}:{time_part[4:]}.{ms_part}"
        print(f"section_index={idx} screenshot_path={path} dimensions={dims} timestamp={readable}")

    print("\n=== ESTIMATED VERTICAL SCROLL DISPLACEMENT (reading-pane ROI, below sticky banner) ===")
    pairs = [(1, 2), (2, 3), (3, 4)]
    for a_idx, b_idx in pairs:
        path_a = SCREENSHOTS_DIR / SECTION_SCREENSHOTS[a_idx]
        path_b = SCREENSHOTS_DIR / SECTION_SCREENSHOTS[b_idx]
        result = estimate_vertical_shift_px(path_a, path_b)
        print(
            f"section {a_idx} -> section {b_idx}: estimated_vertical_shift_px={result['estimated_vertical_shift_px']} "
            f"reading_pane_visible_height_px={result['reading_pane_visible_height_px']} "
            f"shift_as_fraction_of_visible_height={result['shift_as_fraction_of_visible_height']} "
            f"alignment_score={result['alignment_score']}"
        )

    print("\n=== CROSS-CHECK: isolated 'Hi Everyone,' line-top position (full-screen px) ===")
    landmark_tops = {idx: _find_landmark_line_top(SCREENSHOTS_DIR / name) for idx, name in SECTION_SCREENSHOTS.items()}
    for idx, top in landmark_tops.items():
        print(f"section {idx}: line_top_y={top}")
    for a_idx, b_idx in pairs:
        top_a, top_b = landmark_tops[a_idx], landmark_tops[b_idx]
        if top_a is not None and top_b is not None:
            print(f"section {a_idx} -> section {b_idx}: landmark_shift_px={top_a - top_b}")

    print("\n=== TARGET RANGE (from task) ===")
    print("45%-60% of visible reading-pane height per scroll")


if __name__ == "__main__":
    main()
