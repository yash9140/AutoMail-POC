"""Pytest regression tests for rnd/capture/screen_capture.py (RND-001).

These mirror SC-001, SC-002, SC-004, SC-005, SC-006 using an isolated
tmp_path output directory so repeated CI runs don't pile up files under
screenshots/raw/. The full RND-001 experiment (which writes real, timestamped
results to results/raw/rnd001_screen_capture_results.json) is run separately
via rnd/capture/run_rnd001_tests.py — see docs/r_and_d/03_Screen_Capture.md.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "rnd" / "capture"))

from screen_capture import capture_screen, get_environment_info, validate_capture  # noqa: E402


def test_single_capture_produces_valid_png(tmp_path):
    metadata = capture_screen(tmp_path)
    assert metadata.width > 0
    assert metadata.height > 0
    assert Path(metadata.path).exists()

    valid, detail = validate_capture(metadata)
    assert valid, detail


def test_repeated_captures_have_unique_filenames(tmp_path):
    filenames = [capture_screen(tmp_path).filename for _ in range(3)]
    assert len(set(filenames)) == 3


def test_environment_info_reports_monitor_and_resolution():
    info = get_environment_info()
    assert info["monitor_count"] >= 1
    assert info["mss_width"] > 0
    assert info["mss_height"] > 0


def test_capture_timestamp_is_parseable_iso(tmp_path):
    metadata = capture_screen(tmp_path)
    parsed = datetime.fromisoformat(metadata.timestamp)
    assert parsed.year >= 2024
