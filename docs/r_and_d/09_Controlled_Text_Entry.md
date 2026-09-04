# 09 — Controlled Text Entry (RND-007A)

## Objective

Determine whether the POC can safely type a known, fixed sentence into
the already-focused Outlook reply editor and verify that the expected
text appears correctly. Text-entry execution only — no contextual
AI-generated reply content, no Send, no full workflow.

## Safety Controls

- **Outlook foreground check before typing** — the same read-only
  `get_foreground_window_title()` check used throughout RND-002/006A/006B,
  reused, not duplicated. If the check fails, `pyautogui.write` is never
  called (confirmed by a mocked test).
- **Explicit human approval required before typing**, obtained in chat
  before the `--type` invocation — same gate pattern as every prior live
  stage.
- **`pyautogui.FAILSAFE` left at its default `True`**, asserted
  immediately before the write call — never touched anywhere in this
  codebase.
- **Typing uses `pyautogui.write()` only** — no clipboard, no paste.
  Called exactly once per `--type` invocation (unit-tested).
- **Enter, Ctrl+Enter, and Alt+S are never sent anywhere in this
  module** — structurally, not just by convention. Confirmed by a test
  that inspects the module's own source code for these patterns (same
  technique used to prove `pyautogui.click()` was unreachable in
  RND-006A's move-only executor).
- **Send remains fully unreachable** — no click path, no hotkey path, no
  code path of any kind that could interact with Send exists in this
  file.
- **Ctrl+C** works normally as an additional abort path — no special
  signal handling was added or needed.

## Text Used

Fixed for this first test, exactly as specified, never varied mid-run:

```text
This is a controlled POC test reply.
```

(`rnd/models/text_entry.py::FIXED_TEST_TEXT`)

## Execution Flow

```
foreground check
  → pre-typing screenshot (record only)
  → human approval (already obtained in chat)
  → pyautogui.write(FIXED_TEST_TEXT, interval=0.03)   [ONE call, no Enter/Ctrl+Enter/Alt+S/Send]
  → state saved immediately (typing record survives even if a later step fails)
  → wait 1.5s
  → post-typing screenshot
  → Gemini verification call (text_entry_verification_v1, separate from typing)
  → human visual confirmation
  → STOP
```

Split across two invocations (`--type`, `--confirm`) so the human can
review before finalizing — same philosophy as every prior stage.

## Verification Method

A dedicated prompt, `rnd/prompts/text_entry_verification_v1.txt`, shows
the post-typing screenshot to Gemini along with the expected text and
asks it to report what it can actually read in the reply editor, not
assume success. Response schema: `verified`, `detected_text`,
`confidence`, `reason` — no OCR library used (per instruction, only if
"genuinely necessary," and Gemini's own vision reading proved sufficient
for this test).

Per instruction, **three results are recorded as separate fields, never
collapsed**:

- `typing_result` — did the write call itself execute mechanically
  (foreground OK, no exception)
- `vision_verification` — did Gemini's separate verification call report
  the expected text was visible
- `human_verification` — did the human visually confirm

## Actual Result

```text
Expected text:     "This is a controlled POC test reply."
Typed text:         "This is a controlled POC test reply."  (recorded exactly as sent)
Foreground before:  'Mail - Yash Dhanraj - Outlook'

typing_result:       PASS
detected_text:       "This is a controlled POC test reply."
exact_match:          True
vision_verification:  True  (confidence 1.0)
human_verification:   True
overall result:       PASS
```

All three signals agreed in this run — unlike RND-006B Attempt 1, where
human and AI verification diverged. No divergence occurred here to
exercise the "human overrides AI" path, but the mechanism (human
confirmation as the authoritative field for `result`) is the same one
already proven in RND-006B.

## Failures

None. Foreground check passed, single typing action executed cleanly,
verification call succeeded on the first attempt with an exact text
match at full confidence.

## Latency

```text
Verification latency: 5,864.7 ms
```

Single data point. No typing-mechanism latency is meaningful to report
separately — `pyautogui.write` with a 0.03s per-character interval for a
37-character sentence takes on the order of ~1 second, dominated by the
1.5s stabilization wait and the Gemini call, not by typing speed itself.

## Cost

```text
Vision calls:    1 (verification only — no grounding call was needed,
                 since the reply editor was already focused from RND-006B
                 and no coordinate had to be located)
Input tokens:    1,308
Output tokens:      55
Estimated cost: $0.001187
```

## Limitations

- **Single sample, single fixed sentence.** No variation in text length,
  special characters, or content was tested — this establishes that
  controlled plain-ASCII text entry works, not that every possible input
  string would.
- **No grounding/re-click was performed in this stage** — it relies
  entirely on the reply editor already being focused from RND-006B. If
  focus had been lost between stages, this test would likely have typed
  into the wrong location; that failure mode was not exercised here since
  the precondition held.
- **AI and human verification agreed in this one trial** — the
  "human overrides AI" divergence path that RND-006B's `email_row`
  Attempt 1 exercised was not tested again here.
- **No OCR fallback exists** — if Gemini's own vision reading had failed
  or been unavailable, verification would have fallen back to human
  confirmation alone (the code path supports this: `vision_verification`
  stays `None` and `human_verification` remains authoritative), but this
  fallback path was not exercised live in this run since the Gemini call
  succeeded.

## Conclusion

Controlled text entry into the Outlook reply editor works safely and
correctly on this first trial: the exact fixed sentence was typed once
(no Send, no Enter, no Ctrl+Enter), Gemini's independent verification
read back the exact same text at full confidence, and the human confirmed
the same. All three signals — mechanical typing success, AI verification,
and human judgment — agreed, with the human-authoritative design already
proven necessary and correct in RND-006B ready to arbitrate a future
disagreement.

## Next Step

Based on this result, it is reasonable to consider proceeding to
**RND-007B — Contextual Reply Generation + Draft Entry**, which would
generate reply content from the email being replied to (rather than a
fixed sentence) and type that instead. **Not started automatically** —
requires the same explicit review and approval as every prior stage.
