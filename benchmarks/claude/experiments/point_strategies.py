"""Interior-point grounding strategy definitions (P1/P2/P3, 2026-09-03).

All three share the SAME response schema (PointGroundingResponse) —
only the prompt differs in WHICH visible point it asks Claude to
localize (icon center / label-text center / any point on visible
cluster content). None of these ask Claude to infer the invisible
clickable-row boundary at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Type

from pydantic import BaseModel

from benchmarks.claude.experiments.schemas import PointGroundingResponse

EXPERIMENTS_DIR = Path(__file__).resolve().parent
PROMPTS_DIR = EXPERIMENTS_DIR / "prompts"

POINT_ICON_CENTER_PROMPT_PATH = PROMPTS_DIR / "point_icon_center_v1.txt"
POINT_LABEL_CENTER_PROMPT_PATH = PROMPTS_DIR / "point_label_center_v1.txt"
POINT_CLUSTER_INTERIOR_PROMPT_PATH = PROMPTS_DIR / "point_cluster_interior_v1.txt"

POINT_SEMANTIC_KEYS = ["target_visible", "target_type", "visible_label"]


@dataclass
class PointStrategy:
    name: str
    stage_tag: str
    prompt_path: Path
    schema: Type[BaseModel]
    goal: str


STRATEGY_P1_ICON_CENTER = PointStrategy(
    name="P1_icon_center",
    stage_tag="EXPERIMENT_P1_ICON_CENTER",
    prompt_path=POINT_ICON_CENTER_PROMPT_PATH,
    schema=PointGroundingResponse,
    goal="Locate the visual center of the Outlook application icon",
)

STRATEGY_P2_LABEL_CENTER = PointStrategy(
    name="P2_label_center",
    stage_tag="EXPERIMENT_P2_LABEL_CENTER",
    prompt_path=POINT_LABEL_CENTER_PROMPT_PATH,
    schema=PointGroundingResponse,
    goal="Locate the visual center of the 'Outlook' label text",
)

STRATEGY_P3_CLUSTER_INTERIOR = PointStrategy(
    name="P3_cluster_interior",
    stage_tag="EXPERIMENT_P3_CLUSTER_INTERIOR",
    prompt_path=POINT_CLUSTER_INTERIOR_PROMPT_PATH,
    schema=PointGroundingResponse,
    goal="Locate any point on visible content in the Outlook result cluster",
)

ALL_POINT_STRATEGIES: list[PointStrategy] = [
    STRATEGY_P1_ICON_CENTER, STRATEGY_P2_LABEL_CENTER, STRATEGY_P3_CLUSTER_INTERIOR,
]
