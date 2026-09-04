# R&D Preservation Index (post-2026-09-04 reorg)

This is a **where-does-it-live-now** map for preserved Gemini/Claude
research, written for the 2026-09-04 structure cleanup. For the
experiment-by-experiment chronological record (what was tried, what
passed/failed, cost/latency numbers), see `00_RND_INDEX.md` in this
same folder — that file is unchanged, just relocated here from the
former top-level `docs/`.

| Research area | Lives at | Runtime dependency? | Context |
|---|---|---|---|
| Original RND-001..009D experiment scripts | `rnd/experiments/*.py` | No | One script per numbered experiment; see `00_RND_INDEX.md` for what each proved |
| Frozen result/response Pydantic models | `rnd/models/*.py` | **Yes** — `app/outlook/*.py` imports several directly (see `rnd/README.md`) | Reused rather than duplicated once proven correct |
| Frozen prompt text, per experiment | `rnd/prompts/*.txt` | **Yes**, 5 of ~20 files (see `rnd/README.md`) | Prompt wording as actually sent during each experiment |
| Original Claude/Gemini/OpenAI provider clients | `rnd/providers/*.py` | No — `app/vision/providers/*.py` are separately maintained live copies | Historical only |
| Screen-capture implementation | `rnd/capture/*.py` | No — `app/automation/screen_capture.py` is a hand-synced copy | RND-001 |
| Coordinate calibration / grounding evaluation helpers | `rnd/metrics/*.py` | Partially — logic merged into `app/vision/grounding.py`, original left in place | RND-005A/005B |
| Raw per-call provider responses (JSON) | `results/raw/provider_responses/<experiment>/` | No | 2,039 files, one per Vision API call made during R&D |
| Aggregated experiment reports | `results/reports/` | No | 21 files |
| Historical/annotated screenshots | `screenshots/raw/`, `screenshots/annotated/` | `raw/` partially — also the live runtime's active capture output (see `screenshots/README.md`) | Ground-truth dataset for grounding accuracy scoring |
| Claude grounding-strategy evaluation harness (real API) | `benchmarks/claude/` | No | Was `tests/claude_evaluation/`; compares point-vs-bbox/candidate-list/component-grounding prompting strategies |
| Gemini latency-investigation benchmark (real API) | `benchmarks/gemini/` | No | Was `tests/gemini_evaluation/`; the evidence that Gemini's 60s-timeout live failures were transient variance, not a code regression (2026-09-04 investigation) |
| Benchmark run output | `benchmarks/results/` | No | Was the top-level `evaluation_results/` |
| Chronological R&D write-ups | `docs/r_and_d/*.md` | No | Was top-level `docs/00_*.md`–`docs/13_*.md` |
| Current POC architecture docs | `docs/architecture/*.md` | No | Was `docs/poc/*.md` |

## What was learned (high-level, see individual docs for detail)

- **Coordinate contract**: Vision returns 0-1000 normalized coordinates;
  `app/vision/grounding.py` converts to pixels — resolution-independent
  by construction (RND-005A/005B).
- **Bbox over loose points**: a validated bounding box lets a click point
  be self-checked as genuinely inside the target region; a bare `(x, y)`
  claim can never be verified this way (established across RND-005
  through RND-009).
- **Splash screen ≠ ready**: `outlook_visible: true` was insufficient —
  Outlook's splash/loading screen satisfied it without being interactive
  (RND-009B hardening).
- **Two independent completion signals beat one**: `more_content_below`
  AND `end_of_message_visible` must BOTH agree before a long email is
  considered fully read — trusting either alone let the reader stop
  early (RND-009D scrolling fix, later re-confirmed during the
  2026-09-04 live-bug fixes).
- **Gemini has large transient per-call latency variance** (6s–60s+ for
  structurally identical requests) — proven via `benchmarks/gemini/`
  real-API evidence, not assumed. This shaped two 2026-09-04 fixes: the
  `EMAIL_SECTION_UNDERSTANDING` request-splitting change and giving
  `DRAFT_GENERATION` the same retry-on-technical-failure behavior every
  other pipeline stage already had.
