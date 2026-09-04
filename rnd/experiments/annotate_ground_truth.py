"""RND-002 manual ground-truth annotation tool.

Human-only tool. Displays a raw Outlook screenshot, lets a researcher draw
bounding boxes with the mouse and label them, then writes the resulting
targets into the dataset manifest (test_cases/outlook/dataset_manifest.json)
for that test case, marking it "captured".

No AI, no OCR, no object detection, no automatic control location — every
box placed here is placed by a human looking at the actual image. The
Vision AI being evaluated later must never see or influence this file.

Two ways to register a target:

1. Queued (fast path, recommended when you already know what you're about
   to draw): pass --targets, e.g. --targets email_row:row. Finishing a
   drag immediately registers it as the next name in the queue — no key
   press or dialog needed. The status bar always shows "Targets: N" so you
   can see it was registered.

2. Ad hoc (for anything not in --targets, or when --targets is omitted):
   after drawing, press Enter — a dialog asks for a name and type, then
   adds it. Use this for screenshots with multiple/unpredictable targets
   (e.g. Reply + Reply All + Forward all visible together).

Run (from outlook-vision-poc/):
    python rnd/experiments/annotate_ground_truth.py \
        --test-id OUTLOOK-001 \
        --screenshot screenshots/raw/screen_20260827_152852_448.png \
        --targets email_row:row

Controls:
    Left-drag       draw a box (auto-registers if --targets has one queued)
    Enter           after drawing, prompts for target name + type, adds it
    U               undo the last added target
    S               save manifest and exit
    Esc / Q         quit without saving
"""

from __future__ import annotations

import argparse
import ctypes
import json
import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, simpledialog

from PIL import Image, ImageTk

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = PROJECT_ROOT / "test_cases" / "outlook" / "dataset_manifest.json"

sys.path.insert(0, str(PROJECT_ROOT))
from rnd.models.dataset_manifest import TARGET_TYPES  # noqa: E402

DEFAULT_TARGET_TYPE = "button"

# Reserved space below/around the canvas for the status label, window title
# bar, and a safety margin against the taskbar, so the bottom of a
# full-height screenshot (e.g. Send in a maximized Outlook window) is never
# pushed off the visible screen.
RESERVED_CHROME_HEIGHT = 170
RESERVED_CHROME_WIDTH = 60


def _make_process_dpi_aware() -> None:
    """Tell Windows this process handles its own DPI scaling.

    Without this, Windows scales the whole Tk window up by the display
    scaling factor (e.g. 125%) after tkinter has already laid it out in
    unscaled pixels — so a window tkinter believes is 900px tall actually
    renders ~1125px tall on screen, pushing content (like a Send button
    near the bottom) off the visible screen or behind the taskbar. This
    mirrors the DPI-awareness finding from RND-001 (docs/03_Screen_Capture.md):
    mss/pyautogui are already DPI-aware; a plain Tk app is not, unless told.
    Best-effort: silently no-ops on non-Windows or older Windows without
    these APIs.
    """
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # PROCESS_SYSTEM_DPI_AWARE
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def parse_target_queue(spec: str | None) -> list[tuple[str, str]]:
    """Parse '--targets email_row:row,Reply:button' into [('email_row','row'), ('Reply','button')]."""
    if not spec:
        return []
    queue: list[tuple[str, str]] = []
    for item in spec.split(","):
        item = item.strip()
        if not item:
            continue
        if ":" in item:
            name, _, target_type = item.partition(":")
        else:
            name, target_type = item, DEFAULT_TARGET_TYPE
        name, target_type = name.strip(), target_type.strip()
        if target_type not in TARGET_TYPES:
            raise ValueError(f"'{target_type}' (from '{item}') is not one of {sorted(TARGET_TYPES)}")
        queue.append((name, target_type))
    return queue


def compute_display_scale(orig_width: int, orig_height: int, avail_width: int, avail_height: int) -> float:
    """Aspect-ratio-preserving scale factor to fit orig_width x orig_height inside avail_width x avail_height.

    Never scales up (max 1.0) — a screenshot smaller than the available
    area is shown at native size.
    """
    if orig_width <= 0 or orig_height <= 0:
        raise ValueError("orig_width and orig_height must be positive")
    return min(1.0, avail_width / orig_width, avail_height / orig_height)


def to_original_coords(display_x: float, display_y: float, scale: float) -> tuple[int, int]:
    """Convert a point in scaled display-space back to original screenshot pixel coordinates."""
    return round(display_x / scale), round(display_y / scale)


def to_original_bbox(dx0: float, dy0: float, dx1: float, dy1: float, scale: float) -> tuple[int, int, int, int]:
    """Convert a display-space box (any corner order) to an ordered original-pixel bbox."""
    ox0, oy0 = to_original_coords(dx0, dy0, scale)
    ox1, oy1 = to_original_coords(dx1, dy1, scale)
    x0, x1 = sorted((ox0, ox1))
    y0, y1 = sorted((oy0, oy1))
    return x0, y0, x1, y1


class AnnotationApp:
    def __init__(self, root: tk.Tk, test_id: str, screenshot_path: Path, target_queue: list[tuple[str, str]]):
        self.root = root
        self.test_id = test_id
        self.screenshot_path = screenshot_path
        self.target_queue = list(target_queue)

        self.original_image = Image.open(screenshot_path)
        self.orig_width, self.orig_height = self.original_image.size

        # Compute available space from the real (DPI-aware, post-fix) screen
        # size, minus room for the status label / title bar / taskbar, so
        # the full image — including its bottom edge — stays on screen.
        screen_width = root.winfo_screenwidth()
        screen_height = root.winfo_screenheight()
        avail_width = max(200, screen_width - RESERVED_CHROME_WIDTH)
        avail_height = max(200, screen_height - RESERVED_CHROME_HEIGHT)

        self.scale = compute_display_scale(self.orig_width, self.orig_height, avail_width, avail_height)
        display_size = (round(self.orig_width * self.scale), round(self.orig_height * self.scale))
        self.display_image = self.original_image.resize(display_size, Image.LANCZOS)
        self.tk_image = ImageTk.PhotoImage(self.display_image)

        self.targets: list[dict] = []
        self.rect_start: tuple[int, int] | None = None
        self.current_rect_id: int | None = None
        self.pending_bbox_original: tuple[int, int, int, int] | None = None
        self.last_message = ""

        root.title(f"RND-002 Annotation — {test_id} — {screenshot_path.name}")
        root.geometry("+0+0")  # anchor at top-left so the bottom of a full-height image stays reachable

        self.canvas = tk.Canvas(root, width=display_size[0], height=display_size[1], cursor="cross")
        self.canvas.pack()
        self.canvas.create_image(0, 0, anchor=tk.NW, image=self.tk_image)

        self.dims_label = tk.Label(
            root,
            text=f"Original: {self.orig_width}x{self.orig_height}  |  "
                 f"Displayed: {display_size[0]}x{display_size[1]}  |  Scale: {self.scale:.4f}",
            anchor="w", justify="left", font=("Segoe UI", 9, "bold"),
        )
        self.dims_label.pack(fill=tk.X)

        self.status = tk.Label(root, text="", anchor="w", justify="left", wraplength=display_size[0])
        self.status.pack(fill=tk.X)
        self._refresh_status()

        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        # Bind on both root and canvas — belt and suspenders against focus quirks.
        for widget in (root, self.canvas):
            widget.bind("<Return>", self.on_confirm_target)
            widget.bind("u", self.on_undo)
            widget.bind("U", self.on_undo)
            widget.bind("s", self.on_save)
            widget.bind("S", self.on_save)
            widget.bind("<Escape>", self.on_quit)
            widget.bind("q", self.on_quit)
            widget.bind("Q", self.on_quit)

        self.canvas.focus_set()
        root.after(100, root.focus_force)

    def _refresh_status(self, message: str = "") -> None:
        self.last_message = message
        names = ", ".join(t["name"] for t in self.targets) if self.targets else "(none)"
        next_queued = self.target_queue[0][0] if self.target_queue else None
        queue_hint = (
            f"  |  next drag auto-registers as '{next_queued}'"
            if next_queued
            else "  |  drag then press Enter to name a target"
        )
        header = f"Targets: {len(self.targets)}  [{names}]{queue_hint}"
        controls = "drag=draw box | Enter=confirm+name | U=undo | S=save+exit | Esc/Q=quit without saving"
        text = f"{header}\n{message}\n{self.test_id}  |  {controls}"
        self.status.config(text=text)

    def on_press(self, event):
        self.rect_start = (event.x, event.y)
        if self.current_rect_id is not None:
            self.canvas.delete(self.current_rect_id)
            self.current_rect_id = None

    def on_drag(self, event):
        if self.rect_start is None:
            return
        if self.current_rect_id is not None:
            self.canvas.delete(self.current_rect_id)
        x0, y0 = self.rect_start
        self.current_rect_id = self.canvas.create_rectangle(
            x0, y0, event.x, event.y, outline="red", width=2
        )

    def on_release(self, event):
        if self.rect_start is None:
            return
        x0, y0 = self.rect_start
        x1, y1 = event.x, event.y
        self.rect_start = None
        if abs(x1 - x0) < 3 or abs(y1 - y0) < 3:
            # Too small to be a real box; discard.
            if self.current_rect_id is not None:
                self.canvas.delete(self.current_rect_id)
                self.current_rect_id = None
            return

        ox0, oy0, ox1, oy1 = to_original_bbox(x0, y0, x1, y1, self.scale)

        if self.target_queue:
            name, target_type = self.target_queue.pop(0)
            self.targets.append({
                "name": name,
                "type": target_type,
                "bbox": {"x1": ox0, "y1": oy0, "x2": ox1, "y2": oy1},
            })
            if self.current_rect_id is not None:
                self.canvas.delete(self.current_rect_id)
                self.current_rect_id = None
            self._refresh_status(f"'{name}' registered automatically at ({ox0},{oy0})-({ox1},{oy1}).")
            return

        self.pending_bbox_original = (ox0, oy0, ox1, oy1)
        self._refresh_status(
            f"Box pending at ({ox0},{oy0})-({ox1},{oy1}). Press Enter now to name it, or drag again to replace it."
        )

    def on_confirm_target(self, event):
        if self.pending_bbox_original is None:
            self._refresh_status("Nothing pending — draw a box first, then press Enter.")
            return
        name = simpledialog.askstring("Target name", "Target name (e.g. Reply, Send, email_row):", parent=self.root)
        if not name:
            self._refresh_status("Cancelled — no name entered, target not added.")
            return
        type_prompt = f"Target type ({'/'.join(sorted(TARGET_TYPES))}):"
        target_type = simpledialog.askstring("Target type", type_prompt, parent=self.root, initialvalue=DEFAULT_TARGET_TYPE)
        if target_type not in TARGET_TYPES:
            messagebox.showerror("Invalid type", f"'{target_type}' is not one of {sorted(TARGET_TYPES)}. Target not added.")
            self._refresh_status(f"Invalid type '{target_type}' — target not added.")
            return

        x0, y0, x1, y1 = self.pending_bbox_original
        self.targets.append({
            "name": name,
            "type": target_type,
            "bbox": {"x1": x0, "y1": y0, "x2": x1, "y2": y1},
        })
        self.pending_bbox_original = None
        if self.current_rect_id is not None:
            self.canvas.delete(self.current_rect_id)
            self.current_rect_id = None
        self._refresh_status(f"'{name}' added.")

    def on_undo(self, event):
        if self.targets:
            removed = self.targets.pop()
            self._refresh_status(f"Removed '{removed['name']}'.")
        else:
            self._refresh_status("Nothing to undo.")

    def on_save(self, event):
        if not self.targets:
            if not messagebox.askyesno("No targets", "No targets were annotated. Save anyway?"):
                return
        save_case(self.test_id, self.screenshot_path, self.orig_width, self.orig_height, self.targets)
        messagebox.showinfo("Saved", f"Saved {len(self.targets)} target(s) for {self.test_id} to manifest.")
        self.root.destroy()

    def on_quit(self, event):
        self.root.destroy()


def load_manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def save_case(test_id: str, screenshot_path: Path, width: int, height: int, targets: list[dict]) -> None:
    manifest = load_manifest()
    case = next((c for c in manifest["cases"] if c["test_id"] == test_id), None)
    if case is None:
        raise ValueError(f"test_id '{test_id}' not found in manifest — add it to dataset_manifest.json first")

    case["filename"] = screenshot_path.name
    case["status"] = "captured"
    case["screen"] = {
        "width": width,
        "height": height,
        "windows_scaling_percent": 125,
    }
    case["targets"] = targets

    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="RND-002 manual ground-truth annotation tool")
    parser.add_argument("--test-id", required=True, help="e.g. OUTLOOK-003 (must already exist in the manifest)")
    parser.add_argument("--screenshot", required=True, help="Path to the raw screenshot to annotate")
    parser.add_argument(
        "--targets",
        default=None,
        help="Optional comma-separated 'name[:type]' queue, e.g. 'email_row:row' or 'Reply:button,ReplyAll:button'. "
             "Each drawn box auto-registers as the next queued name — no Enter/dialog needed. "
             "Extra boxes beyond the queue still use the Enter+dialog flow.",
    )
    args = parser.parse_args()

    screenshot_path = Path(args.screenshot)
    if not screenshot_path.exists():
        print(f"Screenshot not found: {screenshot_path}")
        raise SystemExit(1)

    manifest = load_manifest()
    if not any(c["test_id"] == args.test_id for c in manifest["cases"]):
        print(f"test_id '{args.test_id}' not found in {MANIFEST_PATH}. Add it first.")
        raise SystemExit(1)

    try:
        target_queue = parse_target_queue(args.targets)
    except ValueError as exc:
        print(f"Invalid --targets value: {exc}")
        raise SystemExit(1)

    _make_process_dpi_aware()  # must happen before the Tk root window is created

    root = tk.Tk()
    AnnotationApp(root, args.test_id, screenshot_path, target_queue)
    root.mainloop()


if __name__ == "__main__":
    main()
