# 10 — Controlled Send + Send Verification (RND-008)

## Objective

Determine whether the POC can safely perform the first real,
externally-visible, irreversible action in this project — clicking
Outlook's Send button — for one approved reply draft, with a real
recipient actually receiving the message. Specifically: (1) verify the
approved draft is genuinely present before Send, (2) visually locate
the Send button, (3) convert Gemini's grounding coordinate correctly,
(4) perform exactly one controlled Send click, (5) wait for Outlook to
stabilize, (6) verify the message was actually sent, (7) obtain human
confirmation. Scope is Send only — no broader end-to-end workflow.

## Why Send Is Treated As High Risk

Every prior stage (RND-001–RND-007B) was reversible or had no
externally-visible effect: screenshots, mouse moves, clicks that only
opened UI already visible on screen, and typed text that stayed in a
draft and was never sent. Send is different in kind, not degree — once
clicked, a real message reaches a real recipient and cannot be
unsent. Accordingly this stage adds safeguards no prior stage needed:
a dedicated, single-purpose target allowlist that contains only
`"Send"` (never reused or widened from RND-006B's `CLICK_ALLOWED_TARGETS`,
which explicitly blocks `"send"`); a hard bounding-box check against
the RND-002 human-annotated ground truth before any click is even
approved; a mandatory human approval gate immediately before the click
that cannot be satisfied by an earlier approval; a hard one-click
limit enforced in code (a second `--execute-send` call raises
`SystemExit` once `send_click_executed` is `True`); and an explicit,
structural ban on any automatic re-send if verification fails.

## Test Email

Subject: **"Mail for project"**, sender: Yash Dhanraj
(`yashdhanraj9140@gmail.com`, as shown in the email's own
header/signature) — the same recurring, repeatedly human-confirmed
non-sensitive test email used across RND-002, RND-006A/B, RND-007A/B.
The approved reply draft was carried forward unchanged from RND-007B's
finalized, PASS result (Attempt 2 Retry 1) — not re-generated.

**Shown to the human before any action, per gate 2:** sender, subject,
body summary, and the approved draft — see below.

```text
Sender:       Yash Dhanraj <yashdhanraj9140@gmail.com>
Subject:      Mail for project
Body summary: The sender, Yash Dhanraj, sends a brief greeting extending well wishes.

Approved draft:
Hi Yash,

Thank you for reaching out. I am doing well and hope you are doing well as well. Please let me know if there is anything I can assist you with.

Best regards,
```

**Human approval (chat):** *"Is this safe test email approved for the
RND-008 send test? yes/no"* → **yes**, recorded via
`--approve-email pass`.

## Human Approvals (all four, in order — none reused for another gate)

1. **Test email approval** — before any action (`--approve-email pass`).
2. **Draft confirmation** — after pre-send vision verification, mandatory
   regardless of the vision result (`--confirm-draft pass`).
3. **Final immediate pre-click approval** — shown fresh immediately
   before the real click, with the exact question *"Proceed with the
   real Send click? yes/no"* (`--approve-send pass`) — this is a
   distinct recorded approval, not a reuse of any earlier "yes."
4. **Post-send human verification** — authoritative over AI
   (`--confirm-send pass`).

## Pre-Send Draft Verification

Foreground checked (`'Inbox - Yash Dhanraj - Outlook'`), reply editor
open confirmed via the same state-check call reused from RND-006B/007B,
fresh screenshot captured, then Gemini's `draft_verification_v1.txt`
prompt (reused verbatim from RND-007B/RND-008's own pre-send gate) compared
the visible text to the approved draft:

```text
expected_draft:  "Hi Yash,\n\nThank you for reaching out. I am doing well and hope you are doing well as well. Please let me know if there is anything I can assist you with.\n\nBest regards,"
detected_draft:  "Hi Yash,\n\nThank you for reaching out. I am doing well and hope you are doing well as well. Please let\nme know if there is anything I can assist you with.\n\nBest regards,"
vision_match:    True (only a line-wrap difference, explicitly tolerated by the prompt)
```

A screenshot was independently inspected directly (not just the Vision
model's report) and confirmed the same. **Human confirmation:**
`--confirm-draft pass`. Per instruction, this gate would still have
required human approval even if `vision_match` had been `False` — the
Vision result never substitutes for it.

## Send Grounding

Fresh screenshot captured, foreground re-confirmed. Gemini was asked to
locate **only** the Send button, via a dedicated target description
(`SEND_GROUNDING_TARGET_DESCRIPTION`) that explicitly excludes the
dropdown/options arrow and every other button (Reply, Reply All,
Forward, Delete, Discard, Archive) — using the same grounding-only
prompt template (`ui_grounding_execution_v1.txt`) reused from
RND-006B/007B, which never asks "what should I do next."

```text
Raw Gemini coordinate:       (443, 954)   [0-1000 normalized]
Converted pixel coordinate:  (851, 1030)
```

## Coordinate Conversion

The confirmed normalized-1000 convention (established in RND-005A,
confirmed in RND-005B, reused unchanged in every stage since) was
applied via the same shared utility, never duplicated:

```text
pixel_x = raw_x / 1000 * image_width   = 443 / 1000 * 1920 = 850.56 → 851
pixel_y = raw_y / 1000 * image_height  = 954 / 1000 * 1080 = 1030.32 → 1030
```

## Send Target Safety

The converted coordinate was checked against the RND-002 human-annotated
ground-truth bounding box for Send (`OUTLOOK-005`):

```text
Ground-truth bbox:      (800,1022)-(902,1060)
Converted coordinate:   (851, 1030)
Inside bbox:            True
Inside screen bounds:   True (screen 1920x1080)
```

Had the converted point fallen outside this box, `cmd_ground_send()`
would have recorded a grounding failure, set `result = "ABORTED"`, and
stopped — never reaching the approval gate. This path is proven by a
mocked test (`test_ground_send_aborts_when_coordinate_outside_known_bbox`)
using a deliberately wrong coordinate, and by a second test confirming
`--approve-send` independently refuses to record `"pass"` for any
out-of-bbox coordinate even if called directly.

## Foreground Checks

Checked and logged at each of the three required points:

```text
foreground_before_capture (verify-draft):   'Inbox - Yash Dhanraj - Outlook'
foreground_before_grounding:                'Inbox - Yash Dhanraj - Outlook'
foreground_before_move:                     'Inbox - Yash Dhanraj - Outlook'
foreground_before_click:                    'Inbox - Yash Dhanraj - Outlook'
```

All four checks passed in this run — no abort was triggered live. The
abort path itself (cursor moved but no click if focus is lost between
move and click; no movement at all if focus is already lost before
move) is proven by two mocked tests, mirroring the same pattern
already proven live in RND-006A/006B/007B.

## Send Execution

```text
human_send_approved:     True  at 2026-08-31T15:37:17.851200
send_click_executed:     True  at 2026-08-31T15:37:46.950314
send_click_result:       PASS
```

Exactly one `pyautogui.click()` call site exists in this module
(structurally proven — `test_module_never_sends_hotkeys_and_click_appears_once_in_source`),
no double-click, no Enter, no Ctrl+Enter, no Alt+S. `send_click_executed`
was persisted to disk immediately after the click, before any
verification step ran. A second `--execute-send` invocation is
structurally refused once `send_click_executed` is `True`
(`test_only_one_send_click_possible`).

## Post-Send Stabilization

```text
Attempt 1 delay: 2.0s (POST_SEND_INITIAL_DELAY_SECONDS)
```

Only one attempt was needed — Gemini verified the send on the first
post-send screenshot, so the second, 1.5s-delayed retry attempt was
never triggered. (The retry path itself — including its different
1.5s delay and the hard 2-attempt ceiling — is proven by three mocked
tests: a successful second attempt, an enforced maximum, and proof
that verification never re-clicks Send under any circumstance.)

## AI Verification

```json
{
  "verified_sent": true,
  "detected_state": "reading pane showing sent reply in email thread, no active composer open",
  "visual_evidence": [
    "The reply composer/editor is no longer open",
    "A status banner stating 'You replied on Mon 8/31/2026 3:37 PM' is visible",
    "The sent reply text is rendered as part of the email thread with standard Reply/Forward options",
    "No Send or Discard draft buttons are visible"
  ],
  "confidence": 1.0
}
```

Classification: **`AI_VERIFIED_FIRST_ATTEMPT`**.

## Human Verification

A direct screenshot inspection (independent of the AI call) confirmed
the same: the "You replied on Mon 8/31/2026 3:37 PM" status banner is
visible, the sent text appears integrated into the thread, the
composer is closed, and only Reply/Reply All/Forward remain. Human
answer to *"Do you confirm that the reply was actually sent? yes/no"*:
**yes**, recorded via `--confirm-send pass`.

## Result Classification

Per instruction, `send_click_result`, `ai_send_verification`, and
`human_send_verification` are kept as three separate fields, never
collapsed:

```text
send_click_result:        PASS
ai_send_verification:     True (AI_VERIFIED_FIRST_ATTEMPT)
human_send_verification:  True

final_classification:     SEND_CONFIRMED
overall result:           PASS
```

This is scenario **A** from the instruction's classification examples
(click PASS, AI PASS, human PASS → `SEND_CONFIRMED`). No divergence
between AI and human occurred in this run to exercise scenarios B–D;
those paths are proven by mocked tests
(`test_ai_fail_human_pass_classification` →
`SEND_SUCCEEDED_AI_FALSE_NEGATIVE`,
`test_human_fail_overrides_ai_pass_to_send_not_confirmed` →
`SEND_NOT_CONFIRMED`) rather than exercised live.

## Latency & Cost

| Call | Latency | Input tokens | Output tokens | Cost |
|---|---|---|---|---|
| Reply-editor-open state check (pre-send) | — (not individually broken out on this model; included in totals) | — | — | — |
| Pre-send draft verification | 9,241.3 ms | 1,417 | 108 | $0.001468 |
| Send grounding | 7,762.9 ms | 1,385 | 119 | $0.001485 |
| Post-send verification (attempt 1) | 53,044.6 ms | 1,421 | 169 | $0.001699 |

**Totals (every real provider call included, per the RND-007B
pre-RND-008 hardening fix — no hidden helper calls):**

```text
Total vision calls:    4
Total input tokens:    5,552
Total output tokens:     521
Total estimated cost:  $0.006118
Total latency:         76,338.65 ms  (~76.3s of API latency; the
                        post-send call's 53s is a single slow outlier,
                        not representative of typical latency)
```

## Failures

None. Every gate passed on the first live attempt: test email approved,
draft verified and confirmed, Send grounded correctly inside the known
bounding box, foreground confirmed at every checkpoint, final approval
given immediately before the click, exactly one Send click executed,
AI verification passed on the first post-send attempt, human
verification agreed. No abort occurred; the abort/failure paths listed
throughout this document were exercised only via mocked tests, not
live.

## Safety Controls (summary)

- Dedicated single-entry target allowlist (`Send` only) — never a reuse
  or widening of any earlier stage's allowlist.
- Hard bounding-box check against RND-002's human-annotated ground
  truth before Send can even be approved.
- Four separately-recorded human approvals, none substitutable for
  another.
- Foreground checked and logged before capture, before grounding,
  before move, and before click.
- One-click hard limit enforced in code, not just by convention.
- No automatic re-send under any verification outcome — structurally
  proven, not just documented.
- `pyautogui.FAILSAFE` left at its default `True` throughout, asserted
  before the click.
- Every real provider call (including the reply-editor-open state
  check) accumulates into the running cost/token/latency totals.

## Limitations

- **Single email, single Send, one live attempt.** This establishes
  that the controlled Send pipeline works correctly once, not that it
  is reliable across repeated sends, different emails, or different
  Outlook window states.
- **No AI/human divergence was exercised live** — scenarios B, C, and D
  of the result-classification scheme are proven only by mocked tests.
- **The reply-editor-open state check's metrics are accumulated into
  the running totals but not individually broken out** on the
  `RND008Result` model (unlike RND-007B, which added a dedicated
  `reply_editor_state_check_metrics` field) — a minor reporting
  granularity gap, not a tracking gap; the totals themselves are
  complete.
- **The post-send verification call took 53 seconds** — well outside
  the typical range seen in earlier stages (usually 5–15s). This did
  not cause any failure here (only one attempt was needed regardless),
  but a slower/more variable network could plausibly exhaust the
  2-attempt ceiling in a future run purely on latency grounds, not on
  the actual send having failed.
- **This is a proof-of-concept single-recipient test**, not validation
  of Send behavior under multi-recipient threads, attachments, or any
  Outlook state more complex than a single already-open reply editor.

## Conclusion

The controlled Send + Send verification pipeline worked correctly and
safely on its first live attempt: a real, previously-approved reply
was sent to a real recipient after four independently-recorded human
approvals, a hard bounding-box safety check, and continuous foreground
validation — with exactly one Send click, no auto-retry, and full
agreement between AI and human verification (`SEND_CONFIRMED`). Every
abort/failure/divergence path this stage is designed to handle was
proven safe via mocked tests even though none was exercised live in
this particular run.

## Next Step

Based on this result, it is reasonable to consider proceeding toward
the **full end-to-end POC run** (open Outlook → read an email →
understand it → reply → type → send → verify, chained together without
stage-by-stage human gating at every micro-step). **Not started
automatically** — requires the same explicit review and approval as
every prior stage, and given Send's irreversibility, likely warrants
its own explicit discussion of what additional safeguards (rate
limiting, a stricter test-email allowlist, or a hard "single send per
session" cap) should carry forward into that stage.
