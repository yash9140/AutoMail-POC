# 09B — Contextual Reply Generation + Draft Entry (RND-007B)

## Objective

Determine whether the POC can, for one real Outlook email: (A) have
Gemini understand the email's content and intent from a screenshot,
(B) draft a contextually appropriate reply from that understanding,
(C) type the human-approved draft into the reply editor, and (D)/(E)
have that draft verified by both Gemini and the human — all gated by
explicit human approval at every stage, with Send never reachable.

Three metrics-separate signals from RND-007A carry forward, joined by
two more (per instruction, never collapsed into one score):

- **A. Email understanding** — Gemini's read of the open email
- **B. Reply generation** — Gemini's drafted reply, from the
  human-approved understanding only
- **C. Text-entry execution** — did the typing action itself complete
  mechanically
- **D. Vision draft verification** — did Gemini's independent
  post-typing read match the approved draft
- **E. Human verification** — the human's own visual judgment,
  authoritative over A–D when they disagree
- **Reply quality** — six independently human-scored PASS/FAIL
  criteria (relevant, accurate, no_hallucination, professional,
  concise, complete), never averaged into one number

## Test Email

Subject: **"Mail for project"**, body: *"Hii Yash, Hope you are fine
and doing well."* — the same recurring, human-confirmed non-sensitive
test email used across RND-002, RND-006A/B, and RND-007A.

**Privacy confirmation (human, in chat):** *"subject: Mail for
project, email body: Hii Yash, Hope you are fine and doing well. ,
this is not a sensitive email."* Confirmed before any screenshot of
this email was sent to Gemini.

*(Known limitation: this confirmation was captured in chat but the
`email_subject`/`email_body_summary_human`/`privacy_confirmed` fields
on the result model were never actually populated by the script — they
remain `null`/`false` in all three finalized result files despite the
real approval having been obtained. The approval is real; the field is
just not wired up. Documented here rather than silently left
inconsistent — worth a small follow-up fix before RND-008.)*

## Safety Controls (all reused, none new except where noted)

- Outlook foreground check before every click/type action, reused from
  RND-002/006A/006B/007A.
- **New in this stage:** a foreground check was added to `cmd_verify()`
  immediately before capture — previously verification captured
  whatever was on screen unconditionally. See Attempt 2's failure
  below for why this was needed.
- `_check_reply_editor_open()` (RND-006B's state-verification prompt,
  reused) decides whether a Reply click is needed at all, specifically
  to avoid RND-004's discovered "repeated click Reply" bias — in all
  three attempts the editor was already open, so **zero Reply clicks
  occurred** across this entire experiment.
- `pyautogui.FAILSAFE` asserted `True` immediately before typing,
  never disabled anywhere in this codebase.
- Send is structurally unreachable: no click path, no `Ctrl+Enter`, no
  `Alt+S` anywhere in the module (proven by a source-inspection test,
  same technique as RND-006A/007A).
- Gated, multi-invocation CLI: `--understand` → `--approve-understanding`
  → `--generate` → `--approve-draft` → `--type` → `--verify` →
  `--confirm` → `--quality`, each requiring the previous stage's
  recorded human approval (enforced by `SystemExit`, unit-tested).
- Every attempt's evidence is preserved under its own `--attempt`/
  `--retry`-suffixed file set — nothing was overwritten.

## Approved Content (unchanged across all three attempts)

**Email understanding (Gemini, human-approved):**

```text
Email summary:    The sender, Yash Dhanraj, sends a brief greeting extending well wishes.
Sender intent:    To initiate contact or send a simple friendly check-in message.
Requires reply:   False
Requested action: (none)
Important points: - The email opens with a standard greeting: 'Hii Yash, Hope you are fine and doing well.'
                   - The signature identifies the sender as Yash Dhanraj from IHS (yashdhanraj9140@gmail.com).
Confidence:        0.95
```

Human approved this understanding despite the `requires_reply: False`
caveat (the email is a casual check-in, not a request) — the test
proceeded anyway per instruction, since the goal was to exercise the
generation + typing pipeline, not to judge whether a reply was
strictly necessary.

**Generated draft (Gemini, human-approved, typed identically in all
three attempts):**

```text
Hi Yash,

Thank you for reaching out. I am doing well and hope you are doing well as well. Please let me know if there is anything I can assist you with.

Best regards,
```

Reasoning: *"The reply politely acknowledges Yash's friendly greeting
and well wishes while inquiring how to assist, maintaining a
professional and concise tone without adding unverified details."*
Confidence: 0.95.

## Attempt Timeline

| Attempt | Typing method | `typing_result` | Vision verification | Human verification | Overall |
|---|---|---|---|---|---|
| **Attempt 1** | Single `pyautogui.write(full_draft)` with embedded `\n` | PASS (mechanical) | **False** — only `"Best regards,"` detected | **FAIL** | **FAIL** |
| **Attempt 2** | Same single-call method (retry before root cause was known) | **FAIL** (corrected post-hoc — see below) | Not obtained (post-typing screenshot showed VS Code, not Outlook) | **FAIL** | **FAIL** |
| **Attempt 2 Retry 1** | Corrected: draft split into 5 segments, `pyautogui.write()` per segment, explicit `pyautogui.press("enter")` between segments, 0.6s focus-settle delay before typing | **PASS** | **True** — exact match, confidence 1.0 | **PASS** | **PASS** |

Result files preserved separately, none overwritten:
`rnd007b_contextual_reply_results_attempt1.json`,
`_attempt2.json`, `_attempt2_retry1.json` (and matching
`_summary*.md` reports).

## Attempt 1 — Failure

The reply editor was already open (no click occurred). The full
multiline draft was passed through **one** `pyautogui.write()` call
containing embedded `\n` characters. Only the final line, `"Best
regards,"`, landed in the Outlook reply editor body — the greeting and
main paragraph were missing.

This was **not** a vision false negative: Gemini's `semantic_match:
false` and `detected_draft: "Best regards,"` were an accurate read of
what was actually on screen, later confirmed by direct human
inspection of the screenshot and by the human directly.

**Root cause hypothesis (recorded, not yet proven):** a single
`write()` call with embedded newlines is not reliable for Outlook's
rich-text editor — the newlines are pressed as literal Enter keys
within one continuous keystroke burst, which may race with the
editor's own input handling and lose earlier content.

## Attempt 2 — Failure (retry, same method, deeper problem found)

Re-run with the same (uncorrected) single-call method as a same-method
retry. The `--verify` capture returned an HTTP 504 timeout on the
first Gemini call. Inspecting the actual post-typing screenshot before
retrying the call revealed **the screen showed VS Code, not Outlook**
— meaning input/screen focus had drifted away from Outlook, most
likely during the gap between typing and verification (or possibly
during typing itself).

The human then directly confirmed in Outlook: **nothing was actually
typed into the reply editor at all.** This meant the automation's
mechanically-recorded `typing_result: PASS` (the `write()`/`press()`
calls executed without raising an exception) was misleading — the
keystrokes did not land in Outlook. This value was corrected to `FAIL`
post-hoc per the human-authoritative rule, with the correction
recorded in `notes` rather than silently overwritten.

**Two real, distinct tooling gaps were found and fixed as a direct
result:**

1. `cmd_verify()` had no foreground check before capturing — it would
   silently capture and pass whatever was on screen, even a completely
   unrelated application, to the verification model. **Fixed:** a
   foreground check now runs immediately before capture; on mismatch
   it aborts with `FOREGROUND_MISMATCH`, captures nothing, and makes
   no vision call (does not auto-switch focus).
2. `cmd_type()` still only foreground-checks *before* the click/type
   sequence begins, not continuously during typing — a real limitation
   that this attempt's evidence suggests may have let focus drift away
   mid-sequence. **Not fixed in this stage** (documented as a
   limitation below; a continuous or post-typing foreground re-check
   is a candidate follow-up).

## Attempt 2 Retry 1 — Pass

Seeded from Attempt 2's already-approved understanding and draft (no
re-approval needed, no re-generation call). Before retyping, the human
manually cleared the failed draft and confirmed the reply editor was
empty and Outlook was foreground.

**Corrected typing method:**

```python
segments = draft.split("\n")
for i, segment in enumerate(segments):
    if segment:
        pyautogui.write(segment, interval=TYPE_INTERVAL_SECONDS)
    if i < len(segments) - 1:
        pyautogui.press("enter")
```

`pyautogui.write()` never receives a string containing `\n`; every
line break is an explicit, separate `press("enter")` call; a 0.6s
focus-settle delay runs before typing begins (whether or not a Reply
click just happened). Structurally verified: exactly one `write()`
call site and exactly one `press("enter")` call site in source, the
latter gated by the segment-loop condition (not a bare/standalone
call reachable outside typing).

**Result:**

```text
typing_result:        PASS
detected_draft:        "Hi Yash,\n\nThank you for reaching out. I am doing well and hope you are doing well as well. Please let me know if there is anything I can assist you with.\n\nBest regards,"
exact_match:            True
semantic_match:         True   (confidence 1.0)
human_verification:     True
overall result:         PASS
```

Human visual confirmation (both from a direct screenshot inspection
and the human's own "pass") agreed: greeting present, body paragraph
present, closing present, correct order, no missing or duplicated
text, cursor resting after "Best regards," with Send untouched.

## Reply Quality (human-scored, Attempt 2 Retry 1 only — not
meaningful to score for the two failed typing attempts)

| Criterion | Result |
|---|---|
| relevant | PASS |
| accurate | PASS |
| no_hallucination | PASS |
| professional | PASS |
| concise | PASS |
| complete | PASS |

## Latency & Cost

Per-call figures (only calls that actually ran are listed; Attempt
2's aborted/failed verification made no billed call):

| Call | Attempt | Latency | Input tokens | Output tokens | Cost |
|---|---|---|---|---|---|
| Email understanding | 1 (reused by 2, 2R1) | 12,192.9 ms | 1,281 | 139 | $0.001482 |
| Reply generation | 1 (reused by 2, 2R1) | 11,150.9 ms | 1,411 | 105 | $0.001452 |
| Draft verification | 1 | 15,034.3 ms | 1,417 | 62 | $0.001295 |
| Draft verification | 2R1 | 50,279.0 ms | 1,417 | 102 | $0.001445 |

**Totals across the whole RND-007B session** (understanding +
generation counted once, not once per attempt, since they were reused
unchanged): **4 tracked vision calls, 5,526 input tokens, 408 output
tokens, $0.005674 estimated total cost.**

**Known undercount (found while compiling this report, not fixed
yet):** `_check_reply_editor_open()` makes its own Gemini call every
`--type` invocation to decide whether a Reply click is needed, but
that call's metrics are never accumulated into `total_vision_calls`/
tokens/cost anywhere in the code. All three attempts found the editor
already open, meaning **at least 3 additional real Gemini calls were
made and billed but are not reflected in any total above.** The
figures above are a verified floor, not an exact total. Flagged
honestly here rather than presented as complete; worth a small
follow-up fix (accumulate `_check_reply_editor_open`'s metrics like
every other call) before relying on RND-007B's cost figures for
budgeting.

## Failures (summary)

1. Attempt 1: single-call multiline typing lost all but the last line.
2. Attempt 2: same method, plus a second, independent problem —
   verification captured the wrong application (foreground drift), and
   the human confirmed nothing was actually typed. Two tooling gaps
   found and one fixed (`cmd_verify` foreground check).
3. Untracked vision-call cost gap found while compiling this report
   (see above) — not yet fixed.

## Limitations

- **Single email, single draft, three attempts of the same test** —
  this establishes that the corrected typing method works for one
  short multi-paragraph reply; it does not establish reliability
  across longer drafts, different line-break patterns, or special
  characters.
- **`cmd_type()` still does not re-check foreground continuously
  during typing** — only immediately before the click/type sequence
  begins. Attempt 2's evidence suggests focus can drift during a live
  session; the corrected method reduces exposure (segmented calls,
  focus-settle delay) but does not eliminate this class of risk.
- **Vision-call cost/token totals are a documented undercount** (see
  above) — the reply-editor-open state check's calls are real,
  billed, and currently untracked in the result model.
- **The privacy-confirmation fields on the result model were never
  populated**, despite a real human approval being obtained in chat
  (see Test Email section) — a reporting/schema gap, not a process
  gap.
- **Attempt 2's `typing_result` required a manual post-hoc correction**
  (JSON edited directly, not through a CLI command) because the
  gated flow had no built-in way to override a mechanically-recorded
  PASS after the fact based on human observation. This worked correctly
  here (human-authoritative rule was honored) but relied on manual
  intervention rather than a dedicated code path.

## Conclusion

Contextual reply generation and draft entry succeeded end-to-end on
the third live attempt (Attempt 2 Retry 1): a real Outlook email was
understood, a contextually appropriate reply was drafted, human-approved,
typed correctly using a corrected segment-based method, and verified
by both Gemini and the human — all six reply-quality criteria scored
PASS, and Send was never touched. Two genuine, non-hallucinated
execution bugs were found and handled honestly along the way, one of
which (`cmd_verify`'s missing foreground check) is now fixed; the
other (typing-time focus-drift) and the vision-call cost-tracking gap
remain open, documented limitations.

## Pre-RND-008 Hardening

After RND-007B was reviewed and approved, two of its documented
limitations were fixed before RND-008 could be authorized. **No Send
action was implemented or executed as part of this hardening.**

### 1. Mid-action foreground protection

`cmd_type()` previously foreground-checked once before the click/type
sequence began, then typed the entire draft with no further checks —
exactly the gap RND-007B Attempt 2 exposed (focus drifted away mid-run
and the automation had no way to notice). The typing loop now
re-checks foreground **before every segment write and before every
explicit Enter press**, not just once at the start:

```python
for i, segment in enumerate(segments):
    if segment:
        title_seg = get_foreground_window_title()
        if REQUIRE_FOREGROUND_SUBSTRING not in title_seg.lower():
            # ABORT: no write, no further keys, no auto-refocus, no retry
            ...
            return
        pyautogui.write(segment, interval=TYPE_INTERVAL_SECONDS)
    if i < len(segments) - 1:
        title_enter = get_foreground_window_title()
        if REQUIRE_FOREGROUND_SUBSTRING not in title_enter.lower():
            # ABORT: no Enter, no further keys, no auto-refocus, no retry
            ...
            return
        pyautogui.press("enter")
```

On drift, the module aborts immediately — no further keys are sent, no
automatic refocus onto Outlook is attempted, and no automatic retry
occurs. `pyautogui.FAILSAFE` remains `True` throughout, unchanged.

New result fields record exactly how much of the draft actually landed
before the abort: `failure_reason = "FOREGROUND_CHANGED_DURING_TYPING"`,
`abort_stage` (`"before_write"` or `"before_enter"`),
`segment_index_aborted_at` (0-indexed position in the draft's line
list), `segments_typed_before_abort` (how many segments had already
been typed), and `typed_text` set to the partial text actually typed
(best-effort reconstruction from the segments that completed).

### 2. Cost / usage accounting fix

`_check_reply_editor_open()` — called once per `--type` invocation to
decide whether a Reply click is needed — made its own real, billed
Gemini call that was never accumulated into
`total_vision_calls`/`total_input_tokens`/`total_output_tokens`/
`total_estimated_cost`. It now builds a `CallMetrics` object and
passes it through the same `_accumulate()` helper every other call
site uses, and additionally stores it on its own
`reply_editor_state_check_metrics` field for individual visibility
(mirroring `grounding_metrics`).

While fixing this, a second, previously-unnoticed gap was found:
**no call site anywhere in this module accumulated latency into a
running total** (each call's own latency was recorded on its
individual metrics object, e.g. `draft_verification_metrics.latency_ms`,
but never summed). A `total_latency_ms` field was added to
`RND007BResult` and `_accumulate()` now sums every call's latency into
it, so latency is tracked consistently with the other three totals
going forward.

**Historical RND-007B figures are not retroactively corrected.** The
three finalized attempt result files (`_attempt1`, `_attempt2`,
`_attempt2_retry1`) keep their originally-recorded totals exactly as
recorded, with the documented undercount caveat preserved in this
document above — no historical cost/latency numbers were invented or
backfilled for calls whose usage data was never captured live. Only
runs from this point forward use the corrected accounting.

### Tests added

12 new mocked tests (no real mouse/keyboard/network in any of them):

- foreground remains Outlook through all segments → typing completes
- foreground changes before a segment → abort, that segment never typed
- foreground changes before an Enter → abort, that Enter never pressed
- no further keys (write or press) are sent after either abort
- the existing source-inspection test was updated to confirm the Enter
  press is still reachable only through the segment-loop condition and
  is now gated by a foreground re-check + `return` immediately before it
- the reply-editor-open state check's own call is included in
  `total_vision_calls`
- its input tokens are accumulated into `total_input_tokens`
- its output tokens are accumulated into `total_output_tokens`
- its cost is accumulated into `total_estimated_cost`
- its latency is accumulated into the new `total_latency_ms`
- its metrics are also individually visible on
  `reply_editor_state_check_metrics`
- (Send/Ctrl+Enter/Alt+S unreachability continues to be proven by the
  existing structural source-inspection test, unaffected by this work)

### Full test result

```text
178 passed, 1 warning in 4.92s
```

(174 passing before this hardening + 4 new test functions covering the
12 behaviors above; some behaviors are asserted together within a
single test function.)

### Pre-RND-008 hardening verdict: **PASS**

Both documented limitations from RND-007B's conclusion are now fixed
and covered by mocked regression tests. Send remains completely
unimplemented and unreachable in this module — nothing in this
hardening pass added, enabled, or exercised any Send-related code
path.

## Completion Question — Is it safe to proceed to RND-008 — Controlled
Send + Send Verification?

**Yes**, now that both follow-ups identified at the end of RND-007B
have been implemented and verified (see Pre-RND-008 Hardening above):

1. **Mid-action foreground protection** — the class of focus-drift bug
   observed for real in Attempt 2 is now caught mid-typing (before
   every segment and every Enter), not just once before typing begins.
2. **Cost/usage accounting** — every real provider call, including the
   previously-invisible reply-editor state check, now contributes to
   the running totals (calls, tokens, cost, and newly, latency).

The core contextual-reply pipeline (understand → draft → approve →
type → verify → confirm) worked correctly and safely once the typing
bug was fixed, and the human-authoritative verification design caught
both real failures without ever risking an incorrect Send (Send was
never reachable in any attempt, working or not, and remains
unimplemented after this hardening pass too).

**RND-008 is not started automatically.** This is a recommendation
only, pending explicit review and approval, per the same gate
structure used at every prior stage.
