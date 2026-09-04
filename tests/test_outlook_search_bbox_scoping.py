"""Region-containment regression tests (2026-09-03, third live-evidence
round): a debug-overlay screenshot proved target_type/visible_label/
confidence were all correctly reported, but the returned bbox mostly
covered the "Best match" section header above the Outlook row instead
of the row itself — a semantic BBOX-SCOPING bug, distinct from the
target-IDENTITY bug the previous round's target_type/visible_label
checks already catch.

Fix mechanism under test:
- visible_sublabel: a secondary identity signal a header/container would
  never have.
- bbox_tightly_scoped: Claude's own explicit self-check, kept separate
  from target_type/visible_label because identity and bbox-scoping are
  different claims that can disagree (exactly what happened live).
- A bounded, SAME-screenshot second-pass refine call, triggered ONLY
  when bbox_tightly_scoped is False, that asks for nothing but a
  tighter bbox for the already-identified row — never more than one
  extra read-only call, never a new screenshot, never a repeated
  physical action.
- A height-alone (not width-alone) generic oversized-bbox geometry
  check that also catches a "whole results column" bbox independent of
  what Claude self-reports.

No real mouse/keyboard/network/provider call happens anywhere in this
file, and no live Outlook run is performed. No exact screen coordinates
are hardcoded — every bbox below is illustrative, normalized 0-1000
sample data.
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
_ENV_MATCH = {"pyautogui_width": 1920, "pyautogui_height": 1080, "dimensions_match": True}


def _steps() -> OutlookLaunchSteps:
    steps = OutlookLaunchSteps(AbortController(), VisionService(MagicMock(), fallback=None), "claude-sonnet-5")
    steps.result.screen_width, steps.result.screen_height = 1920, 1080
    return steps


def _capture(width=1920, height=1080, filename="search.png"):
    return MagicMock(filename=filename, path=filename, width=width, height=height)


def _call(
    bbox=None, confidence=0.95, target_visible=True, target_type="desktop_app",
    visible_label="Outlook", visible_sublabel="App", bbox_tightly_scoped=True, search_visible=True,
):
    return MagicMock(
        parsed_json={
            "search_visible": search_visible, "target_visible": target_visible, "target_type": target_type,
            "visible_label": visible_label, "visible_sublabel": visible_sublabel,
            "bbox": bbox if bbox is not None else [300.0, 400.0, 360.0, 700.0],
            "bbox_tightly_scoped": bbox_tightly_scoped, "confidence": confidence, "reason": "ok",
        },
        raw_text="{}", model="claude-sonnet-5", latency_ms=100.0, input_tokens=10, output_tokens=5,
    )


def _refine_call(bbox=None, target_visible=True, confidence=0.9):
    return MagicMock(
        parsed_json={"target_visible": target_visible, "bbox": bbox, "confidence": confidence, "reason": "ok"},
        raw_text="{}", model="claude-sonnet-5", latency_ms=100.0, input_tokens=10, output_tokens=5,
    )


def _ground(steps: OutlookLaunchSteps) -> bool:
    with patch(f"{MODULE}.get_environment_info", return_value=_ENV_MATCH):
        return steps.ground_search_result(_capture())


# --- A: tight bbox around the actual row — accepted, no refine call needed ---

def test_A_tight_bbox_around_row_accepted_no_refine_needed():
    steps = _steps()
    steps.vision.primary.analyze_screen.return_value = _call(
        bbox=[300.0, 400.0, 360.0, 700.0], bbox_tightly_scoped=True,
    )
    assert _ground(steps) is True
    assert steps.result.refine_pass_used is False
    assert steps.vision.primary.analyze_screen.call_count == 1
    assert steps.result.converted_x is not None


# --- B: bbox around the "Best match" header only — rejected ---

def test_B_header_only_bbox_rejected_when_refine_also_cannot_localize_row():
    steps = _steps()
    header_shaped_bbox = [100.0, 350.0, 200.0, 750.0]  # short band above where the row actually is
    steps.vision.primary.analyze_screen.side_effect = [
        _call(bbox=header_shaped_bbox, bbox_tightly_scoped=False),
        _refine_call(target_visible=False, bbox=None),  # second look still can't confidently localize the row
    ]
    assert _ground(steps) is False
    assert steps.result.failure_reason == LaunchFailureReason.OUTLOOK_RESULT_BBOX_NOT_TIGHT
    assert steps.result.refine_pass_used is True
    assert steps.result.converted_x is None
    assert steps.result.converted_y is None


# --- C: bbox includes header + row — reject if not tightenable, accept if it is ---

def test_C_header_plus_row_bbox_rejected_when_refine_cannot_tighten_it():
    """Same rejection mechanism as B — included separately because this
    is a distinct visual shape (a bbox spanning BOTH the header and the
    row beneath it, not just the header alone)."""
    steps = _steps()
    combined_bbox = [100.0, 350.0, 360.0, 750.0]  # spans header AND the row below it
    steps.vision.primary.analyze_screen.side_effect = [
        _call(bbox=combined_bbox, bbox_tightly_scoped=False),
        _refine_call(target_visible=False, bbox=None),
    ]
    assert _ground(steps) is False
    assert steps.result.failure_reason == LaunchFailureReason.OUTLOOK_RESULT_BBOX_NOT_TIGHT


def test_C_header_plus_row_bbox_accepted_after_successful_tightening():
    steps = _steps()
    combined_bbox = [100.0, 350.0, 360.0, 750.0]
    tight_bbox = [300.0, 400.0, 360.0, 700.0]
    steps.vision.primary.analyze_screen.side_effect = [
        _call(bbox=combined_bbox, bbox_tightly_scoped=False),
        _refine_call(target_visible=True, bbox=tight_bbox, confidence=0.92),
    ]
    assert _ground(steps) is True
    assert steps.result.refine_pass_used is True
    assert steps.result.search_grounding_bbox_raw == tight_bbox  # the REFINED bbox, never the loose original


# --- D: right-side Outlook preview panel — rejected, no hardcoded x-position rule ---

def test_D_right_side_preview_panel_bbox_rejected_via_tightness_check():
    """The right-side preview panel (icon / "Outlook" / "App" / "Open" /
    "Run as administrator") is excluded by the PROMPT instruction, not by
    a hardcoded x-position rule — this project never assumes which side
    of the screen a panel appears on. If Claude nonetheless grounds it,
    the same bbox_tightly_scoped self-check + bounded refine-or-reject
    mechanism used for the header case is what actually catches it."""
    steps = _steps()
    preview_panel_bbox = [200.0, 600.0, 700.0, 950.0]  # tall, right-hand region with multiple action rows
    steps.vision.primary.analyze_screen.side_effect = [
        _call(bbox=preview_panel_bbox, bbox_tightly_scoped=False),
        _refine_call(target_visible=False, bbox=None),
    ]
    assert _ground(steps) is False
    assert steps.result.failure_reason == LaunchFailureReason.OUTLOOK_RESULT_BBOX_NOT_TIGHT


# --- E: whole search-results column — rejected by geometry, regardless of self-report ---

def test_E_whole_results_column_bbox_rejected_by_geometry_even_if_self_reported_tight():
    """Defense in depth: even if Claude overconfidently claims
    bbox_tightly_scoped=true, a bbox whose HEIGHT alone spans nearly the
    entire screenshot can never be a single row (see the height-alone
    oversized-bbox check in ground_search_result()) — caught without
    ever needing a refine call."""
    steps = _steps()
    column_bbox = [20.0, 350.0, 990.0, 700.0]  # tall column, modest/plausible width
    steps.vision.primary.analyze_screen.return_value = _call(bbox=column_bbox, bbox_tightly_scoped=True)
    assert _ground(steps) is False
    assert steps.result.failure_reason == LaunchFailureReason.GROUNDING_INVALID
    assert steps.vision.primary.analyze_screen.call_count == 1  # geometry alone caught it — no refine call spent


# --- F: correct row at different vertical positions — accepted, no hardcoded position ---

def test_F_correct_row_at_different_vertical_positions_all_accepted():
    positions = [
        [40.0, 350.0, 100.0, 700.0],    # near top
        [450.0, 350.0, 510.0, 700.0],   # middle
        [850.0, 350.0, 910.0, 700.0],   # near bottom
    ]
    seen_click_points = set()
    for bbox in positions:
        steps = _steps()
        steps.vision.primary.analyze_screen.return_value = _call(bbox=bbox, bbox_tightly_scoped=True)
        assert _ground(steps) is True, bbox
        seen_click_points.add((steps.result.converted_x, steps.result.converted_y))
    assert len(seen_click_points) == 3  # no fixed/hardcoded click point


# --- Bonus: visible_sublabel mismatch is an independent identity signal ---

def test_sublabel_reported_but_not_app_like_rejected():
    steps = _steps()
    steps.vision.primary.analyze_screen.return_value = _call(visible_sublabel="Web result")
    assert _ground(steps) is False
    assert steps.result.failure_reason == LaunchFailureReason.OUTLOOK_RESULT_SUBLABEL_MISMATCH


def test_sublabel_empty_is_not_disqualifying_alone():
    steps = _steps()
    steps.vision.primary.analyze_screen.return_value = _call(visible_sublabel="")
    assert _ground(steps) is True


# --- Bonus: the refine call itself goes through the same provider-retry path ---

def test_refine_call_transient_error_retries_refine_only_no_duplicate_first_pass():
    steps = _steps()
    tight_bbox = [300.0, 400.0, 360.0, 700.0]
    steps.vision.primary.analyze_screen.side_effect = [
        _call(bbox=[100.0, 350.0, 360.0, 750.0], bbox_tightly_scoped=False),
        NetworkError("[Errno 10054] connection reset"),  # refine call transient failure
        _refine_call(target_visible=True, bbox=tight_bbox, confidence=0.9),  # refine retry succeeds
    ]
    assert _ground(steps) is True
    assert steps.vision.primary.analyze_screen.call_count == 3  # first pass + 2 refine attempts, never a 4th
    assert steps.result.provider_retries == 1
