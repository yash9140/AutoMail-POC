"""Unit tests for RND-007A controlled text entry. pyautogui is mocked
throughout — these tests never send real keyboard input.
"""

import inspect
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rnd.models.text_entry import BLOCKED_KEY_ACTIONS, FIXED_TEST_TEXT, RND007ACaseResult  # noqa: E402


# --- structural: blocked hotkeys never appear in the module source ---

def test_module_never_sends_enter_ctrl_enter_or_alt_s():
    from rnd.experiments import rnd007a_controlled_text_entry as mod

    source = inspect.getsource(mod)
    assert "press('enter')" not in source.replace('"', "'")
    assert 'press("enter")' not in source
    assert "hotkey('ctrl', 'enter')" not in source.replace('"', "'")
    assert "hotkey('alt', 's')" not in source.replace('"', "'")
    assert "'\\n'" not in source  # no embedded newline sent as a keystroke


def test_module_never_calls_click():
    from rnd.experiments import rnd007a_controlled_text_entry as mod

    source = inspect.getsource(mod)
    assert ".click(" not in source


def test_blocked_key_actions_documented():
    assert "enter" in BLOCKED_KEY_ACTIONS
    assert "ctrl+enter" in BLOCKED_KEY_ACTIONS
    assert "alt+s" in BLOCKED_KEY_ACTIONS


# --- foreground mismatch prevents typing ---

def test_typing_aborts_when_foreground_not_outlook():
    with patch(
        "rnd.experiments.rnd007a_controlled_text_entry.get_foreground_window_title",
        return_value="Untitled - Notepad",
    ), patch("rnd.experiments.rnd007a_controlled_text_entry.pyautogui") as mock_pyautogui:
        from rnd.experiments import rnd007a_controlled_text_entry as mod

        mod.cmd_type()

        mock_pyautogui.write.assert_not_called()
        saved = mod.RND007ACaseResult.model_validate_json(mod.PENDING_PATH.read_text(encoding="utf-8"))
        assert saved.typing_result == "ABORTED"
        assert saved.text_entry_executed is False
        assert saved.failure_reason == "FOREGROUND_MISMATCH"
        mod.PENDING_PATH.unlink()


# --- typing function receives exact text ---

def test_typing_receives_exact_fixed_text():
    with patch(
        "rnd.experiments.rnd007a_controlled_text_entry.get_foreground_window_title",
        return_value="Mail - Yash Dhanraj - Outlook",
    ), patch("rnd.experiments.rnd007a_controlled_text_entry.pyautogui") as mock_pyautogui, patch(
        "rnd.experiments.rnd007a_controlled_text_entry.capture_screen",
        side_effect=[MagicMock(filename="pre.png", path="pre.png"), Exception("stop before verification for this test")],
    ), patch("rnd.experiments.rnd007a_controlled_text_entry.time.sleep"):
        mock_pyautogui.FAILSAFE = True

        from rnd.experiments import rnd007a_controlled_text_entry as mod

        try:
            mod.cmd_type()
        except Exception:
            pass  # expected — second capture_screen raises to stop the test right after typing

        mock_pyautogui.write.assert_called_once_with(FIXED_TEST_TEXT, interval=mod.TYPE_INTERVAL_SECONDS)
        saved = mod.RND007ACaseResult.model_validate_json(mod.PENDING_PATH.read_text(encoding="utf-8"))
        assert saved.typed_text == FIXED_TEST_TEXT
        assert saved.typing_result == "PASS"
        mod.PENDING_PATH.unlink()


def test_typing_only_called_once():
    with patch(
        "rnd.experiments.rnd007a_controlled_text_entry.get_foreground_window_title",
        return_value="Mail - Yash Dhanraj - Outlook",
    ), patch("rnd.experiments.rnd007a_controlled_text_entry.pyautogui") as mock_pyautogui, patch(
        "rnd.experiments.rnd007a_controlled_text_entry.capture_screen",
        return_value=MagicMock(filename="x.png", path="x.png"),
    ), patch("rnd.experiments.rnd007a_controlled_text_entry.time.sleep"), patch(
        "rnd.experiments.rnd007a_controlled_text_entry.os.environ.get", return_value=""
    ):
        mock_pyautogui.FAILSAFE = True

        from rnd.experiments import rnd007a_controlled_text_entry as mod

        mod.cmd_type()

        mock_pyautogui.write.assert_called_once()
        mod.PENDING_PATH.unlink()


# --- verification does not trigger a second typing action ---

def test_verification_call_does_not_trigger_second_write():
    """After typing + verification, pyautogui.write must have been called
    exactly once total, even though a verification API call happens
    afterward.
    """
    with patch(
        "rnd.experiments.rnd007a_controlled_text_entry.get_foreground_window_title",
        return_value="Mail - Yash Dhanraj - Outlook",
    ), patch("rnd.experiments.rnd007a_controlled_text_entry.pyautogui") as mock_pyautogui, patch(
        "rnd.experiments.rnd007a_controlled_text_entry.capture_screen",
        return_value=MagicMock(filename="x.png", path="x.png"),
    ), patch("rnd.experiments.rnd007a_controlled_text_entry.time.sleep"), patch(
        "rnd.experiments.rnd007a_controlled_text_entry.GeminiProvider"
    ) as mock_provider_cls, patch.dict(
        "os.environ", {"GEMINI_API_KEY": "fake", "GEMINI_MODEL": "gemini-3.6-flash"}
    ):
        mock_pyautogui.FAILSAFE = True
        mock_provider = mock_provider_cls.return_value
        mock_provider.analyze_screen.return_value = MagicMock(
            raw_text='{"verified": true, "detected_text": "This is a controlled POC test reply.", "confidence": 0.95, "reason": "matches"}',
            parsed_json={"verified": True, "detected_text": "This is a controlled POC test reply.", "confidence": 0.95, "reason": "matches"},
            model="gemini-3.6-flash", latency_ms=100.0, input_tokens=10, output_tokens=5,
        )

        from rnd.experiments import rnd007a_controlled_text_entry as mod

        mod.cmd_type()

        assert mock_pyautogui.write.call_count == 1
        mod.PENDING_PATH.unlink()


# --- human confirmation recorded separately from typing/vision results ---

def test_human_confirmation_recorded_separately_from_typing_and_vision():
    result = RND007ACaseResult(
        text_entry_executed=True,
        typing_result="PASS",
        vision_verification=False,  # AI says no
        human_verification=True,  # human says yes
        result="PASS",  # authoritative: human wins, matching RND-006B's design
    )
    assert result.typing_result == "PASS"
    assert result.vision_verification is False
    assert result.human_verification is True
    assert result.result == "PASS"
    # Confirms these are three independent fields, not one collapsed value.


def test_confirm_refuses_without_prior_typing(tmp_path, monkeypatch):
    from rnd.experiments import rnd007a_controlled_text_entry as mod

    monkeypatch.setattr(mod, "PENDING_PATH", tmp_path / "pending.json")
    never_typed = RND007ACaseResult(text_entry_executed=False)
    mod.PENDING_PATH.write_text(never_typed.model_dump_json(indent=2), encoding="utf-8")

    with pytest.raises(SystemExit):
        mod.cmd_confirm("pass")
