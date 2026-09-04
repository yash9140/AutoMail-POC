# benchmarks/ — real-provider evaluation & latency tooling

Everything here makes **real Claude/Gemini API calls** and costs real
money. None of it is discovered or run by normal `pytest` — every file
here avoids the `test_*.py` naming pattern pytest looks for, on top of
living outside `tests/` entirely.

- `claude/` — Claude grounding-strategy evaluation harness (real-API
  comparison of point-vs-bbox/candidate-list/component-grounding
  prompting strategies against recorded live screenshots). Run with
  `python -m benchmarks.claude.evaluation_runner`.
- `gemini/` — Gemini latency-investigation benchmark (real-API,
  repeated-call variance measurement — the evidence that Gemini's
  60s-timeout live-run failures were transient per-call variance, not a
  code regression). Run with `python -m benchmarks.gemini.benchmark_runner`.
- `results/` — JSON/Markdown output from both harnesses above (moved
  here from the former top-level `evaluation_results/`).

Moved out of `tests/` on 2026-09-04 (was `tests/claude_evaluation/` and
`tests/gemini_evaluation/`) purely for clarity — pytest never picked
them up either way. See `docs/decisions/STRUCTURE_REORGANIZATION.md`.
