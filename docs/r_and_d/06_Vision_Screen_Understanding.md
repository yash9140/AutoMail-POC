# 06 — Vision Screen Understanding Accuracy (RND-004)

## Objective

Evaluate how accurately Gemini understands the 10 required Outlook
states captured in RND-002, using only raw screenshots, measuring:
application recognition, screen-state understanding, relevant-control
detection, correct next-action reasoning, latency, usage/cost, and real
provider failures. No coordinate grounding (RND-005) and no mouse/keyboard
automation are in scope here.

## Dataset

All 10 required RND-002 cases, raw screenshots only:

```
OUTLOOK-001  screen_20260827_152852_448.png   inbox
OUTLOOK-002  screen_20260827_163644_784.png   email_open
OUTLOOK-003  screen_20260827_164739_324.png   email_open, Reply visible
OUTLOOK-004  screen_20260827_165406_262.png   reply_editor_open
OUTLOOK-005  screen_20260827_165946_191.png   reply_editor_open, Send visible
OUTLOOK-006  screen_20260827_172436_226.png   maximized
OUTLOOK-007  screen_20260827_173334_423.png   resized
OUTLOOK-008  screen_20260827_174500_164.png   different email
OUTLOOK-009  screen_20260827_175331_081.png   long body
OUTLOOK-010  screen_20260827_175843_034.png   short body
```

OUTLOOK-011 (skipped in RND-002, no popup available) and OUTLOOK-012–015
(optional, not captured) are excluded, as instructed.

## Model

```text
provider = gemini
model    = gemini-3.6-flash
```

Read from `.env` at runtime — the same model already proven working in
RND-003. Not changed mid-run.

## Prompt Version

`screen_understanding_v2` (`rnd/prompts/screen_understanding_v2.txt`) —
a new file, `v1` untouched. Requests only: application, screen_state,
relevant_visible_controls, recommended_action, target, confidence,
reason. No coordinates, no free-form screen_description (unlike RND-003's
prompt and the readiness-stage prompt) — this stage is scoped to
understanding only.

## Evaluation Methodology

```
raw screenshot (screenshots/raw/ only, never screenshots/annotated/)
        ↓
Gemini (screen_understanding_v2, goal = "Reply to the currently opened email.")
        ↓
ScreenUnderstandingResponse (pydantic schema validation)
        ↓
rnd/metrics/evaluator.py — deterministic, keyword-based, NO AI
        ↓
compared against RND-002 manifest ground truth
        ↓
RND004CaseResult per case
```

The evaluator (`rnd/metrics/evaluator.py`) is entirely rule-based:
`normalize_state()` and `normalize_action()` map Gemini's free-text
answers onto RND-002's controlled vocabulary via fixed keyword rules a
human can read line-by-line; `control_detected()` does the same for
controls. No model grades another model's output anywhere in this
pipeline.

## Ground Truth Source

`test_cases/outlook/dataset_manifest.json` (RND-002), read-only — never
modified by this stage. Only 5 of the 10 cases have a formally annotated
target control (`targets` list): OUTLOOK-001 (`email_row`), OUTLOOK-003
(`Reply`), OUTLOOK-004 (`reply_editor`), OUTLOOK-005 (`Send`); OUTLOOK-002
additionally has a human-confirmed-but-unannotated Reply mention in its
free-text notes, added as `EXTRA_EXPECTED_CONTROLS` in the runner with
that provenance stated explicitly, not silently. The other 5 cases
(006–010) have no formal control ground truth — Relevant-control
detection is scored only on the 5 that do, not fabricated for the rest.
Similarly, `expected_action` is `null` for OUTLOOK-006/007/009/010 (the
manifest never recorded one) — Correct-next-action is scored only on the
6 cases that have one.

## Real Bugs Found and Fixed Before Trusting Any Number

Two real evaluator bugs were found by manually inspecting raw Gemini
responses against the first-pass scores, **not by a test** — this gap is
itself noted under Limitations. Both were fixed and the *entire* result
set was re-scored from the already-collected raw predictions
(`rnd/experiments/rnd004_reevaluate.py`) — **no new Gemini calls were
made**; only local grading was corrected.

1. **"compose" false-triggered `focus_reply_editor`.** Gemini routinely
   phrases the click_reply recommendation as *"Click Reply to compose a
   reply"* — "compose" here describes the click's purpose, not an
   already-open editor. The original heuristic treated "compose" (and
   "focus") as editor signals, which flipped two genuinely-correct
   answers (OUTLOOK-003, OUTLOOK-008) into false `WRONG_NEXT_ACTION`
   failures — moving Correct-next-action from 3/6 to a misleading 1/6.
2. **"Reply All" collapsed to the generic "reply" keyword.** For
   hallucination detection (checking a control claimed absent in ground
   truth), `_control_key_for("Reply All")` matched the same broad "reply"
   substring rule as plain "Reply", so any response merely mentioning
   "Reply" registered as having hallucinated "Reply All" — falsely
   flagging OUTLOOK-003.

Regression tests for both now exist in `tests/test_evaluator.py`
(`test_normalize_action_compose_describing_click_reply_purpose_is_not_focus_editor`,
`test_control_detected_reply_all_not_confused_with_plain_reply`).

## Per-Case Results

Source: `results/raw/rnd004_screen_understanding_results.json` (final,
post-fix scores).

| test_id | app_correct | state_correct | controls_correct | action_correct | confidence | latency_ms | attempts | failure_types |
|---|---|---|---|---|---|---|---|---|
| OUTLOOK-001 | True | True | True | **False** | 0.90 | 25425.7 | 1 | WRONG_NEXT_ACTION |
| OUTLOOK-002 | True | True | True | True | 0.98 | 22021.1 | 1 | - |
| OUTLOOK-003 | True | True | True | True | 1.00 | 40992.1 | 2 | - |
| OUTLOOK-004 | True | True | **False** | **False** | 0.95 | 35323.0 | 2 | CONTROL_MISSED, WRONG_NEXT_ACTION |
| OUTLOOK-005 | True | True | True | **False** | 0.95 | 7290.1 | 2 | WRONG_NEXT_ACTION |
| OUTLOOK-006 | True | **False** | N/A (no GT) | N/A (no GT) | 1.00 | 6898.5 | 1 | STATE_CLASSIFICATION_ERROR |
| OUTLOOK-007 | True | **False** | N/A (no GT) | N/A (no GT) | 1.00 | 33341.3 | 2 | STATE_CLASSIFICATION_ERROR |
| OUTLOOK-008 | True | True | N/A (no GT) | True | 0.95 | 48354.1 | 1 | - |
| OUTLOOK-009 | True | True | N/A (no GT) | N/A (no expected action) | 0.95 | 15689.8 | 1 | - |
| OUTLOOK-010 | True | True | N/A (no GT) | N/A (no expected action) | 0.95 | 22958.8 | 1 | - |

Raw sanitized responses (no keys/secrets): `results/raw/provider_responses/rnd004/<test_id>.json`.

## Aggregate Metrics

```text
Application recognition:       10/10 = 100%
Screen-state understanding:     8/10 = 80%
Relevant-control detection:     4/5  = 80%  (scored only on OUTLOOK-001/002/003/004/005 — the only cases with control ground truth)
Correct next action:            3/6  = 50%  (scored only on OUTLOOK-001/002/003/004/005/008 — the only cases with an expected action)
```

No single averaged "Vision accuracy" number was computed — these four
metrics measure different things and are reported separately, as
instructed.

## Confidence Observations

Gemini's `confidence` is **self-reported, not calibrated** — it is the
model's own stated certainty, not a measured probability, and must not be
read as one.

**High-confidence-wrong cases** (confidence ≥ 0.8 and at least one
comparison wrong) — these are the most important findings in this run:

| test_id | confidence | what was wrong |
|---|---|---|
| OUTLOOK-001 | 0.90 | Recommended clicking Reply on the **inbox** screen, before any email was selected/opened — jumped straight to the reply action instead of recognizing "select an existing email" as the actual next step. |
| OUTLOOK-004 | 0.95 | Correctly identified the state as reply-editor-open, but still recommended "click Reply" as the next action — the same action appropriate for OUTLOOK-002/003, not for a state where the reply flow has already started. Also did not name the compose/text-entry area itself as a relevant control (listed only the surrounding buttons: Reply, Reply All, Forward, Send, Discard). |
| OUTLOOK-005 | 0.95 | Correctly listed "Send button" among relevant controls, correctly described the state as showing an active reply-compose pane, but still recommended clicking "Reply icon" rather than Send — a genuine reasoning inconsistency between what it *saw* and what it *recommended*. |
| OUTLOOK-006 | 1.00 | Described the screen as an open email ("Viewing an open email titled...") where RND-002's ground truth says `inbox`. **This may not be a Gemini error** — see Important Findings below; RND-002's own notes for this case say *"Outlook maximized, inbox or email-open state"*, hedging on which it actually is. |
| OUTLOOK-007 | 1.00 | Same pattern as OUTLOOK-006, same ground-truth caveat applies. |

Three of five high-confidence-wrong cases (004, 005, and arguably 001)
show a consistent pattern: Gemini tends to recommend **"click Reply"**
even when the reply flow is visibly already in progress and a different
action (typing, or clicking Send) is what the goal actually calls for.
This is a real capability finding, not noise — it recurred across
independent cases at high self-reported confidence.

## Latency

```text
min:      6,898.5 ms
max:     48,354.1 ms
average: 25,829.5 ms
median:  24,192.3 ms  (p50)
```

All 10 real, individually measured — none guessed or estimated. With only
10 samples, no p95/p99 is reported (would not be statistically
meaningful).

## Usage / Cost

```text
total input tokens:  13,760
total output tokens:  1,516
total cost:           $0.016005
average cost/screenshot: $0.001601
```

Calculated from `config/model_pricing.json`'s verified `gemini-3.6-flash`
pricing ($0.75/1M input, $3.75/1M output) — real token counts from the
API, not estimated.

## Failures

**Provider-level:** 4 transient network failures during the live run
(`NetworkError: [Errno 10054] An existing connection was forcibly closed
by the remote host`), on OUTLOOK-003, 004, 005, and 007's first attempt —
all 4 succeeded on the automatic retry (attempt 2 of 2 allowed). Zero
timeouts. Zero cases exhausted both attempts. Recorded exactly as they
happened; no case's `attempt_count` was hidden or averaged away.

**Evaluation-level failure types observed**, by RND-004's own
classification:
```text
STATE_CLASSIFICATION_ERROR: OUTLOOK-006, OUTLOOK-007
WRONG_NEXT_ACTION:          OUTLOOK-001, OUTLOOK-004, OUTLOOK-005
CONTROL_MISSED:              OUTLOOK-004
```
No `APP_RECOGNITION_ERROR`, `CONTROL_HALLUCINATED` (after the fix — see
above), `INVALID_SCHEMA`, `TIMEOUT`, or `PROVIDER_ERROR` occurred in the
final scored results.

## Important Findings

1. **Reply-bias in action recommendation.** Across OUTLOOK-001, 004, and
   005, Gemini shows a repeated tendency to recommend clicking Reply even
   when context (an already-open editor, a visible Send button, no email
   yet selected) calls for a different action. This is the single most
   consistent, reportable capability gap from this run.
2. **RND-002 ground-truth ambiguity surfaced by RND-004.** OUTLOOK-006
   and OUTLOOK-007's own capture notes read *"inbox or email-open
   state"* — the human annotator who captured them was not fully certain
   which state they showed. Gemini's answer (`email_open`) may therefore
   be correct, and the two `STATE_CLASSIFICATION_ERROR` flags may
   partially reflect ground-truth uncertainty rather than a model error.
   **RND-002's manifest was deliberately not modified by this experiment**
   — RND-004 only consumes ground truth, it doesn't correct it — but this
   finding should inform whether OUTLOOK-006/007 need a clearer, less
   ambiguous re-annotation before being used as ground truth again.
3. **Local evaluator correctness matters as much as model correctness.**
   Two real grading bugs (documented above) were found only by manually
   reading raw responses against a suspicious aggregate number (83% wrong
   on next-action looked implausible next to 100% app recognition and
   80% state accuracy). Without that manual check, this report would have
   materially understated Gemini's real next-action performance.

## Limitations

- **10 samples per metric is a small sample** — percentages here (e.g.
  "50%" for 6 scorable next-action cases) represent 3/6, not a
  statistically robust rate. No claim of significance is made.
- **The evaluator's bug-finding process was manual, not test-driven.**
  Both bugs were caught by inspecting raw JSON responses against
  suspicious aggregate numbers, not by a pre-existing test catching them
  before the live run. Regression tests exist now, after the fact — this
  reduces but does not eliminate the risk of a similar undetected bug
  elsewhere in the evaluator.
- **Keyword-based state/action normalization is inherently approximate.**
  `normalize_state()`/`normalize_action()` are deterministic and
  auditable, but not a semantic understanding of the text — edge phrasing
  can still slip past the current keyword rules undetected.
- **Relevant-control detection and correct-next-action are scored on
  incomplete ground truth** (5/10 and 6/10 cases respectively) by
  necessity — RND-002 did not formally annotate a target control or an
  expected action for every case. This is stated plainly in every
  aggregate line above, not hidden in a footnote.
- **Single provider, single prompt version, single run.** No comparison
  to OpenAI/Anthropic (both still blocked on billing — see
  `docs/r_and_d/05A_Multi_Provider_Readiness.md`) and no repeated-run
  variance/consistency data exists yet.

## Conclusion

Gemini reliably recognizes Microsoft Outlook (100%) and mostly identifies
the correct screen state (80%, with the two misses plausibly explained by
ground-truth ambiguity rather than model error). Its weakest, most
consistent gap is next-action reasoning (50% on the 6 scorable cases),
specifically a repeated bias toward recommending "click Reply" regardless
of whether that's still the correct next step given the visible state.
This is real, useful signal about Gemini's screen-understanding
capability for this Outlook automation task — obtained only after finding
and fixing two real bugs in the local grading logic, which is itself a
reminder that this project's own tooling needs the same scrutiny as the
model being evaluated.

## Next Step

Not RND-005 automatically, per instruction. When ready:
**RND-005 — UI Grounding Accuracy**, evaluating whether Gemini's
predicted pixel coordinates actually land inside the RND-002 ground-truth
bounding boxes (Reply, reply_editor, Send, email_row) — still no
clicking. OpenAI and Anthropic remain available to add to this same
benchmark once their billing is resolved (`docs/r_and_d/05A_Multi_Provider_Readiness.md`).
