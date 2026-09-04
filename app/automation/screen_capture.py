"""Windows screen capture — final POC runtime copy.

Captures the primary Windows display via `mss`, saves it as PNG,
validates the saved file with Pillow. No AI calls, no Outlook-specific
APIs, no UI Automation, no mouse movement.

Copied from rnd/capture/screen_capture.py so app/ no longer imports
from rnd/. The rnd/ original is left untouched (still used by
rnd/experiments/*.py scripts and their own tests).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import mss
from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "screenshots" / "raw"


class ScreenCaptureError(RuntimeError):
    """Raised when capture, save, or directory setup fails."""


@dataclass
class CaptureMetadata:
    filename: str
    path: str
    width: int
    height: int
    timestamp: str
    monitor_index: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "filename": self.filename,
            "path": self.path,
            "width": self.width,
            "height": self.height,
            "timestamp": self.timestamp,
            "monitor_index": self.monitor_index,
        }


def capture_screen(
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    monitor_index: int = 1,
) -> CaptureMetadata:
    """Capture one full screenshot of the given mss monitor index and save it as PNG.

    monitor_index=1 is the primary monitor in mss's convention (index 0 is
    the combined virtual desktop across all monitors).
    """
    output_dir = Path(output_dir)
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ScreenCaptureError(f"Failed to create output directory {output_dir}: {exc}") from exc

    now = datetime.now()
    filename = f"screen_{now.strftime('%Y%m%d_%H%M%S_%f')[:-3]}.png"
    file_path = output_dir / filename

    try:
        with mss.MSS() as sct:
            if monitor_index >= len(sct.monitors):
                raise ScreenCaptureError(
                    f"Monitor index {monitor_index} not available; "
                    f"mss reports {len(sct.monitors) - 1} monitor(s)."
                )
            monitor = sct.monitors[monitor_index]
            raw = sct.grab(monitor)
    except mss.exception.ScreenShotError as exc:
        raise ScreenCaptureError(f"mss screen capture failed: {exc}") from exc

    try:
        img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
        img.save(file_path, "PNG")
    except (OSError, ValueError) as exc:
        raise ScreenCaptureError(f"Failed to save screenshot to {file_path}: {exc}") from exc

    return CaptureMetadata(
        filename=filename,
        path=str(file_path),
        width=raw.size[0],
        height=raw.size[1],
        timestamp=now.isoformat(),
        monitor_index=monitor_index,
    )


def validate_capture(metadata: CaptureMetadata) -> tuple[bool, str]:
    """Validate a saved screenshot: exists, non-empty, opens as PNG, matches expected dimensions."""
    path = Path(metadata.path)
    if not path.exists():
        return False, "file does not exist"
    if path.stat().st_size == 0:
        return False, "file is empty"

    try:
        with Image.open(path) as img:
            img.verify()
    except Exception as exc:
        return False, f"PNG failed integrity check: {exc}"

    try:
        with Image.open(path) as img:
            width, height = img.size
    except Exception as exc:
        return False, f"PNG could not be reopened after verify: {exc}"

    if width != metadata.width or height != metadata.height:
        return False, f"dimension mismatch: expected {metadata.width}x{metadata.height}, got {width}x{height}"

    return True, "ok"


def get_environment_info() -> dict[str, Any]:
    """Report mss monitor enumeration and compare mss vs pyautogui reported resolution.

    Does not move the mouse. pyautogui.size() only reads the reported screen size.
    """
    info: dict[str, Any] = {}

    with mss.MSS() as sct:
        monitors = sct.monitors
        individual = monitors[1:]
        info["monitor_count"] = len(individual)
        info["virtual_desktop"] = {
            "left": monitors[0]["left"],
            "top": monitors[0]["top"],
            "width": monitors[0]["width"],
            "height": monitors[0]["height"],
        }
        info["monitors"] = [
            {"index": i + 1, "left": m["left"], "top": m["top"], "width": m["width"], "height": m["height"]}
            for i, m in enumerate(individual)
        ]
        primary = individual[0] if individual else monitors[0]
        info["mss_width"] = primary["width"]
        info["mss_height"] = primary["height"]

    try:
        import pyautogui

        pg_width, pg_height = pyautogui.size()
        info["pyautogui_width"] = pg_width
        info["pyautogui_height"] = pg_height
        info["dimensions_match"] = (pg_width == info["mss_width"]) and (pg_height == info["mss_height"])
    except Exception as exc:  # pyautogui can fail to init in headless/RDP edge cases
        info["pyautogui_error"] = str(exc)

    return info
