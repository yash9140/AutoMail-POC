"""Regression tests for the SendFlowSteps._reply_vision_crop_cache
AttributeError (2026-09-06).

Live evidence: a real run crashed with
    AttributeError: 'SendFlowSteps' object has no attribute
    '_reply_vision_crop_cache'
inside prepare_reply_editor() -> _get_reply_vision_crop(), even though
818 tests passed. Root cause: SendFlowSteps.__init__ (app/outlook/
send.py) does NOT call ReplyDraftSteps.__init__() cooperatively — it
duplicates ReplyDraftSteps.__init__'s field assignments instead (its own
docstring says so: "Mirrors ReplyDraftSteps.__init__ exactly"). The new
cache field was only ever added to ReplyDraftSteps.__init__, so any
SendFlowSteps instance — the class app/workers/send_worker.py actually
constructs — never got it. Every existing reply-crop test constructed
ReplyDraftSteps directly, never SendFlowSteps via its real constructor,
so the gap escaped the full suite.

This file proves: (1) the real SendFlowSteps constructor now initializes
the cache, (2) the real prepare_reply_editor() path runs to a REPLY_
SEARCH call with no AttributeError, (3) cache reuse/isolation semantics
are unchanged, (4) REPLY_SEARCH really does receive the crop.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.outlook.draft import ReplyDraftSteps  # noqa: E402
from app.outlook.send import SendFlowSteps  # noqa: E402
from app.safety.abort_controller import AbortController  # noqa: E402
from app.vision.crop import HorizontalVisionCrop  # noqa: E402
from app.vision.service import VisionRequest, VisionService  # noqa: E402
from tests._capture_test_utils import real_capture_image_path, to_reply_crop_relative_bbox  # noqa: E402

SEND_MODULE = "app.outlook.send"
REPLY_MODULE = "app.outlook.reply"


def _real_send_flow_steps() -> SendFlowSteps:
    """Constructs SendFlowSteps via the EXACT same constructor call shape
    app/workers/send_worker.py uses — never an isolated Reply mixin."""
    return SendFlowSteps(
        AbortController(), VisionService(MagicMock(), fallback=None), "test-model",
        send_approval_granted=True, target_sender="Yash", target_subject="Question Regarding Meeting Details",
    )


def _capture(width=1920, height=1080, filename="send_flow_reply.png"):
    return MagicMock(filename=filename, path=real_capture_image_path(width, height, name=filename), width=width, height=height)


def _state_check_call(verified: bool):
    return MagicMock(
        parsed_json={"verified": verified, "detected_state": "x", "confidence": 0.9,
                     "visual_evidence": "x", "reason": "x"},
        raw_text="{}", model="test-model", latency_ms=10.0, input_tokens=5, output_tokens=5,
    )


def _reply_search_call(bbox=(400.0, 600.0, 440.0, 700.0)):
    return MagicMock(
        parsed_json={
            "outlook_visible": True, "reply_visible": True, "control_identity": "Reply",
            "control_type": "button", "bbox": to_reply_crop_relative_bbox(list(bbox)),
            "more_content_below": False, "confidence": 0.9, "reason": "ok",
        },
        raw_text="{}", model="test-model", latency_ms=10.0, input_tokens=5, output_tokens=5,
    )


# --- 6: production-construction regression test ---

def test_A_real_send_flow_steps_has_reply_vision_crop_cache_immediately_after_construction():
    steps = _real_send_flow_steps()
    assert hasattr(steps, "_reply_vision_crop_cache")
    assert steps._reply_vision_crop_cache == {}


def test_A_get_reply_vision_crop_succeeds_on_a_real_send_flow_steps_instance(tmp_path):
    from PIL import Image

    steps = _real_send_flow_steps()
    image_path = tmp_path / "shot.png"
    Image.new("RGB", (1920, 1080), color=(20, 20, 20)).save(image_path)

    crop = steps._get_reply_vision_crop(str(image_path), 1920, 1080)
    assert isinstance(crop, HorizontalVisionCrop)
    assert crop.crop_path.exists()


# --- 7: real prepare_reply_editor() path never raises AttributeError ---

def test_B_prepare_reply_editor_via_real_send_flow_steps_raises_no_attribute_error():
    steps = _real_send_flow_steps()
    steps.result.content_complete = True
    steps.vision.primary.analyze_screen.side_effect = [_state_check_call(False), _reply_search_call()]

    with patch(f"{REPLY_MODULE}.get_foreground_window_title", return_value="Outlook"), \
         patch(f"{REPLY_MODULE}.confirm_outlook_foreground_with_recheck", return_value="Outlook"), \
         patch(f"{REPLY_MODULE}.capture_screen", return_value=_capture()), \
         patch(f"{REPLY_MODULE}.time.sleep"), \
         patch(f"{REPLY_MODULE}.pyautogui") as mock_pyautogui, \
         patch("app.automation.scrolling.pyautogui") as mock_scroll_pg:
        mock_pyautogui.FAILSAFE = True
        mock_scroll_pg.FAILSAFE = True
        # The bug reproduced here: previously raised
        # AttributeError('SendFlowSteps' object has no attribute
        # '_reply_vision_crop_cache'). Must now complete normally.
        ok = steps.prepare_reply_editor()

    assert ok is True
    assert mock_pyautogui.click.call_count == 1


# --- 9: REPLY_SEARCH wiring confirmed on the real SendFlowSteps path ---

def test_C_reply_search_stage_and_screenshot_path_on_real_send_flow_steps():
    steps = _real_send_flow_steps()
    steps.result.content_complete = True
    steps.vision.primary.analyze_screen.side_effect = [_state_check_call(False), _reply_search_call()]

    captured_requests = []
    real_analyze = steps.vision.analyze

    def _spy_analyze(request: VisionRequest, **kwargs):
        captured_requests.append(request)
        return real_analyze(request, **kwargs)

    with patch(f"{REPLY_MODULE}.get_foreground_window_title", return_value="Outlook"), \
         patch(f"{REPLY_MODULE}.confirm_outlook_foreground_with_recheck", return_value="Outlook"), \
         patch(f"{REPLY_MODULE}.capture_screen", return_value=_capture()), \
         patch(f"{REPLY_MODULE}.time.sleep"), \
         patch(f"{REPLY_MODULE}.pyautogui") as mock_pyautogui, \
         patch("app.automation.scrolling.pyautogui") as mock_scroll_pg, \
         patch.object(steps.vision, "analyze", side_effect=_spy_analyze):
        mock_pyautogui.FAILSAFE = True
        mock_scroll_pg.FAILSAFE = True
        assert steps.prepare_reply_editor() is True

    reply_search_requests = [r for r in captured_requests if r.stage == "REPLY_SEARCH"]
    assert len(reply_search_requests) == 1
    reply_crop = steps._reply_vision_crop_cache[list(steps._reply_vision_crop_cache.keys())[0]]
    assert reply_search_requests[0].screenshot_path == reply_crop.crop_path


# --- 8: cache reuse / isolation tests ---

def test_D_same_screenshot_path_returns_identical_cached_crop():
    steps = _real_send_flow_steps()
    path = real_capture_image_path(1920, 1080, name="cache_reuse_test.png")
    crop1 = steps._get_reply_vision_crop(path, 1920, 1080)
    crop2 = steps._get_reply_vision_crop(path, 1920, 1080)
    assert crop1 is crop2


def test_D_different_screenshot_path_creates_a_new_crop():
    steps = _real_send_flow_steps()
    path_a = real_capture_image_path(1920, 1080, name="cache_a.png")
    path_b = real_capture_image_path(1920, 1080, name="cache_b.png")
    crop_a = steps._get_reply_vision_crop(path_a, 1920, 1080)
    crop_b = steps._get_reply_vision_crop(path_b, 1920, 1080)
    assert crop_a is not crop_b
    assert crop_a.crop_path != crop_b.crop_path


def test_D_reply_crop_cache_is_initialized_on_real_send_flow_steps():
    steps = _real_send_flow_steps()
    assert steps._reply_vision_crop_cache == {}


def test_D_reply_crop_cache_never_shares_with_message_list_crop_cache():
    steps = _real_send_flow_steps()
    # SendFlowSteps composes a FindOpenEmailSteps (self.find_open), which
    # owns the message-list crop cache — a completely separate attribute/
    # dict object, never touched by REPLY_SEARCH's crop lookups.
    assert hasattr(steps.find_open, "_message_list_crop_cache")
    assert steps._reply_vision_crop_cache is not steps.find_open._message_list_crop_cache

    path = real_capture_image_path(1920, 1080, name="isolation_test.png")
    steps._get_reply_vision_crop(path, 1920, 1080)
    assert path not in steps.find_open._message_list_crop_cache
    assert path in steps._reply_vision_crop_cache


# --- defensive guard: a hypothetical composing class that forgets to
# initialize the cache degrades to "no caching," never an AttributeError ---

def test_defensive_guard_recovers_from_a_missing_cache_attribute():
    steps = ReplyDraftSteps(AbortController(), VisionService(MagicMock(), fallback=None), "test-model")
    del steps._reply_vision_crop_cache  # simulate a composing class that forgot to set it

    path = real_capture_image_path(1920, 1080, name="defensive_guard_test.png")
    crop = steps._get_reply_vision_crop(path, 1920, 1080)
    assert isinstance(crop, HorizontalVisionCrop)
    assert steps._reply_vision_crop_cache == {path: crop}
