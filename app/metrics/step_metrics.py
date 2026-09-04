"""Generalized per-call and per-step metrics + cost estimation.

Promotes CallMetrics/StepMetrics (previously defined redundantly across
rnd/models/outlook_launch.py and rnd/models/find_open_email.py) and the
estimate_cost() helper (previously duplicated in
app/playbook/outlook_launch_steps.py and
rnd/experiments/rnd008_controlled_send.py) into one shared module.

Reads config/model_pricing.json — a hand-maintained, source-cited table
that is explicitly null-if-unverified rather than guessed. That file is
untouched by this refactor.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from pydantic import BaseModel

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PRICING_PATH = PROJECT_ROOT / "config" / "model_pricing.json"


class CallMetrics(BaseModel):
    latency_ms: Optional[float] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    estimated_cost: Optional[float] = None


class StepMetrics(BaseModel):
    """Aggregate metrics for one named playbook step — kept separate per
    step (never merged into one grand total alone) so latency/cost can be
    attributed to a specific step (e.g. LAUNCH_OUTLOOK vs FINDING_EMAIL).
    Adds scroll_count/provider_retries over the RND-009C-era StepMetrics
    for the scrolling/fallback behavior introduced in later phases."""

    vision_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost: float = 0.0
    latency_ms: float = 0.0
    scroll_count: int = 0
    provider_retries: int = 0


def estimate_cost(provider: str, model: str, input_tokens: Optional[int], output_tokens: Optional[int]) -> Optional[float]:
    if not PRICING_PATH.exists() or input_tokens is None or output_tokens is None:
        return None
    pricing = json.loads(PRICING_PATH.read_text(encoding="utf-8"))
    entry = next((p for p in pricing.get("models", []) if p["provider"] == provider and p["model"] == model), None)
    if entry is None:
        return None
    return round(
        (input_tokens / 1_000_000) * entry["input_rate_per_million_tokens"]
        + (output_tokens / 1_000_000) * entry["output_rate_per_million_tokens"],
        6,
    )
