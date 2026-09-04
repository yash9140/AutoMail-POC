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

See [docs/01_POC_Goal_and_Scope.md](docs/01_POC_Goal_and_Scope.md) for the
full goal statement and [docs/02_RND_Methodology.md](docs/02_RND_Methodology.md)
for how experiments are run and measured.

## Final Target Workflow

```
Login → Terms & Access → Start Automation
  → Observe Desktop → Locate & Open Outlook
  → Observe Outlook → Open Existing Email
  → Understand Email → Locate & Click Reply
  → Verify Reply Editor → Generate Reply → Type Reply
  → Locate & Click Send → Observe Screen → Verify Completion
  → Show Result
```

This POC is built incrementally, one measured experiment at a time — see
the implementation order in [docs/02_RND_Methodology.md](docs/02_RND_Methodology.md).
Automatic Send is disabled until earlier steps are proven safe and reliable
(see `docs/09` onward).

## Project Structure

```
outlook-vision-poc/
├── docs/                  R&D documentation (one file per experiment/topic)
├── rnd/
│   ├── capture/           Screen capture implementation
│   ├── providers/         Vision AI provider interface + implementations
│   ├── experiments/       Runnable experiment scripts
│   ├── metrics/           Result evaluation + cost tracking
│   ├── prompts/           Versioned prompt files sent to the Vision AI
│   └── models/            Pydantic models for experiment results
├── screenshots/
│   ├── raw/               Unmodified captured screenshots
│   └── annotated/         Screenshots annotated with predicted vs actual targets
├── results/
│   ├── raw/                Raw per-test result records (JSON)
│   ├── reports/             Aggregated reports
│   └── benchmark.csv       Cross-experiment/cross-model comparison
├── test_cases/outlook/     Controlled test email definitions
├── config/                 Model pricing / non-secret configuration
└── tests/                  Automated tests
```

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
testing (see `.env.example` for the full list of variables). Never commit
`.env`.

## Running Experiments

Experiments are added incrementally; each will be runnable directly, e.g.:

```bash
python rnd/capture/screen_capture.py
python rnd/experiments/screen_understanding.py
python rnd/experiments/ui_grounding.py
python rnd/experiments/outlook_workflow.py
```

Exact commands and required setup for each experiment are documented in the
corresponding `docs/0N_*.md` file — see [docs/00_RND_INDEX.md](docs/00_RND_INDEX.md)
for the current status of every experiment.

## Where Results Live

- Raw, per-test machine-readable results: `results/raw/`
- Aggregated reports: `results/reports/`
- Cross-model benchmark comparison: `results/benchmark.csv`
- Annotated grounding screenshots: `screenshots/annotated/`

## Where Documentation Lives

All R&D documentation is in [docs/](docs/), indexed by
[docs/00_RND_INDEX.md](docs/00_RND_INDEX.md). Every experiment has a
document following the same structure: Objective, What Was Built,
Technology Used, Why, How It Works, How To Use It, Configuration, Test
Cases, Actual Results, Failures/Observations, Limitations, Conclusion, Next
Step.

## Running the Final POC

Not yet available. The final integrated PySide6 Windows application
(Login → Terms & Access → Automation Status → Final Result) is built only
after the individual Vision AI capabilities have been validated through the
experiments above — see Step 12 in
[docs/02_RND_Methodology.md](docs/02_RND_Methodology.md).

## Scope

This is strictly an **Outlook** Vision AI automation POC. It intentionally
excludes Chrome/browser automation, ERP integration, generic multi-app
support, production architecture, and any Outlook-specific API/COM/Graph
integration. See [docs/01_POC_Goal_and_Scope.md](docs/01_POC_Goal_and_Scope.md)
for the full list of exclusions and why.
