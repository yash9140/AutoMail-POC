# 13 — Find + Open Email Playbook (RND-009C)

## Objective

Chain a single live playbook run from Windows Search through opening
one specific, playbook-approved email:

```
READY -> LAUNCHING_OUTLOOK -> OUTLOOK_VERIFIED -> FINDING_EMAIL -> EMAIL_OPENED -> STOP
```

Scope ends strictly at `EMAIL_OPENED`. No Reply, no reply generation,
no typing, no Send anywhere in this stage.

## Why The Test Starts From Windows Search

Continuing the chain from a cold start (not from an already-open
Outlook) is what makes this a genuine end-to-end test of the playbook
architecture rather than a re-test of RND-009B in isolation — it
proves the state machine, abort handling, and bounded-approval model
all compose correctly across a launch phase built in one stage and a
find/open phase built in a later one, with no special-casing between
them.

## Bounded Approval

Same design as RND-009B's (redesigned) approval model, extended to
cover the longer chain: **one** upfront yes/no, asked before anything
time-sensitive starts, covers press-Windows-key through email-open
verification with no pause in between. The exact question asked:

> "Approve RND-009C? This permits the application to use Windows
> Search to launch Outlook, upload necessary controlled screenshots to
> Gemini, visually locate the approved test email using its subject
> and sender, open that email once, verify the correct email is open,
> and then stop."

This approval explicitly does **not** permit Reply, typing, or Send —
`FindOpenEmailSteps` and `FindOpenEmailWorker` contain no code path for
any of them (structurally proven, see Tests).

## Target Email Definition

Playbook-decided, hardcoded constants
(`app/playbook/find_open_email_steps.py`):

```python
TARGET_EMAIL_SUBJECT = "Mail for project"
TARGET_EMAIL_SENDER = "Yash"
```

The same real, repeatedly human-confirmed non-sensitive test email
reused since RND-002 — `TARGET_EMAIL_SENDER` matches the exact text
Outlook's own inbox row displays (confirmed against the RND-009B
readiness-validation screenshot), not a fabricated value. **Vision's
role is strictly to report whether what it sees matches these
playbook-supplied values — it never decides which email is
interesting** (structurally proven: the prompt supplies
`target_subject`/`target_sender` as format parameters, and Vision is
explicitly told "you are given the exact email to look for — you do
not decide which email is interesting or relevant").

## Outlook Launch (Phases 1-2, Reused Verbatim)

`FindOpenEmailSteps` composes `OutlookLaunchSteps` (RND-009B) rather
than duplicating its logic — `run_launch_and_readiness()` calls the
exact same methods in the exact same order (press key, type, capture,
ground, click, poll, verify readiness), then folds the resulting
`RND009BResult` fields into this stage's own `RND009CResult` (flat,
self-contained, matching this project's per-stage-own-model
convention — not a nested sub-object). One implementation, reused by
composition, exercised for real again in this run rather than assumed
to still work.

## Readiness Verification

Identical hardened logic from RND-009B's readiness-hardening pass: up
to 2 attempts, 6.0s initial wait then 3.0s retry wait, splash screen
never counted as ready. `FindOpenEmailSteps.ground_target_email()` has
its own defense-in-depth guard — it raises if called before
`ready_for_interaction` is `True`, structurally proving `FIND_EMAIL`
cannot begin before Outlook is confirmed ready, independent of
whatever the calling worker/script does.

## Email Grounding

A dedicated prompt (`rnd/prompts/email_target_grounding_v1.txt`)
receives `target_subject`/`target_sender` as format parameters and
asks Gemini only: is a row matching **both** visible, what's its exact
displayed subject/sender text, and one point inside it — never "what
email should I open." Schema: `EmailGroundingResponse` (`target_visible`,
`matched_subject`, `matched_sender`, `x`, `y`, `confidence`, `reason`).

## Subject/Sender Matching

Case-insensitive substring match (`_text_matches()`), tolerant of
minor UI truncation/formatting differences, never a fuzzy match on
genuinely different text. **Both** subject and sender must match — a
match on only one is treated as `TARGET_EMAIL_MISMATCH`, no click.

## Click Safety

Same double-foreground-check pattern used since RND-006A: Outlook
foreground confirmed before mouse movement, confirmed **again**
immediately before the click; either check failing means no
movement/no click and `OUTLOOK_FOREGROUND_LOST`. Exactly one
`pyautogui.click()` call site in this module (the email row) —
structurally proven; the Outlook search-result click lives in
`outlook_launch_steps.py`, reused via composition, not duplicated here.

## Email-Open Verification

`rnd/prompts/email_open_verification_v1.txt` + `EmailOpenVerificationResponse`
— explicitly told not to assume success just because *a* reading pane
is visible; must report the actual detected subject/sender and whether
they match the target. Up to 2 attempts (1.5s initial wait, 1.0s retry
wait, mirroring RND-009B's post-send verification pattern), **never a
second email-row click** regardless of how verification goes.
`EMAIL_OPENED` requires all four: `email_open`, `subject_match`,
`sender_match`, `body_visible` — a generic "some email is open" is
explicitly insufficient.

## Metrics

Every real provider call accumulates into the running totals (the
RND-007B/RND-008/RND-009B "no hidden helper calls" discipline
continued) **and** into one of four separate step-level `StepMetrics`
records — `launch_outlook_metrics`, `verify_outlook_ready_metrics`,
`find_email_metrics`, `verify_email_opened_metrics` — so latency/cost
can be attributed to which part of the chain, never collapsed into one
grand total alone.

## Failures

Reuses RND-009B's classifications for the launch phase, plus 7 new
ones for this stage's own phases, never collapsed into a generic FAIL:
`TARGET_EMAIL_NOT_VISIBLE`, `TARGET_EMAIL_MISMATCH`,
`EMAIL_GROUNDING_INVALID`, `EMAIL_GROUNDING_OUT_OF_BOUNDS`,
`OUTLOOK_FOREGROUND_LOST`, `EMAIL_OPEN_VERIFICATION_FAILED`,
`WRONG_EMAIL_OPENED` (`app/playbook/failure_reasons.py`).

Per instruction, the target-not-visible case does **not** trigger
autonomous scrolling/searching in this first run — it stops and
records `TARGET_EMAIL_NOT_VISIBLE`, deliberately conservative scope for
this first chained live test.

## Latency & Cost

| Step | Vision calls | Input tokens | Output tokens | Cost | Latency |
|---|---|---|---|---|---|
| LAUNCH_OUTLOOK | 1 | 1,498 | 94 | $0.001476 | 17,034.95 ms |
| VERIFY_OUTLOOK_READY | 1 | 1,577 | 110 | $0.001595 | 21,523.60 ms |
| FIND_EMAIL | 1 | 1,522 | 97 | $0.001505 | 6,992.54 ms |
| VERIFY_EMAIL_OPENED | 1 | 1,471 | 118 | $0.001546 | 14,533.80 ms |
| **Total** | **4** | **6,068** | **419** | **$0.006122** | **60,084.89 ms** |

Total end-to-end elapsed (Windows key press to the final verification
call completing): **~74.2s** — derived from raw timestamps/latencies
for this report; see Limitations for why the dedicated
`total_elapsed_ms` field itself is empty.

## Pre-RND-009D Hardening — Total E2E Elapsed Time

After RND-009C was reviewed and approved, the metrics gap noted below
(originally listed as a limitation) was fixed: `session_start`,
`session_end`, and `total_elapsed_ms` are now populated automatically
on every run, no manual derivation required.

**Implementation** (`FindOpenEmailSteps.start_session()` /
`finalize_session()`, `app/playbook/find_open_email_steps.py`):

- `start_session()` — called exactly once, the moment the bounded run
  actually begins (after the upfront approval, before pressing the
  Windows key) — never in `__init__`, so merely constructing a steps
  object (e.g. in a test) never starts a timer. Records
  `session_start` (human-readable wall-clock timestamp) and an
  internal `time.monotonic()` reading (immune to system clock
  adjustments, used for the actual elapsed computation).
- `finalize_session()` — called exactly once by the **caller**
  (`FindOpenEmailWorker`/`scripts/rnd009c_live_run.py`), whenever the
  run reaches **any** terminal outcome — PASS, FAIL, ABORTED, or ERROR
  — not by any single phase method, since a terminal outcome can be
  reached from several different call sites. Records `session_end` and
  `total_elapsed_ms = (finalize time) - (start_session time)`.

**Kept strictly separate, per instruction**: `total_elapsed_ms` is
**total E2E wall-clock time** — waits, Windows UI interaction,
provider calls, mouse/keyboard execution, stabilization delays,
verification time, everything — while `total_latency_ms` remains the
sum of Vision call durations only, a subset of the wall-clock total.
Neither is derived from the other.

**Persisted in all four places requested:**
- Raw results / summary report: `session_start`/`session_end`/
  `total_elapsed_ms` fields on `RND009CResult`, printed in both the
  live-run script's console output and `_build_report()`'s new
  "Timing" section.
- Session metrics: `SessionMetrics` gained a `total_elapsed_ms` field;
  `AutomationController` now also listens to the worker's
  `metrics_update` signal (previously wired but never connected — a
  second small gap closed in the same pass) via a new
  `_on_metrics_update()` handler, so elapsed time reaches session
  metrics on every outcome, not just success.
- Final UI result data: `ResultPage.update_from_metrics()` now prefers
  the precise `metrics.total_elapsed_ms` over diffing `start_time`/
  `end_time` strings, falling back to the old behavior only when the
  worker-measured value isn't available.

**Reset behavior**: `AutomationController.start_automation()` already
constructs a fresh `SessionMetrics()` on every Start click, so
`total_elapsed_ms` naturally starts at `None` each run — no stale
previous-session value can leak forward. At the steps level,
`start_session()` overwrites `_session_start_monotonic` unconditionally,
so a second call always resets the timer (proven by
`test_second_start_session_resets_timer_no_stale_previous_value`).

**Tests: 6 new, 279/279 passing project-wide** — elapsed populated on
PASS (worker-level), on FAIL (worker-level, via `metrics_update`), on
ABORTED (worker-level, abort requested before the run starts), plus
three steps-level unit tests: elapsed computed correctly from
controlled `time.monotonic()` values, `finalize_session()` without a
prior `start_session()` leaves `total_elapsed_ms` as `None` (never
fabricates a reading), and a second `start_session()` call resets the
timer rather than reusing the first run's start.

**Historical RND-009C values not retroactively modified** — the
originally-finalized `results/raw/rnd009c_find_open_email_results.json`
still has `total_elapsed_ms: null`; the ~74.2s figure recorded in the
Live Result section above remains explicitly labeled as manually
derived from timestamps/latencies, not backfilled into the historical
record.

## Limitations

- ~~`session_start`/`session_end`/`total_elapsed_ms` fields exist on
  `RND009CResult` but nothing sets them~~ — **fixed**, see Pre-RND-009D
  Hardening above.
- **Single live run, one target email, one attempt.** Establishes the
  full chain works once under real conditions; does not establish
  behavior across a target email that has scrolled out of view (the
  explicitly-out-of-scope `TARGET_EMAIL_NOT_VISIBLE` path), multiple
  visually-similar rows, or a second consecutive chained run in the
  same session.
- **The `WRONG_EMAIL_OPENED` vs generic `EMAIL_OPEN_VERIFICATION_FAILED`
  split and the retry-without-reclick path were only exercised live on
  their success branch** — the failure/mismatch branches are proven
  only by mocked tests (`test_wrong_email_opened_fails_after_max_attempts`,
  `test_verification_retry_does_not_reclick`), not live, since this
  run's grounding and verification were both correct on the first
  attempt.
- **No Outlook Search or scrolling exists yet** for the case where the
  target email isn't in the current view — explicitly deferred per
  instruction, not an oversight.

## Live Result

**Target:** subject `"Mail for project"`, sender `"Yash"`.

**Outlook launch:** grounded raw (69, 281) → converted (132, 303);
single click; foreground `'Outlook'` detected in 2,005.5 ms.

**Readiness:** PASS on attempt 1 — `ready_for_interaction: true`,
`splash_screen_visible: false`, `detected_state: "Outlook main window
with inbox and email list visible"`, confidence 1.0.

**Email grounding:** `target_visible: true`, `matched_subject: "Mail
for project"`, `matched_sender: "Yash"` — both correct — raw
(420, 965) → converted (806, 1042), confidence 0.95.

**Email click:** executed once at 2026-09-01T10:44:26.99.

**Email-open verification (attempt 1/2, PASS on first attempt):**
`email_open: true`, `subject_match: true`, `sender_match: true`,
`body_visible: true`, confidence 1.0 — independently confirmed by
viewing the actual screenshot: the "Mail for project" thread from Yash
correctly selected and open, reading pane showing "Hii Yash, Hope you
are fine and doing well." and the earlier RND-008 reply beneath it.

**Final playbook state: `EMAIL_OPENED`. Overall result: PASS.**

**Totals:** 4 vision calls, 6,068 input / 419 output tokens,
$0.006122, 60,084.89 ms combined Vision latency, ~74.2s total
end-to-end elapsed. 2 mouse clicks (Outlook result + email row),
2 keyboard actions (Windows key + typed "Outlook"), 0 retries,
1 human intervention (the single bounded approval), 0 safety aborts.
`send_click_count: 0` throughout.

## Next Stage

**RND-009D — Reply + Draft**, building contextual reply generation
(reusing RND-007B's already-proven pipeline) onto this chain, still
stopping well before Send. **Not started automatically** — requires
its own explicit review and approval.
