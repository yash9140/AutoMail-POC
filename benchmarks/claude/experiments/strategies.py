"""Grounding-strategy definitions for the comparison experiment.

Strategy A is the current runtime baseline: SAME prompt file, SAME
Pydantic schema as app/outlook/launch.py actually uses (imported
read-only). Strategies B/C/D are new prompt+schema pairs that live
entirely under benchmarks/claude/experiments/ — none of them are
imported by, or written into, any runtime module.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Type

from pydantic import BaseModel

from app.vision.models import OutlookSearchGroundingResponse

from benchmarks.claude import metrics
from benchmarks.claude.cases import EvalCase, RUNTIME_PROMPTS_DIR
from benchmarks.claude.experiments.schemas import CandidateListResponse, ComponentGroundingResponse

EXPERIMENTS_DIR = Path(__file__).resolve().parent
PROMPTS_DIR = EXPERIMENTS_DIR / "prompts"

RUNTIME_OUTLOOK_SEARCH_PROMPT_PATH = RUNTIME_PROMPTS_DIR / "outlook_search_grounding_v1.txt"
LANDMARK_RELATIVE_PROMPT_PATH = PROMPTS_DIR / "landmark_relative_v1.txt"
COMPONENT_GROUNDING_PROMPT_PATH = PROMPTS_DIR / "component_grounding_v1.txt"
CANDIDATE_LIST_PROMPT_PATH = PROMPTS_DIR / "candidate_list_v1.txt"

BASELINE_SEMANTIC_KEYS = ["search_visible", "target_visible", "target_type", "visible_label", "visible_sublabel"]


@dataclass
class Strategy:
    name: str
    stage_tag: str  # distinct VISION_CALL_*/EXP_CALL_* logging tag per strategy
    prompt_path: Path
    schema: Type[BaseModel]
    goal: str
    extract: Callable[[dict, EvalCase], dict[str, Any]]
    semantic_pass_fn: Callable[[dict, EvalCase], bool]


def _extract_bbox_style(parsed: dict, case: EvalCase) -> dict[str, Any]:
    """Shared by A and B — both use OutlookSearchGroundingResponse's shape."""
    return {
        "predicted_bbox": parsed.get("bbox"),
        "semantic_actual": {k: parsed.get(k) for k in BASELINE_SEMANTIC_KEYS},
    }


def _semantic_pass_baseline(semantic_actual: dict, case: EvalCase) -> bool:
    return metrics.semantic_pass(semantic_actual, case.expected)


def _extract_component(parsed: dict, case: EvalCase) -> dict[str, Any]:
    icon = parsed.get("outlook_icon_bbox")
    label = parsed.get("outlook_label_bbox")
    sublabel = parsed.get("app_sublabel_bbox")
    cluster = metrics.union_bbox([b for b in (icon, label, sublabel) if b is not None])

    expected_bbox = case.expected_bbox
    component: dict[str, Any] = {
        "icon_bbox": icon, "label_bbox": label, "sublabel_bbox": sublabel, "cluster_bbox": cluster,
        "icon_center_in_expected": None, "label_center_in_expected": None,
        "sublabel_center_in_expected": None, "cluster_center_in_expected": None,
        "cluster_iou_with_expected": None,
    }
    if expected_bbox is not None:
        for key, box in (("icon", icon), ("label", label), ("sublabel", sublabel), ("cluster", cluster)):
            if metrics.bbox_valid(box):
                component[f"{key}_center_in_expected"] = metrics.point_in_bbox(metrics.bbox_center(box), expected_bbox)
        if metrics.bbox_valid(cluster):
            component["cluster_iou_with_expected"] = metrics.iou(cluster, expected_bbox)

    return {
        "predicted_bbox": cluster,
        "semantic_actual": {"target_visible": parsed.get("target_visible")},
        "component": component,
    }


def _semantic_pass_component(semantic_actual: dict, case: EvalCase) -> bool:
    return bool(semantic_actual.get("target_visible") is True)


def _normalize_label(s: Any) -> str:
    return str(s or "").strip().lower()


def _extract_candidate_list(parsed: dict, case: EvalCase) -> dict[str, Any]:
    candidates = parsed.get("candidates") or []
    matches = [
        c for c in candidates
        if c.get("target_type") == "desktop_app"
        and _normalize_label(c.get("visible_label")) == "outlook"
        and "app" in _normalize_label(c.get("visible_sublabel"))
    ]
    predicted_bbox = matches[0].get("bbox") if len(matches) == 1 else None
    return {
        "predicted_bbox": predicted_bbox,
        "semantic_actual": {
            "unique_desktop_app_match_found": len(matches) == 1,
            "match_count": len(matches),
            "total_candidates_reported": len(candidates),
        },
        "candidates_reported": candidates,
    }


def _semantic_pass_candidate_list(semantic_actual: dict, case: EvalCase) -> bool:
    return bool(semantic_actual.get("unique_desktop_app_match_found") is True)


STRATEGY_A_BASELINE = Strategy(
    name="A_baseline_runtime_prompt",
    stage_tag="EXPERIMENT_A_BASELINE",
    prompt_path=RUNTIME_OUTLOOK_SEARCH_PROMPT_PATH,
    schema=OutlookSearchGroundingResponse,
    goal="Locate the Outlook search result",
    extract=_extract_bbox_style,
    semantic_pass_fn=_semantic_pass_baseline,
)

STRATEGY_B_LANDMARK_RELATIVE = Strategy(
    name="B_landmark_relative_prompt",
    stage_tag="EXPERIMENT_B_LANDMARK_RELATIVE",
    prompt_path=LANDMARK_RELATIVE_PROMPT_PATH,
    schema=OutlookSearchGroundingResponse,
    goal="Locate the Outlook search result using explicit landmark-relative rules",
    extract=_extract_bbox_style,
    semantic_pass_fn=_semantic_pass_baseline,
)

STRATEGY_C_COMPONENT_GROUNDING = Strategy(
    name="C_component_landmark_grounding",
    stage_tag="EXPERIMENT_C_COMPONENT",
    prompt_path=COMPONENT_GROUNDING_PROMPT_PATH,
    schema=ComponentGroundingResponse,
    goal="Independently locate the Outlook icon, label, and sublabel",
    extract=_extract_component,
    semantic_pass_fn=_semantic_pass_component,
)

STRATEGY_D_CANDIDATE_LIST = Strategy(
    name="D_candidate_list",
    stage_tag="EXPERIMENT_D_CANDIDATE_LIST",
    prompt_path=CANDIDATE_LIST_PROMPT_PATH,
    schema=CandidateListResponse,
    goal="List every visible search-result candidate",
    extract=_extract_candidate_list,
    semantic_pass_fn=_semantic_pass_candidate_list,
)

ALL_STRATEGIES: list[Strategy] = [
    STRATEGY_A_BASELINE, STRATEGY_B_LANDMARK_RELATIVE, STRATEGY_C_COMPONENT_GROUNDING, STRATEGY_D_CANDIDATE_LIST,
]
