"""OUTLOOK_SEARCH semantic-quality + click-lifecycle hardening
(2026-09-03): the bbox-conversion fix alone did not fully resolve a live
report that the cursor moves but doesn't land on the actual clickable
Outlook result. Root cause #2: the stage could ground a bbox that was
geometrically valid but semantically wrong — a web result, a Settings/
Help result, or a container — because target identity was only checked
via a loose "outlook" substring match, with no distinction between
"found nothing" and "found the wrong thing".

This file is the comprehensive, mocked/simulated test suite (Part 9,
items A-T) that exercises the full OUTLOOK_SEARCH grounding + click +
readiness path end-to-end, using OutlookLaunchSteps directly for the
grounding/click-lifecycle tests and OutlookLaunchWorker for the two
tests (N, O) that need the readiness-polling phase too. No real mouse/
keyboard/network/provider call happens anywhere in this file, and no
live Outlook run is performed.
"""

import itertools
import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.outlook.launch import OutlookLaunchSteps  # noqa: E402
from app.playbook.failure_reasons import LaunchFailureReason  # noqa: E402
from app.safety.abort_controller import AbortController  # noqa: E402
from app.vision.providers.base import NetworkError  # noqa: E402
from app.workers.outlook_launch_worker import OutlookLaunchWorker  # noqa: E402

MODULE = "app.outlook.launch"
WORKER_MODULE = "app.workers.outlook_launch_worker"

_ENV_MATCH = {"pyautogui_width": 1920, "pyautogui_height": 1080, "dimensions_match": True}
_ENV_MISMATCH = {"pyautogui_width": 1536, "pyautogui_height": 864, "dimensions_match": False}


def _steps() -> OutlookLaunchSteps:
    steps = OutlookLaunchSteps(AbortController(), MagicMock(), "claude-sonnet-5")
    steps.result.screen_width, steps.result.screen_height = 1920, 1080
    return steps


def _capture(width=1920, height=1080, filename="search.png"):
    return MagicMock(filename=filename, path=filename, width=width, height=height)


def _call(
    bbox=None, confidence=0.95, target_visible=True, target_type="desktop_app",
    visible_label="Outlook", search_visible=True,
):
    return MagicMock(
        parsed_json={
            "search_visible": search_visible, "target_visible": target_visible, "target_type": target_type,
            "visible_label": visible_label,
            "bbox": bbox if bbox is not None else [300.0, 400.0, 360.0, 700.0],
            "confidence": confidence, "reason": "ok",
        },
        raw_text="{}", model="claude-sonnet-5", latency_ms=100.0, input_tokens=10, output_tokens=5,
    )


def _ground(steps: OutlookLaunchSteps, call, env=None) -> bool:
    steps.provider.analyze_screen.return_value = call
    with patch(f"{MODULE}.get_environment_info", return_value=env or _ENV_MATCH):
        return steps.ground_search_result(_capture())


# --- A: valid desktop Outlook result grounds and clicks exactly once ---

def test_A_valid_desktop_outlook_result_grounds_and_clicks_once():
    steps = _steps()
    assert _ground(steps, _call()) is True
    steps.record_human_approval(True)

    with patch(f"{MODULE}.pyautogui") as mock_pyautogui, \
         patch(f"{MODULE}.get_foreground_window_title", return_value="Search"):
        mock_pyautogui.FAILSAFE = True
        assert steps.click_outlook_result() is True
        assert mock_pyautogui.moveTo.call_count == 1
        assert mock_pyautogui.click.call_count == 1


# --- B: wrong result type (web_result) — zero action, explicit safe failure ---

def test_B_web_result_type_rejected_zero_action():
    steps = _steps()
    assert _ground(steps, _call(target_type="web_result")) is False
    assert steps.result.failure_reason == LaunchFailureReason.OUTLOOK_RESULT_WRONG_TYPE
    assert steps.result.converted_x is None
    assert steps.result.converted_y is None


# --- C: Settings / Help result — zero click ---

def test_C_settings_result_rejected_zero_action():
    steps = _steps()
    assert _ground(steps, _call(target_type="settings")) is False
    assert steps.result.failure_reason == LaunchFailureReason.OUTLOOK_RESULT_WRONG_TYPE


def test_C_help_result_rejected_zero_action():
    steps = _steps()
    assert _ground(steps, _call(target_type="help")) is False
    assert steps.result.failure_reason == LaunchFailureReason.OUTLOOK_RESULT_WRONG_TYPE


# --- D: search-panel-container bbox — reject the safely-detectable extreme case ---

def test_D_whole_panel_bbox_rejected():
    """A bbox spanning nearly the entire screenshot in both dimensions is
    definitionally not a single result row — this is the one geometry
    rule this fix adds, deliberately narrow (see
    MAX_PLAUSIBLE_RESULT_BBOX_NORMALIZED_SPAN in app/outlook/launch.py)."""
    steps = _steps()
    assert _ground(steps, _call(bbox=[0.0, 0.0, 1000.0, 1000.0])) is False
    assert steps.result.failure_reason == LaunchFailureReason.GROUNDING_INVALID
    assert "spans nearly the entire screenshot" in (steps.result.notes or "")


def test_D_moderate_container_bbox_not_geometrically_detectable_by_design():
    """A MODERATE container bbox (e.g. just the search-results group, not
    the whole screen) is NOT rejected by geometry alone — deliberately.
    Distinguishing "one wide/tall result tile" from "a container that
    happens to be a similar size" without a resolution/layout-specific
    assumption is not something geometry can safely do (see the comment
    above the oversized-bbox check in ground_search_result()). This is
    caught instead by target_type/visible_label semantic classification,
    the prompt's explicit "never the container" instruction, and the
    human-approval gate before any physical action — never by an
    invented pixel/fraction threshold tuned to one layout. This test
    documents that decision by proving the moderate case is NOT rejected
    on geometry grounds when target_type/visible_label/confidence all
    otherwise pass; the reader should not mistake that as a gap this fix
    silently ignores."""
    steps = _steps()
    # A "moderately large" container: covers most of the results list
    # area but not the whole screen. Geometry alone lets it through.
    assert _ground(steps, _call(bbox=[50.0, 50.0, 850.0, 900.0])) is True


# --- E: ambiguous match (ANY candidate confidently distinguished only via confidence) ---

def test_E_ambiguous_result_low_confidence_safe_stop():
    """The schema reports exactly ONE target (not a candidate list) — a
    Windows Search result set that Claude cannot confidently disambiguate
    is expected to surface as reduced confidence (or target_visible=false),
    both of which are code-side gated; Vision is never trusted to silently
    pick one among several look-alikes without that showing up here."""
    steps = _steps()
    assert _ground(steps, _call(confidence=0.3)) is False
    assert steps.result.failure_reason == LaunchFailureReason.GROUNDING_INVALID


def test_E_target_not_confidently_identified_reports_not_visible():
    steps = _steps()
    assert _ground(steps, _call(target_visible=False, target_type="other", visible_label="", bbox=None)) is False
    assert steps.result.failure_reason == LaunchFailureReason.OUTLOOK_RESULT_NOT_FOUND


# --- F: invalid bbox (out-of-range / degenerate / malformed) — zero action ---

def test_F_degenerate_bbox_rejected():
    steps = _steps()
    assert _ground(steps, _call(bbox=[300.0, 400.0, 300.0, 400.0])) is False  # zero area
    assert steps.result.failure_reason == LaunchFailureReason.GROUNDING_INVALID
    assert steps.result.converted_x is None


def test_F_malformed_bbox_wrong_length_rejected():
    steps = _steps()
    assert _ground(steps, _call(bbox=[300.0, 400.0, 360.0])) is False  # only 3 values
    assert steps.result.failure_reason == LaunchFailureReason.GROUNDING_INVALID


def test_F_out_of_range_bbox_rejected():
    steps = _steps()
    assert _ground(steps, _call(bbox=[300.0, 400.0, 360.0, 1500.0])) is False  # x_max > 1000
    assert steps.result.failure_reason == LaunchFailureReason.GROUNDING_INVALID


# --- G: correct bbox, different screen positions — no hardcoded assumption ---

def test_G_correct_bbox_at_multiple_positions_no_hardcoded_click_point():
    positions = {
        "upper_left": [20.0, 30.0, 60.0, 250.0],
        "center_left": [480.0, 30.0, 520.0, 300.0],
        "lower": [900.0, 30.0, 950.0, 300.0],
    }
    seen_points = set()
    for name, bbox in positions.items():
        steps = _steps()
        assert _ground(steps, _call(bbox=bbox)) is True, name
        seen_points.add((steps.result.converted_x, steps.result.converted_y))
    assert len(seen_points) == 3  # all different — proves the click point tracks the bbox, never a fixed point


# --- H: non-square screen; prove correctness generalizes across resolutions ---

def test_H_non_square_1920x1080_and_alternate_resolution_both_correct():
    steps_a = _steps()
    assert _ground(steps_a, _call()) is True

    steps_b = OutlookLaunchSteps(AbortController(), MagicMock(), "claude-sonnet-5")
    steps_b.result.screen_width, steps_b.result.screen_height = 2560, 1440
    steps_b.provider.analyze_screen.return_value = _call()
    with patch(f"{MODULE}.get_environment_info",
               return_value={"pyautogui_width": 2560, "pyautogui_height": 1440, "dimensions_match": True}):
        assert steps_b.ground_search_result(_capture(2560, 1440)) is True

    # Same normalized bbox, different resolution -> different absolute
    # pixel point, proving the conversion is dynamic, not a cached value.
    assert (steps_a.result.converted_x, steps_a.result.converted_y) != \
           (steps_b.result.converted_x, steps_b.result.converted_y)
    # But both should be proportionally consistent (same normalized bbox
    # -> same relative position within each screen, modulo rounding).
    assert steps_a.result.converted_x / 1920 == pytest.approx(steps_b.result.converted_x / 2560, abs=1e-3)
    assert steps_a.result.converted_y / 1080 == pytest.approx(steps_b.result.converted_y / 1440, abs=1e-3)


# --- I: screenshot vs pyautogui coordinate-space mismatch ---

def test_I_coordinate_space_mismatch_blocks_before_vision_call():
    steps = _steps()
    with patch(f"{MODULE}.get_environment_info", return_value=_ENV_MISMATCH):
        assert steps.ground_search_result(_capture()) is False
    assert steps.result.failure_reason == LaunchFailureReason.COORDINATE_SPACE_MISMATCH
    steps.provider.analyze_screen.assert_not_called()


# --- J: foreground changes before click ---

def test_J_foreground_lost_before_move_zero_click():
    steps = _steps()
    assert _ground(steps, _call()) is True
    steps.record_human_approval(True)
    with patch(f"{MODULE}.pyautogui") as mock_pyautogui, \
         patch(f"{MODULE}.get_foreground_window_title", return_value="Visual Studio Code"):
        mock_pyautogui.FAILSAFE = True
        assert steps.click_outlook_result() is False
        mock_pyautogui.moveTo.assert_not_called()
        mock_pyautogui.click.assert_not_called()
    assert steps.result.failure_reason == LaunchFailureReason.SEARCH_STATE_LOST_BEFORE_CLICK


def test_J_foreground_lost_between_move_and_click_zero_click():
    steps = _steps()
    assert _ground(steps, _call()) is True
    steps.record_human_approval(True)
    with patch(f"{MODULE}.pyautogui") as mock_pyautogui, \
         patch(f"{MODULE}.get_foreground_window_title", side_effect=["Search", "Notepad"]):
        mock_pyautogui.FAILSAFE = True
        assert steps.click_outlook_result() is False
        mock_pyautogui.moveTo.assert_called_once()  # move already happened
        mock_pyautogui.click.assert_not_called()     # click never did
    assert steps.result.failure_reason == LaunchFailureReason.SEARCH_STATE_LOST_BEFORE_CLICK


# --- K: abort requested between grounding and click ---

def test_K_abort_between_grounding_and_click_zero_click():
    steps = _steps()
    assert _ground(steps, _call()) is True
    steps.record_human_approval(True)
    steps.abort_controller.request_abort()

    with patch(f"{MODULE}.pyautogui") as mock_pyautogui, \
         patch(f"{MODULE}.get_foreground_window_title", return_value="Search"):
        mock_pyautogui.FAILSAFE = True
        assert steps.click_outlook_result() is False
        mock_pyautogui.moveTo.assert_not_called()
        mock_pyautogui.click.assert_not_called()
    assert steps.result.failure_reason == LaunchFailureReason.USER_ABORTED


# --- L: mouse move failure — no click, explicit failure ---

def test_L_mouse_move_failure_no_click_explicit_failure():
    steps = _steps()
    assert _ground(steps, _call()) is True
    steps.record_human_approval(True)
    with patch(f"{MODULE}.pyautogui") as mock_pyautogui, \
         patch(f"{MODULE}.get_foreground_window_title", return_value="Search"):
        mock_pyautogui.FAILSAFE = True
        mock_pyautogui.moveTo.side_effect = RuntimeError("simulated move failure")
        assert steps.click_outlook_result() is False
        mock_pyautogui.click.assert_not_called()
    assert steps.result.result == "ERROR"
    assert steps.result.failure_reason == LaunchFailureReason.PHYSICAL_ACTION_FAILED
    assert steps.result.outlook_launch_click_executed is False


# --- M: click failure — explicit failure, no false success ---

def test_M_click_failure_explicit_failure_no_false_success():
    steps = _steps()
    assert _ground(steps, _call()) is True
    steps.record_human_approval(True)
    with patch(f"{MODULE}.pyautogui") as mock_pyautogui, \
         patch(f"{MODULE}.get_foreground_window_title", return_value="Search"):
        mock_pyautogui.FAILSAFE = True
        mock_pyautogui.click.side_effect = RuntimeError("simulated click failure")
        assert steps.click_outlook_result() is False
    assert steps.result.result == "ERROR"
    assert steps.result.failure_reason == LaunchFailureReason.PHYSICAL_ACTION_FAILED
    assert steps.result.outlook_launch_click_executed is False


# --- N: click executes but Outlook never opens — logs must prove the click happened ---

def test_N_click_executes_but_outlook_never_opens_logs_prove_click(caplog):
    caplog.set_level(logging.INFO, logger="app.outlook.launch.grounding")

    worker = OutlookLaunchWorker(AbortController(), bounded_approval_granted=True)
    signals = {"success": [], "failure": []}
    worker.success.connect(lambda r: signals["success"].append(r))
    worker.failure.connect(lambda *a: signals["failure"].append(a))

    mock_provider = MagicMock()
    mock_provider.analyze_screen.return_value = _call()

    titles = itertools.chain(["Search", "Search", "Search"], itertools.repeat("Untitled - Notepad"))

    with patch(f"{WORKER_MODULE}._provider", return_value=(mock_provider, "claude-sonnet-5")), \
         patch(f"{MODULE}.pyautogui") as mock_pyautogui, \
         patch(f"{MODULE}.time.sleep"), \
         patch(f"{MODULE}.get_foreground_window_title", side_effect=titles), \
         patch(f"{MODULE}.get_environment_info", return_value=_ENV_MATCH), \
         patch(f"{MODULE}.OUTLOOK_LAUNCH_TIMEOUT_SECONDS", 0.05), \
         patch(f"{MODULE}.capture_screen", return_value=_capture()):
        mock_pyautogui.FAILSAFE = True
        worker.run()

    assert not signals["success"]
    assert signals["failure"][0][0] == "OUTLOOK_LAUNCH_TIMEOUT"
    mock_pyautogui.click.assert_called_once()

    messages = [record.getMessage() for record in caplog.records]
    assert any("OUTLOOK_CLICK_EXECUTED" in m for m in messages), messages


# --- O: click executes and Outlook becomes ready — successful launch ---

def test_O_click_executes_and_outlook_becomes_ready_success():
    worker = OutlookLaunchWorker(AbortController(), bounded_approval_granted=True)
    signals = {"success": [], "failure": [], "aborted": []}
    worker.success.connect(lambda r: signals["success"].append(r))
    worker.failure.connect(lambda *a: signals["failure"].append(a))
    worker.aborted.connect(lambda *a: signals["aborted"].append(a))

    verify_response = MagicMock(
        parsed_json={"application": "Outlook", "outlook_visible": True, "splash_screen_visible": False,
                     "ready_for_interaction": True, "detected_state": "inbox", "confidence": 0.95, "reason": "ok"},
        raw_text="{}", model="claude-sonnet-5", latency_ms=100.0, input_tokens=10, output_tokens=5,
    )
    mock_provider = MagicMock()
    mock_provider.analyze_screen.side_effect = [_call(), verify_response]

    titles = itertools.chain(["Search", "Search", "Search"], itertools.repeat("Inbox - Outlook"))

    with patch(f"{WORKER_MODULE}._provider", return_value=(mock_provider, "claude-sonnet-5")), \
         patch(f"{MODULE}.pyautogui") as mock_pyautogui, \
         patch(f"{MODULE}.time.sleep"), \
         patch(f"{MODULE}.get_foreground_window_title", side_effect=titles), \
         patch(f"{MODULE}.get_foreground_hwnd", return_value=12345), \
         patch(f"{MODULE}.is_maximized", return_value=True), \
         patch(f"{MODULE}.maximize"), \
         patch(f"{MODULE}.get_environment_info", return_value=_ENV_MATCH), \
         patch(f"{MODULE}.capture_screen", return_value=_capture()):
        mock_pyautogui.FAILSAFE = True
        worker.run()

    assert signals["success"], f"failure={signals['failure']} aborted={signals['aborted']}"
    result = signals["success"][0]
    assert result["result"] == "PASS"
    mock_pyautogui.click.assert_called_once()


# --- P: debug overlay enabled produces the SAME runtime click coordinates as disabled ---

def test_P_debug_overlay_enabled_same_click_coordinates_as_disabled():
    steps_disabled = _steps()
    assert _ground(steps_disabled, _call()) is True

    steps_enabled = _steps()
    with patch(f"{MODULE}._DEBUG_ARTIFACTS_ENABLED", True), \
         patch(f"{MODULE}.save_grounding_debug_artifact", return_value=None) as mock_save:
        assert _ground(steps_enabled, _call()) is True
    mock_save.assert_called_once()

    assert (steps_disabled.result.converted_x, steps_disabled.result.converted_y) == \
           (steps_enabled.result.converted_x, steps_enabled.result.converted_y)


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
    steps.provider.analyze_screen.return_value = call
    with patch(f"{MODULE}.get_environment_info", return_value=_ENV_MATCH):
        assert steps.ground_search_result(_capture()) is False
    assert steps.result.failure_reason == LaunchFailureReason.TECHNICAL_PROVIDER_ERROR
    assert steps.result.converted_x is None


# --- S: provider transient error — retried once, no duplicate physical action ---

def test_S_transient_provider_error_retries_then_succeeds():
    steps = _steps()
    steps.provider.analyze_screen.side_effect = [
        NetworkError("[Errno 10054] connection reset"),
        _call(),
    ]
    with patch(f"{MODULE}.get_environment_info", return_value=_ENV_MATCH):
        assert steps.ground_search_result(_capture()) is True
    assert steps.result.provider_retries == 1
    assert steps.provider.analyze_screen.call_count == 2

    # And exactly one grounding result was produced — no duplicate
    # click-eligible state was left behind by the retry.
    steps.record_human_approval(True)
    with patch(f"{MODULE}.pyautogui") as mock_pyautogui, \
         patch(f"{MODULE}.get_foreground_window_title", return_value="Search"):
        mock_pyautogui.FAILSAFE = True
        assert steps.click_outlook_result() is True
        assert mock_pyautogui.click.call_count == 1


# --- T: provider error exhausts retries — final technical error, zero action ---

def test_T_provider_error_exhausts_retries_final_technical_error_zero_action():
    steps = _steps()
    steps.provider.analyze_screen.side_effect = [
        NetworkError("[Errno 10054] connection reset"),
        NetworkError("[Errno 10054] connection reset"),
    ]
    with patch(f"{MODULE}.get_environment_info", return_value=_ENV_MATCH):
        assert steps.ground_search_result(_capture()) is False
    assert steps.result.result == "ERROR"
    assert steps.result.failure_reason == LaunchFailureReason.TECHNICAL_PROVIDER_ERROR
    assert steps.result.converted_x is None
