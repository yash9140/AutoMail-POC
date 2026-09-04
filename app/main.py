"""RND-009A entry point — Windows POC app interface + playbook-ready
architecture. UI + architecture preparation only: no Outlook launch, no
mouse/keyboard automation, no Send, no real playbook execution.

Run (from outlook-vision-poc/):
    python -m app.main
"""

from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

from app.ui.main_window import MainWindow


def main() -> None:
    # Qt6 high-DPI scaling is on by default, but PassThrough rounding
    # keeps fractional scale factors like Windows' 125% exact instead of
    # snapping to the nearest integer factor, per this stage's requirement
    # to work correctly under 125% display scaling.
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)

    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
