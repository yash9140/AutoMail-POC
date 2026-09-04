"""RND-001 test runner.

Executes SC-001 through SC-007 against the real Windows screen using
rnd/capture/screen_capture.py, and writes a structured result file to
results/raw/rnd001_screen_capture_results.json.

No fabricated results: every test result below comes from an actual
capture/validation performed at run time. A test that cannot be performed
(e.g. Outlook not running) is recorded as SKIPPED with a stated reason,
never as a fake PASS.

Run:
    python rnd/capture/run_rnd001_tests.py
"""

from __future__ import annotations

import json
import platform
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from screen_capture import (
    DEFAULT_OUTPUT_DIR,
    ScreenCaptureError,
    capture_screen,
    get_environment_info,
    validate_capture,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RESULTS_PATH = PROJECT_ROOT / "results" / "raw" / "rnd001_screen_capture_results.json"


def is_outlook_running() -> bool:
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq OUTLOOK.EXE"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return "OUTLOOK.EXE" in out.stdout.upper()
    except Exception:
        return False


def run() -> dict[str, Any]:
    tests: list[dict[str, Any]] = []

    env = get_environment_info()

    # SC-001: Single full-screen capture
    try:
        meta = capture_screen(DEFAULT_OUTPUT_DIR)
        ok = meta.width > 0 and meta.height > 0 and Path(meta.path).exists()
        tests.append({
            "test_id": "SC-001",
            "name": "Single full-screen capture",
            "result": "PASS" if ok else "FAIL",
            "details": meta.as_dict(),
        })
        sc001_meta = meta
    except ScreenCaptureError as exc:
        tests.append({
            "test_id": "SC-001",
            "name": "Single full-screen capture",
            "result": "FAIL",
            "details": {"error": str(exc)},
        })
        sc001_meta = None

    # SC-002: PNG file validation (of the SC-001 capture)
    if sc001_meta is not None:
        valid, detail = validate_capture(sc001_meta)
        tests.append({
            "test_id": "SC-002",
            "name": "PNG file validation",
            "result": "PASS" if valid else "FAIL",
            "details": {"detail": detail, "file": sc001_meta.path},
        })
    else:
        tests.append({
            "test_id": "SC-002",
            "name": "PNG file validation",
            "result": "FAIL",
            "details": {"detail": "no capture available from SC-001"},
        })

    # SC-003: Five sequential captures
    sequential_files: list[str] = []
    sequential_ok = True
    sequential_errors: list[str] = []
    for i in range(5):
        try:
            m = capture_screen(DEFAULT_OUTPUT_DIR)
            valid, detail = validate_capture(m)
            if not valid:
                sequential_ok = False
                sequential_errors.append(f"capture {i + 1} failed validation: {detail}")
            sequential_files.append(m.filename)
        except ScreenCaptureError as exc:
            sequential_ok = False
            sequential_errors.append(f"capture {i + 1} raised: {exc}")
        if i < 4:
            time.sleep(0.5)
    unique = len(set(sequential_files)) == len(sequential_files)
    tests.append({
        "test_id": "SC-003",
        "name": "Five sequential captures",
        "result": "PASS" if (sequential_ok and unique and len(sequential_files) == 5) else "FAIL",
        "details": {
            "files": sequential_files,
            "all_filenames_unique": unique,
            "errors": sequential_errors,
        },
    })

    # SC-004: MSS dimensions vs PyAutoGUI dimensions
    dims_match = env.get("dimensions_match")
    tests.append({
        "test_id": "SC-004",
        "name": "MSS dimensions vs PyAutoGUI dimensions",
        "result": "PASS" if dims_match is True else ("FAIL" if dims_match is False else "FAIL"),
        "details": {
            "mss_width": env.get("mss_width"),
            "mss_height": env.get("mss_height"),
            "pyautogui_width": env.get("pyautogui_width"),
            "pyautogui_height": env.get("pyautogui_height"),
            "dimensions_match": dims_match,
            "pyautogui_error": env.get("pyautogui_error"),
        },
    })

    # SC-005: Monitor enumeration
    monitor_count = env.get("monitor_count", 0)
    tests.append({
        "test_id": "SC-005",
        "name": "Monitor enumeration",
        "result": "PASS" if monitor_count >= 1 else "FAIL",
        "details": {
            "monitor_count": monitor_count,
            "monitors": env.get("monitors"),
            "virtual_desktop": env.get("virtual_desktop"),
        },
    })

    # SC-006: Timestamp / unique filename verification
    try:
        m1 = capture_screen(DEFAULT_OUTPUT_DIR)
        time.sleep(1.1)  # filenames carry second-level+ms precision; ensure a visibly distinct timestamp
        m2 = capture_screen(DEFAULT_OUTPUT_DIR)
        ts1 = datetime.fromisoformat(m1.timestamp)
        ts2 = datetime.fromisoformat(m2.timestamp)
        filenames_unique = m1.filename != m2.filename
        timestamps_increase = ts2 > ts1
        ok = filenames_unique and timestamps_increase
        tests.append({
            "test_id": "SC-006",
            "name": "Timestamp / unique filename verification",
            "result": "PASS" if ok else "FAIL",
            "details": {
                "file_1": m1.filename,
                "file_2": m2.filename,
                "timestamp_1": m1.timestamp,
                "timestamp_2": m2.timestamp,
                "filenames_unique": filenames_unique,
                "timestamps_increase": timestamps_increase,
            },
        })
    except ScreenCaptureError as exc:
        tests.append({
            "test_id": "SC-006",
            "name": "Timestamp / unique filename verification",
            "result": "FAIL",
            "details": {"error": str(exc)},
        })

    # SC-007: Capture while Outlook is visible (visual presence only, no analysis)
    outlook_running = is_outlook_running()
    if outlook_running:
        try:
            m = capture_screen(DEFAULT_OUTPUT_DIR)
            valid, detail = validate_capture(m)
            tests.append({
                "test_id": "SC-007",
                "name": "Capture while Outlook is visible",
                "result": "PASS" if valid else "FAIL",
                "details": {"file": m.path, "validation": detail, "note": "Presence-only check; image content not analyzed."},
            })
        except ScreenCaptureError as exc:
            tests.append({
                "test_id": "SC-007",
                "name": "Capture while Outlook is visible",
                "result": "FAIL",
                "details": {"error": str(exc)},
            })
    else:
        tests.append({
            "test_id": "SC-007",
            "name": "Capture while Outlook is visible",
            "result": "SKIPPED",
            "details": {"reason": "OUTLOOK.EXE was not running on this machine at test time."},
        })

    result_doc = {
        "experiment_id": "RND-001",
        "timestamp": datetime.now().isoformat(),
        "machine": {
            "platform": platform.platform(),
            "mss_width": env.get("mss_width"),
            "mss_height": env.get("mss_height"),
            "pyautogui_width": env.get("pyautogui_width"),
            "pyautogui_height": env.get("pyautogui_height"),
            "monitor_count": env.get("monitor_count"),
        },
        "tests": tests,
    }
    return result_doc


def main() -> None:
    result_doc = run()
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(json.dumps(result_doc, indent=2), encoding="utf-8")

    print(f"Results written to: {RESULTS_PATH}")
    print()
    for t in result_doc["tests"]:
        print(f"{t['test_id']:8s} {t['result']:8s} {t['name']}")


if __name__ == "__main__":
    main()
