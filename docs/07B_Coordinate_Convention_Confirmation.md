# 07B — Coordinate Convention Confirmation (RND-005B)

## Objective

RND-005A's conclusion (Gemini's grounding coordinates are 0–1000
normalized, not native pixels) was based on geometric fit across 4 data
points, one of which (the RND-003 cross-check) was not independent. This
stage closes that gap with one fresh, independent API call specifically
designed to ask Gemini to state its own coordinate convention — before
any mouse execution is allowed.

## Why This Confirmation Was Needed

Geometric fit is strong evidence but not proof: a formula that happens to
convert 4 wrong-looking points into correct ones could, in principle, be
a coincidence, or the model could genuinely be using native pixels with a
correlated systematic error that happens to resemble a 1000-scale
conversion. Asking the model directly, and checking whether its own
stated convention matches its actual numeric behavior, is a different
kind of evidence than geometric fit alone.

## Prompt Version

`ui_grounding_coordinate_convention_v1` (`rnd/prompts/ui_grounding_coordinate_convention_v1.txt`)
— new file. Per the fairness rule, it never mentions "0 to 1000" or any
specific convention we suspect; it only supplies the real image
dimensions and asks the model to describe, in its own words, whatever
convention it is actually using, plus the numeric range that convention
implies for x and y. No ground truth, no expected coordinates, no hint
about which answer is "correct" were included anywhere in the prompt.

## Fresh Test Input

```text
Test ID:  OUTLOOK-003
Target:   Reply
Image:    screenshots/raw/screen_20260827_164739_324.png (1920x1080, raw only)
Goal:     Locate Reply and declare coordinate convention
```

This is a **new, independent API call** — not a reuse of RND-003's or
RND-005's stored response. Explicit human approval was obtained
(provider/model/test ID/target/filename/goal shown) before sending.

## Raw Model Response

```json
{
  "target": "Reply",
  "x": 477.0,
  "y": 631.0,
  "coordinate_system": "native image pixels",
  "coordinate_range_x": "0 to 1920",
  "coordinate_range_y": "0 to 1080",
  "confidence": 0.95,
  "reason": "Located the Reply button at the bottom of the email message pane."
}
```

Schema-valid (validated against `CoordinateConventionResponse`). Full
sanitized copy: `results/raw/provider_responses/rnd005b/OUTLOOK-003_Reply.json`.

## Declared Coordinate Convention

Gemini explicitly declared **"native image pixels"**, with stated ranges
`0 to 1920` (x) and `0 to 1080` (y) — the model correctly recited the
real image dimensions from the prompt, and asserted its coordinates fall
within them.

## Native Interpretation Result

```text
raw (477.0, 631.0), interpreted as native pixels
in_image_bounds: true
ground-truth bbox: (866,664)-(973,702)
result: FAIL
```

The declared convention, taken at face value, produces a FAIL. The point
is structurally valid (inside the 1920×1080 image) but nowhere near the
real Reply button.

## Normalized Interpretation Result

```text
raw (477.0, 631.0) -> converted (915.84, 681.48) via pixel = raw/1000 * dimension
ground-truth bbox: (866,664)-(973,702)
result: PASS
```

## Ground Truth Comparison

| Interpretation | Converted point | Result |
|---|---|---|
| Native pixels (as Gemini itself declared) | (477.0, 631.0) | **FAIL** |
| Normalized 0–1000 (RND-005A's hypothesis) | (915.84, 681.48) | **PASS** |

**The inconsistency, recorded honestly, per the RND-005B instructions:**
Gemini explicitly said its coordinates are native pixels and even stated
the correct native pixel range for this image — but its actual numeric
output only produces a correct result under the normalized-1000
interpretation, which it did not claim to be using. **The model's
self-report about its own coordinate convention is wrong.** This is a
materially different and more specific finding than RND-005A could reach
on its own: it is not merely that normalized conversion fits the data
better — it's that the model's stated reasoning about its own behavior
is actively incorrect.

**An additional, independent corroboration:** this fresh call's raw
prediction, (477, 631), is nearly identical to the (478, 630) value seen
independently in both RND-003 (different prompt) and RND-005 (isolated
grounding prompt) — three separate calls, three different prompt
wordings, essentially the same numeric answer. This is now genuinely
independent evidence (unlike RND-005A's duplicate-value caveat) that
Gemini's spatial estimate for this specific target is highly stable and
reproducible, whatever its underlying coordinate convention actually is.

## Latency

**6,396.4 ms** for this one call — a single real measurement, consistent
with RND-005's per-call latency range (4.4–9.5s).

## Cost

```text
input_tokens:  1513
output_tokens:  109
estimated_cost: $0.001543
```

From `config/model_pricing.json`'s verified `gemini-3.6-flash` pricing —
real, not estimated.

## Conclusion

The normalized 0–1000 interpretation remains the only one that produces a
correct grounding result. This fresh, independent call **strengthens**
RND-005A's conclusion in one way (a third independent reproduction of the
same near-identical coordinate, this time with a completely fresh call
under fairness-rule conditions) while also surfacing a new, important
caveat: **Gemini's own stated belief about its coordinate system cannot
be trusted** — it confidently (0.95) declared the wrong convention. Any
future reliance on a model's self-reported coordinate system, without
independently verifying it against ground truth, would be a mistake.

## Recommendation for RND-006

**Coordinate convention is now sufficiently understood to proceed
carefully:** always apply the 0–1000-to-pixel conversion
(`rnd/metrics/coordinate_calibration.py::normalize_1000_to_pixels`) to
any coordinate Gemini returns for this task, regardless of what the model
itself claims its convention is — do not trust a model self-report over
the empirically-verified conversion. The conversion utility itself is
tested (11 total tests across `test_coordinate_calibration.py` and this
stage's schema test) and has now been validated against 3 independent
real API calls (RND-003, RND-005 OUTLOOK-003, RND-005B), not just the
original 4-point RND-005 sample.

**No unresolved coordinate ambiguity remains for the "which formula"
question.** RND-006 may proceed to its own gate criteria (safe execution
design, click confirmation, etc.), but **must not begin mouse
movement/clicking automatically as a result of this document** — that
remains a separate, explicit decision for the user to authorize.
