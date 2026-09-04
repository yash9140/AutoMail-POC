"""Unit tests for RND-006B Attempt 2: classification logic (pure) and the
stabilization/retry orchestration (pyautogui + timing mocked — never a
real click, never a real sleep).
"""

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rnd.metrics.click_verification import (  # noqa: E402
    AI_VERIFIED_FIRST_ATTEMPT,
    ATTEMPT1_TECHNICAL_ERROR_ATTEMPT2_ALSO_FAILED_OR_ERRORED,
    ATTEMPT1_TECHNICAL_ERROR_ATTEMPT2_PASSED,
    CLICK_FAILED,
    UI_STABILIZATION_DELAY_REQUIRED,
    VISION_VERIFICATION_FALSE_NEGATIVE,
    classify_verification_outcome,
    compute_click_result,
)

# --- classification logic (pure) ---


def test_attempt1_pass_classified_as_ai_verified_first_attempt():
    assert classify_verification_outcome(True, None, True) == AI_VERIFIED_FIRST_ATTEMPT


def test_attempt1_fail_attempt2_pass_classified_as_stabilization_delay_required():
    assert classify_verification_outcome(False, True, True) == UI_STABILIZATION_DELAY_REQUIRED


def test_both_ai_attempts_fail_but_human_passes_classified_as_false_negative():
    assert classify_verification_outcome(False, False, True) == VISION_VERIFICATION_FALSE_NEGATIVE


def test_both_ai_attempts_fail_and_human_fails_classified_as_click_failed():
    assert classify_verification_outcome(False, False, False) == CLICK_FAILED


def test_click_result_is_human_verified_alone_regardless_of_ai():
    assert compute_click_result(human_verified=True) == "PASS"
    assert compute_click_result(human_verified=False) == "FAIL"


def test_click_result_pass_even_when_both_ai_attempts_failed():
    # This is the exact "human PASS + AI FAIL/FAIL" case from the spec:
    # click_result must be PASS, verification classification must be the
    # false-negative category — two independent metrics, not collapsed.
    assert compute_click_result(True) == "PASS"
    assert classify_verification_outcome(False, False, True) == VISION_VERIFICATION_FALSE_NEGATIVE


def test_attempt1_technical_error_attempt2_passes_is_not_stabilization_delay():
    # A network/schema error on attempt 1 that happens to recover on retry
    # is NOT evidence for the stabilization-delay hypothesis — it must get
    # its own distinct classification, not be folded into
    # UI_STABILIZATION_DELAY_REQUIRED (a real bug caught during a live
    # RND-006B Attempt 2 run: attempt 1 failed with a NetworkError, not a
    # genuine verified=False verdict).
    result = classify_verification_outcome(None, True, True, attempt1_had_error=True)
    assert result == ATTEMPT1_TECHNICAL_ERROR_ATTEMPT2_PASSED
    assert result != UI_STABILIZATION_DELAY_REQUIRED


def test_attempt1_technical_error_attempt2_also_fails():
    result = classify_verification_outcome(None, False, False, attempt1_had_error=True)
    assert result == ATTEMPT1_TECHNICAL_ERROR_ATTEMPT2_ALSO_FAILED_OR_ERRORED


def test_attempt1_had_error_false_still_uses_normal_classification():
    # Sanity: when attempt1_had_error is False (the default/normal case),
    # behavior must be unchanged from before this fix.
    assert classify_verification_outcome(False, True, True, attempt1_had_error=False) == UI_STABILIZATION_DELAY_REQUIRED


# --- stabilization/retry orchestration ---


def _make_mock_response(verified: bool, detected_state: str = "state", confidence: float = 0.9):
    mock_response = MagicMock()
    mock_response.text = (
        '{"verified": %s, "detected_state": "%s", "confidence": %s, '
        '"visual_evidence": "evidence", "reason": "reason"}'
        % (str(verified).lower(), detected_state, confidence)
    )
    mock_response.usage_metadata.prompt_token_count = 100
    mock_response.usage_metadata.candidates_token_count = 20
    return mock_response


def test_post_click_delay_happens_before_first_screenshot():
    """time.sleep(1.5) must be called before the first verification
    screenshot is captured.
    """
    call_order = []
    with patch("rnd.experiments.rnd006b_attempt2_safe_click_execution.time.sleep", side_effect=lambda s: call_order.append(f"sleep:{s}")), \
         patch("rnd.experiments.rnd006b_attempt2_safe_click_execution.capture_screen", side_effect=lambda *a, **kw: call_order.append("screenshot") or MagicMock(filename="x.png", path="x.png")), \
         patch("rnd.experiments.rnd006b_attempt2_safe_click_execution.GeminiProvider") as mock_provider_cls:
        mock_provider = mock_provider_cls.return_value
        mock_provider.analyze_screen.return_value = MagicMock(
            raw_text='{"verified": true, "detected_state": "s", "confidence": 0.9, "visual_evidence": "e", "reason": "r"}',
            parsed_json={"verified": True, "detected_state": "s", "confidence": 0.9, "visual_evidence": "e", "reason": "r"},
            model="gemini-3.6-flash", latency_ms=100.0, input_tokens=10, output_tokens=5,
        )

        from rnd.experiments import rnd006b_attempt2_safe_click_execution as mod

        mod.run_verification_cycle(mock_provider, "email_row", "action", "expected", delay_seconds=1.5, attempt_number=1)

        assert call_order[0] == "sleep:1.5"
        assert call_order[1] == "screenshot"


def test_verification_pass_on_attempt_1_skips_second_verification():
    with patch("rnd.experiments.rnd006b_attempt2_safe_click_execution.time.sleep"), \
         patch("rnd.experiments.rnd006b_attempt2_safe_click_execution.capture_screen", return_value=MagicMock(filename="x.png", path="x.png")), \
         patch("rnd.experiments.rnd006b_attempt2_safe_click_execution.GeminiProvider") as mock_provider_cls:
        mock_provider = mock_provider_cls.return_value
        mock_provider.analyze_screen.return_value = MagicMock(
            raw_text='{"verified": true, "detected_state": "s", "confidence": 0.9, "visual_evidence": "e", "reason": "r"}',
            parsed_json={"verified": True, "detected_state": "s", "confidence": 0.9, "visual_evidence": "e", "reason": "r"},
            model="gemini-3.6-flash", latency_ms=100.0, input_tokens=10, output_tokens=5,
        )

        from rnd.experiments import rnd006b_attempt2_safe_click_execution as mod

        attempts = mod.run_stabilized_verification(mock_provider, "email_row", "action", "expected")

        assert attempts["attempt_2"] is None
        assert attempts["attempt_1"].verified is True
        assert mock_provider.analyze_screen.call_count == 1


def test_verification_fail_on_attempt_1_triggers_second_wait_and_screenshot():
    responses = [
        MagicMock(
            raw_text='{"verified": false, "detected_state": "s1", "confidence": 0.9, "visual_evidence": "e1", "reason": "r1"}',
            parsed_json={"verified": False, "detected_state": "s1", "confidence": 0.9, "visual_evidence": "e1", "reason": "r1"},
            model="gemini-3.6-flash", latency_ms=100.0, input_tokens=10, output_tokens=5,
        ),
        MagicMock(
            raw_text='{"verified": true, "detected_state": "s2", "confidence": 0.9, "visual_evidence": "e2", "reason": "r2"}',
            parsed_json={"verified": True, "detected_state": "s2", "confidence": 0.9, "visual_evidence": "e2", "reason": "r2"},
            model="gemini-3.6-flash", latency_ms=100.0, input_tokens=10, output_tokens=5,
        ),
    ]
    sleep_calls = []
    with patch("rnd.experiments.rnd006b_attempt2_safe_click_execution.time.sleep", side_effect=lambda s: sleep_calls.append(s)), \
         patch("rnd.experiments.rnd006b_attempt2_safe_click_execution.capture_screen", return_value=MagicMock(filename="x.png", path="x.png")), \
         patch("rnd.experiments.rnd006b_attempt2_safe_click_execution.GeminiProvider") as mock_provider_cls:
        mock_provider = mock_provider_cls.return_value
        mock_provider.analyze_screen.side_effect = responses

        from rnd.experiments import rnd006b_attempt2_safe_click_execution as mod

        attempts = mod.run_stabilized_verification(mock_provider, "email_row", "action", "expected")

        assert sleep_calls == [1.5, 1.0]
        assert attempts["attempt_1"].verified is False
        assert attempts["attempt_2"] is not None
        assert attempts["attempt_2"].verified is True
        assert mock_provider.analyze_screen.call_count == 2


def test_maximum_verification_attempts_is_two():
    fail_response = MagicMock(
        raw_text='{"verified": false, "detected_state": "s", "confidence": 0.9, "visual_evidence": "e", "reason": "r"}',
        parsed_json={"verified": False, "detected_state": "s", "confidence": 0.9, "visual_evidence": "e", "reason": "r"},
        model="gemini-3.6-flash", latency_ms=100.0, input_tokens=10, output_tokens=5,
    )
    with patch("rnd.experiments.rnd006b_attempt2_safe_click_execution.time.sleep"), \
         patch("rnd.experiments.rnd006b_attempt2_safe_click_execution.capture_screen", return_value=MagicMock(filename="x.png", path="x.png")), \
         patch("rnd.experiments.rnd006b_attempt2_safe_click_execution.GeminiProvider") as mock_provider_cls:
        mock_provider = mock_provider_cls.return_value
        mock_provider.analyze_screen.return_value = fail_response

        from rnd.experiments import rnd006b_attempt2_safe_click_execution as mod

        attempts = mod.run_stabilized_verification(mock_provider, "email_row", "action", "expected")

        # Both attempt 1 and attempt 2 fail — must NOT trigger a third call.
        assert mock_provider.analyze_screen.call_count == 2
        assert attempts["attempt_2"] is not None


def test_second_verification_never_calls_pyautogui_click():
    """The retry means wait+screenshot+verify — never another click."""
    fail_response = MagicMock(
        raw_text='{"verified": false, "detected_state": "s", "confidence": 0.9, "visual_evidence": "e", "reason": "r"}',
        parsed_json={"verified": False, "detected_state": "s", "confidence": 0.9, "visual_evidence": "e", "reason": "r"},
        model="gemini-3.6-flash", latency_ms=100.0, input_tokens=10, output_tokens=5,
    )
    with patch("rnd.experiments.rnd006b_attempt2_safe_click_execution.time.sleep"), \
         patch("rnd.experiments.rnd006b_attempt2_safe_click_execution.capture_screen", return_value=MagicMock(filename="x.png", path="x.png")), \
         patch("rnd.experiments.rnd006b_attempt2_safe_click_execution.GeminiProvider") as mock_provider_cls, \
         patch("rnd.experiments.rnd006b_attempt2_safe_click_execution.pyautogui") as mock_pyautogui:
        mock_provider = mock_provider_cls.return_value
        mock_provider.analyze_screen.return_value = fail_response

        from rnd.experiments import rnd006b_attempt2_safe_click_execution as mod

        mod.run_stabilized_verification(mock_provider, "email_row", "action", "expected")

        mock_pyautogui.click.assert_not_called()
