# 07 — UI Grounding Accuracy (RND-005)

> **AMENDED by RND-005A** (`docs/07A_Coordinate_Calibration.md`): the 0/4
> result below was scored assuming Gemini's coordinates were native
> screenshot pixels. That assumption turned out to be wrong — Gemini's
> `ui_grounding_v1` predictions align with Google's documented 0–1000
> normalized spatial convention. Once converted, all 4 predictions land
> inside their ground-truth boxes (4/4). **The 0/4 result below is kept
> exactly as originally reported — it is correct under the native-pixel
> interpretation — see RND-005A for the corrected interpretation and the
> evidence behind it.**

## Objective

Answer: **given a raw Outlook screenshot and the name of a visible
target, can Gemini return a coordinate that falls inside the
human-annotated clickable target area?**

This is purely a WHERE question. It does not evaluate next-action
reasoning, workflow planning, email understanding, reply generation, or
anything involving mouse control — no click, no keyboard input, no
automation of any kind occurred in this stage.

## Why Grounding Is Measured Separately From Screen Understanding

RND-003's original prompt mixed screen understanding, next-action
reasoning, and coordinate prediction in a single request. That makes it
impossible to know whether a bad coordinate came from bad spatial
perception or from a confused sense of what to do next. RND-005 isolates
the question: `ui_grounding_v1` asks for nothing except a target's
location — no "what should happen next," no action, no target
justification framed around a goal. This is a deliberate methodological
improvement over RND-003, made explicit here rather than left implicit.

## Dataset / Targets

Only the 4 RND-002 targets that have a real, human-verified bounding box.
**N = 4.** This is not a broad grounding benchmark — it measures accuracy
on exactly the controls this POC's dataset happens to have annotated, and
no claim beyond that is made anywhere in this document.

| test_id | target | filename | ground-truth bbox (human-annotated, RND-002) |
|---|---|---|---|
| OUTLOOK-001 | email_row | screen_20260827_152852_448.png | (328,304)-(744,404) |
| OUTLOOK-003 | Reply | screen_20260827_164739_324.png | (866,664)-(973,702) |
| OUTLOOK-004 | reply_editor | screen_20260827_165406_262.png | (788,796)-(1832,962) |
| OUTLOOK-005 | Send | screen_20260827_165946_191.png | (800,1022)-(902,1060) |

All bounding boxes were read directly from `test_cases/outlook/dataset_manifest.json`
at run time — none were retyped or hardcoded from this task's
instructions, per the requirement to avoid transcription drift.

## Human Ground-Truth Method

Unchanged from RND-002: every bounding box above was drawn by a human
researcher dragging a box over the real control in
`rnd/experiments/annotate_ground_truth.py`, then human-confirmed correct
(including one live correction — OUTLOOK-005's Send box was originally
too small and was redrawn during RND-002; see `docs/04_Outlook_Screenshot_Dataset.md`).
No AI was involved in producing any of these four boxes.

## Prompt Version

`ui_grounding_v1` (`rnd/prompts/ui_grounding_v1.txt`) — new file, does not
reuse or extend `screen_understanding_v1`/`v2`. Per case, it fills in only
the actual image width/height and the target name; asks for one point
inside the target's main clickable area; explicitly forbids returning
coordinates outside the image; and does **not** mention next actions,
goals, or workflow context. The same prompt *structure* was used for all
four cases — only screenshot, target name, and dimensions varied. No
prompt was tuned after seeing any result.

## Coordinate System

Identical to every prior stage (RND-001 through RND-004): origin
`(0,0)` at top-left, x increases left→right, y increases top→bottom, in
the screenshot's native 1920×1080 pixel space (no display-scaling
conversion needed — see RND-001's DPI findings).

## PASS/FAIL Methodology

```text
PASS  iff  bbox.x1 <= predicted_x <= bbox.x2  AND  bbox.y1 <= predicted_y <= bbox.y2
```

Inclusive boundaries — a point exactly on the edge of the box counts as
PASS. Approximate/near-distance is never treated as PASS; a coordinate
either lands inside the annotated clickable area or it does not.
Distance-to-bbox and distance-to-bbox-center are computed separately, for
analysis only, and never used to override a FAIL into a PASS.

Implemented in `rnd/metrics/grounding.py` — pure geometry, no AI, no
approximation in the PASS rule itself.

## Per-Target Result

| test_id | target | bbox | bbox size (w×h) | predicted (x,y) | result | confidence | distance to bbox | distance to bbox center |
|---|---|---|---|---|---|---|---|---|
| OUTLOOK-001 | email_row | (328,304)-(744,404) | 416×100 | (280,325) | **FAIL** | 1.00 | 48.0 px | 257.6 px |
| OUTLOOK-003 | Reply | (866,664)-(973,702) | 107×38 | (478,630) | **FAIL** | 0.95 | 389.5 px | 444.7 px |
| OUTLOOK-004 | reply_editor | (788,796)-(1832,962) | 1044×166 | (600,750) | **FAIL** | 0.95 | 193.5 px | 721.6 px |
| OUTLOOK-005 | Send | (800,1022)-(902,1060) | 102×38 | (443,961) | **FAIL** | 0.98 | 362.2 px | 415.8 px |

All 4 predicted coordinates were within the image's 1920×1080 bounds
(structurally valid), and every request succeeded on the first attempt —
0 retries, 0 timeouts. Every failure here is a genuine off-target
prediction, not a malformed or out-of-range response.

## Annotated Visual Comparisons

Generated locally, non-AI (`PIL` drawing only), never sent to Gemini —
ground-truth box in green, predicted point as a red/blue crosshair
(red = FAIL, would be blue for PASS), PASS/FAIL label, test ID, target,
and confidence burned into the top-left corner of each image:

```
screenshots/annotated/rnd005/outlook-001_email_row_grounding.png
screenshots/annotated/rnd005/outlook-003_reply_grounding.png
screenshots/annotated/rnd005/outlook-004_reply_editor_grounding.png
screenshots/annotated/rnd005/outlook-005_send_grounding.png
```

Two were visually inspected directly (not just the numbers) before
trusting this result:

- **OUTLOOK-001**: the predicted point lands just outside the top-left
  corner of the ground-truth box — a genuine 48px near-miss, visually
  confirming the small computed distance.
- **OUTLOOK-003**: the predicted point lands squarely in the *inbox
  message list column* on the left side of the screen — nowhere near the
  Reply button in the reading pane on the right. A dramatic, unambiguous
  miss, visually confirming the large computed distance (389.5px).

## Aggregate Grounding Accuracy

```text
email_row:    FAIL
Reply:        FAIL
reply_editor: FAIL
Send:         FAIL

Overall grounding: 0/4 = 0%   (N = 4)
```

**N = 4.** This result describes grounding on this POC's four annotated
Outlook controls only — it is not a statistically broad measurement of
Gemini's general desktop-grounding capability, and must never be quoted
as one.

## Confidence Analysis

All four failures were **high-confidence failures** (confidence ≥ 0.8,
using the same 0.8 threshold as RND-004):

| test_id | confidence | result |
|---|---|---|
| OUTLOOK-001 | 1.00 | FAIL |
| OUTLOOK-003 | 0.95 | FAIL |
| OUTLOOK-004 | 0.95 | FAIL |
| OUTLOOK-005 | 0.98 | FAIL |

Confidence is self-reported by the model, not calibrated — this result
demonstrates exactly why that caveat matters: Gemini expressed near-total
certainty (0.95–1.00) on every single prediction in this run, and every
one of them was wrong. Self-reported confidence showed **zero
discriminative value** for grounding correctness in this sample.

## Latency

```text
min:      4,368.1 ms
max:      9,493.7 ms
average:  6,461.7 ms
median:   5,992.6 ms  (p50)
```

Notably faster than RND-003/RND-004's latencies (25–49 seconds) — the
coordinate-only prompt produces a much shorter response, plausibly
explaining the lower latency. Only 4 samples; no broader latency claim is
made.

## Cost

```text
total input tokens:   5,348
total output tokens:    253
total cost:           $0.004960
```

From `config/model_pricing.json`'s verified `gemini-3.6-flash` pricing —
real token counts, not estimated.

## Failures

No provider-level failures (0 retries, 0 timeouts, 0 invalid-schema
responses, all coordinates structurally in-bounds). Every failure in this
run is a **grounding failure** — a structurally valid, high-confidence,
wrong coordinate. This is the most important and most concerning category
of failure for a POC aimed at eventually clicking real UI elements.

**A striking observation carried over from RND-003, deliberately not
reused as data:** RND-003's original combined prompt predicted `(478,
630)` for OUTLOOK-003's Reply target. RND-005 made a fresh, independent
API call using the isolated `ui_grounding_v1` prompt — and Gemini
predicted the exact same `(478, 630)`. This was not assumed or carried
forward; it is a new, independent result that happens to exactly
reproduce the old one. That reproducibility suggests Gemini's spatial
misjudgment of this particular screenshot is systematic and consistent,
not a one-off sampling fluke — a materially different (and more
concerning) finding than random noise would be.

## Important Findings

1. **0/4 grounding accuracy, all high-confidence.** Despite RND-004
   showing Gemini correctly *recognizes* these same controls exist
   (application recognition 100%, and it explicitly listed "Send",
   "Reply", etc. as visible controls in its own screen-understanding
   answers), it cannot reliably say *where* they are in pixel space. This
   is the central, actionable finding of RND-005: **Gemini's semantic
   understanding of the Outlook screen substantially outpaces its
   pixel-grounding ability**, at least for `gemini-3.6-flash` on this
   dataset.
2. **A consistent left-bias across all four predictions.** Every
   predicted x-coordinate fell to the left of its target's bounding box
   center (by 258–722px). This is a directional pattern across 4
   independent single-target calls, not scattered noise — worth treating
   as a hypothesis for RND-006+ planning (e.g., whether a systematic
   correction offset could help, though this POC does not attempt one).
   No confirmed cause is claimed here; a plausible but unverified
   hypothesis is that the model reasons about position in some internal,
   possibly-downscaled coordinate frame that isn't perfectly rescaled
   back to the true 1920×1080 image — this is speculation, not something
   this experiment can directly observe.
3. **The exact-reproduction of RND-003's `(478, 630)` prediction** for
   the same target strengthens finding #2 — it argues for a systematic
   spatial misjudgment specific to this screenshot rather than random
   per-call variance.
4. **Confidence is not a useful FAIL detector here.** 4/4 failures at
   0.95–1.00 confidence means, in this sample, confidence carried no
   information about correctness — a POC-relevant fact for anyone
   tempted to gate automatic clicking on a confidence threshold.

## Limitations

- **N = 4.** Explicitly, repeatedly stated: this is not a statistically
  broad grounding benchmark. A different set of controls, screen
  layouts, or window sizes could show a very different pass rate.
- **Single provider, single model, single prompt version, single run per
  target.** No repeated-sampling variance data exists — whether Gemini
  would produce the same wrong coordinate again on a fresh call (beyond
  the RND-003/RND-005 OUTLOOK-003 reproduction already observed) is not
  established for the other three targets.
- **The left-bias hypothesis is unverified.** It is a pattern observed
  across 4 data points, not a diagnosed root cause.
- **reply_editor's bbox is unusually large** (1044×166px, by far the
  biggest target) yet was still missed by 193.5px — this rules out "the
  target was just too small to hit" as an explanation for that case.
- **No OpenAI/Anthropic comparison** — both remain blocked on billing
  (`docs/05A_Multi_Provider_Readiness.md`); whether this grounding gap is
  Gemini-specific or common across providers is unknown.

## Conclusion

*(As originally written — see the amendment banner at the top of this
document and `docs/07A_Coordinate_Calibration.md` for the corrected
reading.)*

Gemini's UI grounding accuracy on this POC's 4 annotated Outlook controls
is **0/4 (0%)**, with every miss made at high self-reported confidence.
Combined with RND-004's finding that screen *understanding* is strong
(100% app recognition, 80% state understanding), this is the clearest
signal yet in this project: for `gemini-3.6-flash`, knowing an Outlook
control exists and clicking the right pixel are very different
capabilities, and the second one is currently unreliable on this
evidence.

**RND-005A update:** this 0% figure was an artifact of scoring
Gemini's coordinates as native pixels when they were actually normalized
to a 0–1000 scale. Once converted, the same 4 predictions score 4/4
(100%, N=4). See `docs/07A_Coordinate_Calibration.md` for the full
evidence and the corrected conclusion.

## Next Step

*(As originally written — superseded by RND-005A's recommendation; kept
here for traceability, not as the current guidance.)*

**Grounding reliability is not good enough to proceed to
RND-006 — Safe Mouse/Keyboard Execution with predicted coordinates.**
Clicking based on these coordinates would, on this sample, miss 100% of
the time. RND-006 should not begin with live coordinate-driven clicking
until either (a) a larger/more diverse grounding sample changes this
picture, (b) a different provider or model shows materially better
grounding, or (c) some correction/verification strategy (e.g. the
verification-loop planned for RND-007, or a larger annotated-target
dataset) is evaluated first. This conclusion is stated here and left for
the user to decide how to proceed — not overridden or worked around
automatically.
