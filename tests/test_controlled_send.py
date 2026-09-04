"""Unit tests for RND-008 controlled Send + Send verification.
pyautogui and all provider calls are mocked throughout — no real
mouse/keyboard actions and no real Send occurs anywhere in this file.
"""

import inspect
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rnd.models.controlled_send import (  # noqa: E402
    CallMetrics,
    PostSendVerificationAttempt,
    RND008Result,
)


def _base_ready_to_send_result(**overrides) -> RND008Result:
    defaults = dict(
        approved_draft="Hi Yash,\n\nThank you.\n\nBest regards,",
        human_email_approved=True,
        expected_draft="Hi Yash,\n\nThank you.\n\nBest regards,",
        human_draft_confirmed=True,
        converted_x=850,
        converted_y=1040,
        coordinate_inside_bbox=True,
        coordinate_in_screen_bounds=True,
        ground_truth_bbox="(800,1022)-(902,1060)",
    )
    defaults.update(overrides)
    return RND008Result(**defaults)


# --- structural: Send is clicked at most once, no other hotkeys reachable ---

def test_module_never_sends_hotkeys_and_click_appears_once_in_source():
    from rnd.experiments import rnd008_controlled_send as mod

    source = inspect.getsource(mod)
    normalized = source.replace('"', "'")
    assert "hotkey('ctrl', 'enter')" not in normalized
    assert "hotkey('alt', 's')" not in normalized
    assert "dblclick" not in normalized.lower()
    assert source.count("pyautogui.click()") == 1  # exactly one click call site, for Send only


# --- no approval -> no Send click ---

def test_execute_send_refuses_without_approval(tmp_path, monkeypatch):
    from rnd.experiments import rnd008_controlled_send as mod

    monkeypatch.setattr(mod, "PENDING_PATH", tmp_path / "pending.json")
    pending = _base_ready_to_send_result(human_send_approved=None)
    mod.save_pending(pending)

    with patch("rnd.experiments.rnd008_controlled_send.pyautogui") as mock_pyautogui:
        with pytest.raises(SystemExit):
            mod.cmd_execute_send()
        mock_pyautogui.click.assert_not_called()
        mock_pyautogui.moveTo.assert_not_called()


# --- wrong foreground -> no Send click ---

def test_execute_send_aborts_when_outlook_not_foreground(tmp_path, monkeypatch):
    from rnd.experiments import rnd008_controlled_send as mod

    monkeypatch.setattr(mod, "PENDING_PATH", tmp_path / "pending.json")
    pending = _base_ready_to_send_result(human_send_approved=True)
    mod.save_pending(pending)

    with patch("rnd.experiments.rnd008_controlled_send.get_foreground_window_title", return_value="Notepad - Untitled"), \
         patch("rnd.experiments.rnd008_controlled_send.pyautogui") as mock_pyautogui:
        mock_pyautogui.FAILSAFE = True
        mod.cmd_execute_send()
        mock_pyautogui.click.assert_not_called()
        mock_pyautogui.moveTo.assert_not_called()

    saved = mod.load_pending()
    assert saved.send_click_executed is False
    assert saved.result == "ABORTED"
    assert saved.failure_reason == "FOREGROUND_MISMATCH"
    assert saved.abort_stage == "execute_send_before_move"
    assert saved.send_was_executed_at_abort is False


def test_execute_send_aborts_when_foreground_lost_between_move_and_click(tmp_path, monkeypatch):
    from rnd.experiments import rnd008_controlled_send as mod

    monkeypatch.setattr(mod, "PENDING_PATH", tmp_path / "pending.json")
    pending = _base_ready_to_send_result(human_send_approved=True)
    mod.save_pending(pending)

    with patch("rnd.experiments.rnd008_controlled_send.get_foreground_window_title",
               side_effect=["Mail - Yash Dhanraj - Outlook", "Notepad - Untitled"]), \
         patch("rnd.experiments.rnd008_controlled_send.pyautogui") as mock_pyautogui:
        mock_pyautogui.FAILSAFE = True
        mod.cmd_execute_send()
        mock_pyautogui.moveTo.assert_called_once()
        mock_pyautogui.click.assert_not_called()  # cursor moved but NO CLICK

    saved = mod.load_pending()
    assert saved.send_click_executed is False
    assert saved.abort_stage == "execute_send_before_click"


# --- invalid / outside-bbox coordinate -> no Send click ---

def test_approve_send_refuses_coordinate_outside_bbox(tmp_path, monkeypatch):
    from rnd.experiments import rnd008_controlled_send as mod

    monkeypatch.setattr(mod, "PENDING_PATH", tmp_path / "pending.json")
    pending = _base_ready_to_send_result(coordinate_inside_bbox=False, converted_x=100, converted_y=100)
    mod.save_pending(pending)

    with pytest.raises(SystemExit):
        mod.cmd_approve_send("pass")

    saved = mod.load_pending()
    assert saved.human_send_approved is None  # never recorded as approved


def test_ground_send_aborts_when_coordinate_outside_known_bbox(tmp_path, monkeypatch):
    from rnd.experiments import rnd008_controlled_send as mod

    monkeypatch.setattr(mod, "PENDING_PATH", tmp_path / "pending.json")
    pending = RND008Result(approved_draft="Hi Yash,", human_draft_confirmed=True)
    mod.save_pending(pending)

    # A point far from the known Send bbox (800,1022)-(902,1060): raw
    # (100,100) normalized -> roughly (192,108) pixels on a 1920x1080 image.
    grounding_response = MagicMock(
        parsed_json={"target": "Send", "x": 100, "y": 100, "confidence": 0.9, "reason": "top-left area"},
        raw_text="{}", model="gemini-3.6-flash", latency_ms=100.0, input_tokens=10, output_tokens=5,
    )
    mock_provider = MagicMock()
    mock_provider.analyze_screen.return_value = grounding_response

    with patch("rnd.experiments.rnd008_controlled_send.get_foreground_window_title", return_value="Mail - Yash Dhanraj - Outlook"), \
         patch("rnd.experiments.rnd008_controlled_send.capture_screen", return_value=MagicMock(filename="x.png", path="x.png", width=1920, height=1080)), \
         patch("rnd.experiments.rnd008_controlled_send._provider", return_value=(mock_provider, "gemini-3.6-flash")):
        mod.cmd_ground_send()

    saved = mod.load_pending()
    assert saved.coordinate_inside_bbox is False
    assert saved.result == "ABORTED"
    assert "DO NOT SEND" in saved.notes

    # and approval must therefore refuse too — the chain holds end to end
    with pytest.raises(SystemExit):
        mod.cmd_approve_send("pass")


# --- only one click is ever possible ---

def test_only_one_send_click_possible(tmp_path, monkeypatch):
    from rnd.experiments import rnd008_controlled_send as mod

    monkeypatch.setattr(mod, "PENDING_PATH", tmp_path / "pending.json")
    pending = _base_ready_to_send_result(
        human_send_approved=True, send_click_executed=True, send_click_result="PASS",
    )
    mod.save_pending(pending)

    with patch("rnd.experiments.rnd008_controlled_send.get_foreground_window_title", return_value="Mail - Yash Dhanraj - Outlook"), \
         patch("rnd.experiments.rnd008_controlled_send.pyautogui") as mock_pyautogui:
        mock_pyautogui.FAILSAFE = True
        with pytest.raises(SystemExit):
            mod.cmd_execute_send()
        mock_pyautogui.click.assert_not_called()


def test_send_click_persisted_immediately(tmp_path, monkeypatch):
    from rnd.experiments import rnd008_controlled_send as mod

    monkeypatch.setattr(mod, "PENDING_PATH", tmp_path / "pending.json")
    pending = _base_ready_to_send_result(human_send_approved=True)
    mod.save_pending(pending)

    with patch("rnd.experiments.rnd008_controlled_send.get_foreground_window_title", return_value="Mail - Yash Dhanraj - Outlook"), \
         patch("rnd.experiments.rnd008_controlled_send.pyautogui") as mock_pyautogui:
        mock_pyautogui.FAILSAFE = True
        mod.cmd_execute_send()
        mock_pyautogui.click.assert_called_once()

    saved = mod.load_pending()
    assert saved.send_click_executed is True
    assert saved.send_click_timestamp is not None
    assert saved.send_click_result == "PASS"


# --- post-send verification: delay before capture, retry never re-clicks,
#     max 2 attempts, AI-fail/human-pass classification ---

def test_post_send_delay_occurs_before_screenshot(tmp_path, monkeypatch):
    from rnd.experiments import rnd008_controlled_send as mod

    monkeypatch.setattr(mod, "PENDING_PATH", tmp_path / "pending.json")
    pending = _base_ready_to_send_result(send_click_executed=True, send_click_result="PASS")
    mod.save_pending(pending)

    call_order = []
    response = MagicMock(
        parsed_json={"verified_sent": True, "detected_state": "reading pane", "visual_evidence": ["composer closed"], "confidence": 0.9, "reason": "no composer visible"},
        raw_text="{}", model="gemini-3.6-flash", latency_ms=100.0, input_tokens=10, output_tokens=5,
    )
    mock_provider = MagicMock()
    mock_provider.analyze_screen.return_value = response

    def fake_sleep(seconds):
        call_order.append(("sleep", seconds))

    def fake_capture(_dir):
        call_order.append(("capture", None))
        return MagicMock(filename="post.png", path="post.png")

    with patch("rnd.experiments.rnd008_controlled_send.time.sleep", side_effect=fake_sleep), \
         patch("rnd.experiments.rnd008_controlled_send.capture_screen", side_effect=fake_capture), \
         patch("rnd.experiments.rnd008_controlled_send._provider", return_value=(mock_provider, "gemini-3.6-flash")):
        mod.cmd_verify_send()

    assert call_order[0] == ("sleep", mod.POST_SEND_INITIAL_DELAY_SECONDS)
    assert call_order[1][0] == "capture"


def test_second_verification_attempt_allowed_with_retry_delay_and_no_reclick(tmp_path, monkeypatch):
    from rnd.experiments import rnd008_controlled_send as mod

    monkeypatch.setattr(mod, "PENDING_PATH", tmp_path / "pending.json")
    pending = _base_ready_to_send_result(send_click_executed=True, send_click_result="PASS")
    mod.save_pending(pending)

    fail_response = MagicMock(
        parsed_json={"verified_sent": False, "detected_state": "composer still open", "visual_evidence": ["Send button visible"], "confidence": 0.8, "reason": "not yet sent"},
        raw_text="{}", model="gemini-3.6-flash", latency_ms=100.0, input_tokens=10, output_tokens=5,
    )
    pass_response = MagicMock(
        parsed_json={"verified_sent": True, "detected_state": "reading pane", "visual_evidence": ["composer closed"], "confidence": 0.95, "reason": "sent"},
        raw_text="{}", model="gemini-3.6-flash", latency_ms=100.0, input_tokens=12, output_tokens=6,
    )
    mock_provider = MagicMock()
    mock_provider.analyze_screen.side_effect = [fail_response, pass_response]

    sleep_calls = []
    with patch("rnd.experiments.rnd008_controlled_send.time.sleep", side_effect=lambda s: sleep_calls.append(s)), \
         patch("rnd.experiments.rnd008_controlled_send.capture_screen", return_value=MagicMock(filename="post.png", path="post.png")), \
         patch("rnd.experiments.rnd008_controlled_send._provider", return_value=(mock_provider, "gemini-3.6-flash")), \
         patch("rnd.experiments.rnd008_controlled_send.pyautogui") as mock_pyautogui:
        mod.cmd_verify_send()  # attempt 1 — fails
        mod.cmd_verify_send()  # attempt 2 — succeeds

        mock_pyautogui.click.assert_not_called()  # verification NEVER re-clicks Send

    assert sleep_calls == [mod.POST_SEND_INITIAL_DELAY_SECONDS, mod.POST_SEND_RETRY_DELAY_SECONDS]

    saved = mod.load_pending()
    assert len(saved.post_send_attempts) == 2
    assert saved.ai_send_verification is True
    assert saved.ai_send_verification_classification == "AI_VERIFIED_AFTER_STABILIZATION"


def test_maximum_post_send_verification_attempts_is_two(tmp_path, monkeypatch):
    from rnd.experiments import rnd008_controlled_send as mod

    monkeypatch.setattr(mod, "PENDING_PATH", tmp_path / "pending.json")
    two_failed_attempts = [
        PostSendVerificationAttempt(attempt_number=1, delay_seconds=2.0, verified_sent=False, schema_valid=True),
        PostSendVerificationAttempt(attempt_number=2, delay_seconds=1.5, verified_sent=False, schema_valid=True),
    ]
    pending = _base_ready_to_send_result(
        send_click_executed=True, send_click_result="PASS",
        post_send_attempts=two_failed_attempts,
        ai_send_verification=False, ai_send_verification_classification="AI_NOT_VERIFIED",
    )
    mod.save_pending(pending)

    with pytest.raises(SystemExit):
        mod.cmd_verify_send()


def test_ai_fail_human_pass_classification(tmp_path, monkeypatch):
    from rnd.experiments import rnd008_controlled_send as mod

    monkeypatch.setattr(mod, "PENDING_PATH", tmp_path / "pending.json")
    two_failed_attempts = [
        PostSendVerificationAttempt(attempt_number=1, delay_seconds=2.0, verified_sent=False, schema_valid=True),
        PostSendVerificationAttempt(attempt_number=2, delay_seconds=1.5, verified_sent=False, schema_valid=True),
    ]
    pending = _base_ready_to_send_result(
        send_click_executed=True, send_click_result="PASS",
        post_send_attempts=two_failed_attempts,
        ai_send_verification=False, ai_send_verification_classification="AI_NOT_VERIFIED",
    )
    mod.save_pending(pending)

    mod.cmd_confirm_send("pass")

    saved = mod.load_pending()
    assert saved.human_send_verification is True
    assert saved.final_classification == "SEND_SUCCEEDED_AI_FALSE_NEGATIVE"
    assert saved.result == "PASS"


def test_human_fail_overrides_ai_pass_to_send_not_confirmed(tmp_path, monkeypatch):
    from rnd.experiments import rnd008_controlled_send as mod

    monkeypatch.setattr(mod, "PENDING_PATH", tmp_path / "pending.json")
    one_passed_attempt = [
        PostSendVerificationAttempt(attempt_number=1, delay_seconds=2.0, verified_sent=True, schema_valid=True),
    ]
    pending = _base_ready_to_send_result(
        send_click_executed=True, send_click_result="PASS",
        post_send_attempts=one_passed_attempt,
        ai_send_verification=True, ai_send_verification_classification="AI_VERIFIED_FIRST_ATTEMPT",
    )
    mod.save_pending(pending)

    mod.cmd_confirm_send("fail")

    saved = mod.load_pending()
    assert saved.final_classification == "SEND_NOT_CONFIRMED"
    assert saved.result == "FAIL"


def test_confirm_send_refuses_without_any_verification_attempt(tmp_path, monkeypatch):
    from rnd.experiments import rnd008_controlled_send as mod

    monkeypatch.setattr(mod, "PENDING_PATH", tmp_path / "pending.json")
    pending = _base_ready_to_send_result(send_click_executed=True, send_click_result="PASS")
    mod.save_pending(pending)

    with pytest.raises(SystemExit):
        mod.cmd_confirm_send("pass")


# --- every real provider call contributes to the running totals ---

def test_all_provider_calls_included_in_usage_totals(tmp_path, monkeypatch):
    from rnd.experiments import rnd008_controlled_send as mod

    monkeypatch.setattr(mod, "PENDING_PATH", tmp_path / "pending.json")
    pending = RND008Result(approved_draft="Hi Yash,", human_email_approved=True)
    mod.save_pending(pending)

    editor_open_response = MagicMock(
        parsed_json={"verified": True, "detected_state": "editor open", "confidence": 0.9, "visual_evidence": "cursor visible", "reason": "ok"},
        model="gemini-3.6-flash", latency_ms=100.0, input_tokens=10, output_tokens=5,
    )
    draft_response = MagicMock(
        parsed_json={"semantic_match": True, "detected_draft": "Hi Yash,", "confidence": 0.95, "reason": "matches"},
        raw_text="{}", model="gemini-3.6-flash", latency_ms=200.0, input_tokens=20, output_tokens=8,
    )
    mock_provider = MagicMock()
    mock_provider.analyze_screen.side_effect = [editor_open_response, draft_response]

    with patch("rnd.experiments.rnd008_controlled_send.get_foreground_window_title", return_value="Mail - Yash Dhanraj - Outlook"), \
         patch("rnd.experiments.rnd008_controlled_send.capture_screen", return_value=MagicMock(filename="x.png", path="x.png", width=1920, height=1080)), \
         patch("rnd.experiments.rnd008_controlled_send._provider", return_value=(mock_provider, "gemini-3.6-flash")):
        mod.cmd_verify_draft()

    saved = mod.load_pending()
    # both the editor-open state check AND the draft-verification call
    # contribute — no hidden helper calls
    assert saved.total_vision_calls == 2
    assert saved.total_input_tokens == 30
    assert saved.total_output_tokens == 13
    assert saved.total_latency_ms == 300.0
    assert saved.total_estimated_cost is not None and saved.total_estimated_cost > 0
