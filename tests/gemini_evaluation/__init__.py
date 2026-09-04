"""Real-Gemini-API static-screenshot latency benchmark (2026-09-04).

Investigates a live report of high OUTLOOK_SEARCH/OUTLOOK_READINESS/
TARGET_EMAIL_SEARCH latency (40-60s+) under Gemini, comparing the
CURRENT runtime request path against the HISTORICAL one preserved in
rnd/ artifacts, on both historical and freshly-captured real
screenshots. Perception-only: no mouse, no keyboard, no Outlook launch
— see benchmark_runner.py's import-safety self-check. Not
pytest-discoverable (no file here is named test_*.py); invoked via
`python -m tests.gemini_evaluation.benchmark_runner`.
"""
