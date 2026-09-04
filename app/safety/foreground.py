"""Foreground-window detection and window-state control.

Renamed/merged from app/safety/window_state.py (interpretation logic)
and rnd/experiments/capture_with_foreground_check.py::get_foreground_window_title
(the actual ctypes call, previously a load-bearing dependency living in
an "experiments" folder) — one module now owns all window-state
concerns for the final POC runtime.

Two DIFFERENT foreground-state allowlists, deliberately never merged:
before Outlook has actually launched, the foreground window may
legitimately belong to the Windows shell (Search, Start,
ShellExperienceHost, SearchHost, or an unlabeled overlay), NOT Outlook.
After the click, the check flips to requiring Outlook specifically.

Maximize detection/control (final POC addition): extends the same
raw-ctypes convention already used for foreground detection —
`IsZoomed`/`ShowWindow(SW_MAXIMIZE)` from user32.dll — rather than
introducing pygetwindow/pywin32 as a new dependency (neither is in
requirements.txt; pygetwindow is present only as pyautogui's unused
transitive dependency).
"""

from __future__ import annotations

import ctypes

SEARCH_STATE_ALLOWED_SUBSTRINGS = (
    "search",
    "start",
    "shellexperiencehost",
    "searchhost",
    "cortana",
)

OUTLOOK_FOREGROUND_SUBSTRING = "outlook"

SW_MAXIMIZE = 3


def get_foreground_window_title() -> str:
    hwnd = ctypes.windll.user32.GetForegroundWindow()
    length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def get_foreground_hwnd() -> int:
    return ctypes.windll.user32.GetForegroundWindow()


def is_maximized(hwnd: int) -> bool:
    """Wraps user32!IsZoomed — true when the given window is maximized."""
    return bool(ctypes.windll.user32.IsZoomed(hwnd))


def maximize(hwnd: int) -> None:
    """Wraps user32!ShowWindow(hwnd, SW_MAXIMIZE). Idempotent — calling it
    on an already-maximized window is a harmless no-op at the OS level,
    but callers should still check is_maximized() first (see
    app/outlook/launch.py::enforce_maximized(), Phase 2) so the action
    log only records a real state change."""
    ctypes.windll.user32.ShowWindow(hwnd, SW_MAXIMIZE)


def is_search_state_foreground(title: str) -> bool:
    normalized = (title or "").strip().lower()
    if normalized == "":
        return True
    return any(s in normalized for s in SEARCH_STATE_ALLOWED_SUBSTRINGS)


def is_outlook_foreground(title: str) -> bool:
    return OUTLOOK_FOREGROUND_SUBSTRING in (title or "").lower()
