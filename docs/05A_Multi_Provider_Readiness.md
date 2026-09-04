# 05A — Multi-Provider Readiness (pre-RND-004)

## Objective

Before running RND-004 (screen understanding accuracy across the full
dataset) with more than one provider, answer: **are OpenAI and Anthropic
technically wired up the same way Gemini already is** — same
`VisionProvider` abstraction, same test screenshot, same task semantics,
real error handling, no fabricated results?

This is a readiness check, not a benchmark. No accuracy is measured here,
and RND-003's Gemini result (`docs/05_First_Vision_Provider_Integration.md`,
`results/raw/rnd003_first_provider_result.json`) is left untouched —
this stage only adds two more providers alongside it.

## Providers Tested

Two rounds were run — round 1 with the originally-configured API keys,
round 2 after the user replaced both keys with a new organization that
had "active subscription/billing." Both rounds are preserved in full
(`results/raw/rnd_provider_readiness_{provider}_history.json`); the table
below reflects the latest (round 2) state.

| Provider | Model configured (round 2) | Vision call result (round 2) |
|---|---|---|
| Gemini (RND-003, for comparison) | gemini-3.6-flash | Succeeded (round 1, unchanged) |
| OpenAI | gpt-4o | Failed — billing (`credit_balance_exhausted`) |
| Anthropic | claude-sonnet-4-20250514 (round 1 was claude-opus-4 → claude-sonnet-5) | Failed — billing (credit balance too low, same message as round 1) |

## Models Used

Read from `.env` at runtime (`OPENAI_MODEL`, `ANTHROPIC_MODEL`,
`GEMINI_MODEL`) — never hardcoded. Two real model-configuration issues
were caught and handled without silently substituting a model:

1. **`ANTHROPIC_MODEL` was initially `claude-opus-4`.** Before attempting
   any call, verified pricing (fetched from
   `https://platform.claude.com/docs/en/about-claude/pricing`) showed this
   model listed as *"retired, except on Google Cloud."* This was surfaced
   to the user directly, who chose to update `.env` to `claude-sonnet-5`
   themselves rather than have it changed silently.
2. No such issue arose for OpenAI — `gpt-4o` was confirmed still active
   via `https://developers.openai.com/api/docs/pricing`.

## Configuration

| Property | Value |
|---|---|
| Prompt version | `provider_readiness_v1` (coordinate-free variant of `screen_understanding_v1`) |
| Request timeout | 60s (`VISION_REQUEST_TIMEOUT_SECONDS`) |
| Max attempts | 2 (1 retry) |
| Temperature | 0 for OpenAI/Gemini; **not sent to Anthropic** — see Problems |

## Test Screenshot

Identical across all three providers, for fairness:

```text
Test ID:    OUTLOOK-003
Filename:   screen_20260827_164739_324.png
Dimensions: 1920 x 1080
```

Loaded exclusively from `screenshots/raw/` —
`rnd/experiments/provider_readiness_check.py::resolve_image_path()`
refuses any path under `screenshots/annotated/`, identical to the RND-003
enforcement.

## Goal

```text
Reply to the currently opened email.
```

Identical string sent to all three providers.

## Prompt Semantics

All three providers were asked the same semantic task via
`rnd/prompts/provider_readiness_v1.txt`: identify the application, screen
state, a short description, relevant visible controls, the single best
next action, its target control, a self-reported confidence if available,
and a short reason — explicitly **no pixel coordinates** (that's RND-005's
job). Provider-specific request *syntax* differs (OpenAI's
`response_format={"type": "json_object"}` JSON mode vs. Anthropic's Claude
having no equivalent strict JSON mode, requiring defensive markdown-fence
stripping — see `rnd/providers/anthropic_provider.py`), but the prompt
text and goal sent to every provider are identical. No provider's prompt
was tuned to improve its results.

## Actual Response

**OpenAI:** none — the call never completed (see Problems).

**Anthropic:** none — the call never completed (see Problems).

Neither provider produced a structured response to show. This is the
honest state of the readiness check, not an omission.

## Latency

Not measured for OpenAI or Anthropic — both calls failed before a
response was returned, so `latency_ms` is `null` in both result files (no
value was guessed or left over from a different call).

## Token Usage

`null` for both OpenAI and Anthropic — neither provider returned usage
data because neither request completed.

## Cost

`estimated_cost: null` for both. Verified pricing *is* configured for
both models in `config/model_pricing.json` (gpt-4o: $2.50/$10 per 1M
tokens; claude-sonnet-5: $2/$10 per 1M tokens, both fetched 2026-08-28)
— but with no token usage returned, there is nothing to multiply the
rate by, so cost correctly resolves to `null` rather than a fabricated
number.

## Retries / Errors

**OpenAI — `gpt-4o`:**
```text
attempt_count: 2
error (both attempts, identical): HTTP 429 insufficient_quota —
  "You exceeded your current quota, please check your plan and billing details."
```
This is an OpenAI account billing/quota problem (no usable credits on the
configured API key's account), not a code defect. Correctly normalized to
`RateLimitError` by `OpenAIProvider`.

**Anthropic — `claude-sonnet-5`:**
```text
attempt_count: 2
error (both attempts, identical): HTTP 400 invalid_request_error —
  "Your credit balance is too low to access the Anthropic API."
```
Also a billing problem, not a code defect. Anthropic returns this specific
condition as a 400 rather than a 429, so it was correctly normalized to
the generic `VisionProviderError` (not `RateLimitError`, which is reserved
for actual rate-limit responses) by `AnthropicProvider`.

**A real code bug was also found and fixed before this result:** the first
Anthropic call attempt crashed with an uncaught `TypeError: Messages.create()
got an unexpected keyword argument 'temperature'` — this SDK version's
Claude 5-generation API surface (`anthropic` 1.2.0) has removed the
`temperature` parameter from `Messages.create()` entirely. This was not
assumed in advance; it surfaced from the real call. Fixed by removing the
parameter from `AnthropicProvider.analyze_screen()`, documented inline in
the code. Neither `OpenAIProvider` nor `GeminiProvider` needed a
corresponding fix — their SDKs still accept `temperature` normally.

## Schema Behavior

Neither call reached the point of returning JSON, so
`ReadinessVisionResponse` schema validation was never exercised against a
real OpenAI or Anthropic response in this run. It has been exercised
against synthetic/mocked data — see Tests below — and against Gemini's
real RND-003 response format, which follows the same coordinate-bearing
shape minus x/y.

## Round 2 — New Organization Billing (2026-08-28, same day)

The user replaced both `OPENAI_API_KEY` and `ANTHROPIC_API_KEY` in `.env`
with keys from a new organization, reporting active subscription/billing
on both, and asked for a retest using the identical controlled input
(OUTLOOK-003, same raw screenshot, same goal, same
`provider_readiness_v1` prompt — nothing about the benchmark semantics
changed).

**A real gap was found and fixed first:** the script's result-saving
function overwrote the same fixed filename on every run
(`rnd_provider_readiness_{provider}.json`), which would have silently
erased round 1's billing-failure record the moment round 2 wrote its
result. Fixed by adding an append-only history file per provider
(`..._history.json`) alongside the existing "latest snapshot" file, and
backfilling round 1's exact results into it (reconstructed from the
records already read back earlier in this session, not guessed) before
round 2 ran. Round 1 is fully preserved.

**OpenAI, round 2 — `gpt-4o` (same model as round 1):**
```text
attempt_count: 2
error (both attempts, identical): HTTP 429 —
  "You have no credits remaining. Add credits to continue using the API..."
  code: credit_balance_exhausted
```
Different, more specific error code than round 1's generic
`insufficient_quota` — this new organization's key authenticates
correctly (proving the new key itself is valid) but still has zero
funded credits.

**Anthropic, round 2 — `claude-sonnet-4-20250514`** (user-reconfigured
from round 1's `claude-sonnet-5`, a dated snapshot rather than an alias):
```text
attempt_count: 2
error (both attempts, identical): HTTP 400 invalid_request_error —
  "Your credit balance is too low to access the Anthropic API."
```
Identical message to round 1, despite the new organization and a
different model — strong evidence the new Anthropic org account also has
no funded credits yet, independent of which model is requested.

Verified pricing was added for both round-2 models before either call was
attempted (`gpt-4o` unchanged from round 1; `claude-sonnet-4-20250514`
newly added at $3/$15 per 1M tokens, sourced from the same
platform.claude.com page, flagged as a retired-family dated snapshot
whose actual live availability was to be confirmed by the real call
result — which never got far enough to confirm it, since billing blocked
the request before any model-availability response could occur).

## Limitations

- **Zero real successful calls to OpenAI or Anthropic yet, across two
  full rounds with two different organizations' API keys.** This
  readiness check proves the *code path* is correct (provider
  construction, error normalization, no crashes, honest recording of
  failure) but does not yet prove either provider can complete a request
  and return schema-valid data — that remains unverified until billing is
  resolved and the script is re-run.
- **Anthropic's `temperature=0` was dropped**, not replaced with an
  equivalent. This SDK version has no visible alternative determinism
  control in `Messages.create()`'s signature. This means Anthropic's
  future real calls will run at whatever the model's default sampling
  behavior is, which is a genuine (if minor) semantic difference from
  Gemini/OpenAI's explicit `temperature=0` — worth keeping in mind if
  RND-004+ compares response consistency across providers.
- **Model configuration is volatile**, same finding as RND-003: `claude-opus-4`
  turned out to be retired before any call was attempted (caught via pricing
  docs research this time, not a live failure) — a second real
  demonstration that hardcoding or assuming model availability is unsafe.

## Conclusion

**Still partially ready, after two full rounds.** The multi-provider
abstraction itself is proven correct: `OpenAIProvider` and
`AnthropicProvider` both construct correctly, refuse to run without an
API key, correctly reject annotated screenshots, and correctly normalize
every error condition encountered across both rounds (two distinct quota
error codes, two billing errors, and one real SDK signature mismatch)
into the shared `VisionProviderError` hierarchy — with 21 mocked unit
tests covering this, all still passing (61/61 full suite). But **neither
OpenAI nor Anthropic has completed a single real Vision call across
either organization's keys**, both blocked purely on account billing, not
code, in both rounds. Gemini remains the only provider proven end-to-end
(RND-003).

## Next Step

Not RND-004 yet, per instruction. Once OpenAI and/or Anthropic billing is
actually funded (both organizations tried so far authenticate but report
zero usable credits), re-run:

```bash
python rnd/experiments/provider_readiness_check.py --provider openai --execute
python rnd/experiments/provider_readiness_check.py --provider anthropic --execute
```

to obtain each provider's first real successful call (schema validation,
latency, tokens, and cost). Both rounds' failures remain in
`results/raw/rnd_provider_readiness_{provider}_history.json` for
traceability, so a future successful run will be round 3, not a
replacement of history. RND-004 begins full-dataset testing only once
whichever providers are actually funded and ready are confirmed.
