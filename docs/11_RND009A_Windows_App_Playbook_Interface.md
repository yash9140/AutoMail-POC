# 11 — Windows POC App Interface + Playbook-Ready Architecture (RND-009A)

## Objective

Build the native Windows POC application interface that will later run
the Outlook automation playbook, plus the architectural skeleton
(playbook state engine, abort controller, metrics model) that stage
carries forward — as UI + architecture preparation only. Nothing in
this stage launches Outlook, performs mouse/keyboard automation, sends
email, executes the real playbook, or modifies any RND-001–RND-008
historical result.

## Approved Architecture

**Playbook-driven + Vision-assisted**, per explicit instruction:

- The **playbook** decides what step is allowed next (`PlaybookEngine`,
  gated by `states.py`'s transition table).
- **Vision** determines what is visible and where a target is — not
  wired up in this stage; the engine has no knowledge of Vision at all
  yet.
- **PyAutoGUI** performs the mouse/keyboard action — not wired up in
  this stage; nothing in the `app/` package imports or calls it
  (structurally proven, see Tests).
- The **safety layer** (`AbortController`, plus the engine's own
  transition guards) validates whether an action is allowed.
- The **metrics layer** (`SessionMetrics`) records performance.
- **Vision never independently decides to Send** — enforced by the
  engine, not by convention: `SENDING` is reachable only from
  `WAITING_FOR_SEND_APPROVAL`, and only after `approve_send()` has been
  called explicitly. No component other than an explicit approval call
  can satisfy that guard.

This is deliberately not a free-form autonomous desktop agent — every
action the future playbook takes will have to be an explicit, engine-
validated state transition, not an ad hoc decision made inside a
Vision response.

## User Flow

```
Application Launch
  → Login Screen
  → Terms & Permissions Screen
  → Automation Dashboard
  → Start Automation
  → (future) playbook execution
  → Result Screen
```

Implemented in `app/ui/main_window.py` via a `QStackedWidget` and
`AppController` (`app/controllers/app_controller.py`), which does
nothing but decide which page is current in response to page-level
signals (`login_succeeded`, `permissions_accepted`). All playbook/
session logic lives in `AutomationController`, kept separate so the
navigation layer stays trivial to review.

## UI Screens

### Login Screen (`app/ui/login_page.py`)

Email + password fields, password masked (`QLineEdit.EchoMode.Password`).
**No real authentication**: both fields non-empty → `login_succeeded`
emitted; either empty → inline validation message, nothing emitted. No
backend call, no credential storage, no password logging — the
password field is cleared immediately after a successful "login," and
the widget stores no separate copy of either field's value anywhere.

### Terms & Permissions Screen (`app/ui/permissions_page.py`)

Explains what the POC may do (observe the screen, use mouse/keyboard
input, launch Outlook, inspect visible email content, generate a
draft, perform controlled actions). Three checkboxes; **Continue stays
disabled until all three are checked** — enforced by the widget
itself, not just by convention (tested by toggling each box
individually).

### Automation Dashboard (`app/ui/automation_page.py`)

Title "Outlook Vision AI Automation." Six status fields (Application
Status, Playbook Status, Current Step, Last Action, Vision Status,
Safety Status), a Start Automation / Stop-Abort button pair, and a
read-only activity log (`QListWidget`, never given edit flags).

### Result Screen (`app/ui/result_page.py`)

Layout + field wiring for Final Status, Completed Steps, Failed Step,
Total Duration, Vision Calls, Total Cost, Safety Aborts. Not reachable
yet from the dashboard in this stage (no real playbook run completes),
but `update_from_metrics()` is implemented and unit-testable in
isolation — it fills these fields only when explicitly called, never
automatically.

## Playbook Concept

`PlaybookStep` (`app/playbook/models.py`): `step_id`, `name`,
`expected_state`, `status`, `started_at`, `completed_at`,
`failure_reason`.

`PlaybookEngine` (`app/playbook/engine.py`): `current_state`,
`current_step`, `steps` (full history), `send_approved`,
`abort_requested`; methods `start()`, `advance(target_state)`,
`approve_send()`, `fail(reason)`, `abort()`, `reset()`. No Vision or
PyAutoGUI call exists anywhere in this file — it is pure state-model
logic, wired to real actions starting in RND-009B.

## State Machine

All 15 states from the spec are defined in `app/playbook/states.py`:

```
READY, LAUNCHING_OUTLOOK, OUTLOOK_VERIFIED, FINDING_EMAIL, EMAIL_OPENED,
UNDERSTANDING_EMAIL, REPLYING, GENERATING_DRAFT, DRAFT_READY,
WAITING_FOR_SEND_APPROVAL, SENDING, VERIFYING_SEND, COMPLETED,
FAILED, ABORTED
```

## Transition Rules

The happy path is strictly linear — each state may advance only to the
next state in `LINEAR_ORDER`, built into a `VALID_TRANSITIONS` dict at
module load time (`{state[i]: {state[i+1]}}`), so no state can skip
ahead or jump backward. Any non-terminal state may additionally
transition to `FAILED` or `ABORTED`. `COMPLETED`, `FAILED`, and
`ABORTED` are terminal — `advance()` always raises
`InvalidTransitionError` once the engine is in one of them.

## Send Protection Design

This is the one transition with an extra, hardcoded guard on top of
the table lookup:

```python
if target_state == SEND_STATE:  # SENDING
    if self.current_state != SEND_REQUIRED_PREDECESSOR:  # WAITING_FOR_SEND_APPROVAL
        raise InvalidTransitionError(...)
    if not self.send_approved:
        raise InvalidTransitionError(...)
```

`send_approved` is set only by an explicit `approve_send()` call — no
other engine method touches it. Both halves of the guard are proven by
mocked tests: `READY → SENDING` and `DRAFT_READY → SENDING` are
rejected outright; `WAITING_FOR_SEND_APPROVAL → SENDING` is rejected
without approval and succeeds only after `approve_send()`; and
approval alone from the wrong predecessor state is also rejected. No
real Send action is wired to this state in RND-009A — it is state-model
logic only, exactly as scoped.

## Abort Design

`AbortController` (`app/safety/abort_controller.py`) is a single
`abort_requested` flag with `request_abort()` / `is_abort_requested()`
/ `reset()`. Clicking Stop/Abort (`AutomationController.abort_automation`)
sets this flag, calls `engine.abort()` (which also sets the engine's
own `abort_requested` and moves `current_state` to `ABORTED`),
increments `metrics.safety_aborts`, and updates the dashboard (Playbook
Status → "Aborted", Safety Status → "Aborted", Start re-enabled, Abort
disabled). This is a **flag future workers must observe between
actions** — explicitly not an unsafe thread-kill, per instruction.
`engine.advance()` checks `abort_requested` first and refuses to move
the state at all once it is set, so no code path can silently keep
advancing after an abort.

## Threading Preparation

No `QThread` subclass exists yet — this stage's only "work"
(`start_automation()` advancing one enum value) is instantaneous, so
introducing a worker thread now would add complexity with nothing real
to offload, which the instruction explicitly allowed skipping ("do not
implement real ... worker execution yet unless required for the
architecture"). The architecture is threading-ready regardless:
`AutomationController` already owns the engine/abort-controller/metrics
as a single unit separate from the UI widgets, which is the shape a
future `QThread` worker would need — it would run inside the worker,
emit Qt signals back to `AutomationPage` for status updates (the
standard cross-thread-safe Qt pattern), and check `AbortController`
between steps. RND-009B, which will actually launch Outlook (a
blocking OS operation), is the natural point to introduce the real
worker.

## Metrics Preparation

`SessionMetrics` (`app/metrics/session_metrics.py`): `session_id`,
`start_time`, `end_time`, `current_step`, `completed_steps`,
`failed_step`, `vision_calls`, `input_tokens`, `output_tokens`,
`estimated_cost`, `total_latency_ms`, `retries`,
`human_interventions`, `safety_aborts`, `send_click_count` (defaults to
`0`). `start_time` is set on Start Automation and `safety_aborts` is
incremented on Abort — the only two fields this stage's UI actually
touches; everything else is a prepared field for RND-009B onward, in
the same "no call site left invisible from the totals" spirit
established in RND-007B/RND-008's cost-tracking fix.

## Tests

25 new tests, all mocked/simulated — no real mouse/keyboard/network
call anywhere:

- `tests/test_app_playbook.py` (15 tests) — pure state-machine logic:
  starts in `READY`; `start()` moves to `LAUNCHING_OUTLOOK`; an
  arbitrary invalid jump is rejected; `READY → SENDING` and
  `DRAFT_READY → SENDING` are both rejected; `SENDING` requires both
  the correct predecessor AND explicit approval (tested as two
  separate failure modes plus the success path); abort moves to
  `ABORTED`; no further `advance()` succeeds after abort; `fail()`
  works from any non-terminal state; terminal states refuse to
  advance; `reset()` returns to a fresh `READY` engine;
  `send_click_count` and all other `SessionMetrics` fields default
  correctly; `AbortController` default/request/reset.
- `tests/test_app_ui.py` (10 tests, via `pytest-qt`'s `qtbot`, which
  simulates Qt-level events, not OS-level input) — application starts
  with the login page current; empty login is blocked (no signal
  emitted); non-empty login succeeds without any auth/network call
  (structurally verified by source inspection); password field uses
  masked echo mode; the password field is cleared and no credential
  attribute is stored after login; all three permission checkboxes are
  required (toggled individually); Start Automation does not call
  `os.startfile` or `subprocess.Popen` (mocked and asserted not
  called); clicking Abort moves the dashboard to `ABORTED` and
  re-enables Start; and a structural test proves no module under
  `app/` imports or calls `pyautogui`.

## Screenshots

Captured by driving the real widgets/controllers directly (same code
path the tests use) and calling Qt's own `grab()` on the rendered
window — genuine pixel output from the actual app, not a mockup —
rather than simulating OS-level mouse/keyboard input:

1. `screenshots/app/01_login_screen.png` — initial login screen
2. `screenshots/app/02_permissions_screen.png` — Terms & Permissions,
   all three boxes unchecked, Continue disabled
3. `screenshots/app/03_automation_dashboard.png` — dashboard in its
   initial Ready state
4. `screenshots/app/04_dashboard_launching_outlook.png` — after Start
   Automation, `Current Step: LAUNCHING_OUTLOOK`, activity log showing
   the state transition and the "Outlook launch module will be
   connected in RND-009B" message
5. `screenshots/app/05_dashboard_aborted.png` — after Stop/Abort,
   `Current Step: ABORTED`, Start re-enabled, Abort disabled

No password value appears in any screenshot (the login screen was
captured before any text was entered).

## Limitations

- **No real navigation to the Result screen yet** — nothing in this
  stage produces a completed/failed/aborted run to display; the page
  exists and is unit-tested in isolation via `update_from_metrics()`
  only.
- **No `QThread` worker exists yet** — a deliberate choice (see
  Threading Preparation), not an oversight; there is no long-running
  work in this stage to justify one.
- **The offscreen Qt platform renders text as glyph-less boxes on this
  machine** — screenshots were captured using the native Windows Qt
  platform instead (briefly showing a real, harmless window) after the
  offscreen attempt produced unreadable output; documented here as a
  real tooling finding, not silently worked around.
- **Login/permissions state does not persist across a page revisit** —
  e.g. navigating back to Login and re-submitting does not reset the
  permissions checkboxes. Not a requirement in this stage's spec, but
  worth noting before building further screens on top of this
  navigation model.
- **This stage's Start Automation stops after exactly one state
  transition** — by design, per instruction; RND-009B is where the
  next real transition (`LAUNCHING_OUTLOOK → OUTLOOK_VERIFIED`, wired
  to an actual Windows Search + Outlook launch) is added.

## Conclusion

The Windows POC app interface and playbook-ready architecture are in
place: a native PySide6 app with the required 4-screen flow, a
state-machine engine that structurally refuses both arbitrary jumps
and any unapproved path into `SENDING`, a shared abort flag future
workers will observe, and prepared (not yet populated) metrics fields
— all covered by 25 mocked tests with no real desktop automation
anywhere in this stage.

## Next Stage

**RND-009B — Windows Search → type "Outlook" → Vision verify result →
open Outlook → wait → verify Outlook foreground → STOP.** This is the
first stage to connect the playbook engine to a real action (via a
`QThread` worker, launching Outlook) and to Vision (verifying the
search result and the launched window). **Not started automatically**
— requires its own explicit review and approval.
