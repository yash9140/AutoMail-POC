"""Evaluation case definitions.

Deliberately imports NOTHING from app.outlook.*/app.automation.* (those
pull in pyautogui and step-executing methods) — the runtime prompt
FILES are read directly by path (the exact same files the live app
reads, guaranteeing identical content) and only the Pydantic response
schemas are imported from app.vision.models (pure data, no side
effects). See evaluation_runner.py's import-safety self-check.

Per the "start with OUTLOOK_SEARCH, don't build hundreds of cases up
front" instruction, only OUTLOOK_SEARCH cases are populated right now,
built from REAL screenshots captured during live debugging runs (copied
into screenshots/outlook_search/ from screenshots/raw/ and debug/ —
never synthesized). The EvalCase/registry shape is written to extend
cleanly to the other 10 stages once real screenshots for them exist.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Type

from pydantic import BaseModel

from app.vision.models import OutlookSearchGroundingResponse

EVAL_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = EVAL_DIR.parents[1]
SCREENSHOTS_DIR = EVAL_DIR / "screenshots"

# Read directly from the SAME path app/outlook/launch.py itself resolves
# (PROJECT_ROOT / "app" / "vision" / "prompts" / "..."), duplicated here
# only as a path string — not a copy of the file's content — so this
# module never has to import the pyautogui-linked launch module just to
# find where the prompt text lives.
RUNTIME_PROMPTS_DIR = PROJECT_ROOT / "app" / "vision" / "prompts"
OUTLOOK_SEARCH_PROMPT_PATH = RUNTIME_PROMPTS_DIR / "outlook_search_grounding_v1.txt"


@dataclass
class EvalCase:
    case_id: str
    stage: str
    screenshot: Path
    prompt_path: Path
    schema: Type[BaseModel]
    goal: str
    expected: dict[str, Any] = field(default_factory=dict)
    expected_bbox: Optional[list[float]] = None  # [y_min, x_min, y_max, x_max], 0-1000 normalized
    prompt_format_kwargs: dict[str, Any] = field(default_factory=dict)
    notes: str = ""


# --- OUTLOOK_SEARCH ---
#
# All three screenshots below are REAL captures from the live debugging
# session that originally surfaced the bbox-scoping bug (2026-09-02,
# ~22:35-23:09): Windows Search open, "Outlook" typed, the "Best match"
# section showing the Outlook desktop-app row, AND the right-side
# preview panel (Open / Run as administrator / ...) also visible — the
# exact layout that caused Claude to sometimes ground the header/panel
# instead of the row. expected_bbox was hand-measured directly from the
# image's own pixel data (edge-detected against the row's white
# highlight rectangle, at 1920x1080), not guessed.
_OUTLOOK_SEARCH_EXPECTED = {
    "search_visible": True,
    "target_visible": True,
    "target_type": "desktop_app",
    "visible_label": "Outlook",
    "visible_sublabel": "App",
}
_OUTLOOK_SEARCH_EXPECTED_BBOX = [268.0, 8.0, 340.0, 239.0]  # [y_min, x_min, y_max, x_max], hand-measured

OUTLOOK_SEARCH_CASES: list[EvalCase] = [
    EvalCase(
        case_id="outlook_search_live_001",
        stage="OUTLOOK_SEARCH",
        screenshot=SCREENSHOTS_DIR / "outlook_search" / "outlook_search_live_001.png",
        prompt_path=OUTLOOK_SEARCH_PROMPT_PATH,
        schema=OutlookSearchGroundingResponse,
        goal="Locate the Outlook search result",
        expected=_OUTLOOK_SEARCH_EXPECTED,
        expected_bbox=_OUTLOOK_SEARCH_EXPECTED_BBOX,
        notes="Live capture 2026-09-02 23:09:38 — the run whose debug overlay first showed the header/panel bbox-scoping bug.",
    ),
    EvalCase(
        case_id="outlook_search_live_002",
        stage="OUTLOOK_SEARCH",
        screenshot=SCREENSHOTS_DIR / "outlook_search" / "outlook_search_live_002.png",
        prompt_path=OUTLOOK_SEARCH_PROMPT_PATH,
        schema=OutlookSearchGroundingResponse,
        goal="Locate the Outlook search result",
        expected=_OUTLOOK_SEARCH_EXPECTED,
        expected_bbox=_OUTLOOK_SEARCH_EXPECTED_BBOX,
        notes="Live capture 2026-09-02 22:35:08 — same session, an earlier attempt; pixel-identical search-panel region (diff-checked).",
    ),
    EvalCase(
        case_id="outlook_search_live_003",
        stage="OUTLOOK_SEARCH",
        screenshot=SCREENSHOTS_DIR / "outlook_search" / "outlook_search_live_003.png",
        prompt_path=OUTLOOK_SEARCH_PROMPT_PATH,
        schema=OutlookSearchGroundingResponse,
        goal="Locate the Outlook search result",
        expected=_OUTLOOK_SEARCH_EXPECTED,
        expected_bbox=_OUTLOOK_SEARCH_EXPECTED_BBOX,
        notes="Live capture 2026-09-02 22:56:01 — same session, a third attempt; pixel-identical search-panel region (diff-checked).",
    ),
]

# Registry — extend by appending more *_CASES lists here as real
# screenshots for other stages become available. Intentionally NOT
# pre-populated with placeholder/synthetic cases for the other 10
# stages (OUTLOOK_READINESS, TARGET_EMAIL_SEARCH, ...) per instruction:
# "Do not immediately build hundreds of cases."
ALL_CASES: list[EvalCase] = [
    *OUTLOOK_SEARCH_CASES,
]


def get_cases(stage: Optional[str] = None, case_id: Optional[str] = None) -> list[EvalCase]:
    cases = ALL_CASES
    if stage is not None:
        cases = [c for c in cases if c.stage == stage]
    if case_id is not None:
        cases = [c for c in cases if c.case_id == case_id]
    return cases


def known_stages() -> list[str]:
    seen: list[str] = []
    for c in ALL_CASES:
        if c.stage not in seen:
            seen.append(c.stage)
    return seen
