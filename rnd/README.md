# rnd/ — Historical R&D, partially reused by the live runtime

**Status: frozen historical evidence, with a live runtime dependency.**
Files here are never edited (see the "never modified" notes throughout
this codebase's docstrings) — they are the original, as-run R&D record
for RND-001 through RND-009D (see `docs/r_and_d/00_RND_INDEX.md`).

## Why this wasn't moved into `research/`

Most of this project's historical R&D was cleanly separable from the
live runtime and could be filed under `research/`. `rnd/` is the one
exception: several of its files are **directly imported or read by
`app/` at runtime**, by original design — new schemas/prompts were
never duplicated once a frozen R&D version already existed and worked.
Moving `rnd/` would mean rewriting production import statements across
6 files in `app/outlook/`, which is a runtime-code change, not a pure
reorganization — out of scope for the 2026-09-04 structure cleanup (see
`docs/decisions/STRUCTURE_REORGANIZATION.md`).

### What the live runtime actually depends on here

- **Models** (`rnd/models/*.py`) — `app/outlook/{draft,find_email,launch,
  read_email,reply,send}.py` import shared base Pydantic models directly
  (e.g. `CallMetrics`, `RND009DResult`, `StepMetrics`,
  `VerificationResponseV2`). These are reused, not duplicated.
- **Prompts** (`rnd/prompts/*.txt`) — 5 specific prompt files are read at
  runtime, not just historically: `reply_generation_v1.txt`,
  `draft_verification_v2.txt` (via `app/outlook/draft.py`),
  `windows_search_grounding_v1.txt`, `outlook_launch_verification_v1.txt`
  (via `app/outlook/launch.py`), and `state_verification_v2.txt` (via
  `app/outlook/reply.py` and `app/outlook/send.py`).

Every other file under `rnd/` — `rnd/experiments/*.py`,
`rnd/capture/*.py`, `rnd/metrics/*.py`, `rnd/providers/*.py`, and the
remaining `rnd/prompts/*.txt` — is pure historical evidence: runnable
standalone experiment scripts from the original R&D phase, never
imported by `app/` or `tests/`.

## What's here

- `capture/` — original screen-capture implementation (RND-001); `app/
  automation/screen_capture.py` is now a copy the runtime actually uses,
  kept in sync by hand, not by import.
- `experiments/` — one runnable script per numbered R&D experiment
  (RND-003 through RND-008).
- `metrics/` — evaluation/coordinate-calibration helpers used by the
  experiment scripts above (and partially reused into `app/vision/
  grounding.py`).
- `models/` — Pydantic result/response schemas per experiment, several
  reused directly by the live runtime (see above).
- `prompts/` — versioned prompt text sent to the Vision provider during
  each experiment, 5 of which the live runtime still reads directly.
- `providers/` — the original R&D provider clients (Claude/Gemini/
  OpenAI); the live runtime has its own, separately maintained copies
  under `app/vision/providers/`.

## A note on stale doc comments inside this folder

Because files here are never edited, comments referencing the pre-2026-
09-04 doc paths (e.g. `docs/03_Screen_Capture.md`) were deliberately
**left un-updated** when `docs/` was reorganized into `docs/r_and_d/`
and `docs/architecture/` — updating them would have meant editing frozen
R&D files, which defeats the purpose of freezing them. Mentally prefix
any bare `docs/NN_*.md` reference you find in `rnd/` with `r_and_d/` (or
check `docs/r_and_d/RND_INDEX.md` if unsure).

## What was learned

See `docs/r_and_d/00_RND_INDEX.md` for the full experiment-by-experiment
index (status, results, cost, and links to each write-up) and
`docs/r_and_d/02_RND_Methodology.md` for how experiments were run and
measured.
