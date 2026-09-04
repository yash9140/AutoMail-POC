# Testing and QA

## Running the suite

```
./.venv/Scripts/python.exe -m pytest -q
```

All tests are fully mocked — `pyautogui`, `ctypes.windll.user32`, screen capture, and the Vision provider are patched at their module boundary in every test. No real UI, network, or desktop call happens during the suite.

## What's covered (Phase 1)

- `tests/test_outlook_launch.py`, `tests/test_find_email.py`, `tests/test_outlook_reply.py`, `tests/test_outlook_draft.py` — the moved/refactored step logic: coordinate conversion, bounds/sidebar/confidence rejection, box_2d self-consistency, dual foreground checks before every move and click, bounded verification loops that never re-click, segmented typing safety.
- `tests/test_typing_regression.py` — golden-trace proof that `type_draft()`'s move was byte-for-byte behavior-preserving.
- `tests/test_settings.py` — every migrated constant matches its original value exactly.
- `tests/test_safety_validators.py`, `tests/test_safety_foreground.py`, `tests/test_vision_grounding.py`, `tests/test_vision_verification.py`, `tests/test_step_metrics.py`, `tests/test_fallback_classifications.py`, `tests/test_playbook_context.py`, `tests/test_step_contracts.py` — the new Phase 1 infrastructure modules.
- `tests/test_app_playbook.py` — the reconciled state machine (happy path, Send-unreachable-before-approval, terminal-state routing).
- `tests/test_app_ui.py` — dashboard/controller behavior, plus the structural proof that `pyautogui` is used only in the four `app/outlook/*.py` step modules.
- Worker-level tests (`tests/test_outlook_launch_worker.py`, `tests/test_find_open_email_worker.py`, `tests/test_reply_draft_worker.py`) — full mocked chains through the QThread workers.

## What's covered (Phase 3)

- `tests/test_find_email.py` (29 tests, rewritten for the candidate-list API) — sender-required/subject-optional matching (both-required-when-subject-given, sender-only-single-candidate), ambiguity handling (multiple candidates safe-stop, deterministic ISO-date resolution, tied dates stay ambiguous), bounded scroll (found-after-one-scroll, exhausted-after-max-attempts with the exact scroll-call count asserted), scrolling never determines the click point (structural source check), every grounding-rejection path (missing bbox, degenerate/zero-area bbox, sidebar, out-of-normalized-range, low confidence) yields zero click, provider-transient-error retried once without ever triggering a scroll or click, foreground loss before/after move blocks the click with `email_click_count` staying 0, click executes exactly once, post-click verification retries without re-clicking (`email_click_count` unchanged), wrong-email-opened classified distinctly, subject-optional verification, Phase 2 launch metrics propagate into the Phase 3 result, and a structural proof the module never references historical annotated screenshots/datasets.
- `tests/test_safety_validators.py` extended (13 tests) — the new normalized-range and degenerate-bbox checks in `validate_grounding()`.
- `tests/test_find_open_email_worker.py`/`test_reply_draft_worker.py` fixtures updated to the candidate-list schema; the not-visible case now exercises the full bounded-scroll-to-exhaustion path with `app.automation.scrolling.pyautogui` mocked separately from the step module's own pyautogui reference.

## What's covered (Phase 5)

- `tests/test_outlook_reply.py` extended substantially — `content_complete` precondition gating (both explicit `False` and unset/`None`), already-open skip path, valid-grounding-clicks-once with bbox/pixel metrics recorded, not-visible-then-found-after-scroll, exhausted-after-max-scroll-attempts with exact scroll-call-count assertions, a structural proof scrolling never determines the click coordinate, "Reply All"/"Forward" returned instead of Reply rejected as an immediate safe stop (never a scroll retry), missing/degenerate/out-of-normalized-range bbox and low-confidence all yield zero click, foreground loss before move and between move/click both block the click, provider-transient-error retried once without duplicating a scroll or click, provider-error-exhausted likewise touches neither, verification retries never re-click (`reply_click_count` stays 1) including through a provider retry mid-verification, verification exhaustion never authorizes a second click, a structural check for no Send-related code, and a structural check the module never references historical annotated screenshots/datasets.

## What's covered (Phase 4)

- `tests/test_outlook_reply.py` extended — short email as the single-section case (no scroll), two-section accumulation with verbatim-overlap dedup (the overlapping substring appears exactly once in the final combined content), a 4-section test proving a date/commitment mentioned only in section 1 survives into the final combined understanding after 3 more sections accumulate on top of it, scroll-until-`more_content_below=false` (exact scroll-call count asserted), bounded-incomplete-read fails safe with `CONTENT_NOT_FULLY_READ` AND blocks `generate_draft()` (asserted via the defensive `RuntimeError`), a structural proof body-scrolling never references a click coordinate, and one provider-transient-error-retried-once case.
- `tests/test_reply_draft_worker.py` fixtures updated to the new `EmailSectionRawResponse` schema.

## What's covered (Phase 2)

- `tests/test_maximize_control.py` (19 tests) — `enforce_maximized()`: already-maximized no-op, restored-window maximize-exactly-once with stabilization + fresh screenshot, maximize never touches `pyautogui` (structurally proven against `app.safety.foreground.maximize`'s own source, not just mocked), foreground lost before/after maximize both stop safely without auto-refocusing, splash-after-maximize still never reaches ready, a fully-loaded Outlook does reach `ready_for_interaction`, readiness retry triggers no re-launch/re-maximize/re-click, readiness timeout is a safe `FAIL`, a technical provider error classifies as `PROVIDER_ERROR` (not a semantic readiness failure) both via `verify_outlook_readiness()` and via the post-maximize screenshot capture path, one bounded provider retry succeeds transparently, abort during the maximize-stabilization wait / before the maximize check at all / during the readiness wait all stop safely, `FINDING_EMAIL` and every email/Reply/draft/Send method are structurally absent from `app/outlook/launch.py`, and the module's pre-existing pyautogui call-count proof (one Windows-key press, one write, one moveTo, one click) is unchanged by the maximize addition.
- Worker tests updated: all three worker test files now mock `get_foreground_hwnd()`/`is_maximized()`/`maximize()` wherever they exercise the full launch chain (a real gap found and fixed this phase — see `03_SAFETY_MODEL.md`).

## What's intentionally left untouched

`tests/test_contextual_reply.py`, `test_click_verification.py`, `test_annotate_ground_truth.py`, `test_dataset_validation.py`, `test_evaluator.py`, `test_provider_readiness_schema.py`, `test_coordinate_convention_schema.py`, `test_vision_result.py`, `test_gemini_provider.py`/`test_openai_provider.py`/`test_anthropic_provider.py`, `test_coordinate_calibration.py`, `test_screen_capture.py`, `test_mouse_execution.py`, `test_text_entry.py`, `test_click_execution.py`, `test_controlled_send.py` — these validate `rnd/` originals or RND-experiment-specific infrastructure that has no POC equivalent (or, for the provider/coordinate tests, cover the `rnd/` copies that `app/vision/*` was copied from). Left exactly as-is.

## Scenario matrix (Phase 9)

Four scenario families, implemented as branches of one playbook, not four scripts:

| | Short email | Long email |
|---|---|---|
| **Target visible** | S1 | S2 |
| **Target needs message-list scroll** | S3 | S4 |

`tests/test_scenarios.py` (Phase 9) drives `app/playbook/playbook.py::Playbook.run()` through all four with fully mocked Vision/scroll-trigger responses, proving they share the same code path.

## Headless QA driver (Phase 9)

`scripts/poc_live_run.py` mirrors the existing `scripts/rnd009{b,c,d}_live_run.py` pattern (same step logic, callable outside the GUI, gated bounded-approval flags, labeled result files so retries never overwrite prior attempts) for manual regression testing of the final POC. **Not exercised during implementation** — only argument-parsing/gating logic is unit-tested; the first real run requires explicit approval.
