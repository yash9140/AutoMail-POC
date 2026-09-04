# 12 — Outlook Launch Playbook (RND-009B)

## Objective

Wire the Start Automation button (and, for the live test, an equivalent
gated driver script) to the first real playbook action: pressing the
Windows key, typing "Outlook" into Windows Search, having Gemini
ground the Outlook result, converting its coordinate, clicking it
once, waiting for Outlook to launch, and verifying it's open and
foreground — transitioning `LAUNCHING_OUTLOOK → OUTLOOK_VERIFIED`, then
stopping. Scope ends strictly there; no email interaction of any kind.

## Architecture

Unchanged core rule, now exercised for real for the first time:
playbook decides what happens next (`PlaybookEngine` + the strictly
linear transition table from RND-009A), Vision determines what's
visible and where (`WindowsSearchGroundingResponse`,
`OutlookLaunchVerificationResponse`), PyAutoGUI performs the one
action it's told, the safety layer (`AbortController` +
search/Outlook-state validators) decides whether that action is
allowed, and the metrics layer records every real call. Vision is
never asked "what should I do next" — both prompts explicitly forbid
that framing; the playbook already knows the next action is
`OPEN_OUTLOOK`.

## Worker / Thread Model

`app/workers/outlook_launch_worker.py::OutlookLaunchWorker` is a
`QObject` moved to a real `QThread`, so the UI thread stays responsive
during the ~20–30s this sequence can take. It never touches a Qt
widget directly — only emits `status`, `current_step`, `vision_status`,
`safety_status`, `log_message`, `success`, `failure`, `aborted`, and
`metrics_update` signals; `AutomationController` (UI thread) does all
widget updates in response. All the actual logic — pressing keys,
capturing screenshots, calling Gemini, moving/clicking, polling, final
verification — lives in `app/playbook/outlook_launch_steps.py::
OutlookLaunchSteps`, a plain class with no Qt dependency at all. The
worker is a thin wrapper: it calls each `OutlookLaunchSteps` method in
sequence and translates the result into signal emissions. This
separation is what let the whole sequence be unit-tested (mocking
pyautogui/the provider) without ever touching Qt threading, and
independently let a small gated driver script
(`scripts/rnd009b_live_run.py`) reuse the exact same step logic for the
supervised live test — one implementation, two callers.

## Bounded Session Approval (design revised mid-stage — see Live Result)

The original design asked for human approval **mid-run**, after
grounding and before the click (showing the detected coordinate and
asking "proceed?"). **This broke on the first live attempt**: leaving
the automation context to show the result in chat brought this VS Code
window back into focus, which made Windows Search no longer the
foreground window — so the subsequent click correctly refused to fire
(`SEARCH_STATE_LOST_BEFORE_CLICK`), but the run failed anyway, because
asking for approval mid-flight is itself an action that can steal the
exact foreground state being validated. The same problem would affect
the real GUI too: a modal `QMessageBox` shown mid-run becomes the
foreground window in place of Windows Search.

**Redesigned to bounded session approval**: a single approval, obtained
once **before** anything time-sensitive starts (in the GUI, a
`QMessageBox.question()` shown before the worker is even constructed;
for the live test, one question asked in chat before running the
script), covers the entire sequence. There is no pause between
grounding and the click. Safety during the uninterrupted run is
enforced entirely by `OutlookLaunchSteps`' own validation gates —
confidence threshold, bbox/screen-bounds check, repeated search-state
checks immediately before move and before click, and abort-flag checks
at every listed checkpoint — never by a human answering a question
mid-flight. If any gate fails, the run stops itself (no click, no
retry) and reports only once the desktop state is no longer
time-sensitive.

This bounded approval permits **only** the Outlook-launch playbook —
explicitly not opening an email, clicking Reply, typing a reply,
clicking Send, or any action after `OUTLOOK_VERIFIED`. `send_click_count`
stays `0` throughout, structurally (this module contains no Send-related
code path at all).

## Windows Search Method

No `subprocess`, `os.system`, direct executable path, shell shortcut,
COM, or Microsoft API — Windows UI interaction only:

```python
pyautogui.press("win")            # once
time.sleep(WINDOWS_KEY_STABILIZE_SECONDS)      # 0.9s
pyautogui.write("Outlook", interval=TYPE_INTERVAL_SECONDS)  # once, never followed by Enter
time.sleep(SEARCH_TYPE_STABILIZE_SECONDS)      # 1.2s
```

Enter is never pressed as part of search — Vision inspects the results
first. Both timings are named constants (`app/playbook/
outlook_launch_steps.py`), not arbitrary sleeps, chosen conservatively
and recorded here rather than tuned against a single live run.

## Vision Grounding

A dedicated prompt (`rnd/prompts/windows_search_grounding_v1.txt`)
asks Gemini to report, from a single fresh screenshot: whether Windows
Search is visible, whether an Outlook app result is visible, its exact
label, and one point inside it — never "what should I do next." Schema:
`WindowsSearchGroundingResponse` (`search_visible`, `outlook_result_visible`,
`result_label`, `x`, `y`, `confidence`, `reason`).

## Coordinate Conversion

The already-confirmed 0–1000 normalized convention, same shared
utility used everywhere since RND-005A — never raw coordinates:

```
pixel_x = raw_x / 1000 * screenshot_width
pixel_y = raw_y / 1000 * screenshot_height
```

## Safety Rules

No historical human-annotated bounding box exists yet for this new
Windows Search state (unlike Send's RND-002 ground truth), so multiple
independent checks substitute for it, all enforced in code:

- coordinate within screen bounds (`coordinate_in_image_bounds`)
- `search_visible` reported true
- `outlook_result_visible` true AND the label contains "outlook"
  (rejects a false-positive web-link/unrelated result)
- confidence ≥ `GROUNDING_CONFIDENCE_THRESHOLD` (0.6)
- (originally) human approval for the first live run — superseded by
  bounded session approval, see above; the *validation gates*
  themselves are unchanged and still the real safety mechanism

## Foreground / Screen-State Checks

A **dedicated, separate allowlist** for this stage
(`app/safety/window_state.py`) — deliberately never reusing "Outlook
must be foreground" (which would make this stage impossible to pass
before Outlook exists):

```python
SEARCH_STATE_ALLOWED_SUBSTRINGS = ("search", "start", "shellexperiencehost", "searchhost", "cortana")
# empty title also accepted — Windows 11's Start/Search overlay is not
# always a normally-titled window
```

**Confirmed live**: `get_foreground_window_title()` reported the literal
string `'Search'` while Windows Search was open on this machine — the
assumption documented as unverified in RND-009A's code comments is now
confirmed correct, not just assumed.

Checked before move and again before click (two separate checks, not
one) — if the state was lost between them, the click is skipped with
`SEARCH_STATE_LOST_BEFORE_CLICK` and the cursor is left wherever it
moved to, uncommitted. No auto-refocus of Windows Search is ever
attempted.

## Outlook Launch Polling

Bounded, lightweight polling — no Vision call per tick:

```
initial wait:     2.0s   (OUTLOOK_LAUNCH_INITIAL_WAIT_SECONDS)
poll interval:     1.0s   (OUTLOOK_LAUNCH_POLL_INTERVAL_SECONDS)
timeout:          18.0s   (OUTLOOK_LAUNCH_TIMEOUT_SECONDS)
```

Each tick only calls `get_foreground_window_title()` and checks for
"outlook" in it (`is_outlook_foreground`) — a real Vision call happens
only once, after the poll succeeds, for final confirmation.

## Vision Verification

`rnd/prompts/outlook_launch_verification_v1.txt` +
`OutlookLaunchVerificationResponse` — explicitly told not to evaluate
inbox state, only whether Outlook is visibly open (loading screen,
inbox, calendar — anything counts). Both signals are required for a
PASS: the lightweight foreground check (`foreground_verified`) AND
this Vision call (`outlook_visible`) — if the foreground check never
succeeded, Vision isn't even asked (`OUTLOOK_FOREGROUND_VERIFICATION_FAILED`
short-circuits first).

## Metrics

`RND009BResult` (`rnd/models/outlook_launch.py`) extends the
established pattern: every real provider call accumulates into
`total_vision_calls`/`total_input_tokens`/`total_output_tokens`/
`total_estimated_cost`/`total_latency_ms` (the RND-007B pre-RND-008
hardening discipline carried forward — no hidden helper calls).
Additional fields: `windows_key_timestamp`, `search_query_typed_timestamp`,
`search_capture_timestamp`, `outlook_click_timestamp`,
`outlook_detected_timestamp`, `outlook_launch_duration_ms`,
`mouse_click_count`, `keyboard_action_count`, `retries`,
`human_interventions`, `safety_aborts`, and `send_click_count`
(kept at `0` throughout — no code path in this module can touch it).

## Failures

Twelve distinct classifications (`app/playbook/failure_reasons.py::
LaunchFailureReason`), never collapsed into a generic FAIL:
`WINDOWS_SEARCH_NOT_VISIBLE`, `OUTLOOK_RESULT_NOT_FOUND`,
`GROUNDING_INVALID`, `GROUNDING_OUT_OF_BOUNDS`, `HUMAN_REJECTED_TARGET`,
`SEARCH_STATE_LOST_BEFORE_CLICK`, `OUTLOOK_LAUNCH_TIMEOUT`,
`OUTLOOK_FOREGROUND_VERIFICATION_FAILED`, `VISION_VERIFICATION_FAILED`,
`TECHNICAL_PROVIDER_ERROR`, `USER_ABORTED`, `OUTLOOK_READY_TIMEOUT`
(added by the readiness hardening below).

**Attempt 1 (mid-run approval design, since redesigned) exercised
`SEARCH_STATE_LOST_BEFORE_CLICK` live** — see Live Result below;
preserved separately as `results/raw/
rnd009b_outlook_launch_results_attempt1.json` /
`results/reports/rnd009b_outlook_launch_summary_attempt1.md`, not
overwritten by Attempt 2's PASS.

## Human Approvals

Bounded design: **one** approval per run, obtained before anything
starts. Attempt 1 used the (now superseded) mid-run design and reached
its approval point, but the click itself was blocked by the
search-state safety gate — so that attempt's `human_interventions` was
still 1, just at a different point in the sequence than Attempt 2's.

## Live Result

**Attempt 1 — FAIL** (`SEARCH_STATE_LOST_BEFORE_CLICK`): Windows key,
typing, capture, and Gemini grounding all worked correctly (label
"Outlook", raw (69,280) → converted (132,302), confidence 0.98, a
genuinely correct detection verified against the screenshot). Between
showing the grounding result in chat and running `--open-outlook`,
this VS Code window regained foreground (most likely from displaying
the earlier privacy-review screenshot inline), so the pre-click
search-state check correctly refused to move or click. This directly
led to the bounded-approval redesign documented above.

**Attempt 2 — PASS** (bounded approval, single uninterrupted run):

```text
Windows key:            2026-08-31T16:58:30.216253
Search typed:            2026-08-31T16:58:31.434986
Foreground before check:  'Search'   (confirms the search-state allowlist assumption)
Search screenshot:        screen_20260831_165832_635.png

Grounding: label='Outlook', raw=(130.0, 303.0), converted=(250, 327), confidence=0.98
Click executed: True at 2026-08-31T16:58:48.706063
Launch duration: 2001.9 ms
Foreground after launch: 'Outlook'
Vision verification: outlook_visible=True, confidence=1.0, detected_state="Outlook splash screen loading"

Overall result: PASS
```

The final verification screenshot was independently inspected (not
just Gemini's report) and directly confirms Outlook's real splash
screen, title bar reading "Outlook" — genuinely open, mid-launch, which
the prompt correctly treats as "open" per this stage's own scope (no
inbox-state requirement).

**Totals across both attempts:**

| | Vision calls | Input tokens | Output tokens | Cost | Latency |
|---|---|---|---|---|---|
| Attempt 1 | 1 (grounding only) | ~1,417 | ~62 | ~$0.0013 | ~15s |
| Attempt 2 | 2 (grounding + verification) | 2,834 | 181 | $0.002804 | 22,084.3 ms |

`send_click_count` remained `0` in both attempts. `mouse_click_count`
was `0` in Attempt 1 (blocked before click) and `1` in Attempt 2.

## Readiness Hardening (post-approval adjustment)

After RND-009B's live result was reviewed, a real gap in it was flagged:
the original `verify_outlook_with_vision()` treated `outlook_visible: True`
as sufficient for `OUTLOOK_VERIFIED` — but Attempt 2's own verification
screenshot showed Outlook's **splash/loading screen**, not a usable UI.
A splash screen legitimately makes `outlook_visible` true (Outlook *is*
open), which is exactly why that single boolean was not strict enough.

**Fixed by distinguishing two separate signals**, never conflated:

- `splash_screen_visible` — is what's shown a loading/splash state
- `ready_for_interaction` — is Outlook open, splash gone, AND a usable
  interactive view visible (inbox, folder list, calendar, etc.)

`OutlookLaunchVerificationResponse` (`rnd/models/outlook_launch.py`)
gained both fields; the prompt
(`rnd/prompts/outlook_launch_verification_v1.txt`) now explicitly
instructs Gemini never to report `ready_for_interaction: true` for a
splash/loading screen, however confident it is that Outlook is open.

**`verify_outlook_with_vision()` was replaced by
`verify_outlook_readiness()`** (`OutlookLaunchSteps`), a bounded retry
loop, each attempt recorded separately in `result.readiness_attempts`
(never collapsed into one pass/fail bit — same "keep signals separate"
discipline as RND-008's `post_send_attempts`):

```text
attempt 1: wait 6.0s  (READINESS_INITIAL_WAIT_SECONDS) -> re-check Outlook foreground -> capture -> verify
attempt 2 (only if attempt 1 wasn't ready): wait 3.0s (READINESS_RETRY_WAIT_SECONDS) -> same
max attempts: 2 (MAX_READINESS_ATTEMPTS)
```

If Outlook loses foreground between attempts, or is still showing a
splash/loading screen after both attempts, the run fails with the new
`OUTLOOK_READY_TIMEOUT` classification (`app/playbook/
failure_reasons.py`) — not a PASS with an asterisk. The worker
(`OutlookLaunchWorker`) and the live-run script
(`scripts/rnd009b_live_run.py`) were both updated to call the renamed
method; a new `--check-readiness` command was added to the live-run
script for validating this logic against an already-open Outlook
window without repeating the launch sequence.

**A second, smaller gap was found and fixed while doing this work**:
the original result model never persisted the verification
screenshot's filename at all (noted as a limitation in the original
Live Result section above) — each `OutlookReadinessAttempt` now
records its own `screenshot` field.

**Validation: 26 new/updated mocked tests, 251/251 passing project-wide.**
Coverage includes: ready on the first attempt after the 6s wait; a
splash screen on attempt 1 that clears by attempt 2 (with the correct
3s retry wait); both attempts still showing a splash screen exhausting
to `OUTLOOK_READY_TIMEOUT`; a splash screen with `outlook_visible: True`
never counted as ready (the exact bug this hardening closes); the
provider never called a 3rd time; and foreground loss between attempts
failing immediately without a wasted Vision call.

**Not re-validated against a live Outlook launch under the new stricter
logic** — by the time this hardening was implemented, Outlook had
already been closed for this session, so the intended live
`--check-readiness` confirmation (against the still-open window from
Attempt 2) could not run. This is an honest gap, not a skipped step:
the mocked tests above are the only evidence for this specific code
path so far. **Recommended before RND-009C**: one live run to confirm
the 6s/3s readiness loop behaves as tested against a real, currently-
loading Outlook window.

**The original RND-009B Attempt 2 result is unchanged and NOT
retroactively reclassified.** Under the new stricter logic, that exact
capture (splash screen, `outlook_visible: True`) would very likely not
have qualified as `ready_for_interaction` and would have triggered a
second readiness attempt — but that is a statement about what the new
code would do, not a correction applied to the historical PASS result,
which stands as originally recorded under the design that existed at
the time.

## Readiness Live Validation

A live run confirmed the splash-vs-ready distinction works against a
real Outlook launch. Preserved as its own labeled result, none of the
three prior attempt files touched: `results/raw/
rnd009b_outlook_launch_results_readiness_validation.json` /
`results/reports/rnd009b_outlook_launch_summary_readiness_validation.md`
(the canonical `results.json`/`summary.md` and `_attempt1` were also
explicitly copied to `_attempt2` before this run, so all three earlier
records stay addressable by name).

**Launch (same bounded flow as before):** Windows key → typed "Outlook"
→ grounded label "Outlook", raw (67,280) → converted (129,302),
confidence 0.98 → single click at 2026-08-31T18:09:07 → Outlook
foreground detected in 2,001.6 ms.

**Readiness attempt 1 — technical ERROR, not a logic failure:** the
Gemini call itself failed with a transient `HTTP 504` (server
deadline exceeded) before returning any classification — genuinely
different from "still loading," and recorded as such
(`schema_valid: false`, `error` set, no `ready_for_interaction` value
at all).

**Readiness attempt 2 — PASS:** retried via the new standalone
`--check-readiness` command against the same, already-launched Outlook
window (no repeated Windows Search, no repeated click — the launch had
already succeeded; only the readiness verification needed retrying):

```text
outlook_visible: true
splash_screen_visible: false
ready_for_interaction: true
detected_state: "Outlook main interface fully loaded and ready"
confidence: 1.0
reason: "Full inbox UI visible: folder list, message list, reading pane
         all rendered; no splash/loading indicators present."
```

Independently verified by viewing the actual screenshot
(`screen_20260831_180943_962.png`): a genuine, fully-rendered Outlook
inbox — folder list, message list with real subject lines, reading
pane, search bar — nothing resembling a splash screen. **This is the
concrete evidence the hardening was built to produce**: the exact same
kind of screenshot that would previously have been accepted at the
`outlook_visible: true` stage (as Attempt 2's splash screen already
was) is now correctly distinguished from a truly ready UI when the two
are compared side by side.

A genuine gap was found while merging this result: `--check-readiness`
starts its own fresh `OutlookLaunchSteps` and always numbers its first
call "attempt 1," so retrying a failed in-progress launch's readiness
check doesn't automatically continue that run's own attempt count —
the merge into attempt 2 above was done by hand (documented, not
hidden) rather than by a clean code path. **Candidate follow-up**:
give `--check-readiness` an option to resume from an existing pending
file's attempt count instead of always starting fresh.

## Limitations

- ~~The verification screenshot's filename is not persisted on the
  result model~~ — **fixed** by the readiness hardening below
  (`OutlookReadinessAttempt.screenshot`).
- ~~The readiness hardening is validated only by mocked tests, not a
  live run~~ — **closed**, see Readiness Live Validation above. One
  open gap remains from that validation: `--check-readiness` doesn't
  resume an in-progress run's own attempt numbering (worked around by
  hand for this run, documented above, not a silent gap).
- **Two attempts, one email-free environment.** This establishes the
  bounded launch sequence works once under real conditions; it does
  not establish behavior across different Windows Search states
  (e.g., multiple matching results, a slow/first-run Outlook install,
  a locked screen) or repeated runs in the same session.
  `poll_for_outlook_foreground` was only exercised on its fast path
  (2s + one immediate success) — the timeout and multi-poll paths are
  proven only by mocked tests, not live.
  - **Attempt 1's failure directly validated a real, previously
  theoretical risk**: showing intermediate results to a human via a
  side channel (chat, in this case) can itself invalidate a
  timing-sensitive desktop-automation state. This is now a documented,
  evidence-backed design constraint for any future stage, not just an
  abstract concern.
- **The bounded-approval design trades a finer-grained safety check
  (approve this specific target) for a coarser one (approve this whole
  class of action, trust the code's own validation gates for the
  specific target)** — a deliberate tradeoff given the alternative
  (mid-run approval) was empirically shown to break the very state it
  was trying to make safer. Worth revisiting if a future Windows
  Search state proves harder to validate purely in code (e.g.,
  multiple similarly-labeled results).

## Conclusion

The Outlook-launch playbook stage works end-to-end: Start Automation
(and the equivalent live-test driver) can press the Windows key, type
"Outlook," have Gemini ground the correct result, click it exactly
once inside validated bounds, wait for a real launch, and verify
Outlook is open — reaching `OUTLOOK_VERIFIED` with `send_click_count`
still at `0` and no email interaction anywhere. The stage's first live
attempt surfaced a genuine, non-obvious architectural problem (mid-run
approval breaking the state it validates) that was found, understood,
fixed, and re-tested successfully within the same stage — not smoothed
over.

## Next Step

**RND-009C** — the next state transition, `OUTLOOK_VERIFIED →
FINDING_EMAIL`. **Not started automatically** — requires its own
explicit review and approval, per the same gate structure used at
every prior stage.
