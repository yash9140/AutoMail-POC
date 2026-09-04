# 05 — First Vision Provider Integration (RND-003)

## Objective

Answer: **Can we reliably send an existing raw Outlook screenshot to a
Vision AI model, receive a validated structured response, and record the
complete technical metadata required for later benchmarking?**

This stage is not a Vision AI accuracy benchmark. It only proves the
technical pipeline — image in, structured/validated result out, with
full latency/usage/cost metadata — is ready for RND-004 (screen
understanding accuracy) and RND-005 (grounding accuracy).

## Provider

**Google Gemini**, model **`gemini-3.6-flash`**.

## Why Selected

The user chose Gemini as the first provider to integrate. The specific
model went through two real, recorded revisions during this experiment:

1. `gemini-2.5-flash` was the initial choice — the live API call failed
   with HTTP 404: *"This model models/gemini-2.5-flash is no longer
   available to new users. Please update your code to use
   models/gemini-3.6-flash."*
2. `gemini-3.6-flash` (the model the API itself recommended) was then
   configured and used.

Model selection here was driven entirely by availability/configuration —
not by any claim about one model being technically superior.

## Files Created

```
rnd/providers/base.py                      VisionProvider ABC, ProviderCallResult, normalized error hierarchy
rnd/providers/gemini_provider.py            GeminiProvider implementation (google-genai SDK)
rnd/models/vision_result.py                 StructuredVisionResponse (model-output schema), VisionResult (full record),
                                             validate_coordinates_in_bounds()
rnd/prompts/screen_understanding_v1.txt     The prompt sent to the model
rnd/experiments/rnd003_first_vision_call.py Experiment runner (dry-run / --execute)
config/model_pricing.json                   Verified Gemini pricing (gemini-2.5-flash historical, gemini-3.6-flash active)
tests/test_vision_result.py                 8 mocked unit tests — schema/coordinate validation
tests/test_gemini_provider.py               7 mocked unit tests — error normalization, no network calls
results/raw/rnd003_first_provider_result.json      Final real result (see below)
results/raw/provider_responses/*.json              Sanitized raw model responses (no secrets)
```

`requirements.txt` gained `google-genai` (installed: 2.20.0).

## Prompt Version

`screen_understanding_v1` — see `rnd/prompts/screen_understanding_v1.txt`.
The template embeds the goal and the image's actual dimensions (read from
the file, never hardcoded) before being sent. It explicitly instructs the
model not to assume hidden state, not to use outside knowledge of the
mailbox, and not to invent controls — and does not include any
ground-truth labels or bounding boxes.

## Screenshot Used

```text
Test ID:    OUTLOOK-003
Filename:   screen_20260827_164739_324.png
Dimensions: 1920 x 1080
```

Loaded exclusively from `screenshots/raw/` — `rnd003_first_vision_call.py`
hard-refuses (raises `SystemExit`) if given a path under
`screenshots/annotated/`, since that would visually leak the ground-truth
Reply bounding box to the model. This is enforced in code
(`resolve_image_path()`), not just by convention.

Email content shown in the screenshot ("MailFlow POC Test Email") was
already human-reviewed and confirmed non-sensitive during RND-002.

## Goal

```text
Reply to the currently opened email.
```

Exactly as sent — no ground-truth coordinates or button names were
revealed to the model anywhere in the prompt or goal text.

## Request Flow

```
screenshots/raw/screen_20260827_164739_324.png
        ↓
rnd/experiments/rnd003_first_vision_call.py
   - resolve_image_path()   (raw/ only, never annotated/)
   - build_prompt()          (fills goal + actual image dimensions)
        ↓
rnd/providers/gemini_provider.py :: GeminiProvider.analyze_screen()
   - google-genai client.models.generate_content(image + prompt, JSON mode)
   - normalizes SDK exceptions → VisionProviderError subclasses
        ↓
StructuredVisionResponse.model_validate(parsed_json)   (pydantic schema check)
        ↓
validate_coordinates_in_bounds(x, y, width, height)     (structural sanity only —
                                                          NOT a grounding-accuracy check)
        ↓
VisionResult(...) built and written to
results/raw/rnd003_first_provider_result.json
```

## Configuration

| Property | Value |
|---|---|
| Provider | gemini |
| Model | gemini-3.6-flash |
| Request timeout | 60s (raised from an initial 30s after two real HTTP 504 timeouts — see Problems) |
| Max attempts | 2 (1 retry) |
| Temperature | 0 |
| Response mode | `response_mime_type="application/json"` (structured JSON mode) |
| Prompt version | screen_understanding_v1 |

## Actual Response

```json
{
  "application": "Microsoft Outlook",
  "screen_state": "Email viewing pane open with an email selected",
  "screen_description": "Outlook desktop view showing the inbox folder list, email message list, and the selected email 'MailFlow POC Test Email' displayed in the reading pane.",
  "visible_controls": [
    "Ribbon bar", "Inbox list", "Mail reading pane",
    "Reply button (reading pane)", "Forward button (reading pane)",
    "Reply button (ribbon)", "Reply all button (ribbon)"
  ],
  "recommended_action": { "action": "click", "target": "Reply button", "x": 478, "y": 630 },
  "confidence": 0.95,
  "reason": "Clicking the Reply button at the bottom of the reading pane opens the inline response editor to reply to the currently opened email."
}
```

This is the model's real, unedited output (full copy in
`results/raw/provider_responses/rnd003_OUTLOOK-003_2026-08-27T18-57-15.407505.json`).
Schema validation: **PASS**. Coordinates `(478, 630)` are within the
1920×1080 image bounds: **PASS**.

**Not evaluated here (by design):** whether `(478, 630)` is actually
inside the ground-truth Reply bounding box `(866,664)-(973,702)` recorded
in RND-002. Interestingly, the model itself listed *two* distinct Reply
controls ("Reply button (reading pane)" and "Reply button (ribbon)") and
picked the reading-pane one — a real, descriptive observation worth
carrying into RND-005's grounding-accuracy measurement, not a pass/fail
verdict to compute now.

## Latency

**49,064 ms** (~49 seconds) for the one successful call. Measured via
`time.perf_counter()` wrapping the real SDK call inside
`GeminiProvider.analyze_screen()` — this is a single real data point, not
an average (RND-011+/benchmarking is where enough samples exist to report
meaningful percentiles).

## Usage

```text
input_tokens:  1452
output_tokens: 224
```

Read directly from `response.usage_metadata` — not estimated.

## Cost

```text
estimated_cost: $0.001929
```

Calculated from `config/model_pricing.json`'s verified Gemini pricing
(fetched from https://ai.google.dev/gemini-api/docs/pricing on
2026-08-27: $0.75/1M input tokens, $3.75/1M output tokens — this is a
promotional rate stated to hold through 2026-12-31, after which it must be
re-verified). Formula: `(1452/1e6 × 0.75) + (224/1e6 × 3.75) = 0.001929`.

## Problems

Three real issues occurred, all handled honestly (no hidden retries, no
fabricated success):

1. **`gemini-2.5-flash` retired (HTTP 404).** First `--execute` attempt
   failed twice identically: *"This model models/gemini-2.5-flash is no
   longer available to new users."* Recorded as a failed
   `VisionResult` (`request_success: false`, `attempt_count: 2`) before
   the model was changed. Nothing was silently retried past the 2-attempt
   cap.
2. **`gemini-3.6-flash` HTTP 504 timeout.** After switching models, the
   next `--execute` attempt failed twice with *"Deadline expired before
   operation could complete"* at the default 30s timeout. Also recorded
   honestly as a failed result before any config change.
3. **Fix: `VISION_REQUEST_TIMEOUT_SECONDS` raised from 30 to 60** in
   `.env`, and the experiment runner was updated to actually read this
   env var (it had been silently using a hardcoded 30s default —
   `rnd003_first_vision_call.py` now passes it through to
   `GeminiProvider(timeout_seconds=...)`). The next attempt succeeded on
   the first try.

Each of these three call attempts is preserved in the git history of
`results/raw/rnd003_first_provider_result.json` only in the sense that
each was written to disk in turn (the file itself holds only the latest
state) — the sequence of events is fully described here and each failure
was surfaced directly to the user in the conversation, not swallowed.

Additionally: the google-genai SDK prints an `AFC` (automatic function
calling) deprecation-style notice to stdout on every call, recommending
`Chat.send_message` over `Models.generate_content`. This is an SDK
informational message, not an error — it did not affect the request and
is not something RND-003 needed to act on, but is noted here for
completeness in case a future SDK version changes this behavior.

## Limitations

- **Self-reported confidence only.** The `0.95` confidence value is the
  model's own stated confidence, not a calibrated probability. It should
  not be interpreted as "95% likely to be correct" — this must be
  explicitly caveated in any later reporting that uses this field.
- **Single data point.** One successful call, one screenshot, one prompt
  version. Latency (49s) is real but should not be treated as
  representative until RND-004+ gathers enough samples for meaningful
  statistics.
- **Only Gemini implemented.** `VisionProvider` is a real abstraction
  (`rnd/providers/base.py`), but OpenAI/Anthropic implementations do not
  exist yet — per RND-003 scope, only the provider with a configured key
  was built.
- **Model availability is volatile.** `gemini-2.5-flash` being retired
  mid-session is a concrete demonstration that model names/availability
  can change without notice; the pricing config explicitly flags the
  `gemini-3.6-flash` promotional rate as needing re-verification after
  2026-12-31.
- **No grounding accuracy computed.** By design — see RND-004/RND-005.

## Conclusion

**Yes** — the first Vision provider integration is technically stable
enough to proceed to screen-understanding evaluation. A real API call
completed successfully, returned schema-valid structured JSON, real
latency/token/cost were recorded (not estimated or guessed), and two real
failure modes were encountered and handled without silent retries or
fabricated results — which is itself useful evidence that the pipeline's
error handling works as designed, not just its happy path.

## Next Step

**RND-004 — Vision Screen Understanding Accuracy.** Run the same pipeline
across the full captured RND-002 dataset (10 states), and measure
application/state/next-action recognition accuracy against the manifest's
human-verified `expected` fields — still no clicking, still no formal
grounding-accuracy scoring (that's RND-005).
