# 07A — UI Grounding Coordinate System Calibration (RND-005A)

## Objective

RND-005 scored Gemini's four grounding predictions as native screenshot
pixels and got 0/4. This follow-up asks: **were those predictions
actually native pixels, or normalized 0–1000 coordinates that RND-005
misinterpreted?**

This is a local re-analysis only. **No new Gemini API calls were made.**
RND-005's original raw predictions, bounding boxes, and result file
(`results/raw/rnd005_ui_grounding_results.json`) are read, never modified.

## 1. Original RND-005 Interpretation (Preserved, Unchanged)

**Raw values treated as native pixels → 0/4 (0%).** This result stands
exactly as reported in `docs/07_UI_Grounding.md` and is not deleted,
altered, or superseded here — it is the correct result *under that
interpretation*. What changes in this document is which interpretation is
correct, not the arithmetic of RND-005 itself.

## 2. Newly Observed Hypothesis

All four raw `(x, y)` values RND-005 recorded are visually consistent
with points expressed on a 0–1000 scale rather than the image's actual
1920×1080 pixel space — every raw value is well under 1000, and the
pattern (a consistent leftward/upward offset noted in RND-005's
Limitations) is exactly what native-pixel-scale ground truth would look
like if the *predictions* were secretly on a much coarser 0–1000 grid.

## 3. Evidence For the Hypothesis

### 3a. Official Gemini documentation

Google's own Gemini API documentation states, for spatial/bounding-box
output: *"The coordinates, relative to image dimensions, scale to
[0, 1000]"* and instructs developers to *"divide each output coordinate
by 1000, multiply the x-coordinates by the original image width, and
multiply the y-coordinates by the original image height"* to recover
pixel coordinates — origin at top-left, x left→right, y top→bottom (same
convention RND-005's prompt already specified).

Source: [Image understanding | Gemini API | Google AI for Developers](https://ai.google.dev/gemini-api/docs/image-understanding)
(fetched 2026-08-28). This is a documented, first-party Google convention
for Gemini's spatial output generally — not something inferred only from
this project's own data.

**Important caveat, stated honestly:** the documented convention is
specifically worded around Gemini's `box_2d` bounding-box output format
(`[ymin, xmin, ymax, xmax]`, y-before-x ordering). RND-005's prompt did
not request `box_2d` — it requested a simple custom `{"x":..., "y":...}`
point via `ui_grounding_v1`. It is not officially documented that a
custom-named point field necessarily inherits the same 0–1000 scaling.
The evidence below (section 3b) is what actually confirms it applies
here, not the documentation alone.

### 3b. Local conversion evidence (the actual proof)

Applying `pixel = raw / 1000 * image_dimension` (image dimensions read
from the real source PNG files, not hardcoded) to all four raw
predictions and re-checking against the same, unmodified human bounding
boxes:

| Target | Raw | Native Pixel Result | Normalized Converted | Normalized Result |
|---|---|---|---|---|
| email_row | (280,325) | FAIL | (537.6,351.0) | **PASS** |
| Reply | (478,630) | FAIL | (917.76,680.4) | **PASS** |
| reply_editor | (600,750) | FAIL | (1152.0,810.0) | **PASS** |
| Send | (443,961) | FAIL | (850.56,1037.88) | **PASS** |

**Native interpretation: 0/4**
**Normalized 0–1000 interpretation: 4/4**

All four conversions land inside their respective ground-truth bounding
boxes. This is the actual evidence — a clean, unambiguous 0/4 → 4/4 flip
across every single case, using one fixed conversion formula with no
per-case tuning. `x` and `y` were scaled independently, consistently
(`x → × image_width/1000`, `y → × image_height/1000`), matching the
documented convention's description of independent per-axis scaling.

A visual check was also produced (not just the arithmetic) for OUTLOOK-003:
`screenshots/annotated/rnd005a/outlook-003_reply_native_vs_normalized.png`
shows the native interpretation (red) landing in the inbox message list,
and the normalized interpretation (blue) landing almost exactly centered
on the real Reply button.

### 3c. Consistency check across x and y independently

The conversion was not tuned separately per axis or per case — one
formula, applied uniformly, worked for all 4 x-values and all 4 y-values
simultaneously, across bounding boxes of very different sizes and aspect
ratios (from `reply_editor`'s 1044×166px box down to `Send`'s
102×38px box). A coincidental fit that happened to work for 4 independent
(x, y) pairs across boxes of that varied size is unlikely.

### 3d. RND-003's independent Reply prediction, cross-checked

RND-003's original combined prompt (screen understanding + action
reasoning + coordinates, a different prompt from `ui_grounding_v1`)
predicted `(478, 630)` for the same OUTLOOK-003 Reply target. Converting
that value the same way also lands inside the Reply bbox (→ PASS).

**Honesty check, as instructed: this is not independent evidence.** The
RND-003 value is numerically identical to RND-005's own Reply raw
prediction — it is the same number appearing twice, not two different
data points agreeing. It is reported here for completeness and
traceability only, and is explicitly **not** counted toward the 4/4
figure above. Its reproducibility across two differently-worded prompts
is still a real, interesting observation (Gemini's internal spatial
estimate for this exact screenshot+target was stable across prompt
variations) — but it does not add statistical weight to the coordinate-
convention hypothesis.

### 3e. What the model's own response text shows

None of the four raw JSON responses contain any explicit statement about
coordinate normalization — the model was never asked, and did not
volunteer, information about which convention it used. No self-report
evidence exists either way; this gap is exactly why section 5 proposes a
follow-up call that would ask directly.

## 4. Evidence Against the Hypothesis

- The prompt (`ui_grounding_v1`) explicitly instructed *"coordinates must
  be within the {width} x {height} image bounds"* using the real
  1920×1080 values — the model was told to use native pixels and, on this
  evidence, did not.
- Only 4 real data points exist, and one of the four "confirmations"
  (RND-003) is a duplicate, not independent (see 3d) — the genuinely
  independent sample size supporting the hypothesis is 4, not 5.
- No response explicitly confirms the convention (see 3e) — the
  conclusion rests entirely on geometric fit, not stated model behavior.

On balance, the geometric evidence (3b) is strong and unambiguous, but it
remains inferred from fit rather than confirmed by the model directly
stating its convention or by a documented guarantee that custom field
names inherit Gemini's `box_2d` scaling convention.

## 5. Whether RND-005's Conclusion Needs Amending

**Yes — with both interpretations reported side by side, not one
replacing the other:**

> Gemini returned spatial predictions that failed when interpreted as
> native pixels but aligned with the human ground truth when interpreted
> as 0–1000 normalized coordinates.

RND-005's original "0/4 under native-pixel interpretation" result is
**preserved as accurate under that interpretation** — the tooling and
scoring in RND-005 were correct; the assumption about what convention
Gemini's output used was not verified before scoring, and this is the
verification. RND-006 planning should proceed on the assumption that
**4/4 (N=4) is the operative grounding result once coordinates are
correctly converted**, while treating the coordinate-convention question
as strongly-evidenced but not 100% certain (see section 6).

## 6. Proposed Follow-Up Experiment (NOT Executed)

To move from "strongly evidenced by geometric fit" to "directly
confirmed," one controlled additional API call is proposed:

**RND-005B (proposed):** Re-run `ui_grounding_v1` — or a lightly modified
version that also asks Gemini to state, in the `reason` field, whether
its returned x/y are native pixel coordinates or normalized to some other
scale — against at least one of the four existing targets (a fresh,
independent call, not reusing any stored value) and confirm:
(a) the new raw value again requires 0–1000 normalization to land inside
the same, unchanged bbox, and (b) whether the model's own stated
reasoning ever references a coordinate convention. This would supply the
first genuinely independent confirmation (unlike the RND-003 duplicate)
and the first direct model self-report on this question.

**This call has not been made.** It requires explicit approval before
execution, per the same privacy-approval requirement as every other live
API stage in this project.

## Tests

`tests/test_coordinate_calibration.py` (8 tests, all passing) — pure math,
no network:
- `normalize_1000_to_pixels(0,0,...)` → `(0,0)`
- `normalize_1000_to_pixels(1000,1000,...)` → image width/height boundary
- `normalize_1000_to_pixels(500,500,...)` → image center
- x-scaling and y-scaling independence
- non-square image scaling correctness
- bbox evaluation after conversion, using the real RND-005 Reply and Send
  data (native → FAIL, normalized → PASS), reusing `point_in_bbox` from
  `rnd/metrics/grounding.py` unchanged

## Files

```
rnd/metrics/coordinate_calibration.py           normalize_1000_to_pixels() — pure function
rnd/experiments/rnd005a_coordinate_calibration.py   local-only analysis, 0 new API calls
tests/test_coordinate_calibration.py            8 unit tests
results/raw/rnd005a_coordinate_calibration_results.json   full per-case + RND-003 cross-check data
results/reports/rnd005a_coordinate_calibration_summary.md
screenshots/annotated/rnd005a/outlook-003_reply_native_vs_normalized.png   visual confirmation
```

`results/raw/rnd005_ui_grounding_results.json` (RND-005's original file)
was read, never written to, by this experiment.

## Conclusion

The hypothesis is well-supported: Gemini's `ui_grounding_v1` coordinate
predictions align with the documented Gemini 0–1000 normalized spatial
convention, not native screenshot pixels. Under that interpretation,
grounding accuracy on this dataset is **4/4 (100%, N=4)**, not 0/4.
RND-005's original tooling and native-pixel scoring were correct given
the assumption in place at the time; the assumption itself was the gap,
now closed with strong (though not 100%-certain-without-a-fresh-call)
evidence.

## Recommendation

Before RND-006 (safe mouse/keyboard execution) uses any Gemini-predicted
coordinate, the grounding pipeline must apply the 0–1000-to-pixel
conversion (`rnd/metrics/coordinate_calibration.py::normalize_1000_to_pixels`)
to every coordinate Gemini returns. Ideally, run the proposed RND-005B
follow-up first (section 6) for direct confirmation via a fresh,
independent call before treating this as fully settled — but the current
evidence is strong enough that RND-006 should not proceed by continuing
to treat Gemini's raw output as native pixels.
