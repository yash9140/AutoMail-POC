# Playbook State Machine

## States

```
READY
LAUNCHING_OUTLOOK
OUTLOOK_READY            reached only after maximize-enforcement passes (Phase 2 — implemented)
FINDING_EMAIL            includes bounded message-list scroll + sender/subject matching (Phase 3 — implemented)
EMAIL_OPENED
READING_EMAIL            includes bounded email-body scroll + structured accumulation (Phase 4 — implemented)
FINDING_REPLY            includes bounded email-body scroll (Phase 5 — implemented)
REPLY_EDITOR_OPEN
GENERATING_DRAFT
TYPING_DRAFT
VERIFYING_DRAFT
DRAFT_READY
WAITING_FOR_SEND_APPROVAL
SENDING
VERIFYING_SEND
COMPLETED

FAILED_SAFE       semantic/grounding/verification/content/ambiguity failures
PROVIDER_ERROR    technical/network/timeout/auth/rate-limit failures
ABORTED           user-initiated
```

Each state advances only to the next state in the list above (`app/playbook/states.py::LINEAR_ORDER`) — no skipping ahead, no going back. Any non-terminal state may transition directly to a terminal state (`fail()`/`abort()`), bypassing the linear table.

No separate `MAXIMIZING` state exists — maximize enforcement is a documented sub-step inside the step that produces `OUTLOOK_READY`, not a new externally-visible state. It has no distinct failure mode the UI needs to show separately: a foreground loss during maximize, or a capture failure right after it, both classify through the same `OUTLOOK_FOREGROUND_LOST`/`TECHNICAL_PROVIDER_ERROR` vocabulary the rest of the launch flow already uses (`app/outlook/launch.py::OutlookLaunchSteps.enforce_maximized()`).

The concrete order producing `OUTLOOK_READY` is now: `LAUNCHING_OUTLOOK` → Windows-Search launch → Outlook foreground detected → **maximize enforcement** (no-op if already maximized) → bounded splash-vs-ready verification → `OUTLOOK_READY`. `FINDING_EMAIL` is never reached from this flow alone — Phase 2 stops at `OUTLOOK_READY`.

## The SENDING guard

`SENDING` is reachable **only** from `WAITING_FOR_SEND_APPROVAL`, and only after `PlaybookEngine.approve_send()` has been explicitly called. This is enforced in `app/playbook/engine.py::advance()` as a hardcoded check that bypasses the normal linear-transition table — the one place this rule could be weakened is deliberately easy to spot in review.

## Terminal-state split

`FAILED_SAFE` and `PROVIDER_ERROR` are two distinct terminal states (both derived from the single old `FAILED` state) so a Gemini HTTP 429/503/504/403 is never recorded the same way as a genuine "target not found" or "draft didn't match." `app/playbook/engine.py::fail(reason)` routes to the correct one via `app/fallback/classifications.py::classify_terminal_state()`, which looks up whether `reason` is in `app/playbook/failure_reasons.py::PROVIDER_ERROR_REASONS`.

## Failure vocabulary additions

- `MULTIPLE_TARGET_EMAILS_FOUND` — Phase 3, implemented. Sender is required, subject is optional; when more than one candidate email matches and recency cannot be confidently determined (a deterministic ISO-8601 `date_or_order` comparison — never a fuzzy guess, never Vision's own confidence as a tiebreaker), the run stops safely rather than letting Vision pick between ambiguous matches.
- `TARGET_EMAIL_NOT_FOUND` — Phase 3, implemented. Bounded message-list scrolling exhausted (`MAX_MESSAGE_LIST_SCROLL_ATTEMPTS`) without finding a matching candidate. Distinct from the pre-existing `TARGET_EMAIL_NOT_VISIBLE` (a single-view signal that now triggers a scroll+re-search rather than an immediate stop).
- `CONTENT_NOT_FULLY_READ` — Phase 4, implemented. Bounded email-body scrolling (`MAX_EMAIL_BODY_SCROLL_ATTEMPTS`) exhausted while Vision still reports `more_content_below=true` — draft generation is never invoked on an incomplete read.
- `REPLY_NOT_FOUND` — Phase 5, implemented. Bounded email-body scrolling (`MAX_REPLY_SEARCH_SCROLL_ATTEMPTS`, reusing Phase 4's `scroll_email_body()` — no second Reply-specific scroll function) exhausted without the Reply control ever becoming visible.
- `SEND_TARGET_INVALID` — Phase 7 (declared now, wired in then).

Note: `OUTLOOK_FOREGROUND_LOST`, `TECHNICAL_PROVIDER_ERROR`, `REPLY_TARGET_NOT_FOUND`, `REPLY_GROUNDING_INVALID`, and `REPLY_EDITOR_NOT_OPEN` (all pre-existing) are reused across Phases 3-5 rather than adding stage-specific duplicates (e.g. no separate `EMAIL_FOREGROUND_LOST` or `REPLY_EDITOR_VERIFICATION_FAILED`) — one vocabulary entry per genuinely distinct failure mode, not one per stage. Phase 5 specifically distinguishes two Reply-search outcomes with the two existing reasons: `REPLY_TARGET_NOT_FOUND` when a control WAS found but isn't semantically Reply (e.g. "Reply All"), vs. `REPLY_NOT_FOUND` when no matching control was ever found after bounded scrolling.
