"""app/safety/foreground.py unit tests. ctypes.windll.user32 is mocked
throughout — no real Windows API call happens in this file.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.safety import foreground  # noqa: E402


def test_is_search_state_foreground_accepts_known_substrings():
    assert foreground.is_search_state_foreground("Search") is True
    assert foreground.is_search_state_foreground("Start") is True
    assert foreground.is_search_state_foreground("") is True  # empty title treated as inconclusive-but-acceptable
    assert foreground.is_search_state_foreground("Microsoft Outlook") is False


def test_is_outlook_foreground_matches_substring_case_insensitively():
    assert foreground.is_outlook_foreground("Mail - Yash Dhanraj - Outlook") is True
    assert foreground.is_outlook_foreground("OUTLOOK") is True
    assert foreground.is_outlook_foreground("Visual Studio Code") is False


def test_is_maximized_wraps_iszoomed():
    with patch(f"{foreground.__name__}.ctypes") as mock_ctypes:
        mock_ctypes.windll.user32.IsZoomed.return_value = 1
        assert foreground.is_maximized(12345) is True
        mock_ctypes.windll.user32.IsZoomed.assert_called_once_with(12345)


def test_is_maximized_false_when_not_zoomed():
    with patch(f"{foreground.__name__}.ctypes") as mock_ctypes:
        mock_ctypes.windll.user32.IsZoomed.return_value = 0
        assert foreground.is_maximized(12345) is False


def test_maximize_calls_show_window_with_sw_maximize():
    with patch(f"{foreground.__name__}.ctypes") as mock_ctypes:
        foreground.maximize(12345)
        mock_ctypes.windll.user32.ShowWindow.assert_called_once_with(12345, foreground.SW_MAXIMIZE)


def test_get_foreground_hwnd_wraps_getforegroundwindow():
    with patch(f"{foreground.__name__}.ctypes") as mock_ctypes:
        mock_ctypes.windll.user32.GetForegroundWindow.return_value = 999
        assert foreground.get_foreground_hwnd() == 999


# --- confirm_outlook_foreground_with_recheck() (2026-09-06 live fix) ---

def test_recheck_returns_immediately_when_outlook_already_foreground_zero_delay():
    with patch(f"{foreground.__name__}.get_foreground_window_title", return_value="Mail - Outlook") as mock_title, \
         patch(f"{foreground.__name__}.time.sleep") as mock_sleep:
        title = foreground.confirm_outlook_foreground_with_recheck(3, 0.5)
    assert title == "Mail - Outlook"
    mock_title.assert_called_once()  # no extra reads on the fast path
    mock_sleep.assert_not_called()  # zero added delay


def test_recheck_recovers_from_a_single_transient_non_outlook_read():
    with patch(f"{foreground.__name__}.get_foreground_window_title",
               side_effect=["Task Switching", "Mail - Outlook"]), \
         patch(f"{foreground.__name__}.time.sleep") as mock_sleep:
        title = foreground.confirm_outlook_foreground_with_recheck(3, 0.5)
    assert title == "Mail - Outlook"
    mock_sleep.assert_called_once_with(0.5)


def test_recheck_gives_up_after_bounded_attempts_returns_last_title():
    with patch(f"{foreground.__name__}.get_foreground_window_title", return_value="Visual Studio Code") as mock_title, \
         patch(f"{foreground.__name__}.time.sleep") as mock_sleep:
        title = foreground.confirm_outlook_foreground_with_recheck(3, 0.5)
    assert title == "Visual Studio Code"
    assert mock_title.call_count == 4  # 1 initial + 3 bounded rechecks, never unbounded
    assert mock_sleep.call_count == 3


def test_recheck_with_zero_max_attempts_behaves_like_a_plain_single_check():
    with patch(f"{foreground.__name__}.get_foreground_window_title", return_value="Visual Studio Code") as mock_title, \
         patch(f"{foreground.__name__}.time.sleep") as mock_sleep:
        title = foreground.confirm_outlook_foreground_with_recheck(0, 0.5)
    assert title == "Visual Studio Code"
    mock_title.assert_called_once()
    mock_sleep.assert_not_called()
