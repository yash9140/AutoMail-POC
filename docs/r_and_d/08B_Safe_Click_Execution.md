# 08B — Safe Click Execution (RND-006B)

## Objective

Determine whether the same validated Vision-grounded coordinates (RND-006A
proved MOVE is safe) can safely perform **one controlled click** each on
Outlook and whether the expected UI state actually occurs afterward.

This document now covers two attempts for `email_row`:
**Attempt 1** (below, preserved exactly as originally recorded — found a
verification false negative) and **Attempt 2** (`docs/08B` §"Attempt 2",
added after Attempt 1 to test whether that false negative was caused by
verifying too soon after the click). Reply and reply_editor remain
untested in both attempts — stopping here for review before proposing
Reply, per the explicit "first test only" instruction, now repeated for
Attempt 2.

---

# Attempt 1 (original, unmodified)

Everything below this line, through "Next Step," is preserved exactly as
originally written and reviewed. Nothing was edited, deleted, or
reinterpreted after the fact — see "Attempt 2" further down for the
follow-up investigation and its own, separate conclusions.

## Safety Gates

Every click follows this exact sequence, with no step skipped:

```
fresh screenshot
  → foreground check (before capture)
  → Gemini grounding (fresh call, never reused from RND-006A)
  → normalized coordinate conversion (rnd/metrics/coordinate_calibration.py)
  → bounds validation
  → human shown: provider/model/target/raw coord/converted coord/foreground/expected state
  → explicit human approval ("Proceed with SAFE CLICK? yes/no")
  → move cursor
  → foreground check AGAIN (immediately before click — separate check from the pre-move one)
  → ONE click
  → post-click screenshot (fresh capture)
  → separate Gemini verification call against that new screenshot
  → human reports result — STOP, no automatic continuation
```

Split across three separate script invocations
(`rnd/experiments/rnd006b_safe_click_execution.py --execute` /
`--click` / `--confirm`) so each phase can be reviewed before the next
proceeds — identical philosophy to RND-006A, extended with the click and
verification steps.

## Click Allowlist

`rnd/models/click_execution.py::CLICK_ALLOWED_TARGETS` — exactly
`email_row`, `reply`, `reply_editor`. `send`, `reply_all`, `forward`,
`delete`, `archive`, and any unrecognized value are rejected two ways:
structurally by `argparse`'s `choices=` (a value like `send` cannot even
be passed on the command line — confirmed: `--target send` fails with
"invalid choice" before any Python code in the script body runs), and
independently by `is_click_target_allowed()` (unit-tested). A third
safety net exists inside `--execute`: if Gemini's own returned `target`
field says "send" or "reply all" regardless of what was asked for, the
result is marked `ABORTED` before any coordinate is even converted.

## Coordinate Conversion

Identical rule to RND-006A, same function, not duplicated:
`normalize_1000_to_pixels(raw_x, raw_y, screen_width, screen_height)`
from `rnd/metrics/coordinate_calibration.py`. Screen dimensions read
fresh from `pyautogui.size()`.

## Per-Test Flow — email_row

### Grounding result (`--execute`)

```text
Foreground window (at capture):  'Mail - Yash Dhanraj - Outlook'
Screenshot (pre-click):          screen_20260828_155046_818.png
Raw Gemini coordinate:           (279.0, 327.0)
Converted pixel coordinate:      (536, 353)
Screen resolution:               1920 x 1080
Coordinate valid:                True
Confidence:                      0.95
Grounding latency:               50,667.6 ms
Expected state after click:      email_open
```

This raw prediction, `(279, 327)`, is again nearly identical to
RND-005's `(280, 325)` and RND-006A's `(280, 327)` for the same target —
a fifth independent reproduction of Gemini's stable spatial estimate for
this control.

### Click result (`--click`)

```text
Foreground before move:   'Mail - Yash Dhanraj - Outlook'  -> OK
Movement executed:        True
Foreground before click:  'Mail - Yash Dhanraj - Outlook'  -> OK (re-checked, separate call)
Click executed:           True (single click only)
Post-click screenshot:    screen_20260828_155211_904.png
```

Both foreground checks passed cleanly on this run — no abort was needed
for `email_row` in RND-006B (unlike RND-006A's `email_row` move, which
did hit a real abort).

### Post-click verification (AI, separate call)

```json
{
  "verified": false,
  "detected_state": "email_selected_in_list_but_reading_pane_not_showing_content",
  "confidence": 0.95
}
```

**This is the most important finding of this test.** The AI verification
call — a separate Gemini request against the post-click screenshot,
distinct from the grounding call — reported the click had *not* achieved
the expected `email_open` state, at high confidence (0.95).

### Human verification (authoritative)

The human reviewer visually confirmed the email **was** actually open
with content visible in the reading pane — contradicting the AI
verification's `verified: false`. Per this project's design, **human
confirmation is authoritative and is never overridden by the AI
verification result** — `human_verification: true` is what determined
the final `PASS`, and the AI's `verification_result: false` is preserved
in the result record for traceability, not discarded or reconciled away.

```text
Final result: PASS
```

## Aborted Actions

None for `email_row` in this stage — both foreground checks passed on the
first attempt. (RND-006A did have a real abort for this same target
during the move-only phase, for comparison — see `docs/r_and_d/08_Safe_Mouse_Execution.md`.)

## Human Approvals

Two explicit approvals were obtained and required, matching the safety
gate design:
1. Before `--execute` (sending the pre-click screenshot to Gemini)
2. Before `--click` (shown: provider, model, target, raw coordinate,
   converted coordinate, foreground window, expected post-click state)

A third human input — the final visual confirmation — was required after
the click+verification completed, and is what actually decided PASS/FAIL
(see above).

## Latency / Cost

```text
Grounding latency:     50,667.6 ms
Verification latency:   9,687.2 ms
Vision calls:           2 (1 grounding + 1 verification)
Input tokens:           2,591
Output tokens:            161
Estimated cost:        $0.002547
```

Single data point — not averaged yet.

## Failures

**A real, important failure mode was found: AI post-click state
verification produced a false negative.** The click objectively
succeeded (human-confirmed), but the separate Gemini verification call
confidently (0.95) reported it had not. This is recorded honestly as a
finding, not smoothed over — it directly informs how much weight
automated verification should be given in later stages (RND-007+): **not
enough to replace human confirmation, at least not yet, based on this
one data point.**

## Limitations

- **Single sample, single target so far.** `reply` and `reply_editor`
  remain untested — this document will be updated as each completes.
- **One false-negative AI verification result** is not enough to
  characterize how often this happens — could be a one-off timing issue
  (screenshot captured before the reading pane finished rendering) or a
  more systematic verification weakness. Not established either way from
  N=1.
- **Human confirmation, while authoritative here, is still the
  bottleneck** for any less-supervised future execution — this design
  deliberately keeps it that way for now.
- **`reply_editor`'s expected post-click state (focus) is explicitly
  documented as not reliably visually verifiable** (`rnd/models/click_execution.py::EXPECTED_STATE_AFTER_CLICK`
  notes this) — when that target is tested, human confirmation will be
  required and AI verification treated as supplementary at best, per the
  original instruction not to fabricate focus verification.

## Conclusion

The full safe-click pipeline — fresh grounding, coordinate conversion,
dual foreground checks (before move and immediately before click), a
single controlled click, post-click screenshot, separate AI verification,
and mandatory human confirmation — worked correctly for `email_row`,
including surfacing a genuine AI-verification false negative that human
judgment correctly caught and overrode. The click itself was safe and
accurate; the AI verification step needs more data before it could be
trusted unsupervised.

## Next Step (as originally written; superseded by Attempt 2 below)

Per instruction, do not propose or run additional targets automatically.
`reply` is the proposed next safe-click test, to be run only after this
result is reviewed and explicit approval is given to continue.

---

# Attempt 2 — Stabilized Verification

## Why This Was Repeated

Attempt 1's `email_row` test found a real discrepancy: the click
objectively succeeded (human-confirmed), but the AI verification call
reported `verified: false` at 0.95 confidence, describing the state as
`"email_selected_in_list_but_reading_pane_not_showing_content"`. Two
explanations were possible and neither was assumed:

- **(A)** Genuine Vision verification failure — Gemini simply
  misjudged a correctly-rendered screen.
- **(B)** The verification screenshot was captured before Outlook
  finished rendering the reading pane after the click — a timing
  artifact, not a model failure.

Attempt 2 exists specifically to distinguish these two, by adding a
stabilization delay before verification and measuring whether that
changes the outcome — without weakening any existing safety gate and
without ever clicking twice.

## Safety Gates (unchanged from Attempt 1)

All of the following were preserved exactly: fresh screenshot before
grounding, live Gemini grounding call, `normalize_1000_to_pixels`
conversion, bounds validation, foreground check before move, foreground
check again immediately before click, single click only,
`pyautogui.FAILSAFE = True` (asserted before every action), Send/unsafe
targets structurally blocked. Nothing about how the click itself happens
was changed.

## Stabilization Logic (the only actual change)

```
click
  → wait 1.5s
  → capture verification screenshot #1
  → Gemini verification (state_verification_v2)
  → if verified: STOP here (max 1 verification call)
  → if NOT verified:
      → wait an additional 1.0s (total 2.5s since click)
      → capture verification screenshot #2
      → Gemini verification again
      → STOP (maximum 2 verification attempts — never more)
```

**The retry is always wait → new screenshot → new verification call. It
is never another click.** Enforced in code (the click happens once,
before the verification function is ever called) and confirmed by a
mocked unit test that asserts `pyautogui.click` is never invoked during
`run_stabilized_verification`.

## New Verification Prompt

`rnd/prompts/state_verification_v2.txt` (v1 preserved, not overwritten).
Changes from v1: adds a `visual_evidence` field to the required response
(what specifically supports the verified/not-verified judgment), and
tightens the expected-state wording to describe only the visual outcome,
never a next action — e.g. for `email_row`:
*"An email is open and its message content is visible in the reading
pane."* (`rnd/models/click_execution.py::EXPECTED_VERIFICATION_STATEMENT_V2`,
kept separate from Attempt 1's `EXPECTED_STATE_AFTER_CLICK` so Attempt
1's exact prompt input remains unchanged for traceability.)

## Authoritative Result Model

Click correctness and verification-subsystem correctness are recorded as
two independent fields, never collapsed:

- `click_result` — `PASS`/`FAIL`, decided by human confirmation alone
  (`rnd/metrics/click_verification.py::compute_click_result`)
- `verification_classification` — one of `AI_VERIFIED_FIRST_ATTEMPT`,
  `UI_STABILIZATION_DELAY_REQUIRED`, `VISION_VERIFICATION_FALSE_NEGATIVE`,
  or `CLICK_FAILED`, decided by
  `classify_verification_outcome(attempt1_verified, attempt2_verified, human_verified)`
  — a pure, unit-tested function with no AI involved in the classification
  itself.

## email_row — Attempt 2 Result

### Grounding

```text
Foreground (at capture):     'Mail - Yash Dhanraj - Outlook'
Raw Gemini coordinate:       (280.0, 325.0)
Converted pixel coordinate:  (538, 351)
Coordinate valid:            True
Confidence:                  0.9
```

Yet another near-identical reproduction of this target's coordinate
across every independent call so far (RND-005: (280,325); RND-006A:
(280,327); RND-006B Attempt 1: (279,327); RND-006B Attempt 2: (280,325)).

### Click

```text
Foreground before move:   OK
Movement executed:        True
Foreground before click:  OK (re-checked, separate call)
Click executed:           True (single click)
Click timestamp:          2026-08-28T17:14:07.801066
```

### Verification Attempt 1 (1.5s stabilization delay)

```json
{
  "verified": true,
  "detected_state": "The selected email's header and full body text are displayed in the reading pane on the right side of the screen.",
  "confidence": 1.0,
  "visual_evidence": "The email with the subject 'Important Update Regarding Current Work and Next Steps' ... is highlighted in the message list and its content ... is visible in the reading pane.",
  "reason": "The expected action succeeded as the clicked email is highlighted and its complete content is rendered in the reading pane."
}
```

**Verified on the first attempt, at full confidence (1.0).** No second
verification attempt was needed —
`time_since_click_ms: 2024.1` (screenshot taken ~2.0 seconds after the
click, per the 1.5s delay plus screenshot/processing overhead).

### Human Verification

Confirmed PASS — email genuinely open with content visible.

### Classification

```text
click_result:                 PASS
verification_classification:  AI_VERIFIED_FIRST_ATTEMPT
```

## Attempt 1 vs Attempt 2 Comparison (email_row)

| | Attempt 1 (no stabilization delay) | Attempt 2 (1.5s delay) |
|---|---|---|
| Click result | PASS (human) | PASS (human) |
| AI verification attempt 1 | `verified: false`, confidence 0.95 | `verified: true`, confidence 1.0 |
| AI verification attempt 2 | not applicable (v1 had no retry logic) | not needed |
| Detected state | "email selected in list but reading pane not showing content" | "header and full body text are displayed in the reading pane" |
| Classification | (not computed in Attempt 1 — that framework didn't exist yet) | `AI_VERIFIED_FIRST_ATTEMPT` |

**Adding stabilization changed the outcome.** With no delay, verification
failed; with a 1.5s delay, verification passed cleanly at full
confidence, describing the exact same underlying state (email open,
content in reading pane) that the human had already confirmed in both
attempts. This is a single before/after comparison on one target, not
proof across all conditions, but it is a real, direct, same-target,
same-day comparison — not a hypothetical.

## Answering the Two Separate Questions

1. **"How much post-click stabilization time does Outlook need before
   Vision AI can reliably verify the state change?"** For `email_row`
   specifically: 1.5 seconds was sufficient in this trial (verified on
   the first attempt, no second delay needed). This is one data point,
   not a general answer for every target/state.
2. **"Is Gemini's verification itself reliable once the UI is stable?"**
   Not yet separately established — this trial's `AI_VERIFIED_FIRST_ATTEMPT`
   classification means the stabilization question was answered
   affirmatively for this case, but it does not by itself prove Gemini's
   verification is *generally* reliable — a `VISION_VERIFICATION_FALSE_NEGATIVE`
   result (both attempts failing while a human passes) has not been
   observed yet in either attempt, so the reliability question remains
   open, not confirmed positive.

## Latency / Cost — Attempt 2, email_row

```text
Grounding latency:          21,795.6 ms
Verification attempt 1:      8,436.8 ms (only attempt needed)
Vision calls:                2 (1 grounding + 1 verification)
Total input tokens:          2,658
Total output tokens:           209
Total estimated cost:       $0.002777
```

## Failures

None for this test — no abort, no schema-validation failure, no blocked
target returned, verification passed on the first attempt.

## Limitations

- **Single sample.** One `AI_VERIFIED_FIRST_ATTEMPT` result does not
  establish that 1.5s is always sufficient, nor that Gemini verification
  is reliable in general — it establishes that *for this one click, on
  this one screenshot, on this one day*, the delay fixed the discrepancy
  seen in Attempt 1.
- **`VISION_VERIFICATION_FALSE_NEGATIVE` (both AI attempts failing while
  human passes) has still never been observed** — Attempt 1's false
  negative pre-dates the stabilization logic and the 2-attempt retry
  framework, so it cannot be directly reclassified into one of the new
  categories; it remains documented as-is under Attempt 1, not
  retroactively relabeled.
- **`reply` and `reply_editor` remain completely untested under either
  attempt.** This document will be updated as each completes, per the
  "first test only" instruction — repeated again for Attempt 2.

## Conclusion

For `email_row`, adding a 1.5-second post-click stabilization delay
before verification resolved the exact discrepancy found in Attempt 1 —
verification passed cleanly on the first attempt where it had previously
failed. This is meaningful, direct evidence favoring explanation (B)
(render-timing artifact) over explanation (A) (genuine Vision failure)
**for this specific case** — it does not yet prove verification is
reliable in general, since no case has forced a genuine
`VISION_VERIFICATION_FALSE_NEGATIVE` classification to test that
question directly.

## A Second Real Bug Found and Fixed Mid-Session — Reply Attempt 2

While running the `reply` target, verification attempt 1 returned
`verified: None` (not `False`) with `schema_valid: false` and an explicit
`error`: `"NetworkError: Network failure calling Gemini: [WinError 10054]
An existing connection was forcibly closed by the remote host"` — a
transient network failure, not a genuine model verdict.

The original `classify_verification_outcome()` did not distinguish "the
model said no" from "the call never produced a verdict at all." Both
collapsed into the same `UI_STABILIZATION_DELAY_REQUIRED` classification
whenever attempt 2 subsequently passed — which would have **misreported
a network retry as evidence for the stabilization-delay hypothesis**,
diluting the real signal this experiment exists to measure.

Fixed before finalizing this result: `classify_verification_outcome()`
now takes an explicit `attempt1_had_error` flag and returns one of two
new, distinct categories — `ATTEMPT1_TECHNICAL_ERROR_ATTEMPT2_PASSED` or
`ATTEMPT1_TECHNICAL_ERROR_ATTEMPT2_ALSO_FAILED_OR_ERRORED` — instead of
silently reusing `UI_STABILIZATION_DELAY_REQUIRED`. Three new regression
tests cover this (`tests/test_click_verification.py`), and the full suite
(150/150) was re-run before recording the `reply` result below.

## reply — Attempt 2 Result

### Grounding

```text
Raw Gemini coordinate:       (477.0, 584.0)
Converted pixel coordinate:  (916, 631)
Coordinate valid:            True
Confidence:                  0.95
```

Consistent with every prior independent Reply prediction across RND-003,
RND-005, RND-005B, RND-006A, and RND-006B Attempt 1 (all cluster near
x≈477-478, y≈584-631).

### Click

Both foreground checks (before move, immediately before click) passed
cleanly. Single click executed.

### Verification Attempt 1 — technical error, not a verdict

```text
error: NetworkError: Network failure calling Gemini:
       [WinError 10054] An existing connection was forcibly closed by the remote host
schema_valid: false
verified: None
```

### Verification Attempt 2 (additional 1.0s wait, fresh screenshot)

```json
{
  "verified": true,
  "detected_state": "The reply editor pane is open below the received email message, displaying 'From', 'To' fields, and a 'Send' button.",
  "confidence": 1.0,
  "visual_evidence": "An inline reply composition area is visible in the right reading pane with 'From: yash.d@innomick.com', 'To: Yash', an editor input area, and Send/Discard buttons.",
  "reason": "The reply composer is visibly open and active, matching the expected resulting state."
}
```

### Human Verification

Confirmed PASS — reply editor genuinely open.

### Classification

```text
click_result:                 PASS
verification_classification:  ATTEMPT1_TECHNICAL_ERROR_ATTEMPT2_PASSED
```

**Correctly not counted as evidence for or against the stabilization-delay
hypothesis** — this case doesn't speak to UI render timing at all; it
speaks to network reliability, which is a separate concern already
handled by this project's standard retry-and-record-honestly pattern.

## Latency / Cost — reply, Attempt 2

```text
Grounding latency:          13,551.0 ms
Verification attempt 1:      (errored — no latency/tokens/cost recorded, none fabricated)
Verification attempt 2:      8,849.9 ms
Vision calls:                3 (1 grounding + 1 failed verification + 1 successful verification)
Total input tokens:          2,634
Total output tokens:           203
Total estimated cost:       $0.002736
```

## Updated Comparison Table

| Target | Attempt 1 result | Attempt 2 result | Attempt 2 classification |
|---|---|---|---|
| email_row | AI verified=false (0.95) | AI verified=true (1.0), 1st try | `AI_VERIFIED_FIRST_ATTEMPT` |
| reply | *(untested)* | AI attempt 1 network error; attempt 2 verified=true (1.0) | `ATTEMPT1_TECHNICAL_ERROR_ATTEMPT2_PASSED` |

`VISION_VERIFICATION_FALSE_NEGATIVE` (both AI attempts genuinely
disagreeing with a human PASS) has still never been observed under the
Attempt 2 framework — one clean first-try pass, one network-recovered
pass. The original hypothesis-testing question (does stabilization delay
fix genuine false negatives) remains supported by the `email_row` result
alone; `reply` neither strengthens nor weakens it, since its retry was
network-driven, not render-timing-driven.

## reply_editor — Attempt 2 Result

### Grounding

```text
Raw Gemini coordinate:       (650.0, 750.0)
Converted pixel coordinate:  (1248, 810)
Coordinate valid:            True
Confidence:                  0.95
```

### Click

Both foreground checks passed cleanly. Single click executed.

### Verification Attempt 1 (1.5s delay) — passed, no second attempt needed

```json
{
  "verified": true,
  "detected_state": "The reply compose editor is open and focused, with a visible text cursor in the message body area.",
  "confidence": 1.0,
  "visual_evidence": "A vertical text cursor ('|') is visible in the top-left of the reply body text box below 'To: Yash', and the message formatting ribbon at the top of Outlook is active.",
  "reason": "The presence of the text cursor inside the reply body area confirms that the reply text-entry editor has focus and is ready for typing."
}
```

Notably, Gemini's verification did identify a **visible text cursor** as
concrete evidence of focus — the specific kind of visual signal the
original RND-006B instructions anticipated might not be reliably
determinable, but which this trial's screenshot happened to make visible.

### Human Verification

Confirmed PASS, with particular attention paid per the original
instruction that focus can be hard to verify reliably — the human
reviewer's confirmation remains authoritative regardless of what the AI
verification found.

### Classification

```text
click_result:                 PASS
verification_classification:  AI_VERIFIED_FIRST_ATTEMPT
```

## Latency / Cost — reply_editor, Attempt 2

```text
Grounding latency:          18,512.8 ms
Verification attempt 1:     13,287.2 ms (only attempt needed)
Vision calls:                2 (1 grounding + 1 verification)
Total input tokens:          2,646
Total output tokens:           204
Total estimated cost:       $0.00275
```

## Final Comparison — All Three Targets, Attempt 2

| Target | Converted coord | Attempt 1 verified | Attempt 2 verified | Human | Click result | Classification |
|---|---|---|---|---|---|---|
| email_row | (538,351) | True (1.0) | not needed | PASS | **PASS** | `AI_VERIFIED_FIRST_ATTEMPT` |
| reply | (916,631) | error (network) | True (1.0) | PASS | **PASS** | `ATTEMPT1_TECHNICAL_ERROR_ATTEMPT2_PASSED` |
| reply_editor | (1248,810) | True (1.0) | not needed | PASS | **PASS** | `AI_VERIFIED_FIRST_ATTEMPT` |

**Click PASS: 3/3.** `VISION_VERIFICATION_FALSE_NEGATIVE` (both AI
attempts genuinely disagreeing with a human PASS) was never observed
anywhere in Attempt 2 — every AI verification either passed outright or
recovered from a technical error, never a genuine model misjudgment once
the 1.5s stabilization delay was in place.

## Totals — Attempt 2, All Three Targets

```text
Total vision calls:    7  (3 grounding + 4 verification, including 1 errored attempt)
Total input tokens:    ~7,938
Total output tokens:     ~616
Total estimated cost:  ~$0.008263
```

(Sum of the three per-target totals recorded above:
$0.002777 + $0.002736 + $0.00275.)

## Overall Conclusion

Both of the original, separate questions now have real answers from this
data:

1. **"How much post-click stabilization time does Outlook need before
   Vision AI can reliably verify the state change?"** 1.5 seconds was
   sufficient for all three targets tested — every verification that
   actually produced a model verdict (not a network error) passed on the
   very first attempt once the delay was in place. Zero cases needed the
   second 1.0s wait for a genuine re-verification.
2. **"Is Gemini's verification itself reliable once the UI is stable?"**
   On this evidence, yes — 3/3 targets, every genuine verification
   verdict (excluding the one network error) matched the human's
   judgment, all at confidence 1.0. The `VISION_VERIFICATION_FALSE_NEGATIVE`
   category — which would indicate the model is unreliable even with a
   stable UI — was never triggered.

Combined with Attempt 1's original finding (a real false negative with
*no* stabilization delay), the overall picture is coherent: **the false
negative was very likely caused by verifying too soon after the click,
not by an unreliable verification model.** This is now supported by
direct repetition across all three targets, not just the single
`email_row` case that originally prompted the investigation — though
still a modest sample (3 targets, 1 trial each) rather than a
statistically robust benchmark.

A second, real software bug was also found and fixed mid-session (the
network-error-vs-genuine-false-negative classification conflation) —
consistent with this project's pattern of catching and correcting its
own tooling issues before trusting the numbers they produce, not just
finding issues in the model being evaluated.

## Next Step

RND-006B Attempt 2 is complete (3/3 PASS, all completion criteria met:
fresh grounding per target, human approval before every click, dual
foreground checks, single click only, post-click verification with
stabilization, no Send interaction, no unintended action). Per
instruction, **RND-007 is not started automatically** — this remains for
explicit review and approval before any controlled text entry begins.
