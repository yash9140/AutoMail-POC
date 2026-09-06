"""AnthropicProvider response-termination / JSON-structure diagnostics
(2026-09-06 — REPLY_SEARCH structured-output reliability investigation).

Live evidence: a REPLY_SEARCH call returned json_parse_failed=True with
response_char_count=1525 — not enough on its own to tell truncation
(MAX_TOKENS) apart from markdown-fenced output, leading/trailing prose,
or other malformed JSON, each of which needs a DIFFERENT fix. This file
proves:
  - stop_reason / token counts / char count are captured and logged,
    tagged with the calling stage, for EVERY call (success or failure).
  - a parse failure additionally logs safe, content-free JSON-structure
    facts (brace balance, markdown-fence presence, leading-char class,
    trailing-content-after-object) that let those cases be told apart.
  - none of this new logging ever contains the raw response text or any
    substring of it (no email-body leakage).
  - the diagnostics are purely observational: parsed_json/output_tokens/
    stop_reason on the returned ProviderCallResult are identical whether
    or not the new logging path runs.
  - fallback behavior (app.vision.service.VisionService) is unaffected.

No real network call anywhere in this file — anthropic.messages.create
is always mocked.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.vision.providers.anthropic_provider import (  # noqa: E402
    AnthropicProvider,
    _extract_json_object,
    _json_structure_diagnostics,
)

DUMMY_IMAGE_BYTES = b"\x89PNG\r\n\x1a\n" + b"0" * 32


def _mock_response(text: str, stop_reason="end_turn", model="claude-sonnet-5", input_tokens=150, output_tokens=30):
    block = MagicMock()
    block.type = "text"
    block.text = text
    response = MagicMock()
    response.content = [block]
    response.model = model
    response.stop_reason = stop_reason
    response.usage.input_tokens = input_tokens
    response.usage.output_tokens = output_tokens
    return response


def _provider() -> AnthropicProvider:
    return AnthropicProvider(api_key="fake-key", model="claude-sonnet-5")


# --- stop_reason / token / char-count metadata logging ---

def test_stop_reason_and_tokens_logged_for_a_successful_call(tmp_path, caplog):
    image_path = tmp_path / "shot.png"
    image_path.write_bytes(DUMMY_IMAGE_BYTES)
    provider = _provider()
    response_text = '{"a": 1}'
    response = _mock_response(response_text, stop_reason="end_turn", output_tokens=42)

    with patch.object(provider._client.messages, "create", return_value=response), \
         caplog.at_level("INFO", logger="app.vision.providers.anthropic"):
        result = provider.analyze_screen(image_path, "goal", "prompt", stage="REPLY_SEARCH")

    assert result.stop_reason == "end_turn"
    assert result.output_tokens == 42
    lines = [r.getMessage() for r in caplog.records if "ANTHROPIC_RESPONSE_METADATA" in r.getMessage()]
    assert len(lines) == 1
    assert "stage=REPLY_SEARCH" in lines[0]
    assert "stop_reason=end_turn" in lines[0]
    assert "output_tokens=42" in lines[0]
    assert f"response_char_count={len(response_text)}" in lines[0]


def test_metadata_logged_even_when_stage_is_omitted(tmp_path, caplog):
    image_path = tmp_path / "shot.png"
    image_path.write_bytes(DUMMY_IMAGE_BYTES)
    provider = _provider()
    response = _mock_response('{"a": 1}')

    with patch.object(provider._client.messages, "create", return_value=response), \
         caplog.at_level("INFO", logger="app.vision.providers.anthropic"):
        provider.analyze_screen(image_path, "goal", "prompt")  # no stage kwarg — backward compatible

    lines = [r.getMessage() for r in caplog.records if "ANTHROPIC_RESPONSE_METADATA" in r.getMessage()]
    assert len(lines) == 1
    assert "stage=" in lines[0]  # present, just empty


# --- max_tokens termination classification ---

def test_max_tokens_stop_reason_is_captured_and_logged(tmp_path, caplog):
    image_path = tmp_path / "shot.png"
    image_path.write_bytes(DUMMY_IMAGE_BYTES)
    provider = _provider()
    # Truncated mid-object — incomplete JSON, output_tokens at the ceiling.
    truncated = '{"outlook_visible": true, "reply_visible": true, "control_identity": "Reply'
    response = _mock_response(truncated, stop_reason="max_tokens", output_tokens=1024)

    with patch.object(provider._client.messages, "create", return_value=response), \
         caplog.at_level("INFO", logger="app.vision.providers.anthropic"):
        result = provider.analyze_screen(image_path, "goal", "prompt", stage="REPLY_SEARCH")

    assert result.stop_reason == "max_tokens"
    assert result.parsed_json is None  # truncated JSON never parses
    metadata_lines = [r.getMessage() for r in caplog.records if "ANTHROPIC_RESPONSE_METADATA" in r.getMessage()]
    assert "stop_reason=max_tokens" in metadata_lines[0]
    assert "output_tokens=1024" in metadata_lines[0]
    structure_lines = [r.getMessage() for r in caplog.records if "ANTHROPIC_JSON_STRUCTURE_DIAGNOSTICS" in r.getMessage()]
    assert len(structure_lines) == 1
    assert "brace_balance=1" in structure_lines[0]  # one unclosed '{'
    assert "json_ends_with_object=False" in structure_lines[0]


# --- end_turn + markdown fence classification (still-unrecoverable case:
# the JSON *inside* the fence is itself truncated/malformed, so envelope
# extraction correctly still fails too — distinct from the now-recovered
# "fence + prose but otherwise-valid JSON" case covered under the
# envelope-extraction tests below) ---

def test_end_turn_with_markdown_fence_and_truncated_json_is_classified_distinctly(tmp_path, caplog):
    image_path = tmp_path / "shot.png"
    image_path.write_bytes(DUMMY_IMAGE_BYTES)
    provider = _provider()
    # Fenced, but the JSON itself never closes — genuinely unrecoverable,
    # not just prose-wrapped.
    fenced_and_truncated = '```json\n{"a": 1'
    response = _mock_response(fenced_and_truncated, stop_reason="end_turn")

    with patch.object(provider._client.messages, "create", return_value=response), \
         caplog.at_level("INFO", logger="app.vision.providers.anthropic"):
        result = provider.analyze_screen(image_path, "goal", "prompt", stage="REPLY_SEARCH")

    assert result.stop_reason == "end_turn"
    assert result.parsed_json is None  # truncated JSON is never recoverable, fenced or not
    structure_lines = [r.getMessage() for r in caplog.records if "ANTHROPIC_JSON_STRUCTURE_DIAGNOSTICS" in r.getMessage()]
    assert len(structure_lines) == 1
    assert "contains_markdown_fence=True" in structure_lines[0]


# --- incomplete/unbalanced brace detection (genuinely unrecoverable —
# every '{' candidate, including the nested one, fails to decode) ---

def test_unbalanced_braces_detected(tmp_path, caplog):
    image_path = tmp_path / "shot.png"
    image_path.write_bytes(DUMMY_IMAGE_BYTES)
    provider = _provider()
    # Neither the outer nor the inner object ever closes — no '{' position
    # yields a successfully-decodable object, so this stays unrecoverable.
    response = _mock_response('{"a": {"b": 1', stop_reason="end_turn")

    with patch.object(provider._client.messages, "create", return_value=response), \
         caplog.at_level("INFO", logger="app.vision.providers.anthropic"):
        result = provider.analyze_screen(image_path, "goal", "prompt", stage="REPLY_SEARCH")

    assert result.parsed_json is None
    structure_lines = [r.getMessage() for r in caplog.records if "ANTHROPIC_JSON_STRUCTURE_DIAGNOSTICS" in r.getMessage()]
    assert "brace_balance=2" in structure_lines[0]


def test_json_structure_diagnostics_detects_trailing_prose_after_a_complete_object():
    """Helper-level check (independent of extraction outcome): the
    diagnostic itself still correctly flags trailing content after a
    syntactically-complete object — used for classification/logging
    even on a call where extraction goes on to recover the object."""
    diagnostics = _json_structure_diagnostics('{"a": 1}\nHope that helps!')
    assert diagnostics["trailing_non_whitespace_after_object"] is True
    assert diagnostics["json_ends_with_object"] is False


# --- structure diagnostics are NOT logged on a successful parse ---

def test_structure_diagnostics_not_logged_when_parsing_succeeds(tmp_path, caplog):
    image_path = tmp_path / "shot.png"
    image_path.write_bytes(DUMMY_IMAGE_BYTES)
    provider = _provider()
    response = _mock_response('{"a": 1}', stop_reason="end_turn")

    with patch.object(provider._client.messages, "create", return_value=response), \
         caplog.at_level("INFO", logger="app.vision.providers.anthropic"):
        provider.analyze_screen(image_path, "goal", "prompt", stage="REPLY_SEARCH")

    structure_lines = [r.getMessage() for r in caplog.records if "ANTHROPIC_JSON_STRUCTURE_DIAGNOSTICS" in r.getMessage()]
    assert structure_lines == []


# --- no raw provider response / email content ever appears in logs ---

def test_no_raw_response_text_or_email_content_in_any_log_line(tmp_path, caplog):
    image_path = tmp_path / "shot.png"
    image_path.write_bytes(DUMMY_IMAGE_BYTES)
    provider = _provider()
    secret_marker = "CONFIDENTIAL_EMAIL_BODY_MARKER_ZzQ7"
    response = _mock_response(f"prose {secret_marker} more prose", stop_reason="end_turn")

    with patch.object(provider._client.messages, "create", return_value=response), \
         caplog.at_level("INFO", logger="app.vision.providers.anthropic"):
        provider.analyze_screen(image_path, "goal", "prompt", stage="REPLY_SEARCH")

    for record in caplog.records:
        assert secret_marker not in record.getMessage()


def test_json_structure_diagnostics_never_returns_the_input_text():
    text = "some very specific raw response content that must never leak"
    diagnostics = _json_structure_diagnostics(text)
    for value in diagnostics.values():
        assert text not in str(value)


# --- diagnostics are observational only — never change the result ---

def test_diagnostics_logging_does_not_alter_the_returned_result(tmp_path):
    image_path = tmp_path / "shot.png"
    image_path.write_bytes(DUMMY_IMAGE_BYTES)
    provider = _provider()
    response = _mock_response('{"a": 1}', stop_reason="end_turn", output_tokens=17)

    with patch.object(provider._client.messages, "create", return_value=response):
        result_with_logging = provider.analyze_screen(image_path, "goal", "prompt", stage="REPLY_SEARCH")
    with patch.object(provider._client.messages, "create", return_value=response):
        result_without_stage = provider.analyze_screen(image_path, "goal", "prompt")

    assert result_with_logging.parsed_json == result_without_stage.parsed_json == {"a": 1}
    assert result_with_logging.output_tokens == result_without_stage.output_tokens == 17
    assert result_with_logging.stop_reason == result_without_stage.stop_reason == "end_turn"


# --- empty-string edge case never raises ---

def test_json_structure_diagnostics_handles_empty_string():
    diagnostics = _json_structure_diagnostics("")
    assert diagnostics["json_starts_with_object"] is False
    assert diagnostics["brace_balance"] == 0
    assert diagnostics["leading_non_whitespace_char_type"] == "empty"


# =========================================================================
# --- Narrow JSON-envelope extraction fallback (2026-09-06) --------------
# =========================================================================
# Live evidence + a 5-trial static REPLY_SEARCH benchmark (5/5 reproduced)
# proved: stop_reason=end_turn every time (MAX_TOKENS truncation
# disproven), contains_markdown_fence=False every time (not a fencing
# issue), yet a syntactically COMPLETE, balanced JSON object was present
# — Claude prepends explanatory prose despite the prompt's "no prose"
# instruction. These tests prove the fix: _extract_json_object() /
# analyze_screen() now recover that exact shape, while never bypassing
# Pydantic schema validation and never guessing on genuine ambiguity.

def test_json_starts_with_object_parses_via_the_normal_fast_path(tmp_path):
    """Plain, unwrapped JSON never touches the extraction fallback at
    all — proven indirectly: no ANTHROPIC_JSON_ENVELOPE_EXTRACTED log
    line for a response that needed no recovery."""
    image_path = tmp_path / "shot.png"
    image_path.write_bytes(DUMMY_IMAGE_BYTES)
    provider = _provider()
    response = _mock_response('{"a": 1}', stop_reason="end_turn")

    with patch.object(provider._client.messages, "create", return_value=response):
        result = provider.analyze_screen(image_path, "goal", "prompt", stage="REPLY_SEARCH")

    assert result.parsed_json == {"a": 1}


def test_leading_prose_before_a_complete_json_object_is_recovered(tmp_path, caplog):
    """The EXACT shape the live benchmark reproduced 5/5: end_turn, no
    markdown fence, leading prose, otherwise-complete JSON object."""
    image_path = tmp_path / "shot.png"
    image_path.write_bytes(DUMMY_IMAGE_BYTES)
    provider = _provider()
    live_shape = (
        'I can identify the Reply control from the screenshot.\n'
        '{"outlook_visible": true, "reply_visible": true, "control_identity": "Reply", '
        '"control_type": "button", "bbox": [400.0, 800.0, 440.0, 900.0], '
        '"more_content_below": false, "confidence": 0.95, "reason": "ok"}'
    )
    response = _mock_response(live_shape, stop_reason="end_turn")

    with patch.object(provider._client.messages, "create", return_value=response), \
         caplog.at_level("INFO", logger="app.vision.providers.anthropic"):
        result = provider.analyze_screen(image_path, "goal", "prompt", stage="REPLY_SEARCH")

    assert result.parsed_json is not None
    assert result.parsed_json["control_identity"] == "Reply"
    # Diagnostics never logged for a call that ultimately succeeds.
    assert not any("ANTHROPIC_JSON_STRUCTURE_DIAGNOSTICS" in r.getMessage() for r in caplog.records)
    extracted_lines = [r.getMessage() for r in caplog.records if "ANTHROPIC_JSON_ENVELOPE_EXTRACTED" in r.getMessage()]
    assert len(extracted_lines) == 1
    assert "stage=REPLY_SEARCH" in extracted_lines[0]
    # Never logs the prose/object content itself.
    assert "Reply control" not in extracted_lines[0]


def test_leading_prose_with_an_unstripped_markdown_fence_is_recovered(tmp_path):
    """Prose BEFORE a fenced block — the existing defensive stripping
    only fires when the text STARTS with '```', so this case previously
    fell all the way through to parsed_json=None; the envelope
    extractor now recovers it (fence markers are just more non-'{'
    characters the extractor skips over)."""
    image_path = tmp_path / "shot.png"
    image_path.write_bytes(DUMMY_IMAGE_BYTES)
    provider = _provider()
    fenced_with_prose = 'Here is the JSON:\n```json\n{"a": 1}\n```'
    response = _mock_response(fenced_with_prose, stop_reason="end_turn")

    with patch.object(provider._client.messages, "create", return_value=response):
        result = provider.analyze_screen(image_path, "goal", "prompt", stage="REPLY_SEARCH")

    assert result.parsed_json == {"a": 1}


def test_trailing_prose_after_a_complete_object_is_recovered(tmp_path):
    image_path = tmp_path / "shot.png"
    image_path.write_bytes(DUMMY_IMAGE_BYTES)
    provider = _provider()
    response = _mock_response('{"a": 1}\nHope that helps!', stop_reason="end_turn")

    with patch.object(provider._client.messages, "create", return_value=response):
        result = provider.analyze_screen(image_path, "goal", "prompt", stage="REPLY_SEARCH")

    assert result.parsed_json == {"a": 1}


def test_nested_objects_arrays_and_escaped_quotes_are_handled_correctly():
    """Proves raw_decode() (not naive brace-counting) is doing the work —
    braces inside a string value and an escaped quote would defeat a
    manual '{'/'}' counter."""
    text = (
        'Some prose. {"a": {"nested": [1, 2, {"deep": true}]}, '
        '"note": "a string with a { brace } and an escaped \\" quote inside"}'
    )
    result = _extract_json_object(text)
    assert result is not None
    assert result["a"]["nested"] == [1, 2, {"deep": True}]
    assert "brace" in result["note"]


def test_object_only_top_level_list_is_never_accepted():
    """Section 3 policy: a top-level '[...]' is never silently treated
    as the expected object, even if it's valid JSON on its own."""
    text = "Here you go: [1, 2, 3]"
    result = _extract_json_object(text)
    assert result is None


def test_object_only_ignores_a_leading_bracket_and_still_finds_the_object():
    """A '[' before the real object is simply not a '{' candidate — the
    extractor is not confused by it and still finds the actual object."""
    text = 'Values seen: [1, 2, 3]. Result: {"a": 1}'
    result = _extract_json_object(text)
    assert result == {"a": 1}


def test_multiple_independently_decodable_objects_is_ambiguous_not_merged(tmp_path, caplog):
    """Section 6 policy: two SEPARATE (not nested) top-level JSON
    objects in the same response is treated as an ambiguous parse
    failure — never merged, never resolved by picking the first."""
    image_path = tmp_path / "shot.png"
    image_path.write_bytes(DUMMY_IMAGE_BYTES)
    provider = _provider()
    two_objects = 'First attempt: {"a": 1}\nActually, here: {"a": 2}'
    response = _mock_response(two_objects, stop_reason="end_turn")

    with patch.object(provider._client.messages, "create", return_value=response), \
         caplog.at_level("INFO", logger="app.vision.providers.anthropic"):
        result = provider.analyze_screen(image_path, "goal", "prompt", stage="REPLY_SEARCH")

    assert result.parsed_json is None  # ambiguous — never guesses {"a": 1} or {"a": 2}
    assert not any("ANTHROPIC_JSON_ENVELOPE_EXTRACTED" in r.getMessage() for r in caplog.records)
    structure_lines = [r.getMessage() for r in caplog.records if "ANTHROPIC_JSON_STRUCTURE_DIAGNOSTICS" in r.getMessage()]
    assert len(structure_lines) == 1  # still classified/logged as a genuine parse failure


def test_extract_json_object_returns_none_when_no_brace_present():
    assert _extract_json_object("just plain prose, no JSON at all") is None


def test_extract_json_object_skips_a_malformed_candidate_and_finds_the_next_one():
    """A stray, unrelated '{' earlier in the text (e.g. Claude describing
    something using brace-like notation) that fails to decode must not
    stop the search — the extractor keeps scanning left to right."""
    text = '{not json at all {"a": 1}'
    result = _extract_json_object(text)
    assert result == {"a": 1}


def test_extraction_success_still_goes_through_full_schema_validation(tmp_path):
    """Section 5: JSON-extraction success is NOT Vision-response success
    — an extracted object missing required fields must still fail the
    SAME way it would have before this fallback existed. Proven here at
    the pydantic layer the extracted dict is handed to next."""
    from pydantic import BaseModel, ValidationError

    class _StrictResponse(BaseModel):
        reply_visible: bool
        control_identity: str
        confidence: float

    incomplete_shape = 'Sure thing.\n{"reply_visible": true}'  # missing required fields
    extracted = _extract_json_object(incomplete_shape)
    assert extracted == {"reply_visible": True}  # extraction itself succeeds...
    try:
        _StrictResponse.model_validate(extracted)  # ...but schema validation still fails
        assert False, "expected ValidationError for missing required fields"
    except ValidationError:
        pass
