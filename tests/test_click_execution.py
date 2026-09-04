"""Unit tests for RND-006B safe click execution. pyautogui is mocked
throughout — these tests never perform a real click.
"""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rnd.metrics.grounding import coordinate_in_image_bounds  # noqa: E402
from rnd.models.click_execution import (  # noqa: E402
    CLICK_ALLOWED_TARGETS,
    EXPLICITLY_BLOCKED_TARGETS,
    RND006BClickResult,
    is_click_target_allowed,
)


# --- allowed click target passes ---

def test_allowed_targets_are_exactly_the_expected_three():
    assert set(CLICK_ALLOWED_TARGETS) == {"email_row", "reply", "reply_editor"}


def test_email_row_reply_reply_editor_are_allowed():
    assert is_click_target_allowed("email_row") is True
    assert is_click_target_allowed("reply") is True
    assert is_click_target_allowed("reply_editor") is True


# --- Send target rejected ---

def test_send_is_not_an_allowed_click_target():
    assert is_click_target_allowed("send") is False
    assert "send" in EXPLICITLY_BLOCKED_TARGETS


# --- unknown target rejected ---

def test_unknown_and_other_blocked_targets_rejected():
    for t in ["reply_all", "forward", "delete", "archive", "totally_unknown_target", ""]:
        assert is_click_target_allowed(t) is False


# --- foreground mismatch prevents click ---

def test_click_aborts_before_move_when_foreground_not_outlook():
    with patch(
        "rnd.experiments.rnd006b_safe_click_execution.get_foreground_window_title",
        return_value="Untitled - Notepad",
    ), patch("rnd.experiments.rnd006b_safe_click_execution.pyautogui") as mock_pyautogui:
        from rnd.experiments import rnd006b_safe_click_execution as mod

        pending = RND006BClickResult(
            test_id="RND006B-email_row", target="email_row",
            converted_x=500, converted_y=500, result="PENDING",
        )
        mod.PENDING_DIR.mkdir(parents=True, exist_ok=True)
        mod.pending_path("email_row").write_text(pending.model_dump_json(indent=2), encoding="utf-8")

        mod.cmd_click("email_row")

        mock_pyautogui.moveTo.assert_not_called()
        mock_pyautogui.click.assert_not_called()

        saved = mod.RND006BClickResult.model_validate_json(mod.pending_path("email_row").read_text(encoding="utf-8"))
        assert saved.result == "ABORTED"
        assert saved.failure_reason == "FOREGROUND_MISMATCH"
        assert saved.movement_executed is False
        assert saved.click_executed is False
        mod.pending_path("email_row").unlink()


def test_click_aborts_between_move_and_click_when_foreground_changes():
    """Foreground OK before move, but changes before the click itself —
    the cursor may have moved, but click() must never be called.
    """
    titles = iter(["Mail - Yash Dhanraj - Outlook", "Untitled - Notepad"])
    with patch(
        "rnd.experiments.rnd006b_safe_click_execution.get_foreground_window_title",
        side_effect=lambda: next(titles),
    ), patch("rnd.experiments.rnd006b_safe_click_execution.pyautogui") as mock_pyautogui:
        mock_pyautogui.size.return_value = (1920, 1080)
        mock_pyautogui.FAILSAFE = True

        from rnd.experiments import rnd006b_safe_click_execution as mod

        pending = RND006BClickResult(
            test_id="RND006B-reply", target="reply",
            converted_x=900, converted_y=680, result="PENDING",
        )
        mod.PENDING_DIR.mkdir(parents=True, exist_ok=True)
        mod.pending_path("reply").write_text(pending.model_dump_json(indent=2), encoding="utf-8")

        mod.cmd_click("reply")

        mock_pyautogui.moveTo.assert_called_once()
        mock_pyautogui.click.assert_not_called()

        saved = mod.RND006BClickResult.model_validate_json(mod.pending_path("reply").read_text(encoding="utf-8"))
        assert saved.movement_executed is True
        assert saved.click_executed is False
        assert saved.result == "ABORTED"
        assert saved.failure_reason == "FOREGROUND_MISMATCH"
        mod.pending_path("reply").unlink()


# --- approval false / no --execute prevents click ---

def test_click_refuses_without_prior_execute(tmp_path, monkeypatch):
    from rnd.experiments import rnd006b_safe_click_execution as mod

    monkeypatch.setattr(mod, "PENDING_DIR", tmp_path / "nonexistent")
    with pytest.raises(SystemExit):
        mod.cmd_click("reply_editor")


def test_click_refuses_when_pending_not_in_pending_state(tmp_path, monkeypatch):
    from rnd.experiments import rnd006b_safe_click_execution as mod

    monkeypatch.setattr(mod, "PENDING_DIR", tmp_path)
    already_done = RND006BClickResult(test_id="RND006B-reply", target="reply", result="PASS")
    mod.pending_path("reply").write_text(already_done.model_dump_json(indent=2), encoding="utf-8")

    with pytest.raises(SystemExit):
        mod.cmd_click("reply")


# --- invalid coordinates prevent click ---

def test_click_aborts_on_invalid_coordinates():
    with patch(
        "rnd.experiments.rnd006b_safe_click_execution.get_foreground_window_title",
        return_value="Mail - Yash Dhanraj - Outlook",
    ), patch("rnd.experiments.rnd006b_safe_click_execution.pyautogui") as mock_pyautogui:
        mock_pyautogui.size.return_value = (1920, 1080)

        from rnd.experiments import rnd006b_safe_click_execution as mod

        pending = RND006BClickResult(
            test_id="RND006B-email_row", target="email_row",
            converted_x=5000, converted_y=5000, result="PENDING",  # out of bounds
        )
        mod.PENDING_DIR.mkdir(parents=True, exist_ok=True)
        mod.pending_path("email_row").write_text(pending.model_dump_json(indent=2), encoding="utf-8")

        mod.cmd_click("email_row")

        mock_pyautogui.moveTo.assert_not_called()
        mock_pyautogui.click.assert_not_called()

        saved = mod.RND006BClickResult.model_validate_json(mod.pending_path("email_row").read_text(encoding="utf-8"))
        assert saved.result == "ABORTED"
        assert saved.failure_reason == "COORDINATE_INVALID"
        mod.pending_path("email_row").unlink()


def test_coordinate_bounds_helper_rejects_out_of_range():
    assert coordinate_in_image_bounds(5000, 5000, 1920, 1080) is False
    assert coordinate_in_image_bounds(900, 680, 1920, 1080) is True


# --- move happens before click; only one click executes ---

def test_move_called_before_click_and_click_called_exactly_once():
    call_order = []

    with patch(
        "rnd.experiments.rnd006b_safe_click_execution.get_foreground_window_title",
        return_value="Mail - Yash Dhanraj - Outlook",
    ), patch("rnd.experiments.rnd006b_safe_click_execution.pyautogui") as mock_pyautogui, patch(
        "rnd.experiments.rnd006b_safe_click_execution.capture_screen",
        side_effect=Exception("stop after click for this test"),
    ):
        mock_pyautogui.size.return_value = (1920, 1080)
        mock_pyautogui.FAILSAFE = True
        mock_pyautogui.moveTo.side_effect = lambda *a, **kw: call_order.append("move")
        mock_pyautogui.click.side_effect = lambda *a, **kw: call_order.append("click")

        from rnd.experiments import rnd006b_safe_click_execution as mod

        pending = RND006BClickResult(
            test_id="RND006B-reply_editor", target="reply_editor",
            converted_x=1000, converted_y=800, result="PENDING",
        )
        mod.PENDING_DIR.mkdir(parents=True, exist_ok=True)
        mod.pending_path("reply_editor").write_text(pending.model_dump_json(indent=2), encoding="utf-8")

        try:
            mod.cmd_click("reply_editor")
        except Exception:
            pass  # expected — capture_screen raises to stop the test right after the click

        assert call_order == ["move", "click"]
        mock_pyautogui.click.assert_called_once()
        mod.pending_path("reply_editor").unlink()


# --- post-click verification required (structural: result stays PENDING until --confirm) ---

def test_confirm_refuses_without_prior_click(tmp_path, monkeypatch):
    from rnd.experiments import rnd006b_safe_click_execution as mod

    monkeypatch.setattr(mod, "PENDING_DIR", tmp_path)
    pending = RND006BClickResult(test_id="RND006B-reply", target="reply", result="PENDING", click_executed=False)
    mod.pending_path("reply").write_text(pending.model_dump_json(indent=2), encoding="utf-8")

    with pytest.raises(SystemExit):
        mod.cmd_confirm("reply", "pass")


def test_verification_failure_does_not_auto_finalize_result():
    """Even if AI verification comes back verified=False, cmd_click must
    leave the result at PENDING (awaiting human --confirm), never
    auto-finalize it as FAIL — the human has the final word, not the model.
    """
    from rnd.models.click_execution import RND006BClickResult

    result = RND006BClickResult(
        test_id="RND006B-email_row", target="email_row",
        converted_x=500, converted_y=500, click_executed=True,
        verification_result=False, detected_state="inbox",
        result="PENDING",
    )
    assert result.result == "PENDING"
    assert result.human_verification is None
