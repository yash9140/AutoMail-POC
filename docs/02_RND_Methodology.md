# 02 — R&D Methodology

## Objective

Define how this R&D project is actually run, so work stays incremental,
measured, and documented instead of jumping straight to a finished POC.

## Development Loop

Every piece of work in this project follows the same loop:

```
Question → Experiment → Measure → Document → Decision → Next experiment
```

We explicitly do **not** generate the entire POC up front and investigate
problems afterward. Each step below is built, tested, and documented before
the next one starts.

## Implementation Order

| Step | Name | Gate to proceed |
|------|------|------------------|
| 0 | Project Initialization | Structure + core docs exist |
| 1 | Screen Capture | Repeated capture works, documented |
| 2 | Outlook Dataset | Controlled screenshots + ground truth captured |
| 3 | First Vision AI Integration | One provider returns a valid, logged response |
| 4 | Screen Understanding | Accuracy measured across dataset |
| 5 | UI Grounding (no clicks) | Predicted coordinates compared to manual ground truth |
| 6 | Safe Mouse Execution | Click/type only, Send still disabled |
| 7 | Action Verification | Post-action state checked by Vision AI, accuracy measured |
| 8 | Email Understanding | Controlled test emails, human-evaluated |
| 9 | Reply Generation | Controlled test emails, human-evaluated |
| 10 | Reply Draft Automation | Open → Reply → Type, stops before Send |
| 11 | Send Enablement | Controlled mailbox only, after 10 is stable |
| 12 | Full POC | All steps integrated into the target workflow |
| 13 | Model Benchmark | Same dataset run against a 2nd provider/model |
| 14 | Final Findings | `docs/14_Final_RND_Findings.md` completed |

Each step has (or will have) a corresponding `docs/0N_*.md` file written
using the template below, and a row in
[docs/00_RND_INDEX.md](00_RND_INDEX.md) that is updated as soon as the step
produces a result.

## Document Numbering History

The Step 0 plan originally paired Step 2 (Outlook Dataset) and Step 3
(First Vision AI Integration) under a single `04_Vision_Screen_Understanding.md`
doc. When RND-002 (Outlook Screenshot Dataset) turned out to need its own
full document — dataset methodology, ground truth, annotation, validation
are a distinct concern from Vision AI accuracy — it was split out as its
own file and everything after it renumbered by one:

```
03_Screen_Capture.md               (unchanged)
04_Outlook_Screenshot_Dataset.md   (new — RND-002)
05_Vision_Screen_Understanding.md  (RND-003/004; was 04)
06_UI_Grounding.md                 (was 05)
07_Mouse_Keyboard_Execution.md     (was 06)
08_Action_Verification.md          (was 07)
09_Email_Content_Understanding.md  (was 08)
10_Reply_Generation.md             (was 09)
11_End_to_End_Outlook_POC.md       (was 10)
12_Model_Benchmark.md              (was 11)
13_Known_Limitations.md            (was 12)
14_Final_RND_Findings.md           (was 13)
```

No prior documentation content was lost in the renumbering — only file
names/links changed, at RND-002 time (2026-08-27), before docs 05+ existed.

**Second revision (RND-003 time, 2026-08-27):** RND-003 (First Vision
Provider Integration) similarly turned out to warrant its own document
separate from RND-004 (Screen Understanding Accuracy) — provider setup,
API error handling, and the first real call's technical metadata are a
distinct concern from later accuracy measurement. `05_Vision_Screen_Understanding.md`
was replaced by `05_First_Vision_Provider_Integration.md`, and everything
from `06` onward shifted by one again:

```
05_First_Vision_Provider_Integration.md  (new — RND-003; was 05_Vision_Screen_Understanding.md)
06_Vision_Screen_Understanding.md        (RND-004; was 05)
07_UI_Grounding.md                       (was 06)
08_Mouse_Keyboard_Execution.md           (was 07)
09_Action_Verification.md                (was 08)
10_Email_Content_Understanding.md        (was 09)
11_Reply_Generation.md                   (was 10)
12_End_to_End_Outlook_POC.md             (was 11)
13_Model_Benchmark.md                    (was 12)
14_Known_Limitations.md                  (was 13)
15_Final_RND_Findings.md                 (was 14)
```

Again, no prior documentation content was lost — this happened before
docs 06+ existed.

## Documentation Template

Every experiment document must contain, in this order:

1. **Objective** — what is being tested
2. **What Was Built** — files/components created
3. **Technology Used** — libraries, model, API, utilities
4. **Why This Technology Was Used**
5. **How It Works** — step-by-step technical flow
6. **How To Use It** — exact commands to run it
7. **Configuration / Properties** — model, resolution, temperature, timeout,
   confidence threshold, coordinate system, API config
8. **Test Cases** — what exactly was tested
9. **Actual Results** — only real, measured results
10. **Failures / Observations** — what failed or behaved unexpectedly
11. **Limitations** — what limitations were discovered
12. **Conclusion** — what the experiment proved
13. **Next Step**

No document is written speculatively before its experiment runs — results
sections only contain data from real executions.

## Rules That Apply To Every Experiment

- **No hidden shortcuts.** No hardcoded target coordinates, no Windows UI
  Automation / pywinauto / COM used to locate controls for the AI, no
  template matching that already knows the answer. If the Vision AI can't
  find something, that's a recorded failure.
- **No clicking during pure-vision tests.** Steps 4 and 5 only observe and
  predict — the mouse does not move until Step 6, and Send is not enabled
  until Step 11.
- **Ground truth is independent.** Bounding boxes and expected states used
  to grade the AI are manually annotated by a human, never generated by
  asking the same (or any) AI to self-grade.
- **No fabricated metrics.** Accuracy is always `correct / total × 100`
  from real test runs. Cost is computed from real token counts and a
  documented pricing table, or marked "unavailable" if it can't be
  computed — never estimated. Latency percentiles are only reported once
  there are enough samples for them to mean something.
- **Failures are data.** Confused buttons, bad coordinates, invalid JSON,
  false-positive verification, etc. are documented, not hidden or retried
  until they disappear.
- **Controlled test data only** for anything committed or shared — no real
  confidential emails, passwords, or API keys in screenshots or results
  (see the screenshot-handling notes to be added in `docs/08` once email
  test cases exist).

## Structured Result Recording

Every experiment that calls a Vision AI provider produces a machine-readable
result (JSON and/or a row in `results/benchmark.csv`) using the fields
defined in the project brief (test_id, provider, model, prompt_version,
goal, screenshot metadata, expected vs detected application/state/target,
expected vs predicted coordinates, target_detected,
coordinate_inside_target, recommended_action, action_correct, confidence,
latency_ms, token counts, estimated_cost, result, failure_reason, notes).
Fields that aren't available from a given provider/response are left absent
— never fabricated.

## Prompt Versioning

Prompts live in `rnd/prompts/` as versioned files (`*_v1.txt`, `*_v2.txt`,
...). A prompt used in a completed benchmark is never edited in place —
changes get a new version file so past results stay reproducible/comparable.

## Model Benchmarking

Once the full experiment pipeline works end-to-end with one provider, the
same dataset and prompts are run against at least one additional
provider/model (Step 13). Comparison is on accuracy, grounding reliability,
verification accuracy, latency, and cost per successful workflow — not
price alone (see the project brief's model-selection criteria).

## Conclusion

This methodology exists to keep the project honest: small, measured,
documented steps, real ground truth, and no shortcuts that would make
Vision AI look more capable than it actually is.

## Next Step

Proceed to [docs/03_Screen_Capture.md](03_Screen_Capture.md) — RND-001,
Screen Capture — the first technical implementation.
