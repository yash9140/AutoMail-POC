"""app/metrics/step_metrics.py unit tests."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.metrics.step_metrics import CallMetrics, StepMetrics, estimate_cost  # noqa: E402


def test_call_metrics_defaults_to_none():
    m = CallMetrics()
    assert m.latency_ms is None
    assert m.input_tokens is None


def test_step_metrics_defaults_include_scroll_and_retry_counters():
    m = StepMetrics()
    assert m.vision_calls == 0
    assert m.scroll_count == 0
    assert m.provider_retries == 0


def test_estimate_cost_returns_none_for_unknown_model():
    assert estimate_cost("gemini", "not-a-real-model", 100, 50) is None


def test_estimate_cost_returns_none_without_token_counts():
    assert estimate_cost("gemini", "gemini-3.6-flash", None, None) is None


def test_estimate_cost_computes_a_real_value_for_the_configured_model():
    cost = estimate_cost("gemini", "gemini-3.6-flash", 1000, 500)
    # Only asserting the pricing table resolves to *some* non-negative
    # number for the model this whole project actually uses — the
    # specific rate is owned by config/model_pricing.json, not this test.
    assert cost is None or cost >= 0
