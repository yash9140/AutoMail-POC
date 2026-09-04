"""Unit tests for RND-007B contextual reply generation + draft entry.
pyautogui is mocked throughout — no real keyboard/mouse actions occur.
"""

import inspect
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rnd.models.contextual_reply import (  # noqa: E402
    EmailUnderstandingResponse,
    RND007BResult,
    ReplyGenerationResponse,
)


# --- structural: Send / send-hotkeys never reachable ---

def test_module_never_sends_send_hotkeys_or_clicks_send():
    from rnd.experiments import rnd007b_contextual_reply as mod

    source = inspect.getsource(mod)
    normalized = source.replace('"', "'")
    assert "hotkey('ctrl', 'enter')" not in normalized
    assert "hotkey('alt', 's')" not in normalized
    assert '"send"' not in source.lower().replace("'", '"') or "blocked target" in source.lower()
    # "send" appears only in comparison/blocklist contexts (e.g. checking the
    # model didn't return "send" as a grounding target), never as a target
    # this module clicks toward.

    # press('enter') is allowed ONLY as the line-break mechanism inside the
    # multiline typing loop (RND-007B Attempt 2 correction) — never as a
    # bare submit/shortcut press elsewhere. Exactly one call site, and it
    # must be gated by the segment-loop condition (not a standalone call),
    # positioned after the pre-RND-008 mid-action foreground check.
    assert normalized.count("press('enter')") == 1
    assert "if i < len(segments) - 1:" in normalized
    loop_body = normalized.split("if i < len(segments) - 1:", 1)[1].split("pyautogui.press('enter')", 1)[0]
    assert "get_foreground_window_title" in loop_body  # foreground re-checked before the Enter press
    assert "return" in loop_body  # aborts rather than falling through to the press


def test_click_and_write_each_appear_at_most_once_in_source():
    from rnd.experiments import rnd007b_contextual_reply as mod

    source = inspect.getsource(mod)
    assert source.count("pyautogui.click()") == 1  # exactly one click call site, for Reply only
    assert source.count("pyautogui.write(") == 1   # exactly one write call site, for the draft only


# --- incorrect email understanding stops flow / human rejection prevents generation ---

def test_reject_understanding_stops_flow(tmp_path, monkeypatch):
    from rnd.experiments import rnd007b_contextual_reply as mod

    monkeypatch.setattr(mod, "PENDING_PATH", tmp_path / "pending.json")
    pending = RND007BResult(
        email_understanding=EmailUnderstandingResponse(
            email_summary="s", sender_intent="i", requires_reply=True,
            requested_action="a", important_points=[], confidence=0.9,
        )
    )
    mod.save_pending(pending)

    mod.cmd_approve_understanding("fail")

    saved = mod.load_pending()
    assert saved.human_understanding_approved is False
    assert saved.result == "ABORTED"


def test_generate_refuses_when_understanding_not_approved(tmp_path, monkeypatch):
    from rnd.experiments import rnd007b_contextual_reply as mod

    monkeypatch.setattr(mod, "PENDING_PATH", tmp_path / "pending.json")
    pending = RND007BResult(
        email_understanding=EmailUnderstandingResponse(
            email_summary="s", sender_intent="i", requires_reply=True,
            requested_action="a", important_points=[], confidence=0.9,
        ),
        human_understanding_approved=False,
    )
    mod.save_pending(pending)

    with pytest.raises(SystemExit):
        mod.cmd_generate()


# --- human rejection of draft prevents typing ---

def test_reject_draft_prevents_typing(tmp_path, monkeypatch):
    from rnd.experiments import rnd007b_contextual_reply as mod

    monkeypatch.setattr(mod, "PENDING_PATH", tmp_path / "pending.json")
    pending = RND007BResult(
        reply_generation=ReplyGenerationResponse(draft_reply="Hi", reasoning_summary="r", confidence=0.9),
    )
    mod.save_pending(pending)

    mod.cmd_approve_draft("fail")

    saved = mod.load_pending()
    assert saved.human_draft_approved is False
    assert saved.result == "ABORTED"


def test_type_refuses_when_draft_not_approved(tmp_path, monkeypatch):
    from rnd.experiments import rnd007b_contextual_reply as mod

    monkeypatch.setattr(mod, "PENDING_PATH", tmp_path / "pending.json")
    pending = RND007BResult(
        reply_generation=ReplyGenerationResponse(draft_reply="Hi", reasoning_summary="r", confidence=0.9),
        human_draft_approved=False,
    )
    mod.save_pending(pending)

    with pytest.raises(SystemExit):
        mod.cmd_type()


# --- reply editor already open -> no extra Reply click ---

def test_reply_editor_already_open_skips_click(tmp_path, monkeypatch):
    from rnd.experiments import rnd007b_contextual_reply as mod

    monkeypatch.setattr(mod, "PENDING_PATH", tmp_path / "pending.json")
    pending = RND007BResult(
        reply_generation=ReplyGenerationResponse(draft_reply="Thanks for your email.", reasoning_summary="r", confidence=0.9),
        human_draft_approved=True,
    )
    mod.save_pending(pending)

    with patch("rnd.experiments.rnd007b_contextual_reply.get_foreground_window_title", return_value="Mail - Yash Dhanraj - Outlook"), \
         patch("rnd.experiments.rnd007b_contextual_reply.capture_screen", return_value=MagicMock(filename="x.png", path="x.png", width=1920, height=1080)), \
         patch("rnd.experiments.rnd007b_contextual_reply._provider", return_value=(MagicMock(), "gemini-3.6-flash")), \
         patch("rnd.experiments.rnd007b_contextual_reply._check_reply_editor_open", return_value=True), \
         patch("rnd.experiments.rnd007b_contextual_reply.pyautogui") as mock_pyautogui:
        mock_pyautogui.FAILSAFE = True

        mod.cmd_type()

        mock_pyautogui.moveTo.assert_not_called()
        mock_pyautogui.click.assert_not_called()
        mock_pyautogui.write.assert_called_once()

        saved = mod.load_pending()
        assert saved.reply_editor_state_before_typing == "already_open"
        assert saved.typing_result == "PASS"


# --- reply editor closed -> safe Reply flow required ---

def test_reply_editor_closed_requires_click_before_typing(tmp_path, monkeypatch):
    from rnd.experiments import rnd007b_contextual_reply as mod

    monkeypatch.setattr(mod, "PENDING_PATH", tmp_path / "pending.json")
    pending = RND007BResult(
        reply_generation=ReplyGenerationResponse(draft_reply="Thanks for your email.", reasoning_summary="r", confidence=0.9),
        human_draft_approved=True,
    )
    mod.save_pending(pending)

    grounding_response = MagicMock(
        parsed_json={"target": "Reply", "x": 477, "y": 584, "confidence": 0.95, "reason": "r"},
        model="gemini-3.6-flash", latency_ms=100.0, input_tokens=10, output_tokens=5,
    )
    mock_provider = MagicMock()
    mock_provider.analyze_screen.return_value = grounding_response

    with patch("rnd.experiments.rnd007b_contextual_reply.get_foreground_window_title", return_value="Mail - Yash Dhanraj - Outlook"), \
         patch("rnd.experiments.rnd007b_contextual_reply.capture_screen", return_value=MagicMock(filename="x.png", path="x.png", width=1920, height=1080)), \
         patch("rnd.experiments.rnd007b_contextual_reply._provider", return_value=(mock_provider, "gemini-3.6-flash")), \
         patch("rnd.experiments.rnd007b_contextual_reply._check_reply_editor_open", return_value=False), \
         patch("rnd.experiments.rnd007b_contextual_reply.time.sleep"), \
         patch("rnd.experiments.rnd007b_contextual_reply.pyautogui") as mock_pyautogui:
        mock_pyautogui.size.return_value = (1920, 1080)
        mock_pyautogui.FAILSAFE = True

        mod.cmd_type()

        mock_pyautogui.moveTo.assert_called_once()
        mock_pyautogui.click.assert_called_once()
        mock_pyautogui.write.assert_called_once()

        saved = mod.load_pending()
        assert saved.reply_editor_state_before_typing == "clicked_reply"


# --- text typed exactly once ---

def test_draft_text_typed_exactly_once_with_correct_content(tmp_path, monkeypatch):
    from rnd.experiments import rnd007b_contextual_reply as mod

    monkeypatch.setattr(mod, "PENDING_PATH", tmp_path / "pending.json")
    draft = "Thanks for your email. I will confirm by Friday."
    pending = RND007BResult(
        reply_generation=ReplyGenerationResponse(draft_reply=draft, reasoning_summary="r", confidence=0.9),
        human_draft_approved=True,
    )
    mod.save_pending(pending)

    with patch("rnd.experiments.rnd007b_contextual_reply.get_foreground_window_title", return_value="Mail - Yash Dhanraj - Outlook"), \
         patch("rnd.experiments.rnd007b_contextual_reply.capture_screen", return_value=MagicMock(filename="x.png", path="x.png", width=1920, height=1080)), \
         patch("rnd.experiments.rnd007b_contextual_reply._provider", return_value=(MagicMock(), "gemini-3.6-flash")), \
         patch("rnd.experiments.rnd007b_contextual_reply._check_reply_editor_open", return_value=True), \
         patch("rnd.experiments.rnd007b_contextual_reply.pyautogui") as mock_pyautogui:
        mock_pyautogui.FAILSAFE = True

        mod.cmd_type()

        mock_pyautogui.write.assert_called_once_with(draft, interval=mod.TYPE_INTERVAL_SECONDS)


# --- verification does not trigger retyping ---

def test_verify_never_calls_pyautogui_write(tmp_path, monkeypatch):
    from rnd.experiments import rnd007b_contextual_reply as mod

    monkeypatch.setattr(mod, "PENDING_PATH", tmp_path / "pending.json")
    pending = RND007BResult(
        reply_generation=ReplyGenerationResponse(draft_reply="Hi there.", reasoning_summary="r", confidence=0.9),
        text_entry_executed=True, typing_result="PASS", typed_text="Hi there.",
    )
    mod.save_pending(pending)

    verify_response = MagicMock(
        parsed_json={"semantic_match": True, "detected_draft": "Hi there.", "confidence": 0.95, "reason": "matches"},
        raw_text="{}", model="gemini-3.6-flash", latency_ms=100.0, input_tokens=10, output_tokens=5,
    )
    mock_provider = MagicMock()
    mock_provider.analyze_screen.return_value = verify_response

    with patch("rnd.experiments.rnd007b_contextual_reply.get_foreground_window_title", return_value="Mail - Yash Dhanraj - Outlook"), \
         patch("rnd.experiments.rnd007b_contextual_reply.capture_screen", return_value=MagicMock(filename="post.png", path="post.png")), \
         patch("rnd.experiments.rnd007b_contextual_reply._provider", return_value=(mock_provider, "gemini-3.6-flash")), \
         patch("rnd.experiments.rnd007b_contextual_reply.time.sleep"), \
         patch("rnd.experiments.rnd007b_contextual_reply.pyautogui") as mock_pyautogui:
        mod.cmd_verify()

        mock_pyautogui.write.assert_not_called()
        mock_pyautogui.click.assert_not_called()

        saved = mod.load_pending()
        assert saved.semantic_match is True
        assert saved.detected_draft == "Hi there."


def test_verify_aborts_when_outlook_not_foreground(tmp_path, monkeypatch):
    from rnd.experiments import rnd007b_contextual_reply as mod

    monkeypatch.setattr(mod, "PENDING_PATH", tmp_path / "pending.json")
    pending = RND007BResult(
        reply_generation=ReplyGenerationResponse(draft_reply="Hi there.", reasoning_summary="r", confidence=0.9),
        text_entry_executed=True, typing_result="PASS", typed_text="Hi there.",
    )
    mod.save_pending(pending)

    with patch("rnd.experiments.rnd007b_contextual_reply.get_foreground_window_title", return_value="screen_x.png - AutoLook-POC - Visual Studio Code"), \
         patch("rnd.experiments.rnd007b_contextual_reply.capture_screen") as mock_capture, \
         patch("rnd.experiments.rnd007b_contextual_reply._provider") as mock_provider_fn, \
         patch("rnd.experiments.rnd007b_contextual_reply.time.sleep"):
        mod.cmd_verify()

        # verification never captures when the foreground check fails, and
        # never even constructs a vision-provider client for a call it
        # isn't going to make
        mock_capture.assert_not_called()
        mock_provider_fn.assert_not_called()

    saved = mod.load_pending()
    assert saved.result == "ABORTED"
    assert saved.failure_reason == "FOREGROUND_MISMATCH"
    assert saved.post_screenshot is None
    assert saved.semantic_match is None
    assert saved.detected_draft is None


def test_typing_failure_remains_separate_from_verification_result(tmp_path, monkeypatch):
    from rnd.experiments import rnd007b_contextual_reply as mod

    monkeypatch.setattr(mod, "PENDING_PATH", tmp_path / "pending.json")
    pending = RND007BResult(
        reply_generation=ReplyGenerationResponse(draft_reply="Hi there.", reasoning_summary="r", confidence=0.9),
        text_entry_executed=True, typing_result="FAIL", typed_text="Hi there.",
    )
    mod.save_pending(pending)

    verify_response = MagicMock(
        parsed_json={"semantic_match": False, "detected_draft": "", "confidence": 0.9, "reason": "editor empty"},
        raw_text="{}", model="gemini-3.6-flash", latency_ms=100.0, input_tokens=10, output_tokens=5,
    )
    mock_provider = MagicMock()
    mock_provider.analyze_screen.return_value = verify_response

    with patch("rnd.experiments.rnd007b_contextual_reply.get_foreground_window_title", return_value="Mail - Yash Dhanraj - Outlook"), \
         patch("rnd.experiments.rnd007b_contextual_reply.capture_screen", return_value=MagicMock(filename="post.png", path="post.png")), \
         patch("rnd.experiments.rnd007b_contextual_reply._provider", return_value=(mock_provider, "gemini-3.6-flash")), \
         patch("rnd.experiments.rnd007b_contextual_reply.time.sleep"):
        mod.cmd_verify()

    saved = mod.load_pending()
    # typing_result ("FAIL", set by --type or a human correction) is never
    # touched by verification, and verification's own judgment is recorded
    # independently — the two metrics are never collapsed into one.
    assert saved.typing_result == "FAIL"
    assert saved.semantic_match is False
    assert saved.human_verification is None


# --- RND-007B Attempt 2: corrected multiline text entry ---

def test_multiline_draft_split_into_segments_never_passed_to_write_with_newline(tmp_path, monkeypatch):
    from rnd.experiments import rnd007b_contextual_reply as mod

    monkeypatch.setattr(mod, "PENDING_PATH", tmp_path / "pending.json")
    draft = "Hi Yash,\n\nThank you for reaching out.\n\nBest regards,"
    pending = RND007BResult(
        reply_generation=ReplyGenerationResponse(draft_reply=draft, reasoning_summary="r", confidence=0.9),
        human_draft_approved=True,
    )
    mod.save_pending(pending)

    with patch("rnd.experiments.rnd007b_contextual_reply.get_foreground_window_title", return_value="Mail - Yash Dhanraj - Outlook"), \
         patch("rnd.experiments.rnd007b_contextual_reply.capture_screen", return_value=MagicMock(filename="x.png", path="x.png", width=1920, height=1080)), \
         patch("rnd.experiments.rnd007b_contextual_reply._provider", return_value=(MagicMock(), "gemini-3.6-flash")), \
         patch("rnd.experiments.rnd007b_contextual_reply._check_reply_editor_open", return_value=True), \
         patch("rnd.experiments.rnd007b_contextual_reply.time.sleep"), \
         patch("rnd.experiments.rnd007b_contextual_reply.pyautogui") as mock_pyautogui:
        mock_pyautogui.FAILSAFE = True

        mod.cmd_type()

        write_calls = [c.args[0] for c in mock_pyautogui.write.call_args_list]
        # pyautogui.write() never receives a string containing "\n"
        for w in write_calls:
            assert "\n" not in w

        # draft typed exactly once: concatenating the non-empty segments
        # (in order) reproduces every line of the original draft, no
        # duplication, nothing dropped.
        expected_non_empty_segments = [s for s in draft.split("\n") if s]
        assert write_calls == expected_non_empty_segments

        # newline is converted to explicit Enter presses: one press per "\n"
        enter_presses = [c for c in mock_pyautogui.press.call_args_list if c.args and c.args[0] == "enter"]
        assert len(enter_presses) == draft.count("\n")

        saved = mod.load_pending()
        assert saved.typed_text == draft
        assert saved.typing_result == "PASS"


def test_two_consecutive_newlines_produce_two_enter_presses_in_order(tmp_path, monkeypatch):
    from rnd.experiments import rnd007b_contextual_reply as mod

    monkeypatch.setattr(mod, "PENDING_PATH", tmp_path / "pending.json")
    draft = "A\n\nB"
    pending = RND007BResult(
        reply_generation=ReplyGenerationResponse(draft_reply=draft, reasoning_summary="r", confidence=0.9),
        human_draft_approved=True,
    )
    mod.save_pending(pending)

    with patch("rnd.experiments.rnd007b_contextual_reply.get_foreground_window_title", return_value="Mail - Yash Dhanraj - Outlook"), \
         patch("rnd.experiments.rnd007b_contextual_reply.capture_screen", return_value=MagicMock(filename="x.png", path="x.png", width=1920, height=1080)), \
         patch("rnd.experiments.rnd007b_contextual_reply._provider", return_value=(MagicMock(), "gemini-3.6-flash")), \
         patch("rnd.experiments.rnd007b_contextual_reply._check_reply_editor_open", return_value=True), \
         patch("rnd.experiments.rnd007b_contextual_reply.time.sleep"), \
         patch("rnd.experiments.rnd007b_contextual_reply.pyautogui") as mock_pyautogui:
        mock_pyautogui.FAILSAFE = True

        mod.cmd_type()

        ordered = [name for name, _args, _kwargs in mock_pyautogui.method_calls if name in ("write", "press")]
        assert ordered == ["write", "press", "press", "write"]


# --- pre-RND-008 hardening: mid-action foreground protection ---

def test_foreground_remains_outlook_through_all_segments_typing_completes(tmp_path, monkeypatch):
    from rnd.experiments import rnd007b_contextual_reply as mod

    monkeypatch.setattr(mod, "PENDING_PATH", tmp_path / "pending.json")
    draft = "Hi Yash,\n\nThank you.\n\nBest regards,"
    pending = RND007BResult(
        reply_generation=ReplyGenerationResponse(draft_reply=draft, reasoning_summary="r", confidence=0.9),
        human_draft_approved=True,
    )
    mod.save_pending(pending)

    with patch("rnd.experiments.rnd007b_contextual_reply.get_foreground_window_title", return_value="Mail - Yash Dhanraj - Outlook"), \
         patch("rnd.experiments.rnd007b_contextual_reply.capture_screen", return_value=MagicMock(filename="x.png", path="x.png", width=1920, height=1080)), \
         patch("rnd.experiments.rnd007b_contextual_reply._provider", return_value=(MagicMock(), "gemini-3.6-flash")), \
         patch("rnd.experiments.rnd007b_contextual_reply._check_reply_editor_open", return_value=True), \
         patch("rnd.experiments.rnd007b_contextual_reply.time.sleep"), \
         patch("rnd.experiments.rnd007b_contextual_reply.pyautogui") as mock_pyautogui:
        mock_pyautogui.FAILSAFE = True

        mod.cmd_type()

    saved = mod.load_pending()
    assert saved.typing_result == "PASS"
    assert saved.typed_text == draft
    assert saved.text_entry_executed is True


def test_foreground_changes_before_segment_aborts_without_typing_it(tmp_path, monkeypatch):
    from rnd.experiments import rnd007b_contextual_reply as mod

    monkeypatch.setattr(mod, "PENDING_PATH", tmp_path / "pending.json")
    draft = "Line one\nLine two\nLine three"  # 3 segments, boundaries between each
    pending = RND007BResult(
        reply_generation=ReplyGenerationResponse(draft_reply=draft, reasoning_summary="r", confidence=0.9),
        human_draft_approved=True,
    )
    mod.save_pending(pending)

    # Outlook foreground for every check up through typing "Line one" and
    # pressing the Enter after it, then focus drifts away right before
    # "Line two" would be typed.
    titles = [
        "Mail - Yash Dhanraj - Outlook",  # initial --type foreground check
        "Mail - Yash Dhanraj - Outlook",  # pre-typing foreground check (title4)
        "Mail - Yash Dhanraj - Outlook",  # before segment 0 ("Line one")
        "Mail - Yash Dhanraj - Outlook",  # before Enter after segment 0
        "Notepad - Untitled",             # before segment 1 ("Line two") — drift
    ]

    with patch("rnd.experiments.rnd007b_contextual_reply.get_foreground_window_title", side_effect=titles), \
         patch("rnd.experiments.rnd007b_contextual_reply.capture_screen", return_value=MagicMock(filename="x.png", path="x.png", width=1920, height=1080)), \
         patch("rnd.experiments.rnd007b_contextual_reply._provider", return_value=(MagicMock(), "gemini-3.6-flash")), \
         patch("rnd.experiments.rnd007b_contextual_reply._check_reply_editor_open", return_value=True), \
         patch("rnd.experiments.rnd007b_contextual_reply.time.sleep"), \
         patch("rnd.experiments.rnd007b_contextual_reply.pyautogui") as mock_pyautogui:
        mock_pyautogui.FAILSAFE = True

        mod.cmd_type()

        # exactly one segment written ("Line one"), one Enter pressed, and
        # NO further keys sent once the drift was detected
        mock_pyautogui.write.assert_called_once_with("Line one", interval=mod.TYPE_INTERVAL_SECONDS)
        mock_pyautogui.press.assert_called_once_with("enter")

    saved = mod.load_pending()
    assert saved.typing_result == "ABORTED"
    assert saved.result == "ABORTED"
    assert saved.failure_reason == "FOREGROUND_CHANGED_DURING_TYPING"
    assert saved.abort_stage == "before_write"
    assert saved.segment_index_aborted_at == 1
    assert saved.segments_typed_before_abort == 1
    assert saved.typed_text == "Line one"
    assert saved.text_entry_executed is False  # never reached the successful-completion assignment


def test_foreground_changes_before_enter_aborts_without_pressing_it(tmp_path, monkeypatch):
    from rnd.experiments import rnd007b_contextual_reply as mod

    monkeypatch.setattr(mod, "PENDING_PATH", tmp_path / "pending.json")
    draft = "Line one\nLine two"  # 2 segments, one boundary
    pending = RND007BResult(
        reply_generation=ReplyGenerationResponse(draft_reply=draft, reasoning_summary="r", confidence=0.9),
        human_draft_approved=True,
    )
    mod.save_pending(pending)

    titles = [
        "Mail - Yash Dhanraj - Outlook",  # initial --type foreground check
        "Mail - Yash Dhanraj - Outlook",  # pre-typing foreground check (title4)
        "Mail - Yash Dhanraj - Outlook",  # before segment 0 ("Line one")
        "Notepad - Untitled",             # before Enter after segment 0 — drift
    ]

    with patch("rnd.experiments.rnd007b_contextual_reply.get_foreground_window_title", side_effect=titles), \
         patch("rnd.experiments.rnd007b_contextual_reply.capture_screen", return_value=MagicMock(filename="x.png", path="x.png", width=1920, height=1080)), \
         patch("rnd.experiments.rnd007b_contextual_reply._provider", return_value=(MagicMock(), "gemini-3.6-flash")), \
         patch("rnd.experiments.rnd007b_contextual_reply._check_reply_editor_open", return_value=True), \
         patch("rnd.experiments.rnd007b_contextual_reply.time.sleep"), \
         patch("rnd.experiments.rnd007b_contextual_reply.pyautogui") as mock_pyautogui:
        mock_pyautogui.FAILSAFE = True

        mod.cmd_type()

        mock_pyautogui.write.assert_called_once_with("Line one", interval=mod.TYPE_INTERVAL_SECONDS)
        mock_pyautogui.press.assert_not_called()  # the drift was caught before this Enter

    saved = mod.load_pending()
    assert saved.typing_result == "ABORTED"
    assert saved.failure_reason == "FOREGROUND_CHANGED_DURING_TYPING"
    assert saved.abort_stage == "before_enter"
    assert saved.segment_index_aborted_at == 0
    assert saved.segments_typed_before_abort == 1
    assert saved.typed_text == "Line one"


# --- pre-RND-008 hardening: helper-level (_check_reply_editor_open) calls
# must contribute to the running totals, not be invisible from them ---

def test_reply_editor_state_check_call_is_accumulated_into_totals(tmp_path, monkeypatch):
    from rnd.experiments import rnd007b_contextual_reply as mod

    monkeypatch.setattr(mod, "PENDING_PATH", tmp_path / "pending.json")
    pending = RND007BResult(
        reply_generation=ReplyGenerationResponse(draft_reply="Thanks for your email.", reasoning_summary="r", confidence=0.9),
        human_draft_approved=True,
    )
    mod.save_pending(pending)

    state_check_response = MagicMock(
        parsed_json={
            "verified": True, "detected_state": "reply editor open", "confidence": 0.9,
            "visual_evidence": "cursor visible in body", "reason": "editor is focused",
        },
        model="gemini-3.6-flash", latency_ms=321.0, input_tokens=44, output_tokens=11,
    )
    mock_provider = MagicMock()
    mock_provider.analyze_screen.return_value = state_check_response

    with patch("rnd.experiments.rnd007b_contextual_reply.get_foreground_window_title", return_value="Mail - Yash Dhanraj - Outlook"), \
         patch("rnd.experiments.rnd007b_contextual_reply.capture_screen", return_value=MagicMock(filename="x.png", path="x.png", width=1920, height=1080)), \
         patch("rnd.experiments.rnd007b_contextual_reply._provider", return_value=(mock_provider, "gemini-3.6-flash")), \
         patch("rnd.experiments.rnd007b_contextual_reply.time.sleep"), \
         patch("rnd.experiments.rnd007b_contextual_reply.pyautogui") as mock_pyautogui:
        mock_pyautogui.FAILSAFE = True

        # _check_reply_editor_open is NOT mocked here — it runs for real
        # against the mocked provider, so its own accumulation code path
        # is what's under test.
        mod.cmd_type()

    saved = mod.load_pending()
    # helper Vision call is included in the total call count
    assert saved.total_vision_calls == 1
    # helper input/output tokens are accumulated
    assert saved.total_input_tokens == 44
    assert saved.total_output_tokens == 11
    # helper cost is accumulated (non-zero given pricing config has an entry for this model)
    assert saved.total_estimated_cost is not None
    # helper latency is accumulated
    assert saved.total_latency_ms == 321.0
    # and individually visible, not just folded into the totals silently
    assert saved.reply_editor_state_check_metrics.latency_ms == 321.0
    assert saved.reply_editor_state_check_metrics.input_tokens == 44
    assert saved.reply_editor_state_check_metrics.output_tokens == 11


def test_confirm_refuses_without_prior_typing(tmp_path, monkeypatch):
    from rnd.experiments import rnd007b_contextual_reply as mod

    monkeypatch.setattr(mod, "PENDING_PATH", tmp_path / "pending.json")
    pending = RND007BResult(text_entry_executed=False)
    mod.save_pending(pending)

    with pytest.raises(SystemExit):
        mod.cmd_confirm("pass")
