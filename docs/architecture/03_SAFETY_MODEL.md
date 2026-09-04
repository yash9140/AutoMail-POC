# Safety Model

## Core principle

> IF UNCERTAIN → STOP SAFELY. DO NOT GUESS.

## Foreground checks

Every action is gated by a foreground-window title check (`app/safety/foreground.py::get_foreground_window_title()`, raw `ctypes` call to `user32.dll` — no `pygetwindow`/`pywin32` dependency). Two separate allowlists are deliberately never merged: `is_search_state_foreground()` (before Outlook has launched — the shell may legitimately own the foreground) vs. `is_outlook_foreground()` (after launch). Foreground is re-checked immediately before every mouse move and every click — never just once at the start of a step.

## Maximize enforcement (Phase 2)

`app/outlook/launch.py::OutlookLaunchSteps.enforce_maximized()` follows the same foreground-safety discipline as every click: foreground is checked before reading the window's maximize state, again before issuing the maximize action, and again after the stabilization wait. If Outlook loses foreground at any point, the run stops with `OUTLOOK_FOREGROUND_LOST` — it never auto-refocuses Outlook and never continues on top of whatever application now has focus. The maximize action itself (`app/safety/foreground.py::maximize()`) is a pure `ctypes` `ShowWindow(hwnd, SW_MAXIMIZE)` call on the current foreground window's handle — **never mouse coordinates**, so there is no maximize-button pixel position to get wrong across resolutions or Outlook UI versions. If Outlook is already maximized, `maximize()` is never called at all (`is_maximized()` short-circuits first) — no redundant action, no extra wait.

## No re-click after failed verification

Every verification loop (readiness, email-open, reply-editor, and — from later phases — send) follows the same bounded pattern: wait → fresh screenshot → Vision call → check pass/fail → on failure, wait again → re-check. **Never a second physical action.** This is enforced structurally: `app/playbook/step_contract.py::RetryPolicy.allows_reclick` must be `False` for every verification-retry step, and `tests/test_step_contracts.py` checks this for every declared step contract.

## Grounding validation

`app/safety/validators.py::validate_grounding()` is the one shared function every bbox-grounded click (email row, Reply, and from Phase 7, Send) runs through: normalized-range check (0-1000, before any conversion — never trusts a provider's self-described coordinate convention), bbox self-consistency (the returned point must fall inside its own returned box), a degenerate-bbox check (positive width AND height — a zero-area box is rejected, never clamped or repaired), pixel-bounds check, an optional sidebar-fraction rejection, and a confidence threshold. The click point is computed from the validated bbox's **center**, never from Vision's raw loose x/y when a bbox is available. Every failure is a safe stop with no click — this function never clamps a malformed coordinate, never infers a fixed row geometry, and never falls back to a historical R&D ground-truth coordinate.

Phase 3 extends this to a full-row bbox specifically requested in the prompt ("the full clickable message-list row," never just sender/subject text, a checkbox, star, or icon) and binds validation to the exact screenshot dimensions that produced the bbox — `app/outlook/find_email.py::_validate_and_record_candidate()` receives `screen_width`/`screen_height` as direct arguments from the same capture used for that search attempt, never a cached or stale value from an earlier scroll iteration.

**Layout-fraction constants (e.g. `LEFT_SIDEBAR_MAX_X_FRACTION`) are a secondary safety heuristic only** — an independent, code-side backstop that fires regardless of what Vision claims about the current layout (this is exactly why the sidebar-rejection check exists: an earlier live attempt showed Vision's *stated* confidence/region can be confidently wrong). They are never the primary source of where UI regions actually are — that comes from Vision's own per-request report of the live screenshot.

## Abort

A single flag (`app/safety/abort_controller.py::AbortController`), checked between every meaningful action and wait — never a thread-kill. Once set, no further `advance()` call on the playbook engine can succeed.

## Send safety (implemented Phase 7; principles fixed now)

- Max 1 Send click per session, hard-enforced in code.
- `SENDING` only reachable from `WAITING_FOR_SEND_APPROVAL`, after two separate pre-run approvals (general automation consent + a distinct Send-specific approval), both obtained before the worker starts — zero mid-run approval dialogs, because any interruption mid-flight risks stealing OS foreground away from the exact state being validated.
- Send grounding uses the same dynamic bbox-validation pattern as every other click (no hardcoded ground-truth pixel region), hardened by a size/shape plausibility check and a blocked-target-name list.
- Failed send-verification never triggers a re-click.

## Bounded message-list scrolling (Phase 3)

`app/automation/scrolling.py::scroll_message_list()` decides only **when** to look again — it never determines **where** to click. It moves the mouse to a resolution-relative anchor point (a fraction of the current screenshot, safely clear of the sidebar) purely so the scroll-wheel event lands on the message list, then scrolls; the function never touches `email_converted_x`/`_y` or any click-target state. Scrolling is bounded by `MAX_MESSAGE_LIST_SCROLL_ATTEMPTS`, and every re-search after a scroll runs through the exact same `validate_grounding()` path as the first look — scrolling gates *when* grounding is retried, never *how* a returned coordinate is validated.

## Provider vs. semantic failure separation

HTTP 429/503/504/403 and other technical/network/timeout/auth/rate-limit failures are classified as `PROVIDER_ERROR`, never conflated with a genuine "target not found" or "Vision said no." See `02_STATE_MACHINE.md`.

Since Phase 2, this is backed by a real bounded retry: `app/fallback/recovery.py::call_with_provider_retry()` re-issues a failed Vision call up to `PROVIDER_RETRY_COUNT` (1) more times, but only for a `VisionProviderError` — never for a schema-valid semantic response. If every attempt still fails, the result is classified `TECHNICAL_PROVIDER_ERROR` (→ `PROVIDER_ERROR` at the playbook-engine level), and no provider failure of any kind triggers another Windows-Search-launch, maximize, or click action — it only ever re-issues the same read-only Vision call.

## A bug found and fixed during Phase 2

Adding `enforce_maximized()` into the full launch → find-email → reply → draft worker chains initially left the existing worker-level tests (`test_outlook_launch_worker.py`, `test_find_open_email_worker.py`, `test_reply_draft_worker.py`) without mocks for `get_foreground_hwnd()`/`is_maximized()`/`maximize()` — meaning those tests would have made real, unmocked `ctypes` calls against whatever window actually had focus on the machine running the suite. Caught immediately (before it could repeat) by re-running the suite and reasoning through what each newly-added call path touches; fixed by mocking all three in every test that now reaches `enforce_maximized()`. Recorded here per the project's standing rule to log every bug found honestly, not just the ones that made it into a final report unnoticed.
