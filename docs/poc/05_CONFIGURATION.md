# Configuration

All safety-policy constants live in `app/config/settings.py` — grouped, every value named and documented, migrated verbatim from the scattered RND-stage modules (moving them here changed nothing behaviorally; every value below matches its original).

## Timing (Windows Search / Outlook launch)

| Constant | Value | Governs |
|---|---|---|
| `WINDOWS_KEY_STABILIZE_SECONDS` | 0.9 | Wait after pressing Win before typing |
| `SEARCH_TYPE_STABILIZE_SECONDS` | 1.2 | Wait after typing the search query |
| `TYPE_INTERVAL_SECONDS` | 0.03 | Per-character typing interval |
| `MOVE_DURATION_SECONDS` | 0.6 | Mouse move duration before any click |
| `OUTLOOK_LAUNCH_INITIAL_WAIT_SECONDS` | 2.0 | Wait before polling for Outlook foreground |
| `OUTLOOK_LAUNCH_POLL_INTERVAL_SECONDS` | 1.0 | Poll interval while waiting for launch |
| `OUTLOOK_LAUNCH_TIMEOUT_SECONDS` (alias `OUTLOOK_READY_TIMEOUT`) | 18.0 | Hard timeout for Outlook to appear |
| `READINESS_INITIAL_WAIT_SECONDS` | 6.0 | First readiness-check wait |
| `READINESS_RETRY_WAIT_SECONDS` | 3.0 | Second readiness-check wait |
| `MAX_READINESS_ATTEMPTS` | 2 | Bounded readiness re-checks |
| `MAXIMIZE_STABILIZE_WAIT_SECONDS` (alias `UI_STABILIZATION_WAIT`) | 1.0 | Wait after maximizing before checking foreground / capturing |

Aliases pointing at the constants above, matching the names used in the Phase 2 spec (the aliased name is documentation only — the name on the left remains what `app/outlook/launch.py` actually imports, to avoid a gratuitous rename of already-tested code): `OUTLOOK_READINESS_MAX_ATTEMPTS = MAX_READINESS_ATTEMPTS`, `OUTLOOK_READINESS_RETRY_WAIT = READINESS_RETRY_WAIT_SECONDS`.

## Vision confidence

| Constant | Value | Governs |
|---|---|---|
| `VISION_CONFIDENCE_THRESHOLD` | 0.6 | Shared threshold for email-row/Reply grounding (consolidated from two identical originals) |

## Find/open email

| Constant | Value | Governs |
|---|---|---|
| `POST_CLICK_INITIAL_WAIT_SECONDS` | 1.5 | First open-verification wait |
| `POST_CLICK_RETRY_WAIT_SECONDS` | 1.0 | Second open-verification wait |
| `MAX_EMAIL_OPEN_VERIFICATION_ATTEMPTS` | 2 | Bounded open-verification re-checks |
| `LEFT_SIDEBAR_MAX_X_FRACTION` | 0.17 | Secondary safety heuristic — see `03_SAFETY_MODEL.md` |

## Reply / draft

| Constant | Value | Governs |
|---|---|---|
| `FOCUS_SETTLE_DELAY_SECONDS` | 0.6 | Wait before typing begins |
| `REPLY_EDITOR_INITIAL_WAIT_SECONDS` | 1.5 | First reply-editor-verification wait |
| `REPLY_EDITOR_RETRY_WAIT_SECONDS` | 1.0 | Second reply-editor-verification wait |
| `MAX_REPLY_EDITOR_VERIFICATION_ATTEMPTS` | 2 | Bounded editor-verification re-checks |
| `POST_TYPING_WAIT_SECONDS` | 1.5 | Wait before draft-verification capture |
| `DRAFT_MIN_LENGTH` | 5 | Local quality gate — minimum draft length |
| `PLACEHOLDER_MARKERS` | `("[insert", "TODO", "lorem ipsum", "<placeholder>", "XXX")` | Local quality gate — obvious placeholder rejection |

## Find Reply (Phase 5)

| Constant | Value | Governs |
|---|---|---|
| `MAX_REPLY_SEARCH_SCROLL_ATTEMPTS` | 5 | Bounded search-and-scroll attempts before `REPLY_NOT_FOUND` — reuses Phase 4's `scroll_email_body()`, no second Reply-specific scroll function |
| `REPLY_SEARCH_SCROLL_STABILIZE_WAIT_SECONDS` | 1.0 | Wait after a scroll before the next fresh Reply search |

## Declared for a later phase (not yet referenced by any step module)

| Constant | Default | Phase |
|---|---|---|
| `SEND_CLICK_MAX` | 1 | 7 — hard ceiling, enforced in code |

## Provider retry (wired in — Phase 2, extended to find-email in Phase 3, read-email in Phase 4, find-reply in Phase 5)

| Constant | Value | Governs |
|---|---|---|
| `PROVIDER_RETRY_COUNT` | 1 | Bounded retries for a technical (not semantic) Vision-call failure — `app/fallback/recovery.py::call_with_provider_retry()`, used by `OutlookLaunchSteps`, `FindOpenEmailSteps.find_target_email()`/`verify_email_opened()`, `EmailUnderstandingSteps.understand_email()`, and (Phase 5) `ReplyDiscoverySteps.prepare_reply_editor()`/`verify_reply_editor()` |

## Read email / long-email accumulation (Phase 4)

| Constant | Value | Governs |
|---|---|---|
| `MAX_EMAIL_BODY_SCROLL_ATTEMPTS` | 5 | Bounded section reads before `CONTENT_NOT_FULLY_READ` |
| `EMAIL_BODY_SCROLL_AMOUNT` | -6 | Scroll-wheel units per attempt |
| `EMAIL_BODY_SCROLL_STABILIZE_WAIT_SECONDS` | 1.0 | Wait after a body scroll before the next fresh screenshot |
| `EMAIL_BODY_SCROLL_ANCHOR_X_FRACTION` / `_Y_FRACTION` | 0.7 / 0.5 | Mouse position before a body scroll — reading-pane region, distinct from the message-list scroll anchor |
| `EMAIL_SECTION_TAIL_CHARS` | 200 | How much of the accumulated content's tail is carried into the next section's prompt as `already_read_tail`, verbatim |

## Find email (Phase 3)

| Constant | Value | Governs |
|---|---|---|
| `MAX_MESSAGE_LIST_SCROLL_ATTEMPTS` | 5 | Bounded search-and-scroll attempts before `TARGET_EMAIL_NOT_FOUND` |
| `MESSAGE_LIST_SCROLL_AMOUNT` | -6 | Scroll-wheel units per attempt (negative = toward older mail) |
| `MESSAGE_LIST_SCROLL_STABILIZE_WAIT_SECONDS` | 1.0 | Wait after a scroll before the next fresh screenshot |
| `MESSAGE_LIST_SCROLL_ANCHOR_X_FRACTION` / `_Y_FRACTION` | 0.35 / 0.5 | Where the mouse is positioned before a scroll — a resolution-relative fraction, never a fixed pixel, and never reused as a click location |

## Provider / model selection

Unchanged from the RND stages — `.env` (`GEMINI_API_KEY`, `GEMINI_MODEL`, `VISION_REQUEST_TIMEOUT_SECONDS`), loaded via `app/config/settings.py::get_provider()`. `config/model_pricing.json` remains the hand-maintained, source-cited pricing table (never fabricated — `null` when a model/provider combination isn't verified), read by `app/metrics/step_metrics.py::estimate_cost()`.

## Target email matching (dashboard inputs)

**Target Sender is required. Target Subject is optional.** Both are validated non-empty/blank on the dashboard before `Start Automation` enables (sender only). Neither is persisted to disk or logged. See `04_SCROLLING_AND_LONG_EMAIL.md` for the matching rule.

As of Phase 3, `FindOpenEmailSteps.__init__` accepts `target_sender`/`target_subject` directly (raising `ValueError` if `target_sender` is empty) — the module-level `TARGET_EMAIL_SUBJECT`/`TARGET_EMAIL_SENDER` constants remain only as defaults for callers that don't yet pass their own (the actual dashboard→playbook wiring is Phase 8's concern).
