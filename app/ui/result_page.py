"""RND-009A result screen — layout + field wiring only.

No real automation has run yet in this stage, so this page is
populated with placeholder/demo values only when explicitly asked to
be (via update_from_metrics); it is never populated automatically.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from app.metrics.session_metrics import SessionMetrics

RESULT_STATES = ("SUCCESS", "FAILED", "ABORTED")


class ResultPage(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self.final_status_label = QLabel("Final Status: -")
        self.completed_steps_label = QLabel("Completed Steps: -")
        self.failed_step_label = QLabel("Failed Step: -")
        self.total_duration_label = QLabel("Total Duration: -")
        self.vision_calls_label = QLabel("Vision Calls: -")
        self.total_cost_label = QLabel("Total Cost: -")
        self.safety_aborts_label = QLabel("Safety Aborts: -")

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("<h2>Result</h2>"))
        for label in (
            self.final_status_label,
            self.completed_steps_label,
            self.failed_step_label,
            self.total_duration_label,
            self.vision_calls_label,
            self.total_cost_label,
            self.safety_aborts_label,
        ):
            layout.addWidget(label)
        layout.addStretch()

    def update_from_metrics(self, metrics: SessionMetrics, final_status: str) -> None:
        assert final_status in RESULT_STATES, f"Unknown final status: {final_status}"

        self.final_status_label.setText(f"Final Status: {final_status}")
        self.completed_steps_label.setText(f"Completed Steps: {metrics.completed_steps}")
        self.failed_step_label.setText(f"Failed Step: {metrics.failed_step or '-'}")

        # Pre-RND-009D hardening: prefer the worker-measured
        # total_elapsed_ms (precise, monotonic-clock-based) over diffing
        # the controller's own start_time/end_time strings, which is only
        # a fallback for a run that never reached the worker at all.
        if metrics.total_elapsed_ms is not None:
            duration = f"{metrics.total_elapsed_ms / 1000:.1f}s"
        else:
            duration = self._duration_text(metrics.start_time, metrics.end_time)
        self.total_duration_label.setText(f"Total Duration: {duration}")
        self.vision_calls_label.setText(f"Vision Calls: {metrics.vision_calls}")
        self.total_cost_label.setText(f"Total Cost: ${metrics.estimated_cost}")
        self.safety_aborts_label.setText(f"Safety Aborts: {metrics.safety_aborts}")

    @staticmethod
    def _duration_text(start_time: Optional[str], end_time: Optional[str]) -> str:
        if not start_time or not end_time:
            return "-"
        from datetime import datetime

        started = datetime.fromisoformat(start_time)
        ended = datetime.fromisoformat(end_time)
        return f"{(ended - started).total_seconds():.1f}s"
