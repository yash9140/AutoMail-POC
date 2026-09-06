"""Controlled message-list scrolling.

Scrolling only decides WHEN to look again — it never determines WHERE
to click. The mouse is positioned before the scroll purely so the wheel
event lands on the message-list pane (Windows delivers wheel events to
whatever control is under the cursor); that position is a resolution-
relative fraction of the CURRENT screenshot's dimensions, never a fixed
pixel, and is never reused as a click target.
"""

from __future__ import annotations

import pyautogui

from app.config.settings import (
    EMAIL_BODY_SCROLL_AMOUNT,
    EMAIL_BODY_SCROLL_ANCHOR_X_FRACTION,
    EMAIL_BODY_SCROLL_ANCHOR_Y_FRACTION,
    MESSAGE_LIST_SCROLL_AMOUNT,
    MESSAGE_LIST_SCROLL_ANCHOR_X_FRACTION,
    MESSAGE_LIST_SCROLL_ANCHOR_Y_FRACTION,
)


def scroll_message_list(screen_width: int, screen_height: int) -> None:
    anchor_x = round(screen_width * MESSAGE_LIST_SCROLL_ANCHOR_X_FRACTION)
    anchor_y = round(screen_height * MESSAGE_LIST_SCROLL_ANCHOR_Y_FRACTION)
    assert pyautogui.FAILSAFE is True
    pyautogui.moveTo(anchor_x, anchor_y)
    pyautogui.scroll(MESSAGE_LIST_SCROLL_AMOUNT)


def scroll_email_body(screen_width: int, screen_height: int) -> None:
    """Scrolls the reading pane (never the message list) — anchored at a
    resolution-relative point inside the reading-pane region, distinct
    from scroll_message_list()'s anchor.

    Used ONLY by app.outlook.reply.py's own Reply-search scroll loop
    (see that module's docstring) — left completely unchanged by the
    2026-09-06 email-reading scroll-distance fix so Reply's own scroll
    behavior is never affected by it. app.outlook.read_email.py's own
    scrolling now goes through scroll_email_reading_body() below
    instead, with its own independent, explicit scroll amount."""
    anchor_x = round(screen_width * EMAIL_BODY_SCROLL_ANCHOR_X_FRACTION)
    anchor_y = round(screen_height * EMAIL_BODY_SCROLL_ANCHOR_Y_FRACTION)
    assert pyautogui.FAILSAFE is True
    pyautogui.moveTo(anchor_x, anchor_y)
    pyautogui.scroll(EMAIL_BODY_SCROLL_AMOUNT)


def scroll_email_reading_body(screen_width: int, screen_height: int, scroll_amount: int) -> None:
    """Scrolls the reading pane for LONG-EMAIL READING only (2026-09-06
    scroll-distance fix) — takes an EXPLICIT scroll_amount so
    app.outlook.read_email.py's bounded normal/boosted policy (see
    app.config.settings.EMAIL_READING_SCROLL_AMOUNT/_BOOSTED) can choose
    it per call, never a module-level constant baked in here.

    Deliberately a SEPARATE function from scroll_email_body() above —
    NOT a parameterized version of it — so recalibrating email-reading's
    own scroll magnitude can never silently change Reply's unrelated
    scroll behavior (Reply reuses scroll_email_body() and its own
    EMAIL_BODY_SCROLL_AMOUNT constant, untouched). Reuses the SAME
    anchor-position fractions as scroll_email_body() — that cursor
    position is genuinely shared geometry (a safe point inside the
    reading pane, clear of the sidebar), not a value with any
    independent calibration history of its own."""
    anchor_x = round(screen_width * EMAIL_BODY_SCROLL_ANCHOR_X_FRACTION)
    anchor_y = round(screen_height * EMAIL_BODY_SCROLL_ANCHOR_Y_FRACTION)
    assert pyautogui.FAILSAFE is True
    pyautogui.moveTo(anchor_x, anchor_y)
    pyautogui.scroll(scroll_amount)
