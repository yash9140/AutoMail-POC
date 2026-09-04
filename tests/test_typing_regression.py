"""Golden-trace regression test for app/outlook/draft.py::type_draft().

Proves the segmented-typing method — moved character-for-character from
the original app/playbook/reply_draft_steps.py::type_draft() (RND-007B's
hardened method) — produces the exact same call sequence for a
representative multi-line draft: one pyautogui.write() per non-empty
line (never a string containing '\\n'), one explicit pyautogui.press
("enter") between segments (never after the last), and a foreground
check immediately before every write and every Enter.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.settings import TYPE_INTERVAL_SECONDS  # noqa: E402
from app.outlook.draft import ReplyDraftSteps  # noqa: E402
from app.safety.abort_controller import AbortController  # noqa: E402

MODULE = "app.outlook.draft"

GOLDEN_DRAFT = "Hi Yash,\n\nSure, I will complete the testing by EOD today.\n\nThanks"

# Expected golden trace, hand-derived from the original method's
# behavior: split on "\n" -> ["Hi Yash,", "", "Sure, I will complete the "
# "testing by EOD today.", "", "Thanks"]. Empty segments are skipped for
# write() (no call for the blank line) but Enter is still pressed after
# every segment except the last, per the original `if i < len(segments) - 1`.
EXPECTED_WRITE_CALLS = [
    "Hi Yash,",
    "Sure, I will complete the testing by EOD today.",
    "Thanks",
]
EXPECTED_ENTER_COUNT = 4  # segments has 5 entries -> 4 Enters between them


def test_type_draft_matches_golden_segment_and_enter_trace():
    steps = ReplyDraftSteps(AbortController(), MagicMock(), "gemini-3.6-flash")
    steps.result.draft_reply = GOLDEN_DRAFT
    steps.result.draft_quality_passed = True

    with patch(f"{MODULE}.pyautogui") as mock_pyautogui, patch(f"{MODULE}.time.sleep"), \
         patch(f"{MODULE}.get_foreground_window_title", return_value="Outlook"):
        mock_pyautogui.FAILSAFE = True
        assert steps.type_draft() is True

        write_texts = [c.args[0] for c in mock_pyautogui.write.call_args_list]
        assert write_texts == EXPECTED_WRITE_CALLS
        for call in mock_pyautogui.write.call_args_list:
            assert "\n" not in call.args[0]
            assert call.kwargs.get("interval") == TYPE_INTERVAL_SECONDS

        press_calls = [c.args[0] for c in mock_pyautogui.press.call_args_list]
        assert press_calls == ["enter"] * EXPECTED_ENTER_COUNT

    assert steps.result.typed_text == GOLDEN_DRAFT
    assert steps.result.typing_result == "PASS"
    assert steps.result.text_entry_executed is True


def test_single_line_draft_produces_no_enter_press():
    steps = ReplyDraftSteps(AbortController(), MagicMock(), "gemini-3.6-flash")
    steps.result.draft_reply = "Just one line, no newlines at all."
    steps.result.draft_quality_passed = True

    with patch(f"{MODULE}.pyautogui") as mock_pyautogui, patch(f"{MODULE}.time.sleep"), \
         patch(f"{MODULE}.get_foreground_window_title", return_value="Outlook"):
        mock_pyautogui.FAILSAFE = True
        assert steps.type_draft() is True
        assert mock_pyautogui.write.call_count == 1
        mock_pyautogui.press.assert_not_called()
