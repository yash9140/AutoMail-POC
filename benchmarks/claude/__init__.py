"""Real-Claude-API visual evaluation harness (2026-09-03).

Measures actual Claude behavior (semantic understanding, bbox grounding,
structured-output reliability, confidence, latency, cost, consistency)
against STATIC saved screenshots, using the exact same runtime prompts
and Pydantic schemas the live app uses.

This package is intentionally NOT pytest-discoverable (no file here is
named test_*.py) — it is invoked explicitly via
`python -m benchmarks.claude.evaluation_runner`, never picked up
by a normal `pytest` run. It makes REAL calls to the configured
Anthropic API (real cost, real latency) and must never be imported by,
or run as part of, the mocked unit-test suite.

Nothing in this package imports pyautogui, keyboard, or any
app.outlook.*/app.automation.* step module that could perform a
physical mouse/keyboard action — see evaluation_runner.py's own
import-safety self-check. This is perception/evaluation only.
"""
