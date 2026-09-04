# Decision: Repository Structure Reorganization (2026-09-04)

## Why

The repository had grown to mix active runtime POC code, historical R&D
experiments, two real-API evaluation harnesses, benchmark
screenshots/results, debug output, and documentation all at the
top level, making it hard for a new engineer to tell what was live
runtime versus historical evidence. This was a pure organizational
cleanup — explicitly not a runtime redesign, latency optimization, or
business-logic change.

## What moved

| From | To | Reason |
|---|---|---|
| `tests/claude_evaluation/` | `benchmarks/claude/` | Real-API cost; self-contained (no other test imports it); already invisible to pytest, now also visibly separated |
| `tests/gemini_evaluation/` | `benchmarks/gemini/` | Same |
| `evaluation_results/` | `benchmarks/results/` | Output of the two harnesses above, kept alongside them |
| `docs/00_RND_INDEX.md` .. `docs/13_RND009C_Find_Open_Email_Playbook.md` | `docs/r_and_d/` | Chronological R&D write-ups, grouped |
| `docs/poc/*.md` | `docs/architecture/` | Current POC design docs, grouped separately from R&D history |

Depth-preserving moves (`tests/X/` → `benchmarks/Y/` keeps the same
nesting depth from repo root) meant every file's own `Path(__file__).
resolve().parents[N]` → `PROJECT_ROOT` computation kept working
unmodified. Only the internal `tests.claude_evaluation.*` /
`tests.gemini_evaluation.*` dotted-module imports, and the
`evaluation_results` output-directory constants, needed editing —
9 files total, all within the moved harnesses themselves.

## What was audited and deliberately left in place

Four directories that the target structure implied would move were kept
at the repository root, because — unlike everything above — the live
`app/` runtime genuinely imports from or reads them at run time:

- **`rnd/`** — `app/outlook/{draft,find_email,launch,read_email,reply,
  send}.py` import shared Pydantic models directly from `rnd/models/*.py`,
  and 5 files under `rnd/prompts/` are read by the runtime, not just by
  historical experiment scripts. See `rnd/README.md`.
- **`config/`** — `config/model_pricing.json` is read by both `app/
  metrics/step_metrics.py` (runtime) and ~12 `rnd/experiments/*.py`
  scripts, via identical `PROJECT_ROOT`-relative paths.
- **`test_cases/`** — fixture data read by both `tests/test_find_email.py`
  / `tests/test_outlook_reply.py` and several `rnd/experiments/*.py`
  scripts.
- **`screenshots/`** — `screenshots/raw/` is `app/automation/
  screen_capture.py`'s live capture output directory, not just a
  historical dataset.

Moving any of these would require rewriting production import
statements or hardcoded path constants inside `app/` — a runtime-code
change, which was explicitly out of scope for this cleanup. Each has an
explanatory `README.md` in place instead.

`tests/` itself was **not** split into `unit/`/`integration/`/
`regression/` subfolders: all 49 test files assume they sit directly
under `tests/` for their `sys.path.insert()` calls, and physically
moving them would break every one for a cosmetic-only gain.

## Confirmation: no runtime behavior was intentionally changed

- `python -m compileall app` — clean.
- `python -c "import app.main"` — clean.
- Full test suite: 598 passed before this reorg, 598 passed after (see
  the same test count, not a coincidence — no test was added, removed,
  or altered in behavior by this cleanup).
- Target Sender/Subject matching, sidebar safety, click-point policy,
  reply/send grounding, `SEND_CLICK_MAX`, provider retry counts, timeout
  values, and the playbook state machine were not touched by this task.

## Deferred / out of scope

- Fully relocating `rnd/` into `research/` by rewriting its ~15
  production import sites — larger, riskier follow-up, not attempted
  here (see `rnd/README.md`).
- The Gemini end-to-end latency (8-10 minutes wall-clock across ~11
  sequential Vision calls) — a separate, already-identified performance
  task, explicitly not started as part of this reorganization.
