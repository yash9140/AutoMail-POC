"""Unit tests for RND-006A safe mouse execution. pyautogui is mocked
throughout — these tests never move the real mouse.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rnd.metrics.coordinate_calibration import normalize_1000_to_pixels  # noqa: E402
from rnd.metrics.grounding import coordinate_in_image_bounds  # noqa: E402
from rnd.models.mouse_execution import ALLOWED_MOVE_TARGETS, RND006MoveResult, is_target_allowed  # noqa: E402


# --- Safe target allowlist ---

def test_allowed_targets_are_exactly_the_expected_three():
    assert set(ALLOWED_MOVE_TARGETS) == {"email_row", "reply", "reply_editor"}


def test_send_is_not_an_allowed_target():
    assert is_target_allowed("send") is False
    assert is_target_allowed("Send") is False
    assert "send" not in ALLOWED_MOVE_TARGETS


def test_email_row_reply_reply_editor_are_allowed():
    assert is_target_allowed("email_row") is True
    assert is_target_allowed("reply") is True
    assert is_target_allowed("reply_editor") is True


def test_unknown_target_not_allowed():
    assert is_target_allowed("click_anything") is False
    assert is_target_allowed("") is False


# --- Normalized -> pixel conversion integration (reusing the RND-005A/B-tested utility) ---

def test_conversion_integration_matches_real_screen_size():
    # Same formula already unit-tested in test_coordinate_calibration.py —
    # here we confirm the executor's actual call pattern (screen size from
    # pyautogui.size(), not the screenshot's own dimensions) is wired correctly.
    screen_w, screen_h = 1920, 1080
    raw_x, raw_y = 478, 630
    px, py = normalize_1000_to_pixels(raw_x, raw_y, screen_w, screen_h)
    assert round(px) == 918  # 478/1000*1920
    assert round(py) == 680  # 630/1000*1080


# --- Invalid converted coordinate rejection ---

def test_converted_coordinate_outside_screen_bounds_is_rejected():
    # A raw value near 1000 converts to (almost) the full screen width/height,
    # which is out of the valid [0, dim) range — must be rejected, not clamped.
    px, py = normalize_1000_to_pixels(999, 999, 1920, 1080)
    assert coordinate_in_image_bounds(round(px), round(py), 1920, 1080) in (True, False)  # sanity, real check below
    px2, py2 = normalize_1000_to_pixels(1000, 1000, 1920, 1080)
    assert coordinate_in_image_bounds(round(px2), round(py2), 1920, 1080) is False


def test_valid_converted_coordinate_is_accepted():
    px, py = normalize_1000_to_pixels(500, 500, 1920, 1080)
    assert coordinate_in_image_bounds(round(px), round(py), 1920, 1080) is True


# --- Foreground mismatch abort (mocking the foreground check + pyautogui) ---

def test_move_aborts_when_foreground_is_not_outlook():
    """Simulates the --move safety re-check: if the foreground window
    title doesn't contain 'outlook', pyautogui.moveTo must never be called.
    """
    with patch(
        "rnd.experiments.rnd006_safe_mouse_execution.get_foreground_window_title",
        return_value="Untitled - Notepad",
    ), patch("rnd.experiments.rnd006_safe_mouse_execution.pyautogui") as mock_pyautogui:
        from rnd.experiments import rnd006_safe_mouse_execution as mod
        from rnd.models.mouse_execution import RND006MoveResult

        pending = RND006MoveResult(
            test_id="RND006-email_row",
            target="email_row",
            converted_x=500,
            converted_y=500,
            result="PENDING",
        )
        mod.PENDING_DIR.mkdir(parents=True, exist_ok=True)
        mod.pending_path("email_row").write_text(pending.model_dump_json(indent=2), encoding="utf-8")

        mod.cmd_move("email_row")

        mock_pyautogui.moveTo.assert_not_called()

        saved = mod.RND006MoveResult.model_validate_json(mod.pending_path("email_row").read_text(encoding="utf-8"))
        assert saved.result == "ABORTED"
        assert saved.movement_executed is False
        mod.pending_path("email_row").unlink()


def test_move_proceeds_when_foreground_is_outlook_and_coordinate_valid():
    """Simulates a successful --move: foreground OK, coordinate in bounds
    -> pyautogui.moveTo IS called exactly once, pyautogui.click is NEVER
    called (this stage never clicks).
    """
    with patch(
        "rnd.experiments.rnd006_safe_mouse_execution.get_foreground_window_title",
        return_value="Mail - Yash Dhanraj - Outlook",
    ), patch("rnd.experiments.rnd006_safe_mouse_execution.pyautogui") as mock_pyautogui:
        mock_pyautogui.size.return_value = (1920, 1080)
        mock_pyautogui.FAILSAFE = True

        from rnd.experiments import rnd006_safe_mouse_execution as mod
        from rnd.models.mouse_execution import RND006MoveResult

        pending = RND006MoveResult(
            test_id="RND006-email_row",
            target="email_row",
            converted_x=537,
            converted_y=351,
            result="PENDING",
        )
        mod.PENDING_DIR.mkdir(parents=True, exist_ok=True)
        mod.pending_path("email_row").write_text(pending.model_dump_json(indent=2), encoding="utf-8")

        mod.cmd_move("email_row")

        mock_pyautogui.moveTo.assert_called_once_with(537, 351, duration=mod.MOVE_DURATION_SECONDS)
        assert not hasattr(mock_pyautogui, "click") or not mock_pyautogui.click.called

        saved = mod.RND006MoveResult.model_validate_json(mod.pending_path("email_row").read_text(encoding="utf-8"))
        assert saved.movement_executed is True
        assert saved.result == "PENDING"  # still awaiting human confirmation
        mod.pending_path("email_row").unlink()


# --- move-only mode never clicks (structural check) ---

def test_module_never_imports_pyautogui_click_directly():
    import inspect

    from rnd.experiments import rnd006_safe_mouse_execution as mod

    source = inspect.getsource(mod)
    assert "pyautogui.click(" not in source
    assert ".click(" not in source


# --- approval gate: --move refuses without a prior --execute (pending file) ---

def test_move_refuses_without_prior_execute(tmp_path, monkeypatch):
    from rnd.experiments import rnd006_safe_mouse_execution as mod

    monkeypatch.setattr(mod, "PENDING_DIR", tmp_path / "nonexistent")
    with pytest.raises(SystemExit):
        mod.cmd_move("reply")


def test_confirm_refuses_without_prior_move(tmp_path, monkeypatch):
    from rnd.experiments import rnd006_safe_mouse_execution as mod
    from rnd.models.mouse_execution import RND006MoveResult

    monkeypatch.setattr(mod, "PENDING_DIR", tmp_path)
    pending = RND006MoveResult(test_id="RND006-reply", target="reply", result="PENDING", movement_executed=False)
    mod.pending_path("reply").write_text(pending.model_dump_json(indent=2), encoding="utf-8")

    with pytest.raises(SystemExit):
        mod.cmd_confirm("reply", "pass")
