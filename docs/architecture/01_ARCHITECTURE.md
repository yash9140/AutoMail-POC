# Architecture

## Governing principle

> The playbook determines intent; Vision determines current state and location; deterministic code validates and executes the action. Vision never independently chooses the business action.

Example:
- **Playbook**: "Find the latest matching email from the configured sender."
- **Vision**: detects whether/where a matching email is visible on the current screenshot.
- **Validator** (deterministic code): checks bbox validity, screen bounds, correct UI region, target name/content match.
- **Automation**: performs the one allowed action (click/scroll/type) computed from the validated result.
- **Verification**: fresh screenshot, confirms the expected result occurred.

Vision is never asked open-ended "what should I do next?" — every prompt asks a bounded, specific question the playbook already decided to ask.

## The action loop, used identically everywhere

```
fresh screenshot
  -> Vision detects current layout/region + target bbox
  -> deterministic code validates the bbox (self-consistency, screen bounds,
     expected region, plausible dimensions, no blocked-target overlap)
  -> click point computed from the validated bbox's CENTER (never Vision's
     raw loose x/y, when a bbox is available)
  -> act (single click / scroll / type segment)
  -> fresh screenshot
  -> verify
```

This is implemented once, in `app/safety/validators.py::validate_grounding()`, and reused by every grounded click (email row, Reply, and — from Phase 7 — Send).

## Provider-call retry (Phase 2)

Every Vision call in `app/outlook/launch.py` is wrapped by `app/fallback/recovery.py::call_with_provider_retry()` — up to `PROVIDER_RETRY_COUNT` (1) extra attempts, but ONLY on a technical `VisionProviderError` (HTTP 429/503/504/403, timeout, network error). A schema-valid semantic response (Vision genuinely says "no," or reports low confidence) is never retried — retrying a real answer would blur the provider-error/semantic-failure distinction this project insists on keeping separate. This is a pure call-retry: it never performs a second physical mouse/keyboard action and never triggers another Windows-Search-launch or maximize attempt.

## Directory map

```
app/
  config/settings.py       centralized safety-policy constants (timings, thresholds, limits)
  vision/                  provider abstraction, coordinate math, response schemas, generic bounded-verify loop
    providers/             GeminiProvider (empirically proven) + OpenAI/Anthropic (mock-tested only)
  automation/               screen capture (mouse/keyboard/scrolling helpers land here in later phases)
  outlook/                  the actual step logic — launch, find_email, read_email, reply, draft, (send in Phase 7)
  safety/                   foreground/maximize detection+control, abort flag, grounding validation
  fallback/                 failure-reason -> terminal-state classification (technical vs. semantic)
  playbook/                 state machine, step contract, session context, failure vocabulary
  metrics/                  session + per-step metrics, cost estimation
  controllers/, workers/, ui/   PySide6 app layer
```

`rnd/` (the R&D library) is left fully intact and untouched — `app/` no longer imports from it. A handful of small, stable utility modules (`rnd/providers/*`, `rnd/metrics/{coordinate_calibration,grounding}.py`, `rnd/capture/screen_capture.py`, `rnd/experiments/capture_with_foreground_check.py::get_foreground_window_title`) were **copied** rather than moved, since several `rnd/experiments/*.py` scripts and their own tests still depend on the originals.

## Reused vs. new (Phase 1)

**Carried forward, behavior-preserving move:** the Windows-Search-launch flow, splash-vs-ready readiness verification, email-row grounding (box_2d self-consistency + sidebar-fraction rejection), Reply discovery/click/verification, draft generation, the segmented-typing safety method (never `pyautogui.write()` with an embedded newline; foreground re-checked before every segment and every Enter), and draft verification.

**New this phase:** `app/config/settings.py` (centralized constants), `app/safety/validators.py::validate_grounding()` (the shared grounding-check function, now used by email-row grounding), `app/safety/foreground.py::is_maximized()`/`maximize()` (declared, not yet called — Phase 2), `app/vision/verification.py::bounded_verify()` (generic retry-loop helper, for new loops added in later phases), `app/playbook/context.py`/`step_contract.py` (scaffolding for Phase 5's unified orchestrator), the reconciled state machine (see `02_STATE_MACHINE.md`).

## Reused vs. new (Phase 2)

**Carried forward, unchanged:** Windows-Search launch, coordinate conversion, dual foreground checks around every move/click, the splash-vs-ready readiness distinction and its bounded 2-attempt loop.

**New this phase:** `app/outlook/launch.py::OutlookLaunchSteps.enforce_maximized()` — maximize enforcement as a sub-step of reaching readiness, using `app/safety/foreground.py::is_maximized()`/`maximize()` (now actually called) — never mouse coordinates, a pure `ctypes` `IsZoomed`/`ShowWindow(SW_MAXIMIZE)` pair. `app/fallback/recovery.py::call_with_provider_retry()` (new module, wired into both of `OutlookLaunchSteps`' Vision calls). `OutlookLaunchResult` (a subclass of `rnd.models.outlook_launch.RND009BResult`, defined in `app/outlook/launch.py` rather than editing the historical R&D model) carries the new Phase 2 metrics fields — see `05_CONFIGURATION.md` and the Phase 2 completion report for the full field list.
