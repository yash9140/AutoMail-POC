"""RND-009A top-level app controller.

Thin: its only job is deciding which screen is current in response to
page-level signals (login succeeded, permissions accepted). All
playbook/session logic lives in AutomationController.
"""

from __future__ import annotations

from PySide6.QtWidgets import QStackedWidget

from app.ui.automation_page import AutomationPage
from app.ui.login_page import LoginPage
from app.ui.permissions_page import PermissionsPage
from app.ui.result_page import ResultPage


class AppController:
    def __init__(
        self,
        stack: QStackedWidget,
        login_page: LoginPage,
        permissions_page: PermissionsPage,
        automation_page: AutomationPage,
        result_page: ResultPage,
    ) -> None:
        self.stack = stack
        self.login_page = login_page
        self.permissions_page = permissions_page
        self.automation_page = automation_page
        self.result_page = result_page

        self.login_page.login_succeeded.connect(self.show_permissions)
        self.permissions_page.permissions_accepted.connect(self.show_dashboard)

    def show_login(self) -> None:
        self.stack.setCurrentWidget(self.login_page)

    def show_permissions(self) -> None:
        self.stack.setCurrentWidget(self.permissions_page)

    def show_dashboard(self) -> None:
        self.stack.setCurrentWidget(self.automation_page)

    def show_result(self) -> None:
        self.stack.setCurrentWidget(self.result_page)
