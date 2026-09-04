"""RND-009A shared abort controller.

A single, simple flag that future automation workers (running outside
the UI thread, starting RND-009B) will check BETWEEN actions — never a
thread-kill. Setting the flag never stops anything by itself; whoever
is doing the work must observe it. This module is the one place that
flag lives so the UI, the playbook engine, and any future worker all
agree on the same source of truth.
"""

from __future__ import annotations


class AbortController:
    def __init__(self) -> None:
        self._abort_requested = False

    def request_abort(self) -> None:
        self._abort_requested = True

    def is_abort_requested(self) -> bool:
        return self._abort_requested

    def reset(self) -> None:
        self._abort_requested = False
