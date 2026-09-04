"""RND-002 capture with foreground-window confirmation.

Added after OUTLOOK-001 attempt 1 was captured while VS Code (not Outlook)
was the active foreground window. This script closes that gap: it waits,
reads which window is *actually* currently in the foreground using a
read-only Win32 API call, and refuses to take a screenshot at all unless
that window's title looks like Outlook.

This is a read-only check (GetForegroundWindow / GetWindowText). It does
NOT move, focus, minimize, or otherwise control any window or process —
bringing Outlook to the foreground remains a manual, human action, per the
RND-002 rule against automating mouse/keyboard/window control during
dataset collection.

Even when this check passes, the captured screenshot is NOT marked
"captured" in the dataset manifest by this script. That only happens after
a human visually confirms (in chat) that the image is the correct Outlook
state — see docs/04_Outlook_Screenshot_Dataset.md.

Run (from outlook-vision-poc/):
    python rnd/experiments/capture_with_foreground_check.py --test-id OUTLOOK-001 --delay 2.5
"""

from __future__ import annotations

import argparse
import ctypes
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "rnd" / "capture"))
from screen_capture import DEFAULT_OUTPUT_DIR, ScreenCaptureError, capture_screen, validate_capture  # noqa: E402


def get_foreground_window_title() -> str:
    hwnd = ctypes.windll.user32.GetForegroundWindow()
    length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Capture a dataset screenshot only if the foreground window title confirms Outlook is active"
    )
    parser.add_argument("--test-id", required=True, help="e.g. OUTLOOK-001")
    parser.add_argument("--delay", type=float, default=2.5, help="Seconds to wait before checking/capturing")
    parser.add_argument("--output-dir", type=str, default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument(
        "--require-substring",
        type=str,
        default="outlook",
        help="Case-insensitive substring the foreground window title must contain",
    )
    args = parser.parse_args()

    print(f"Waiting {args.delay}s before checking the foreground window...")
    time.sleep(args.delay)

    title = get_foreground_window_title()
    print(f"Foreground window title at check time: {title!r}")

    if args.require_substring.lower() not in title.lower():
        print(
            f"ABORTED: foreground window does not appear to be Outlook "
            f"(expected '{args.require_substring}' in the title)."
        )
        print("No screenshot was taken. Bring Outlook to the foreground yourself and try again.")
        raise SystemExit(2)

    try:
        metadata = capture_screen(Path(args.output_dir))
    except ScreenCaptureError as exc:
        print(f"Capture FAILED: {exc}")
        raise SystemExit(1)

    valid, detail = validate_capture(metadata)

    print()
    print(f"Test ID: {args.test_id}")
    print(f"Foreground window at capture time: {title!r}")
    print(f"File: {metadata.path}")
    print(f"Resolution: {metadata.width} x {metadata.height}")
    print(f"Timestamp: {metadata.timestamp}")
    print(f"File validation: {'PASS' if valid else 'FAIL'} ({detail})")
    print()
    print("NOT yet marked captured in the manifest.")
    print("A human must visually confirm this is the correct Outlook state before it is recorded.")


if __name__ == "__main__":
    main()
