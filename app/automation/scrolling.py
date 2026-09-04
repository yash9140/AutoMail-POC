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
    from scroll_message_list()'s anchor."""
    anchor_x = round(screen_width * EMAIL_BODY_SCROLL_ANCHOR_X_FRACTION)
    anchor_y = round(screen_height * EMAIL_BODY_SCROLL_ANCHOR_Y_FRACTION)
    assert pyautogui.FAILSAFE is True
    pyautogui.moveTo(anchor_x, anchor_y)
    pyautogui.scroll(EMAIL_BODY_SCROLL_AMOUNT)
