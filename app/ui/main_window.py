"""RND-009A main window — wires the 4 pages together via QStackedWidget.

Navigation: Login -> Permissions -> Dashboard -> (future) Result.
"""

from __future__ import annotations

from PySide6.QtWidgets import QMainWindow, QStackedWidget

from app.controllers.app_controller import AppController
from app.controllers.automation_controller import AutomationController
from app.ui.automation_page import AutomationPage
from app.ui.login_page import LoginPage
from app.ui.permissions_page import PermissionsPage
from app.ui.result_page import ResultPage

WINDOW_TITLE = "Outlook Vision AI Automation POC"
DEFAULT_WIDTH = 900
DEFAULT_HEIGHT = 600


class MainWindow(QMainWindow):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(WINDOW_TITLE)
        self.resize(DEFAULT_WIDTH, DEFAULT_HEIGHT)

        self.login_page = LoginPage()
        self.permissions_page = PermissionsPage()
        self.automation_page = AutomationPage()
        self.result_page = ResultPage()

        self.stack = QStackedWidget()
        self.stack.addWidget(self.login_page)
        self.stack.addWidget(self.permissions_page)
        self.stack.addWidget(self.automation_page)
        self.stack.addWidget(self.result_page)
        self.setCentralWidget(self.stack)

        self.app_controller = AppController(
            self.stack, self.login_page, self.permissions_page, self.automation_page, self.result_page
        )
        self.automation_controller = AutomationController(self.automation_page)

        # Dashboard needs its initial status labels populated the moment
        # it becomes reachable, not only when the widget is constructed.
        self.permissions_page.permissions_accepted.connect(self.automation_controller.initialize)

        self.app_controller.show_login()
