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
import time

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


def confirm_outlook_foreground_with_recheck(max_recheck_attempts: int = 0, recheck_wait_seconds: float = 0.0) -> str:
    """Returns the observed foreground window title. Requires Outlook on
    the FIRST check for the common/fast path — zero added delay when
    Outlook is already foreground, exactly like a plain
    get_foreground_window_title() call.

    2026-09-06 live fix: a live run's physical-action foreground check
    (immediately before moving/clicking Reply) observed the Windows
    Alt-Tab / task-switcher overlay ("Task Switching") as the foreground
    window for a single instantaneous check, right after a long pair of
    Vision calls — a transient window-manager state, not a genuine loss
    of Outlook focus. Only when the FIRST check is not Outlook does this
    retry up to max_recheck_attempts more times, each after
    recheck_wait_seconds, mirroring the same bounded-poll idiom already
    used by OutlookLaunchSteps.poll_for_outlook_foreground() — never an
    unbounded loop, and never a reason to skip the caller's own final
    is_outlook_foreground(title) decision on the returned title. If
    Outlook never reappears within the bounded attempts, the LAST
    observed (non-Outlook) title is returned, and the caller's existing
    safe-stop behavior is completely unchanged."""
    title = get_foreground_window_title()
    if is_outlook_foreground(title):
        return title
    for _ in range(max_recheck_attempts):
        time.sleep(recheck_wait_seconds)
        title = get_foreground_window_title()
        if is_outlook_foreground(title):
            return title
    return title
