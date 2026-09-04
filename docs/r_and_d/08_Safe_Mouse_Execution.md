# 08 — Safe Mouse Execution (RND-006A)

## Objective

Answer: **can normalized Gemini grounding coordinates be safely converted
into real Windows screen coordinates and used to move the mouse to the
intended Outlook control?**

All three planned move-only targets are complete: **`email_row`,
`reply`, and `reply_editor` — 3/3 PASS.**

## Why Move-Only Testing Comes Before Clicking

RND-005/RND-005A/RND-005B established that Gemini's coordinates require a
0–1000-normalized-to-pixel conversion, verified against static
screenshots. Moving the real OS cursor is a materially different risk
category — a wrong coordinate here is not just a wrong number in a JSON
file, it is the system's cursor actually being somewhere unintended on a
live desktop. Testing MOVE before CLICK means a wrong prediction is
merely visible and correctable, not something that fires an unintended
action against a real application.

## Coordinate Conversion Rule

Every coordinate is passed through the single existing, already-tested
utility:

```python
rnd.metrics.coordinate_calibration.normalize_1000_to_pixels(raw_x, raw_y, screen_width, screen_height)
```

No duplicate conversion logic exists anywhere in the RND-006 executor
(`rnd/experiments/rnd006_safe_mouse_execution.py`) — it imports and calls
this function directly, the same one validated in RND-005A/B. Screen
dimensions are read fresh from `pyautogui.size()` at both the `--execute`
and `--move` steps (not assumed to equal the screenshot's own recorded
dimensions), matching this machine's confirmed 1920×1080 from RND-001.

## Foreground Window Safety

Before every capture and again immediately before every move, the
executor calls the same read-only `get_foreground_window_title()`
function built in RND-002 (`rnd/experiments/capture_with_foreground_check.py`)
— reused, not duplicated. If the foreground window title doesn't contain
"outlook" (case-insensitive) at either check, the action is aborted and
no screenshot/API call/mouse movement happens. Outlook is never brought
to the foreground automatically — the human is responsible for that.

**This was exercised for real, not just in theory:** between the
`--execute` step (which validated a coordinate) and the first `--move`
attempt for `email_row`, the foreground window changed to VS Code
(the IDE auto-opened the freshly captured screenshot file). The
foreground safety check correctly detected this and **aborted the move —
`pyautogui.moveTo` was never called.** Only after the human confirmed
Outlook was foreground again was the move retried (see Failures below for
exactly how that retry was authorized).

## PyAutoGUI Failsafe

`pyautogui.FAILSAFE` is left at its Python-default value, `True`, and is
never set anywhere in this codebase (confirmed by `grep`). This means
PyAutoGUI's built-in emergency stop remains active: **slamming the mouse
cursor into any physical screen corner (0,0 or a far corner) at any time
raises `pyautogui.FailSafeException` and aborts whatever PyAutoGUI call is
in progress.** `Ctrl+C` in the terminal running the script works normally
as an additional abort path (no special signal handling was added or
needed). The executor also asserts `pyautogui.FAILSAFE is True`
immediately before every `moveTo` call — if something ever disabled it,
the move would refuse to happen rather than silently proceeding unsafely.

`pyautogui.click` is not imported, referenced, or reachable anywhere in
`rnd/experiments/rnd006_safe_mouse_execution.py` — confirmed by a unit
test that greps the module's own source
(`test_module_never_imports_pyautogui_click_directly`), not just by
manual inspection.

## Targets Tested

| Target | Status |
|---|---|
| `email_row` (OUTLOOK-001) | **Tested — PASS** |
| `reply` | **Tested — PASS** |
| `reply_editor` | **Tested — PASS** |
| `send` | **Not in the allowed target list — structurally rejected by argparse `choices` before any code runs, and independently by `is_target_allowed()`** |

## Per-Target Result

### email_row

```text
Foreground window (at capture):  'Inbox - Yash Dhanraj - Outlook'
Screenshot:                      screen_20260828_130859_973.png (fresh, captured live — not reused from RND-002)
Raw Gemini coordinate:           (280.0, 327.0)
Converted pixel coordinate:      (538, 353)
Screen resolution:               1920 x 1080
Coordinate valid:                True
Confidence:                      0.9
Vision API attempts:             2 (1 transient NetworkError, recovered on retry — recorded honestly, not hidden)
Movement executed:               True
Human verification:              PASS — "cursor is correctly positioned on the email row"
Final result:                    PASS
```

Notably, this fresh raw prediction, `(280, 327)`, is nearly identical to
RND-005's static-screenshot prediction for the same target, `(280, 325)`
— a fourth independent reproduction of Gemini's stable spatial estimate
for this kind of control, now confirmed to work through the full live
capture→predict→convert→move→human-verify pipeline, not just offline
analysis.

### reply

```text
Foreground window (at capture):  'Inbox - Yash Dhanraj - Outlook'
Screenshot:                      screen_20260828_152641_252.png (fresh, live capture)
Raw Gemini coordinate:           (477.0, 585.0)
Converted pixel coordinate:      (916, 632)
Screen resolution:               1920 x 1080
Coordinate valid:                True
Confidence:                      0.95
Vision API attempts:             1 (no retry needed)
Movement executed:               True
Human verification:              PASS
Final result:                    PASS
```

Foreground check passed cleanly on both the `--execute` and `--move`
steps this time — no abort needed. The raw x-value, `477`, is close to
(though not identical to) the `478` seen in RND-003/RND-005/RND-005B for
the same Reply target; the y-value, `585`, differs more (vs. `630`/`631`
previously) — a real, worth-noting variation this time, plausibly because
this was a genuinely different live screenshot (different window
state/scroll position) rather than the exact static image reused across
RND-003/005/005B. Despite that numeric difference, the human-verified
outcome was still PASS.

### reply_editor

```text
Foreground window (at capture):  'Inbox - Yash Dhanraj - Outlook'
Screenshot:                      screen_20260828_153557_159.png (fresh, live capture; Reply had been manually clicked beforehand so the editor was open)
Raw Gemini coordinate:           (700.0, 780.0)
Converted pixel coordinate:      (1344, 842)
Screen resolution:               1920 x 1080
Coordinate valid:                True
Confidence:                      0.95
Vision API attempts:             1 (no retry needed)
Movement executed:               True
Human verification:              PASS
Final result:                    PASS
```

Foreground check passed cleanly on both steps. This target's raw
coordinate, `(700, 780)`, is the largest bounding target of the three
(the RND-002 reply_editor bbox was 1044×166px, by far the biggest of the
four annotated controls) — a comparatively easier target to land inside,
consistent with it passing cleanly on the first attempt.

## Human Verification

Required and obtained for every completed move — this stage never treats
a movement as successful based on coordinate math alone.
`human_verified: true` / `result: "PASS"` for `email_row` reflects an
explicit "PASS — cursor is correctly positioned on the email row"
response, not an assumption.

## Failures

**One real, expected safety abort occurred** (see Foreground Window
Safety above) — not a bug, the system working as designed. Recovery
process, done transparently and only on explicit human instruction:

1. `--move` aborted (foreground was VS Code, not Outlook) and wrote
   `result: "ABORTED"` to the pending state file.
2. The human confirmed Outlook was foreground again and explicitly asked
   to retry using the *same already-approved coordinate*, with no new
   Gemini call.
3. Since the executor's `--move` command structurally refuses to act on
   any pending state that isn't `"PENDING"` (a deliberate guard against
   silently retrying after an abort), the pending file's `result` field
   was manually reset from `"ABORTED"` back to `"PENDING"` — a small,
   explicit, human-directed action, logged in the result's `notes` field
   verbatim, not silently done.
4. `--move` was re-run, foreground re-checked fresh (now Outlook), and
   the movement succeeded using the exact same coordinate the human had
   already approved.

No coordinate was ever moved without both (a) a passing foreground check
at the moment of the move and (b) the human having already approved that
specific coordinate.

## Latency / Cost

| target | vision latency | vision calls | input tokens | output tokens | cost |
|---|---|---|---|---|---|
| email_row | 10,701.4 ms | 2 (1 transient network failure + 1 success) | 1,327 | 68 | $0.00125 |
| reply | 49,735.1 ms | 1 (no retry needed) | 1,315 | 72 | $0.001256 |
| reply_editor | 8,952.5 ms | 1 (no retry needed) | 1,321 | 69 | $0.001249 |

**Totals across all 3 targets:** 3,963 input tokens, 209 output tokens,
**$0.003755** total cost, 4 vision calls (1 retry across all 3 tests).

Two data points — not averaged or treated as representative of general
latency yet. `reply`'s much higher latency (49.7s vs 10.7s) is a real,
notable measurement, not an error — no timeout occurred at the
30–60s configured limit.

## Safety Limitations

- **All 3 planned move-only targets are tested (3/3 PASS)** — but this
  is still a single successful run per target, not a repeated-trial
  reliability measurement. Send remains structurally excluded, not merely
  "not yet tested" — it was never a candidate target in this stage.
- **The foreground-abort-then-manual-reset flow, while transparent and
  human-directed, is a manual JSON edit** — not yet a first-class CLI
  command (e.g. a `--retry` flag). This works for a slow, heavily
  human-supervised R&D pace but would need a cleaner mechanism before any
  less-supervised execution mode.
- **Screen resolution and Outlook window position could differ** between
  machines/sessions — this test only validates the current
  1920×1080/125%-scaling environment established since RND-001.
- **One sample per target.** 3/3 PASS on the first attempt for each does
  not establish statistical reliability across repeated trials —
  RND-006A's purpose is a safety/mechanism proof, not an accuracy
  benchmark (that already exists, separately, in
  RND-005/RND-005A/RND-005B). `reply_editor`'s clean pass is also
  partially explained by it being by far the largest target (1044×166px)
  — a large margin for error that `reply` and `email_row` don't have.
- **The foreground-abort-then-manual-reset flow** used once for
  `email_row` remains a manual JSON edit, not a first-class CLI command —
  noted again here since it was exercised for real in this run.

## Completion Criteria Check

Per the stated RND-006A completion criteria:

- ✅ Live fresh-screen grounding works (3/3 fresh captures, 3/3 successful Gemini calls)
- ✅ Normalized conversion is applied (every coordinate passed through `normalize_1000_to_pixels`, no raw value used directly)
- ✅ Foreground checks work (passed cleanly twice, and correctly aborted once under real conditions)
- ✅ Mouse moves safely (0 clicks, `pyautogui.click` structurally unreachable, FAILSAFE untouched)
- ✅ Human verifies target placement (all 3 explicitly human-confirmed, not assumed)
- ✅ No unintended clicks occurred
- ✅ Send remains blocked (never a valid `--target` value)
- ✅ Results/docs updated

**All criteria met.**

## Conclusion

The full safe-execution pipeline — fresh capture, foreground check,
live Gemini call, normalized-coordinate conversion, coordinate-bounds
validation, a second pre-move foreground re-check, move-only cursor
movement, and mandatory human visual verification — worked correctly for
all three targets (`email_row`, `reply`, `reply_editor` — **3/3 PASS**),
including correctly aborting once under real changing conditions rather
than moving blindly. No click occurred at any point, anywhere, in this
stage. Send remains fully excluded from what this executor can even be
asked to do.

## Next Step

Per the completion criteria, RND-006A is complete and **RND-006B — Safe
Click Execution** may now be proposed as the next stage (testing: click
email row → verify email opens; click Reply → verify editor opens; click
reply_editor — still no Send). **RND-006B is not started automatically**
— it requires the same explicit approval process as every live stage in
this project.
