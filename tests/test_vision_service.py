"""app/vision/service.py unit tests — the Claude-primary/Gemini-fallback
orchestration layer, in isolation from any Outlook business logic.

Mirrors the task's fallback test matrix (A-U in the architecture-refactor
brief) at the level VisionService itself is responsible for: same-
screenshot fallback, technical-failure-only triggering, schema-valid
semantic answers never triggering fallback, provider metadata, and
retry/fallback bookkeeping. Worker/Outlook-level fallback wiring is
covered separately (see test_provider_migration.py /
test_gemini_provider_migration.py). No real provider/API calls anywhere
in this file — every provider is a MagicMock.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pydantic import BaseModel  # noqa: E402

from app.vision.providers.base import (  # noqa: E402
    MalformedResponseError,
    NetworkError,
    ProviderCallResult,
    ProviderTimeoutError,
    RateLimitError,
    VisionProviderError,
)
from app.vision.service import VisionRequest, VisionService  # noqa: E402


class _DummyResponse(BaseModel):
    """A minimal stand-in for a real app.vision.models response schema —
    VisionService's fallback logic must not care what the schema means,
    only whether a payload validates against it."""

    target_visible: bool = False
    candidate_count: int = 0
    confidence: float = 0.0


def _provider(name: str) -> MagicMock:
    mock = MagicMock()
    mock.provider_name = name
    return mock


def _call(parsed_json=None, model="test-model", latency_ms=10.0) -> ProviderCallResult:
    return ProviderCallResult(
        raw_text="{}", parsed_json=parsed_json, model=model, latency_ms=latency_ms, input_tokens=5, output_tokens=5,
    )


def _request(response_model=_DummyResponse, screenshot_path=None) -> VisionRequest:
    return VisionRequest(
        stage="TEST_STAGE",
        screenshot_path=screenshot_path or Path("shot.png"),
        goal="Test goal",
        prompt_text="Test prompt",
        response_model=response_model,
    )


# --- A: Claude (primary) success -> Gemini never called ---

def test_A_primary_success_fallback_never_called():
    primary = _provider("anthropic")
    primary.analyze_screen.return_value = _call({"target_visible": True, "confidence": 0.9})
    fallback = _provider("gemini")

    service = VisionService(primary, fallback, default_max_retries=1)
    outcome = service.analyze(_request())

    assert outcome.parsed.target_visible is True
    assert outcome.provider_used == "anthropic"
    assert outcome.fallback_used is False
    fallback.analyze_screen.assert_not_called()


# --- B/C/D/E: technical failures on primary -> Gemini fallback, same screenshot ---

def test_B_primary_timeout_then_fallback_succeeds_same_screenshot():
    shot = Path("target_shot.png")
    primary = _provider("anthropic")
    primary.analyze_screen.side_effect = ProviderTimeoutError("timed out")
    fallback = _provider("gemini")
    fallback.analyze_screen.return_value = _call({"target_visible": True, "confidence": 0.8})

    service = VisionService(primary, fallback, default_max_retries=0)
    outcome = service.analyze(_request(screenshot_path=shot))

    assert outcome.parsed.target_visible is True
    assert outcome.provider_used == "gemini"
    assert outcome.fallback_used is True
    # Same screenshot_path object passed to both calls — no recapture.
    primary_call_args = primary.analyze_screen.call_args
    fallback_call_args = fallback.analyze_screen.call_args
    assert primary_call_args[0][0] == shot
    assert fallback_call_args[0][0] == shot
    # 2026-09-06 diagnostics addition: stage is passed through to BOTH
    # calls as a kwarg (so provider-level diagnostic logging, e.g.
    # AnthropicProvider's ANTHROPIC_RESPONSE_METADATA, can be attributed
    # to the right stage) — purely additive, never changes what the
    # provider is asked to do.
    assert primary_call_args.kwargs["stage"] == "TEST_STAGE"
    assert fallback_call_args.kwargs["stage"] == "TEST_STAGE"


def test_C_primary_rate_limited_then_fallback_succeeds():
    primary = _provider("anthropic")
    primary.analyze_screen.side_effect = RateLimitError("429")
    fallback = _provider("gemini")
    fallback.analyze_screen.return_value = _call({"target_visible": True})

    service = VisionService(primary, fallback, default_max_retries=0)
    outcome = service.analyze(_request())

    assert outcome.parsed is not None
    assert outcome.provider_used == "gemini"
    assert outcome.fallback_used is True


def test_D_primary_5xx_then_fallback_succeeds():
    primary = _provider("anthropic")
    primary.analyze_screen.side_effect = VisionProviderError("500 server error")
    fallback = _provider("gemini")
    fallback.analyze_screen.return_value = _call({"target_visible": True})

    service = VisionService(primary, fallback, default_max_retries=0)
    outcome = service.analyze(_request())

    assert outcome.parsed is not None
    assert outcome.provider_used == "gemini"
    assert outcome.fallback_used is True


def test_E_primary_network_failure_then_fallback_succeeds():
    primary = _provider("anthropic")
    primary.analyze_screen.side_effect = NetworkError("connection refused")
    fallback = _provider("gemini")
    fallback.analyze_screen.return_value = _call({"target_visible": True})

    service = VisionService(primary, fallback, default_max_retries=0)
    outcome = service.analyze(_request())

    assert outcome.parsed is not None
    assert outcome.provider_used == "gemini"
    assert outcome.fallback_used is True


# --- F: malformed technical response -> fallback (parsed_json is None,
# and JSON that doesn't match the schema, are BOTH "malformed" here) ---

def test_F_primary_empty_parsed_json_triggers_fallback():
    primary = _provider("anthropic")
    primary.analyze_screen.return_value = _call(parsed_json=None)  # e.g. non-JSON text response
    fallback = _provider("gemini")
    fallback.analyze_screen.return_value = _call({"target_visible": True})

    service = VisionService(primary, fallback, default_max_retries=0)
    outcome = service.analyze(_request())

    assert outcome.parsed is not None
    assert outcome.provider_used == "gemini"
    assert outcome.fallback_used is True


def test_F_primary_schema_invalid_json_triggers_fallback():
    primary = _provider("anthropic")
    # Valid JSON, but doesn't match _DummyResponse's types at all —
    # ValidationError, not a raised VisionProviderError.
    primary.analyze_screen.return_value = _call(parsed_json={"candidate_count": "not-a-number"})
    fallback = _provider("gemini")
    fallback.analyze_screen.return_value = _call({"target_visible": True})

    service = VisionService(primary, fallback, default_max_retries=0)
    outcome = service.analyze(_request())

    assert outcome.parsed is not None
    assert outcome.provider_used == "gemini"
    assert outcome.fallback_used is True


class _RequiredFieldResponse(BaseModel):
    """A stand-in schema with a REQUIRED field (no default) — _DummyResponse
    above has none, so it can't exercise the 'missing_fields' diagnostic."""

    must_be_present: str
    confidence: float = 0.0


def test_schema_validation_failure_logs_diagnostics_without_raw_content(caplog):
    """2026-09-06: safe diagnostics for WHY a response failed schema
    validation — never the raw response content itself."""
    primary = _provider("anthropic")
    primary.analyze_screen.return_value = _call(parsed_json={"candidate_count": "not-a-number-xyz123"})
    service = VisionService(primary, fallback=None, default_max_retries=0)

    with caplog.at_level("INFO", logger="app.vision.service"):
        outcome = service.analyze(_request())

    assert outcome.parsed is None
    messages = [r.getMessage() for r in caplog.records]
    diag_lines = [m for m in messages if "VISION_SCHEMA_VALIDATION_FAILED" in m]
    assert len(diag_lines) == 1
    assert "stage=TEST_STAGE" in diag_lines[0]
    assert "provider=anthropic" in diag_lines[0]
    assert "json_parse_failed=False" in diag_lines[0]
    assert "response_char_count=" in diag_lines[0]
    # Never the actual (possibly sensitive) field value itself.
    assert "not-a-number-xyz123" not in diag_lines[0]


def test_schema_validation_failure_reports_missing_required_field_names(caplog):
    primary = _provider("anthropic")
    primary.analyze_screen.return_value = _call(parsed_json={"confidence": 0.9})  # must_be_present omitted
    service = VisionService(primary, fallback=None, default_max_retries=0)

    with caplog.at_level("INFO", logger="app.vision.service"):
        outcome = service.analyze(_request(response_model=_RequiredFieldResponse))

    assert outcome.parsed is None
    assert outcome.schema_invalid is True
    diag_lines = [r.getMessage() for r in caplog.records if "VISION_SCHEMA_VALIDATION_FAILED" in r.getMessage()]
    assert len(diag_lines) == 1
    assert "must_be_present" in diag_lines[0]
    assert "validation_error_type=ValidationError" in diag_lines[0]


def test_schema_validation_failure_json_parse_failed_when_no_json(caplog):
    primary = _provider("anthropic")
    primary.analyze_screen.return_value = _call(parsed_json=None)
    service = VisionService(primary, fallback=None, default_max_retries=0)

    with caplog.at_level("INFO", logger="app.vision.service"):
        service.analyze(_request())

    diag_lines = [r.getMessage() for r in caplog.records if "VISION_SCHEMA_VALIDATION_FAILED" in r.getMessage()]
    assert len(diag_lines) == 1
    assert "json_parse_failed=True" in diag_lines[0]
    assert "validation_error_type=None" in diag_lines[0]
    assert "missing_fields=[]" in diag_lines[0]


def test_schema_validation_diagnostic_logging_never_changes_fallback_outcome():
    """Purely additive: the diagnostic log must not alter which provider
    ultimately answers or whether fallback triggers."""
    primary = _provider("anthropic")
    primary.analyze_screen.return_value = _call(parsed_json={"candidate_count": "not-a-number"})
    fallback = _provider("gemini")
    fallback.analyze_screen.return_value = _call({"target_visible": True})

    service = VisionService(primary, fallback, default_max_retries=0)
    outcome = service.analyze(_request())

    assert outcome.parsed is not None
    assert outcome.provider_used == "gemini"
    assert outcome.fallback_used is True


def test_MalformedResponseError_from_provider_also_triggers_fallback():
    primary = _provider("anthropic")
    primary.analyze_screen.side_effect = MalformedResponseError("empty response body")
    fallback = _provider("gemini")
    fallback.analyze_screen.return_value = _call({"target_visible": True})

    service = VisionService(primary, fallback, default_max_retries=0)
    outcome = service.analyze(_request())

    assert outcome.parsed is not None
    assert outcome.fallback_used is True


# --- G: schema-valid semantic "no" answer -> NO fallback, ever ---

def test_G_schema_valid_negative_answer_never_triggers_fallback():
    primary = _provider("anthropic")
    primary.analyze_screen.return_value = _call({"target_visible": False, "candidate_count": 0, "confidence": 0.95})
    fallback = _provider("gemini")

    service = VisionService(primary, fallback, default_max_retries=1)
    outcome = service.analyze(_request())

    assert outcome.parsed is not None
    assert outcome.parsed.target_visible is False
    assert outcome.fallback_used is False
    fallback.analyze_screen.assert_not_called()


def test_G_schema_valid_multiple_results_never_triggers_fallback():
    """Same principle as above with a different semantic shape (e.g. the
    'multiple candidates found, ambiguous' case) — VisionService is
    response-model-agnostic; it only cares whether the payload validates."""
    primary = _provider("anthropic")
    primary.analyze_screen.return_value = _call({"target_visible": True, "candidate_count": 3, "confidence": 0.9})
    fallback = _provider("gemini")

    service = VisionService(primary, fallback, default_max_retries=1)
    outcome = service.analyze(_request())

    assert outcome.parsed.candidate_count == 3
    assert outcome.fallback_used is False
    fallback.analyze_screen.assert_not_called()


# --- No fallback configured ---

def test_no_fallback_configured_technical_failure_returns_none():
    primary = _provider("anthropic")
    primary.analyze_screen.side_effect = ProviderTimeoutError("timed out")

    service = VisionService(primary, fallback=None, default_max_retries=0)
    outcome = service.analyze(_request())

    assert outcome.parsed is None
    assert outcome.provider_used is None
    assert outcome.fallback_used is False
    assert outcome.error is not None


# --- schema_invalid distinguishes "no response body" from "response
# body received but didn't validate" on the terminal (parsed=None)
# outcome — some call sites route these two differently. ---

def test_schema_invalid_true_when_no_fallback_and_primary_response_malformed():
    primary = _provider("anthropic")
    primary.analyze_screen.return_value = _call(parsed_json=None)

    service = VisionService(primary, fallback=None, default_max_retries=0)
    outcome = service.analyze(_request())

    assert outcome.parsed is None
    assert outcome.schema_invalid is True


def test_schema_invalid_false_when_no_fallback_and_primary_raised_exception():
    primary = _provider("anthropic")
    primary.analyze_screen.side_effect = ProviderTimeoutError("timed out")

    service = VisionService(primary, fallback=None, default_max_retries=0)
    outcome = service.analyze(_request())

    assert outcome.parsed is None
    assert outcome.schema_invalid is False


def test_schema_invalid_reflects_fallback_not_primary_when_both_fail():
    primary = _provider("anthropic")
    primary.analyze_screen.side_effect = ProviderTimeoutError("timed out")  # primary: raised exception
    fallback = _provider("gemini")
    fallback.analyze_screen.return_value = _call(parsed_json=None)  # fallback: malformed response

    service = VisionService(primary, fallback, default_max_retries=0)
    outcome = service.analyze(_request())

    assert outcome.parsed is None
    assert outcome.schema_invalid is True  # reflects the LAST attempt (fallback), not the primary


# --- Both primary and fallback technically fail ---

def test_both_primary_and_fallback_fail_returns_none_with_combined_error():
    primary = _provider("anthropic")
    primary.analyze_screen.side_effect = ProviderTimeoutError("timed out")
    fallback = _provider("gemini")
    fallback.analyze_screen.side_effect = NetworkError("also down")

    service = VisionService(primary, fallback, default_max_retries=0)
    outcome = service.analyze(_request())

    assert outcome.parsed is None
    assert outcome.provider_used is None
    assert outcome.fallback_used is True
    assert "primary" in outcome.error and "fallback" in outcome.error


# --- Same-provider retry succeeds before ever considering fallback ---

def test_primary_retries_once_and_succeeds_fallback_never_called():
    primary = _provider("anthropic")
    primary.analyze_screen.side_effect = [ProviderTimeoutError("timed out"), _call({"target_visible": True})]
    fallback = _provider("gemini")

    service = VisionService(primary, fallback, default_max_retries=1)
    outcome = service.analyze(_request())

    assert outcome.parsed is not None
    assert outcome.provider_used == "anthropic"
    assert outcome.fallback_used is False
    assert outcome.primary_retries == 1
    fallback.analyze_screen.assert_not_called()


def test_analyze_max_retries_override_bounds_primary_attempts():
    primary = _provider("anthropic")
    primary.analyze_screen.side_effect = ProviderTimeoutError("timed out")
    fallback = _provider("gemini")
    fallback.analyze_screen.return_value = _call({"target_visible": True})

    service = VisionService(primary, fallback, default_max_retries=1)
    service.analyze(_request(), max_retries=0)  # override: zero same-provider retries this call

    assert primary.analyze_screen.call_count == 1  # not 2 — override respected over the constructor default


# --- Provider metadata reporting (R) ---

def test_R_provider_metadata_reports_correctly_on_success():
    primary = _provider("anthropic")
    primary.analyze_screen.return_value = _call({"target_visible": True}, model="claude-sonnet-5")
    service = VisionService(primary, fallback=_provider("gemini"), default_max_retries=1)

    outcome = service.analyze(_request())

    assert outcome.provider_used == "anthropic"
    assert outcome.model_used == "claude-sonnet-5"
    assert outcome.call_metrics.provider_name == "anthropic"
    assert outcome.call_metrics.model == "claude-sonnet-5"


def test_R_provider_metadata_reports_correctly_on_fallback():
    primary = _provider("anthropic")
    primary.analyze_screen.side_effect = NetworkError("down")
    fallback = _provider("gemini")
    fallback.analyze_screen.return_value = _call({"target_visible": True}, model="gemini-3.6-flash")

    service = VisionService(primary, fallback, default_max_retries=0)
    outcome = service.analyze(_request())

    assert outcome.provider_used == "gemini"
    assert outcome.model_used == "gemini-3.6-flash"
    assert outcome.call_metrics.provider_name == "gemini"


# --- Call-count bookkeeping across multiple analyze() calls ---

def test_primary_and_fallback_call_counters_accumulate_across_calls():
    primary = _provider("anthropic")
    primary.analyze_screen.side_effect = [
        _call({"target_visible": True}),  # call 1: primary succeeds
        NetworkError("down"),             # call 2: primary fails technically
    ]
    fallback = _provider("gemini")
    fallback.analyze_screen.return_value = _call({"target_visible": True})

    service = VisionService(primary, fallback, default_max_retries=0)
    service.analyze(_request())
    service.analyze(_request())

    assert service.primary_calls == 2
    assert service.fallback_calls == 1
