# 04 — Outlook Screenshot Dataset (RND-002)

## Objective

Answer: **Can we create a controlled, reproducible Outlook visual test
dataset with reliable human-verified ground truth that can later be used to
objectively measure Vision AI screen understanding and UI grounding
accuracy?**

The output of this stage is **not** an AI result — it is the evaluation
dataset every later Vision AI experiment (RND-003 onward) will be measured
against. No AI model may generate or influence any part of this dataset's
ground truth.

## What Was Built

```
test_cases/outlook/dataset_manifest.json      Dataset manifest — 15 planned cases (OUTLOOK-001..015)
test_cases/outlook/test_emails.json           3 controlled synthetic test emails (EMAIL-A/B/C)
rnd/models/dataset_manifest.py                Pydantic schema: BoundingBox, Target, DatasetCase, DatasetManifest,
                                               controlled state/action vocabularies
rnd/experiments/annotate_ground_truth.py      Human-only tkinter tool: draw boxes, label them, save to manifest
rnd/experiments/validate_outlook_dataset.py   Dataset validator (schema + file + bbox checks)
rnd/experiments/generate_annotated_previews.py  Draws ground truth onto copies in screenshots/annotated/
rnd/experiments/run_rnd002_summary.py         Writes results/raw/rnd002_dataset_results.json
rnd/experiments/capture_with_foreground_check.py  Capture that aborts unless Outlook is confirmed foreground (see Live Capture Workflow)
rnd/experiments/mark_case_captured.py         Marks a case captured with 0 targets, for states needing no bbox annotation
tests/test_dataset_validation.py              10 unit tests using synthetic fixtures (no real Outlook screenshots needed)
results/raw/rnd002_dataset_results.json       Actual current results (see below)
results/raw/rnd002_invalid_attempts.json      Log of rejected capture attempts, preserved for traceability
screenshots/raw/invalid/                      Rejected screenshots, moved out of the active dataset pool
```

## Environment Check (RND-002 §3 — actual findings)

This check was performed by reading installed-program registry entries,
enumerating Store packages, and checking running processes — no COM, no
Graph, no inspection of email content. No application was launched by the
agent.

| Item | Finding |
|---|---|
| Classic desktop Outlook (`OUTLOOK.EXE`) | **Not installed.** No file found under any Office install root. |
| Installed Office edition | Microsoft Office Home and Student 2019 — this edition does not include Outlook (only Word/Excel/PowerPoint/OneNote). |
| "Outlook for Windows" (new, Store-distributed app) | **Installed** — `Microsoft.OutlookForWindows` v1.2026.818.100. This is genuinely Microsoft Outlook: a native windowed app with its own title bar/taskbar presence, not a browser tab, so it satisfies the project's "Outlook, not browser automation" scope. |
| Running at check time | **Not running**, not signed in to any mailbox. |
| Resolution | 1920 × 1080 (per RND-001) |
| Windows scaling | 125% (per RND-001) |
| Monitor count | 1 (per RND-001) |

**Conclusion of this check:** Outlook *is* available on this machine (the
new Outlook for Windows app), but no mailbox is currently open in it. RND-002
cannot capture real screenshots until a human launches it, signs in to a
test/developer mailbox, and manually navigates the required states — this
is by design (§20 of the RND-002 spec: "The researcher/user may open
Outlook manually").

## Controlled Mail/Test Data

Three synthetic, non-confidential test emails were prepared in
`test_cases/outlook/test_emails.json`, to be manually created in a test
mailbox before capture (no email-sending API/automation was used to create
them):

| ID | Purpose | Used for |
|---|---|---|
| EMAIL-A | Short body, single clear request | OUTLOOK-002/003/004/005/010 |
| EMAIL-B | Long body, multiple paragraphs, a date, a requested action | OUTLOOK-009 |
| EMAIL-C | Different sender/subject than EMAIL-A | OUTLOOK-008 |

No real client, customer, HR, financial, or otherwise confidential content
is used anywhere in this dataset.

## Screenshot States

| Test ID | State | Purpose | Captured / Planned |
|---|---|---|---|
| OUTLOOK-001 | inbox | Inbox normal, message list visible | **Captured** (email_row annotated) |
| OUTLOOK-002 | email_open | Existing email selected/open | **Captured** (subject: "MailFlow POC Test Email") |
| OUTLOOK-003 | email_open | Reply control clearly visible | **Captured** (Reply annotated) |
| OUTLOOK-004 | reply_editor_open | Reply editor open | **Captured** (reply_editor annotated) |
| OUTLOOK-005 | reply_editor_open | Send control visible | **Captured** (Send annotated) |
| OUTLOOK-006 | inbox | Outlook maximized | **Captured** |
| OUTLOOK-007 | inbox | Outlook resized | **Captured** |
| OUTLOOK-008 | email_open | Different existing email | **Captured** (subject: "Mail for project"; attempt 1 rejected — HR-sensitive) |
| OUTLOOK-009 | email_open | Long email body | **Captured** (subject: "Important Update Regarding Current Work and Next Steps") |
| OUTLOOK-010 | email_open | Short email body | **Captured** (subject: "Mail for project" — same real email as OUTLOOK-008, intentionally reused) |
| OUTLOOK-011 | dialog_or_overlay | Popup/notification, only if it occurs naturally | **Skipped** (none available — valid outcome) |
| OUTLOOK-012 | email_open | OPTIONAL — Reply All + Reply both visible | Planned |
| OUTLOOK-013 | email_open | OPTIONAL — Conversation/thread view | Planned |
| OUTLOOK-014 | email_open | OPTIONAL — Scrollable long body | Planned |
| OUTLOOK-015 | reply_editor_open | OPTIONAL — Reply editor with draft text present | Planned |

**None of these have been captured yet** — see Actual Results below.

## Ground Truth

Ground truth is entirely human-produced and stored per-case in the
manifest:

- **State labels** — one of a fixed, small vocabulary (`inbox`,
  `email_open`, `reply_editor_open`, `dialog_or_overlay`, etc. — see
  `rnd/models/dataset_manifest.py::STATE_VOCABULARY`).
- **Action labels** — one of a fixed vocabulary (`click_reply`,
  `click_send`, `select_existing_email`, etc. —
  `ACTION_VOCABULARY`).
- **Bounding boxes** — `{x1, y1, x2, y2}` in original screenshot pixel
  coordinates (per the RND-001 coordinate system), one per annotated
  target (`Reply`, `Reply All`, `Forward`, the reply editor, `Send`, an
  email row).

No AI model writes to, reads for calibration, or otherwise influences this
data.

## Annotation Method

`rnd/experiments/annotate_ground_truth.py` is a tkinter GUI meant to be run
**by a human** on this machine, after a screenshot has already been taken:

1. Researcher captures a screenshot manually navigating Outlook to the
   target state, using `rnd/capture/screen_capture.py` (RND-001 module).
2. Researcher runs the annotation tool against that file, pointing it at
   the `test_id` it corresponds to.
3. Researcher left-drags a box directly on the displayed image over the
   real target (e.g., the actual Reply button), presses **Enter**, and
   types a name (`Reply`) and type (`button`).
4. Repeats for every relevant target in that screenshot, then presses **S**
   to save.
5. The tool converts the on-screen (possibly downscaled-for-display) box
   back to original screenshot pixel coordinates before saving, and marks
   the case `"status": "captured"` in the manifest with the recorded image
   dimensions.

No OCR, no object detection, no automatic control location is used
anywhere in this tool — every coordinate comes from a human's mouse drag
over the actual pixels.

## Live Capture Workflow (added after OUTLOOK-001 attempt 1)

OUTLOOK-001's first capture attempt (2026-08-27T15:20:14) was taken while
**VS Code**, not Outlook, was the foreground window — the researcher had
just switched to Outlook and reported readiness, but the capture was
triggered before actually confirming which window was active. The image
was never marked `captured` in the manifest, was moved to
`screenshots/raw/invalid/OUTLOOK-001_attempt1_INVALID_vscode_foreground.png`,
and is logged in `results/raw/rnd002_invalid_attempts.json`. It is excluded
from annotation, dataset validation, Vision AI testing, and any accuracy
calculation.

To prevent a repeat, every capture from OUTLOOK-001 onward now follows:

```
Human names the target state
        ↓
Human manually switches to Outlook
        ↓
Human says "ready"
        ↓
rnd/experiments/capture_with_foreground_check.py waits 2-3s, then reads the
actual Windows foreground window title (read-only Win32 API call — no
window is moved, focused, or controlled)
        ↓
If the title doesn't look like Outlook → ABORT, no screenshot taken
        ↓
If it does → capture + file/dimension validation
        ↓
Human visually confirms the resulting image is the correct Outlook state
        ↓
Only then is the case marked "captured" in the manifest (via the
annotation tool, which sets status/filename/screen together on save)
```

## How To Capture Additional Cases

```bash
python rnd/experiments/capture_with_foreground_check.py --test-id OUTLOOK-001 --delay 2.5
```
(Navigate Outlook to the target state and confirm it's in the foreground
before running this — see Live Capture Workflow above. Falls back to the
plain `rnd/capture/screen_capture.py` only for non-Outlook, non-dataset
captures, e.g. RND-001-style environment checks.)

## How To Annotate

```bash
python rnd/experiments/annotate_ground_truth.py --test-id OUTLOOK-003 --screenshot screenshots/raw/<filename>.png
```

## How To Validate Dataset

```bash
python rnd/experiments/validate_outlook_dataset.py
```

Generate visual verification copies (only meaningful once cases are
captured and annotated):

```bash
python rnd/experiments/generate_annotated_previews.py
```

Regenerate the results summary:

```bash
python rnd/experiments/run_rnd002_summary.py
```

## Configuration / Properties

| Property | Value |
|---|---|
| Screen resolution | 1920 × 1080 |
| Windows scaling | 125% |
| Raw directory | `screenshots/raw/` (git-ignored) |
| Annotated directory | `screenshots/annotated/` (git-ignored) |
| Manifest location | `test_cases/outlook/dataset_manifest.json` |
| Test email definitions | `test_cases/outlook/test_emails.json` |

## Actual Results

Source: `results/raw/rnd002_dataset_results.json`, generated 2026-08-27.

```text
total_planned_cases: 15
captured_cases: 10   (OUTLOOK-001..OUTLOOK-010)
skipped_cases: 1   (OUTLOOK-011)
still_planned_cases: 4
targets_annotated: 4  (email_row on OUTLOOK-001, Reply on OUTLOOK-003, reply_editor on OUTLOOK-004, Send on OUTLOOK-005)
states_covered: ["email_open", "inbox", "reply_editor_open"]
validation_passed: true
```

**OUTLOOK-001 — real, human-verified result:**

- File: `screenshots/raw/screen_20260827_152852_448.png`
- Captured at 2026-08-27T15:28:52, foreground window confirmed `'Mail - Yash Dhanraj - Outlook'` at capture time via `capture_with_foreground_check.py`
- Visually confirmed by the researcher as the correct Inbox screenshot before being recorded
- Ground truth: one `email_row` target (type `row`), bbox `(328,304)-(744,404)` in original 1920×1080 pixel coordinates, drawn by the researcher in `annotate_ground_truth.py`
- `validate_outlook_dataset.py`: **PASS** — file exists, correctly under `screenshots/raw/` (not `annotated/`), opens cleanly with Pillow, recorded dimensions (1920×1080) match actual, bounding box lies inside image bounds
- Annotated preview generated: `screenshots/annotated/outlook-001_annotated.png`

**OUTLOOK-002 — real, human-verified result:**

- File: `screenshots/raw/screen_20260827_163644_784.png`
- Captured at 2026-08-27T16:36:44, foreground window confirmed `'Mail - Yash Dhanraj - Outlook'` via `capture_with_foreground_check.py`
- Visually confirmed by the researcher as the correct email-open screenshot before being recorded
- Ground truth: subject **"MailFlow POC Test Email"** — a real pre-existing email in the test mailbox, *not* one of the synthetic EMAIL-A/B/C test emails from `test_cases/outlook/test_emails.json`. The manifest's `email.test_email_id` was left `null` and `email.subject` records the actual observed text, so this case is never confused with the controlled EMAIL-A set. (`EmailInfo.subject` was added to the schema in `rnd/models/dataset_manifest.py` specifically to support this.)
- Reply button human-confirmed visible near the bottom of the opened email pane — recorded as a note; no bounding box drawn here, since Reply's bbox is OUTLOOK-003's job, not OUTLOOK-002's (avoids duplicate/premature annotation)
- Marked captured via the new `rnd/experiments/mark_case_captured.py` helper (0 targets — reuses the same `save_case()` write path as the annotation tool, so captured-case shape stays consistent regardless of which tool wrote it)
- `validate_outlook_dataset.py`: **PASS**

**OUTLOOK-003 — real, human-verified result:**

- File: `screenshots/raw/screen_20260827_164739_324.png`
- Captured at 2026-08-27T16:47:39, foreground window confirmed `'Mail - Yash Dhanraj - Outlook'`
- Human-confirmed: Reply and Forward both visible; Reply All was **not** present (correctly not recorded — no fabricated target)
- Same real "MailFlow POC Test Email" as OUTLOOK-002, not synthetic EMAIL-A — `email.test_email_id` corrected to `null` with `subject` set, matching the OUTLOOK-002 fix
- Ground truth: one `Reply` target (type `button`), bbox `(866,664)-(973,702)` — drawn by the researcher, auto-registered via the `--targets Reply:button` queue
- Forward, though visible, was intentionally **not** annotated this round per explicit human instruction (Reply only, as the primary target for this case) — this is a deliberate scope choice, not a missed step; Forward can be annotated in a later pass if needed
- `validate_outlook_dataset.py`: **PASS**
- Annotated preview: `screenshots/annotated/outlook-003_annotated.png`

**OUTLOOK-004 — real, human-verified result:**

- File: `screenshots/raw/screen_20260827_165406_262.png`
- Captured at 2026-08-27T16:54:06, foreground window confirmed `'Mail - Yash Dhanraj - Outlook'`
- Human-confirmed: reply text-entry area visible after manually clicking Reply
- Same real "MailFlow POC Test Email" as OUTLOOK-002/003 — `email` field corrected accordingly (same EMAIL-A template mislabel found and fixed)
- Ground truth: one `reply_editor` target (type `text_area`), bbox `(788,796)-(1832,962)`
- `validate_outlook_dataset.py`: **PASS**
- Annotated preview: `screenshots/annotated/outlook-004_annotated.png`

**Annotation tool DPI-clipping bug (fixed, not a data problem):** while
annotating OUTLOOK-005, the researcher reported the Send button — visible
at the bottom of the raw screenshot — was not visible in the annotation
window; the lower portion appeared clipped. Root cause: this machine runs
Windows at 125% display scaling (established in RND-001), and a plain Tk
window is not DPI-aware by default. Windows was scaling the *entire
already-laid-out* window up by 1.25x after the fact, so a window tkinter
believed was ~900px tall actually rendered ~1125px tall on screen — pushing
its bottom (where Send was) off the visible screen/behind the taskbar. The
underlying screenshot file itself was never cropped; this was purely a
display bug in the tool. Fixed in `annotate_ground_truth.py`:
- The process now calls `SetProcessDpiAwareness`/`SetProcessDPIAware`
  before creating the Tk window, so tkinter's pixel math matches physical
  screen pixels (same class of fix as the RND-001 DPI findings).
- Display size is computed from the actual screen size minus reserved
  space for the label/title bar/taskbar (`compute_display_scale`), instead
  of a fixed 1600×900 guess, so the full image always fits.
- The window now shows an explicit `Original: WxH | Displayed: WxH | Scale: X.XXXX`
  readout.
- Coordinate conversion was pulled into pure functions
  (`to_original_coords`, `to_original_bbox`) and covered by 6 new unit
  tests in `tests/test_annotate_ground_truth.py` (top-left, center,
  bottom-right, and full-bbox conversion, plus scale computation itself).
- OUTLOOK-005 was reopened in the fixed tool and the researcher visually
  confirmed the full image (including Send) was visible before annotating.

**OUTLOOK-005 — real, human-verified result:**

- File: `screenshots/raw/screen_20260827_165946_191.png`
- Captured at 2026-08-27T16:59:46, foreground window confirmed `'Mail - Yash Dhanraj - Outlook'`
- Human-confirmed: Send button visible, not clicked during capture
- Same real "MailFlow POC Test Email" as OUTLOOK-002/003/004 — `email` field corrected accordingly
- Ground truth: one `Send` target (type `button`), bbox **`(800,1022)-(902,1060)`** (102×38px)
- `validate_outlook_dataset.py`: **PASS**
- Annotated preview: `screenshots/annotated/outlook-005_annotated.png`

**Correction — Send bbox too small (caught by human review, fixed):** the
first saved Send box, `(803,1021)-(815,1033)` (12×12px), was flagged by the
researcher as too small to represent the actual clickable button area —
likely only an icon glyph rather than the full button. The screenshot was
reopened in the annotation tool with `--targets Send:button` again; the
researcher redrew the box around the complete clickable Send button and
saved, which replaced the previous box entirely (the annotation tool always
writes the full current target list on save, so there is exactly one
`Send` target for OUTLOOK-005, not two). The corrected box was re-validated
with `validate_outlook_dataset.py` (**PASS**) and the annotated preview was
regenerated. The undersized box was never used for anything beyond this
manifest entry (no downstream Vision AI or accuracy work exists yet), so no
other file required correction.

**OUTLOOK-006 — real, human-verified result:**

- File: `screenshots/raw/screen_20260827_172436_226.png`
- Captured at 2026-08-27T17:24:36, foreground window confirmed `'Mail - Yash Dhanraj - Outlook'`
- Human-confirmed: Outlook maximized. `window_mode: maximized` per manifest; no bounding box required for this state
- Marked captured via `mark_case_captured.py` (0 targets)
- `validate_outlook_dataset.py`: **PASS**

**OUTLOOK-007 — real, human-verified result:**

- File: `screenshots/raw/screen_20260827_173334_423.png`
- Captured at 2026-08-27T17:33:34, foreground window confirmed `'Mail - Yash Dhanraj - Outlook'`
- Human-confirmed: window visibly resized/smaller than the maximized OUTLOOK-006 capture. `window_mode: resized` per manifest; no bounding box required for this state
- Marked captured via `mark_case_captured.py` (0 targets)
- `validate_outlook_dataset.py`: **PASS**

**OUTLOOK-008 — real, human-verified result:**

- Attempt 1 **rejected**: subject "Joining Letter and Bond Lette[r]" — human reviewer identified likely HR/onboarding-sensitive content and declined to use it, per the dataset privacy guidance in this document. Moved to `screenshots/raw/invalid/OUTLOOK-008_attempt1_INVALID_sensitive_hr_content.png`, logged in `results/raw/rnd002_invalid_attempts.json`, never marked captured. This is the privacy safeguard working as intended.
- Attempt 2 (used): File `screenshots/raw/screen_20260827_174500_164.png`, captured 2026-08-27T17:45:00, foreground confirmed `'Inbox - Yash Dhanraj - Outlook'`
- Human-confirmed: different email than "MailFlow POC Test Email", non-sensitive, subject **"Mail for project"**
- No bounding box required for this state; marked captured via `mark_case_captured.py`
- `validate_outlook_dataset.py`: **PASS**

**OUTLOOK-009 — real, human-verified result:**

- File: `screenshots/raw/screen_20260827_175331_081.png`
- Captured at 2026-08-27T17:53:31, foreground window confirmed `'Inbox - Yash Dhanraj - Outlook'`
- Human-confirmed: long body (multiple paragraphs), non-sensitive, subject **"Important Update Regarding Current Work and Next Steps"**
- Real pre-existing test-mailbox email, not synthetic EMAIL-B
- No bounding box required; marked captured via `mark_case_captured.py`
- `validate_outlook_dataset.py`: **PASS**

**OUTLOOK-010 — real, human-verified result:**

- File: `screenshots/raw/screen_20260827_175843_034.png`
- Captured at 2026-08-27T17:58:43, foreground window confirmed `'Inbox - Yash Dhanraj - Outlook'`
- Human-confirmed: short body, non-sensitive, subject "Mail for project"
- **Important caveat, explicitly flagged by the researcher and recorded here and in the manifest notes:** this is the *same real email* used for OUTLOOK-008 (same account, no other clearly non-sensitive email was available at the time). This is intentional, not a data-entry error. **OUTLOOK-008 and OUTLOOK-010 must not be treated as two independent email content samples** in any later "different examples" evaluation — they only differ in what dataset dimension they're testing (a different-email case vs. a short-body case), not in actual email content.
- No bounding box required; marked captured via `mark_case_captured.py`
- `validate_outlook_dataset.py`: **PASS**

**OUTLOOK-011 — real, human-verified result: SKIPPED (valid outcome, not a failure).**

- Human confirmed no natural popup, dialog, or notification was present in Outlook during the capture session
- Per the RND-002 spec, artificially manipulating Windows into an unsafe state to force a popup is explicitly disallowed — so this was correctly left unattempted
- Manifest: `"status": "skipped"`, `"skip_reason": "No natural popup/notification/overlay available during the capture session; no artificial popup was created."`
- `validate_outlook_dataset.py`: **PASS** (skipped cases are validated for having a `skip_reason`, not for file/image checks)

Tooling correctness was also verified independently of real Outlook data:

- Manifest schema validation: **PASS** (all 15 cases parse cleanly against the Pydantic schema)
- Unit tests (`tests/test_dataset_validation.py` + `tests/test_annotate_ground_truth.py`), using synthetic fixtures only: **21/21 PASS**, covering valid bbox, x1≥x2 rejection, y1≥y2 rejection, duplicate test ID rejection, captured-without-filename rejection, invalid-state rejection, missing-file detection, dimension-mismatch detection, well-formed captured case, out-of-bounds bbox detection, target-queue parsing, and display/original coordinate conversion (top-left, center, bottom-right, full bbox, no-upscale).
- Full project test suite: **25/25 PASS**

## Failures / Skipped Cases

**Invalid attempts:**

```text
OUTLOOK-001 attempt 1:
INVALID — foreground application was VS Code, not Outlook.
Retake required.
```

Full record in `results/raw/rnd002_invalid_attempts.json`. This attempt
was never marked `captured` in the manifest, so no downstream tooling
(validator, annotation, previews) ever treated it as valid data — it is
preserved only as an observation/traceability record, per RND-002 rules.

**Annotation tool UX issue (fixed, not a data problem):** on the first
annotation attempt for OUTLOOK-001, the researcher drew a box correctly,
but pressing Save reported "No targets were annotated." Root cause: the
original tool required drawing a box *and then* pressing Enter to open a
name/type dialog before the box was added to the target list — drawing
alone only staged a "pending" box. This wasn't obvious from the on-screen
state. Fixed by adding an optional `--targets name:type` queue to
`annotate_ground_truth.py`: when a target name is already known (as it is
for every case in this manifest), finishing the drag auto-registers it
immediately, no Enter/dialog required, and the status bar now always shows
`Targets: N` so the count is visible at a glance. The Enter+dialog path
remains available for ad hoc targets beyond the queue (e.g. Reply/Reply
All/Forward all in one screenshot). Covered by
`tests/test_annotate_ground_truth.py`.

**OUTLOOK-005 Send bbox too small (fixed):** see the "Correction — Send
bbox too small" writeup under Actual Results — human review caught a
12×12px box that was really just the icon, not the full clickable button;
redrawn and re-validated.

**Initial environment blocker (resolved by the live session):** before
this live capture session started, classic desktop Outlook was found not
installed on this machine (Office edition is Home & Student 2019, which
excludes Outlook), and the installed "Outlook for Windows" (new) app
was not signed in. That blocker is resolved — the researcher signed in
and ran the full live capture workflow described above.

**OUTLOOK-008 attempt 1 rejected — HR-sensitive content:** see the
OUTLOOK-008 write-up under Actual Results and the Privacy Considerations
section — the mailbox in use turned out to contain a real HR/onboarding
email, which was caught and rejected before annotation.

**OUTLOOK-011 SKIPPED:** no popup/notification/overlay occurred naturally
during the session; per spec, none was artificially created. See the
OUTLOOK-011 write-up under Actual Results — this is a valid, expected
outcome, not a failure.

## Privacy Considerations

- Raw screenshots (`screenshots/raw/`) and annotated copies
  (`screenshots/annotated/`) are excluded from Git via `.gitignore`
  (`screenshots/raw/*` and `screenshots/annotated/*`, both keeping only a
  `.gitkeep`) — verified present and correct in `.gitignore`.
- All planned test email content is synthetic (see `test_cases/outlook/test_emails.json`)
  — no real client/customer/financial/HR data.
- When real capture happens, the mailbox used must be a test/developer
  mailbox, not a real business inbox, to avoid exposing confidential
  content in locally-stored screenshots.
- **This safeguard was exercised for real**, not just theoretical: during
  live capture, the mailbox in use turned out to contain a real email
  ("Joining Letter and Bond Lette[r]") that looked HR/onboarding-sensitive.
  It was caught before annotation, rejected, moved to
  `screenshots/raw/invalid/`, and logged in
  `results/raw/rnd002_invalid_attempts.json` rather than used for
  OUTLOOK-008 — see that case's write-up under Actual Results.

## Limitations

- Only one Windows scaling configuration (125%) and one monitor have been
  characterized (inherited from RND-001) — the dataset is only
  representative of this single environment; grounding results measured
  against it should not be assumed to generalize to other scaling/monitor
  setups without re-validation.
- Only the "Outlook for Windows" (new) client was used — classic desktop
  Outlook is not installed on this machine (Office Home & Student 2019
  excludes it), so the dataset does not cover the classic Outlook UI.
- Real, pre-existing mailbox content was used for most email-open states
  (OUTLOOK-002/003/004/005/008/009/010) rather than the originally-planned
  synthetic EMAIL-A/B/C set (`test_cases/outlook/test_emails.json`), which
  remains unused. This happened because it was more practical to work with
  real mailbox content already present than to compose new synthetic
  emails mid-session — every such case was human-reviewed for
  sensitivity before being recorded (see Privacy Considerations), but the
  dataset's email *content* is therefore less tightly controlled than the
  original synthetic-email design intended.
- OUTLOOK-010 intentionally reuses the exact same email as OUTLOOK-008 —
  flagged in both the manifest and this document, but this means the
  dataset has one fewer independent email-content sample than its 15 case
  IDs might suggest.
- Only 4 of the ~10 grounding-relevant targets have annotated bounding
  boxes (`email_row`, `Reply`, `reply_editor`, `Send`) — `Reply All` and
  `Forward` were visible in OUTLOOK-003 but intentionally left
  unannotated (human instruction: Reply only, as the primary target).
- The four optional cases (OUTLOOK-012 Reply+ReplyAll together,
  OUTLOOK-013 conversation view, OUTLOOK-014 scrollable body,
  OUTLOOK-015 reply editor with draft text) remain `planned` — not
  attempted, by deliberate scope decision to stop after the required set.
- OUTLOOK-011 is `skipped`, not `captured` — the dataset has zero
  popup/overlay/dialog examples; if that state matters to a later
  experiment, it will need to be captured opportunistically if one ever
  occurs naturally.

## Conclusion

**Yes** — the required portion of the dataset (OUTLOOK-001 through
OUTLOOK-011) is complete and reliable enough to begin RND-003. All 10
required-and-captured cases are real, human-confirmed-before-recording,
and independently validated by `validate_outlook_dataset.py` (file
exists, correct directory, opens cleanly, dimensions match, all bounding
boxes inside image bounds). OUTLOOK-011 is a correctly-recorded skip, not
a gap. Two real issues surfaced and were fixed during capture (an
annotation-tool DPI-clipping bug, and an undersized Send bounding box),
and one privacy safeguard was exercised for real (an HR-sensitive email
was caught and rejected before use) — all documented above with full
traceability. The dataset's limitations (single environment, one Outlook
client, reused/real email content, only 4 annotated targets, no popup
example) are known and stated above, not hidden, and should be kept in
mind when interpreting any RND-003+ accuracy numbers measured against it.

## Next Step

RND-002 is **Complete** for its required scope. Proceed to
**RND-003 — First Vision Provider Integration**: connect one Vision AI
provider, run it against this dataset's raw screenshots (never the
annotated copies), and record the first real model response with full
latency/token/cost logging, per `docs/02_RND_Methodology.md`. The four
optional cases (OUTLOOK-012–015) remain available to capture later if a
specific later experiment needs them — see Limitations above.
