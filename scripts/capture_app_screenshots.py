"""One-off script (RND-009A) to capture real, pixel-accurate renders of
each app screen for documentation. Drives the actual widgets/controllers
directly (same code path the UI tests exercise) rather than simulating
OS-level mouse/keyboard input — no pyautogui, no real Outlook, no
network. Uses the native Windows Qt platform (the offscreen platform's
font rendering produced glyph-less boxes on this machine) — a real
window may briefly appear on screen while this runs; it performs no
external action and can be closed/ignored.

Run (from outlook-vision-poc/):
    python scripts/capture_app_screenshots.py
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.ui.main_window import MainWindow  # noqa: E402

OUTPUT_DIR = PROJECT_ROOT / "screenshots" / "app"


def save(widget, name: str) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / name
    widget.grab().save(str(path))
    print(f"Saved {path.relative_to(PROJECT_ROOT)}")


def main() -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow()
    window.resize(900, 600)
    window.show()

    # 1. Login screen (initial state)
    save(window, "01_login_screen.png")

    # Fill placeholder credentials and "log in" — no real auth occurs.
    window.login_page.email_input.setText("demo.user@example.com")
    window.login_page.password_input.setText("placeholder-not-real")
    window.login_page._on_login_clicked()

    # 2. Terms & Permissions screen
    save(window, "02_permissions_screen.png")

    window.permissions_page.checkbox_observe.setChecked(True)
    window.permissions_page.checkbox_control.setChecked(True)
    window.permissions_page.checkbox_vision_ai.setChecked(True)
    window.permissions_page.permissions_accepted.emit()

    # 3. Automation dashboard (initial Ready state)
    save(window, "03_automation_dashboard.png")

    # 4. Dashboard after Start Automation -> LAUNCHING_OUTLOOK
    window.automation_controller.start_automation()
    save(window, "04_dashboard_launching_outlook.png")

    # 5. Dashboard after Stop / Abort -> ABORTED
    window.automation_controller.abort_automation()
    save(window, "05_dashboard_aborted.png")

    print("Done. No real Outlook, network, or OS-level input was used.")


if __name__ == "__main__":
    main()
