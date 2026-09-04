# Outlook Vision AI Automation POC

## Overview

This is an R&D proof-of-concept that measures how accurately **Vision AI**
can visually understand Microsoft Outlook and carry out a real email-reply
workflow using human-like screen observation, mouse movement, and keyboard
input — **with no Outlook-specific integration** (no Graph API, no COM, no
SMTP, no Office.js).

The research question is not "can Python send an email" — it's:

> How capable, accurate, fast, reliable, and cost-effective is Vision AI at
> visually operating Outlook end-to-end?

See [docs/r_and_d/01_POC_Goal_and_Scope.md](docs/r_and_d/01_POC_Goal_and_Scope.md)
for the full goal statement and [docs/r_and_d/02_RND_Methodology.md](docs/r_and_d/02_RND_Methodology.md)
for how R&D experiments were run and measured.

## Final Workflow (implemented and live-runnable)

```
Login → Terms & Access → Start Automation
  → Observe Desktop → Locate & Open Outlook
  → Observe Outlook → Open Existing Email
  → Understand Email → Locate & Click Reply
  → Verify Reply Editor → Generate Reply → Type Reply
  → Locate & Click Send → Observe Screen → Verify Completion
  → Show Result
```

See [docs/architecture/01_ARCHITECTURE.md](docs/architecture/01_ARCHITECTURE.md)
and [docs/architecture/02_STATE_MACHINE.md](docs/architecture/02_STATE_MACHINE.md)
for how this is actually wired together.

## Project Structure

```
outlook-vision-poc/
├── app/            Live runtime POC — the only code the app actually runs
├── tests/          Automated offline tests (no real API calls)
├── benchmarks/     Real-provider evaluation/latency tools (costs money, never run by pytest)
├── rnd/            Historical R&D — partially a live runtime dependency, see rnd/README.md
├── results/        Preserved historical run evidence (raw provider responses, reports)
├── screenshots/    raw/ is also the live runtime's active screenshot output, see screenshots/README.md
├── debug/          Runtime-generated diagnostic overlays (opt-in via env var)
├── test_cases/     Shared fixture data, used by both tests/ and rnd/experiments/
├── config/         model_pricing.json — read by both app/ and rnd/experiments/
├── docs/           architecture/ (current design) + r_and_d/ (chronological history) + decisions/ + demo/
└── scripts/        Utility + historical live-run driver scripts
```

Full rationale for this layout, including which folders were
deliberately **not** moved and why, is in
[docs/architecture/PROJECT_STRUCTURE.md](docs/architecture/PROJECT_STRUCTURE.md)
and [docs/decisions/STRUCTURE_REORGANIZATION.md](docs/decisions/STRUCTURE_REORGANIZATION.md).

## Environment Setup

Requires Python 3.11+.

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
```

## API Configuration

```bash
copy .env.example .env
```

Then fill in the API key(s) for whichever Vision AI provider(s) you're
using (see `.env.example` for the full list of variables, including
`AI_PROVIDER=gemini` or `AI_PROVIDER=anthropic`). Never commit `.env`.

## Running the App

```bash
python -m app.main
```

Launches the PySide6 desktop app (Login → Terms & Access → Automation
Dashboard). Requires a real, visible Outlook desktop window on the same
machine — it drives the real OS via `pyautogui`, never a Graph/COM/SMTP
API.

## Running Tests

```bash
pytest
```

Runs the full offline test suite (598 tests as of the 2026-09-04
structure cleanup) — fully mocked, no real network/API calls, no
pyautogui, no live Outlook interaction. `pytest` from the repository
root discovers only `tests/*.py`; the real-API benchmark harnesses below
live outside `tests/` entirely and are never picked up.

## Running Real-Provider Benchmarks

These make **real Claude/Gemini API calls** and cost real money —
run them deliberately, not as part of normal testing.

```bash
# Claude grounding-strategy evaluation
python -m benchmarks.claude.evaluation_runner

# Gemini latency-investigation benchmark
python -m benchmarks.gemini.benchmark_runner
```

Output lands in `benchmarks/results/`. See
[benchmarks/README.md](benchmarks/README.md) for what each harness
measures.

## Where Historical R&D Results Live

- Raw, per-call provider responses: `results/raw/provider_responses/`
- Aggregated reports: `results/reports/`
- Annotated grounding screenshots: `screenshots/annotated/`
- Chronological write-up per experiment: `docs/r_and_d/`, indexed by
  [docs/r_and_d/00_RND_INDEX.md](docs/r_and_d/00_RND_INDEX.md)

## Where Documentation Lives

- [docs/architecture/](docs/architecture/) — current POC design: overview,
  architecture, state machine, safety model, scrolling/long-email
  handling, configuration, testing/QA, known limitations, project
  structure.
- [docs/r_and_d/](docs/r_and_d/) — the historical R&D record, one
  document per experiment (RND-001 through RND-009D), following the same
  structure throughout: Objective, What Was Built, Technology Used, Why,
  How It Works, How To Use It, Configuration, Test Cases, Actual
  Results, Failures/Observations, Limitations, Conclusion, Next Step.
- [docs/decisions/](docs/decisions/) — why structural/architectural
  changes were made.

## Scope

This is strictly an **Outlook** Vision AI automation POC. It intentionally
excludes Chrome/browser automation, ERP integration, generic multi-app
support, production architecture, and any Outlook-specific API/COM/Graph
integration. See [docs/r_and_d/01_POC_Goal_and_Scope.md](docs/r_and_d/01_POC_Goal_and_Scope.md)
for the full list of exclusions and why.
