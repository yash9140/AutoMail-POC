"""OUTLOOK_SEARCH semantic-quality tests: is the Vision response correctly
interpreted as "the Outlook desktop app result, confidently identified" vs.
everything else (wrong type, low confidence, malformed/technical failure)?

History: originally (2026-09-03) this file also covered bbox-tightness and
click-lifecycle mechanics, because the physical action for this stage used
to be a coordinate-based mouse click computed from Vision's bbox. That
mechanism was removed entirely in the 2026-09-06 keyboard-activation fix
(see app/outlook/launch.py::ground_search_result()/activate_outlook_result()
docstrings) — Vision's bbox is now diagnostic-only, never converted to a
click point. The click-lifecycle/bbox-geometry tests that used to live here
were superseded by tests/test_outlook_search_keyboard_activation.py, which
covers the new Enter-based activation mechanism, fallback behavior, and
safety. This file now covers ONLY semantic interpretation of the Vision
response — the part that's still exactly as safety-critical as before,
since a wrong target_type/visible_label/confidence judgment must still
block activation regardless of how activation itself works.

No real mouse/keyboard/network/provider call happens anywhere in this
file, and no live Outlook run is performed.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.outlook.launch import OutlookLaunchSteps  # noqa: E402
from app.playbook.failure_reasons import LaunchFailureReason  # noqa: E402
from app.safety.abort_controller import AbortController  # noqa: E402
from app.vision.providers.base import NetworkError  # noqa: E402
from app.vision.service import VisionService  # noqa: E402

MODULE = "app.outlook.launch"


def _steps() -> OutlookLaunchSteps:
    steps = OutlookLaunchSteps(AbortController(), VisionService(MagicMock(), fallback=None), "claude-sonnet-5")
    steps.result.screen_width, steps.result.screen_height = 1920, 1080
    return steps


def _capture(width=1920, height=1080, filename="search.png"):
    return MagicMock(filename=filename, path=filename, width=width, height=height)


def _call(
    bbox=None, confidence=0.95, target_visible=True, target_type="desktop_app",
    visible_label="Outlook", visible_sublabel="App", search_visible=True,
):
    return MagicMock(
        parsed_json={
            "search_visible": search_visible, "target_visible": target_visible, "target_type": target_type,
            "visible_label": visible_label, "visible_sublabel": visible_sublabel,
            "bbox": bbox if bbox is not None else [300.0, 400.0, 360.0, 700.0],
            "bbox_tightly_scoped": True,
            "confidence": confidence, "reason": "ok",
        },
        raw_text="{}", model="claude-sonnet-5", latency_ms=100.0, input_tokens=10, output_tokens=5,
    )


def _ground(steps: OutlookLaunchSteps, call) -> bool:
    steps.vision.primary.analyze_screen.return_value = call
    return steps.ground_search_result(_capture())


# --- A: valid desktop Outlook result passes semantic verification ---

def test_A_valid_desktop_outlook_result_passes():
    steps = _steps()
    assert _ground(steps, _call()) is True
    assert steps.result.search_grounding.target_type == "desktop_app"


# --- B: wrong result type (web_result) — rejected ---

def test_B_web_result_type_rejected():
    steps = _steps()
    assert _ground(steps, _call(target_type="web_result")) is False
    assert steps.result.failure_reason == LaunchFailureReason.OUTLOOK_RESULT_WRONG_TYPE


# --- C: Settings / Help result — rejected ---

def test_C_settings_result_rejected():
    steps = _steps()
    assert _ground(steps, _call(target_type="settings")) is False
    assert steps.result.failure_reason == LaunchFailureReason.OUTLOOK_RESULT_WRONG_TYPE


def test_C_help_result_rejected():
    steps = _steps()
    assert _ground(steps, _call(target_type="help")) is False
    assert steps.result.failure_reason == LaunchFailureReason.OUTLOOK_RESULT_WRONG_TYPE


# --- D: sublabel disagreement — rejected even though type/label look right ---

def test_D_sublabel_reported_but_not_app_like_rejected():
    steps = _steps()
    assert _ground(steps, _call(visible_sublabel="Best match")) is False
    assert steps.result.failure_reason == LaunchFailureReason.OUTLOOK_RESULT_SUBLABEL_MISMATCH


def test_D_sublabel_empty_is_not_disqualifying_alone():
    steps = _steps()
    assert _ground(steps, _call(visible_sublabel="")) is True


# --- E: low confidence / not confidently identified — safe stop ---

def test_E_low_confidence_safe_stop():
    steps = _steps()
    assert _ground(steps, _call(confidence=0.3)) is False
    assert steps.result.failure_reason == LaunchFailureReason.GROUNDING_INVALID


def test_E_target_not_confidently_identified_reports_not_visible():
    steps = _steps()
    assert _ground(steps, _call(target_visible=False, target_type="other", visible_label="", bbox=None)) is False
    assert steps.result.failure_reason == LaunchFailureReason.OUTLOOK_RESULT_NOT_FOUND


def test_E_windows_search_itself_not_visible():
    steps = _steps()
    assert _ground(steps, _call(search_visible=False)) is False
    assert steps.result.failure_reason == LaunchFailureReason.WINDOWS_SEARCH_NOT_VISIBLE


def test_E_label_not_mentioning_outlook_rejected():
    steps = _steps()
    assert _ground(steps, _call(visible_label="Microsoft Word")) is False
    assert steps.result.failure_reason == LaunchFailureReason.OUTLOOK_RESULT_NOT_FOUND


# --- F: bbox is diagnostic-only — reported/logged, never validated/actionable ---

def test_F_bbox_reported_but_never_computes_a_click_point():
    steps = _steps()
    assert _ground(steps, _call(bbox=[300.0, 400.0, 360.0, 700.0])) is True
    assert steps.result.search_grounding.bbox == [300.0, 400.0, 360.0, 700.0]
    assert steps.result.converted_x is None
    assert steps.result.converted_y is None


def test_F_degenerate_or_missing_bbox_does_not_block_activation():
    """Since bbox is no longer actionable, a degenerate/absent bbox on an
    otherwise valid, confidently-identified result must NOT block
    activation — this is the key behavioral change from before the
    2026-09-06 keyboard-activation fix, where a bad bbox was fatal."""
    steps = _steps()
    assert _ground(steps, _call(bbox=None)) is True


# --- Q: Claude structured output wrapped in a markdown code fence still parses ---

def test_Q_anthropic_provider_strips_markdown_fence_before_parsing(tmp_path):
    from app.vision.providers.anthropic_provider import AnthropicProvider

    fake_image = tmp_path / "x.png"
    fake_image.write_bytes(b"fake-image-bytes")

    fenced_text = (
        "```json\n"
        '{"search_visible": true, "target_visible": true, "target_type": "desktop_app", '
        '"visible_label": "Outlook", "bbox": [300.0, 400.0, 360.0, 700.0], "confidence": 0.9, "reason": "ok"}\n'
        "```"
    )
    mock_response = MagicMock()
    mock_response.content = [MagicMock(type="text", text=fenced_text)]
    mock_response.model = "claude-sonnet-5"
    mock_response.usage = MagicMock(input_tokens=10, output_tokens=5)

    with patch("app.vision.providers.anthropic_provider.anthropic.Anthropic") as mock_client_cls:
        mock_client_cls.return_value.messages.create.return_value = mock_response
        provider = AnthropicProvider(api_key="fake-key", model="claude-sonnet-5")
        result = provider.analyze_screen(fake_image, "goal", "prompt")

    assert result.parsed_json is not None
    assert result.parsed_json["target_type"] == "desktop_app"

    from app.vision.models import OutlookSearchGroundingResponse
    structured = OutlookSearchGroundingResponse.model_validate(result.parsed_json)
    assert structured.target_visible is True


# --- R: malformed Claude JSON — safe provider/schema failure, zero action ---

def test_R_malformed_json_is_safe_schema_failure_zero_action():
    steps = _steps()
    call = MagicMock(
        parsed_json=None,  # AnthropicProvider itself already returns None when json.loads() fails
        raw_text="this is not json at all", model="claude-sonnet-5",
        latency_ms=100.0, input_tokens=10, output_tokens=5,
    )
    steps.vision.primary.analyze_screen.return_value = call
    assert steps.ground_search_result(_capture()) is False
    assert steps.result.failure_reason == LaunchFailureReason.TECHNICAL_PROVIDER_ERROR
    assert steps.result.converted_x is None


# --- S: provider transient error — retried once, no duplicate physical action ---

def test_S_transient_provider_error_retries_then_succeeds():
    steps = _steps()
    steps.vision.primary.analyze_screen.side_effect = [
        NetworkError("[Errno 10054] connection reset"),
        _call(),
    ]
    assert steps.ground_search_result(_capture()) is True
    assert steps.result.provider_retries == 1
    assert steps.vision.primary.analyze_screen.call_count == 2

    # And exactly one grounding result was produced — no duplicate
    # activation-eligible state was left behind by the retry.
    steps.record_human_approval(True)
    with patch(f"{MODULE}.pyautogui") as mock_pyautogui, \
         patch(f"{MODULE}.get_foreground_window_title", return_value="Search"):
        mock_pyautogui.FAILSAFE = True
        assert steps.activate_outlook_result() is True
        assert mock_pyautogui.press.call_count == 1


# --- T: provider error exhausts retries — final technical error, zero action ---

def test_T_provider_error_exhausts_retries_final_technical_error_zero_action():
    steps = _steps()
    steps.vision.primary.analyze_screen.side_effect = [
        NetworkError("[Errno 10054] connection reset"),
        NetworkError("[Errno 10054] connection reset"),
    ]
    assert steps.ground_search_result(_capture()) is False
    assert steps.result.result == "ERROR"
    assert steps.result.failure_reason == LaunchFailureReason.TECHNICAL_PROVIDER_ERROR
    assert steps.result.converted_x is None
