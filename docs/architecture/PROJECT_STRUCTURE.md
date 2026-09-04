# Project Structure

Reorganized 2026-09-04 for maintainability (see `docs/decisions/
STRUCTURE_REORGANIZATION.md` for the full rationale and move log). This
document is the map — what each top-level folder is for, and whether
the live runtime depends on it.

```
outlook-vision-poc/
├── app/                    Live runtime POC — the only code the app actually runs
│   ├── main.py             Entry point: python -m app.main
│   ├── outlook/            Outlook automation steps (launch, find, read, reply, draft, send)
│   ├── vision/             Vision provider clients, prompts, models, grounding math
│   ├── safety/             Deterministic validators, abort controller, foreground checks
│   ├── fallback/           Provider-retry wrapper, failure classification
│   ├── playbook/           State machine (engine, states, transitions)
│   ├── controllers/        Qt controllers wiring workers to the UI
│   ├── workers/             QThread workers (one per playbook stage group)
│   ├── ui/                 PySide6 screens
│   ├── automation/          Screen capture, scrolling
│   ├── metrics/             Cost/latency accumulation
│   └── config/               Settings, provider selection
│
├── tests/                  Automated offline tests (598 tests, no real API calls)
│   └── test_*.py            Flat — see "Why tests/ wasn't split" below
│
├── benchmarks/             Real-provider evaluation & latency tooling (costs money, never run by pytest)
│   ├── claude/              Claude grounding-strategy evaluation harness
│   ├── gemini/              Gemini latency-investigation benchmark
│   └── results/              JSON/Markdown output from both
│
├── rnd/                    Historical R&D (RND-001..009D) — frozen, but PARTIALLY a live
│                            runtime dependency. See rnd/README.md before assuming it's safe
│                            to move or delete anything here.
│
├── results/                Historical run evidence (raw provider responses, reports).
│                            Written only by rnd/ scripts, never read by app/.
│
├── screenshots/             raw/ is the LIVE runtime's active screenshot output directory,
│                            mixed with historical R&D captures. annotated/ and app/ are
│                            historical/documentation only. See screenshots/README.md.
│
├── debug/                   Runtime-generated grounding-debug overlays (opt-in via
│                            OUTLOOK_GROUNDING_DEBUG=1 / EMAIL_GROUNDING_DEBUG=1). Never
│                            read by runtime code.
│
├── test_cases/               Shared fixture data — used by BOTH tests/ and rnd/experiments/*.py
│
├── config/                   model_pricing.json — read by BOTH app/ and rnd/experiments/*.py
│
├── docs/
│   ├── architecture/         Current POC design docs (was docs/poc/) — state machine, safety
│   │                          model, scrolling/long-email handling, configuration, testing,
│   │                          known limitations, this file
│   ├── r_and_d/               Chronological R&D write-ups, RND-001 through RND-009D (was docs/00-13_*.md)
│   ├── decisions/             Why structural/architectural changes were made
│   └── demo/                  Demo-running documentation
│
├── scripts/                   Utility + historical live-run driver scripts
│
├── .env / .env.example
├── requirements.txt
└── README.md
```

## Why `rnd/`, `config/`, `test_cases/`, `screenshots/` stay at the repo root

These four are the exception to "runtime code only imports from `app/`."
Each has a real import or file-path dependency from live runtime code
(see `rnd/README.md`, `screenshots/README.md`, `test_cases/README.md`
for the specifics of each). Moving them would mean rewriting production
import statements and path constants in `app/outlook/*.py` and `app/
automation/screen_capture.py` — a runtime-code change, which the
2026-09-04 cleanup deliberately did not do (organizational-only scope).

## Why `tests/` wasn't split into `unit/`/`integration/`/`regression/`

All 49 test files use `Path(__file__).resolve().parents[1]` for their
`sys.path.insert()` call, assuming they sit directly under `tests/`.
Physically splitting them into subfolders would break every single one
and require touching all 49 files for a cosmetic-only gain — the higher
priority, per the reorg's own guidance, was safety over cosmetic
restructuring. `tests/` stays flat.

## Import-safety guarantee

`app/` imports only from `app/`, `rnd/` (the specific reused files noted
above), and third-party packages — never from `tests/`, `benchmarks/`,
`results/`, or `debug/`. This is enforced by `tests/test_app_ui.py::
test_no_pyautogui_actions_occur_outside_the_named_outlook_modules` (a
structural pyautogui-surface check) and can be verified directly:

```bash
python -m compileall app
python -c "import app.main"
```
