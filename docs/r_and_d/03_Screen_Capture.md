# 03 — Screen Capture (RND-001)

## Objective

Answer: **Can we reliably capture the actual Windows screen at the correct
resolution and preserve a trustworthy pixel coordinate system for later
Vision AI grounding and mouse interaction?**

This is the foundation every later experiment depends on. If the captured
image's pixel coordinates don't match the coordinate space `pyautogui` uses
to move the mouse, every later grounding/click result would be wrong for a
reason that has nothing to do with the Vision AI's actual capability. This
experiment tests only capture + coordinate-system trust — no AI, no
Outlook-specific logic, no mouse movement.

## What Was Built

```
rnd/capture/screen_capture.py       Reusable capture module (capture, validate, environment info, CLI)
rnd/capture/run_rnd001_tests.py     Test runner for SC-001..SC-007, writes results/raw/rnd001_screen_capture_results.json
rnd/__init__.py, rnd/capture/__init__.py   Package markers (enables `python -m rnd.capture.screen_capture`)
tests/test_screen_capture.py        Pytest regression tests (isolated tmp_path, safe to re-run/CI)
results/raw/rnd001_screen_capture_results.json   Actual recorded results from this run
screenshots/raw/*.png               Real screenshots captured during testing
```

## Technology Used

- **`mss`** — primary screen capture library.
- **`Pillow`** — converts the raw BGRA buffer from `mss` to a PNG file, and
  is used afterward to independently re-open and validate the saved file.
- **`pyautogui.size()`** — used *only* to read the mouse-control library's
  reported screen size, for comparison against `mss`'s reported size. No
  mouse movement or clicking occurs anywhere in this experiment.

## Why These Technologies Were Used

**MSS** was chosen for capture because it talks to the Windows GDI screen
buffer directly (no browser, no external process, no screenshot-tool
dependency), returns raw pixel data fast, and — critically for this
project — is not fooled by non-DPI-aware virtualization the way some
higher-level Windows APIs can be (see DPI findings below). It also gives
direct, explicit access to per-monitor geometry (`left`, `top`, `width`,
`height`), which we need to eventually reason about multi-monitor setups.

**Pillow** was chosen because it's the standard, dependency-light way to
both write the captured raw buffer as PNG and — separately — re-open and
verify that file, giving us an independent check that the saved file isn't
corrupt rather than trusting the writer's own success return.

**`pyautogui.size()`** was used purely as a second, independent measurement
of screen resolution. Since `pyautogui` is the library that will later
move the mouse and type, if it disagreed with `mss` about screen
dimensions, every future coordinate handed to it by the Vision AI (grounded
against `mss` screenshots) would land in the wrong place. This check exists
to catch that *before* it becomes a silent, hard-to-diagnose bug.

## How It Works

```
Windows display (physical pixels)
        ↓
mss.MSS().grab(monitor)        → raw BGRA buffer at physical resolution
        ↓
Pillow Image.frombytes(...)    → converted to RGB
        ↓
Image.save(path, "PNG")        → written to screenshots/raw/
        ↓
Pillow re-opens + verifies     → integrity + dimension check
        ↓
CaptureMetadata returned       → filename, path, width, height, timestamp, monitor_index
```

`get_environment_info()` runs independently: it enumerates `mss` monitors
and separately calls `pyautogui.size()`, then compares the two.

## How To Use

Setup (from `outlook-vision-poc/`):

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Single capture:

```bash
python rnd/capture/screen_capture.py
```

Both invocation styles are supported and were verified to work identically;
running the file directly is the documented default because it needs no
`PYTHONPATH`/working-directory setup, but the module form also works from
the project root:

```bash
python -m rnd.capture.screen_capture
```

Multiple captures (repeated-capture reliability test):

```bash
python rnd/capture/screen_capture.py --count 5 --delay 0.5
```

Full RND-001 test suite (SC-001 through SC-007), writes the structured
result file:

```bash
python rnd/capture/run_rnd001_tests.py
```

Pytest regression suite (isolated temp directory, safe for repeated runs):

```bash
python -m pytest tests/test_screen_capture.py -v
```

## Configuration / Properties

| Property | Value | Notes |
|---|---|---|
| Capture directory | `screenshots/raw/` (default) | Overridable via `--output-dir`; not read from `.env` yet — no env var was needed for this experiment |
| Capture monitor | mss index `1` (primary) | Overridable via `--monitor`; index `0` in mss is the combined virtual desktop, not an individual monitor |
| File format | PNG | Written via Pillow |
| Filename pattern | `screen_YYYYMMDD_HHMMSS_mmm.png` | Millisecond precision included |
| Capture count | `1` (default) | `--count N` for repeated captures |
| Delay between captures | `1.0s` (default) | `--delay SECONDS` |

## Coordinate System

- Origin `(0, 0)` is the **top-left** corner of the captured monitor.
- X increases left → right; Y increases top → bottom.
- Coordinates are in **physical screen pixels**, matching the monitor's
  native resolution (1920×1080 on this machine), **not** the
  Windows-scaled "logical" pixel space that non-DPI-aware applications see
  (see DPI findings — this distinction is the main risk area for this
  project and is why it was checked explicitly rather than assumed).
- `mss` capture width/height and `pyautogui.size()` were confirmed to
  report the **same physical-pixel dimensions** on this machine (see
  below), so a coordinate read off an `mss` screenshot can be handed
  directly to `pyautogui` without any scaling conversion, on this machine.
- This equivalence is what later Vision AI grounding experiments will rely
  on: a predicted `(x, y)` from a screenshot analyzed by the Vision AI can
  be passed straight to `pyautogui.click(x, y)` with no transform, **as
  long as future screenshots keep being captured at native resolution via
  `mss`** (i.e., screenshots must never be resized before being used for
  coordinate grounding, or a documented scale-back step will be required —
  see `docs/r_and_d/06_UI_Grounding.md`, not yet written).

## DPI / Windows Scaling Findings (actual, measured)

This machine is running **125% Windows display scaling**, not 100%. This
was not visible from the initial `mss`/`pyautogui` comparison alone — both
already agreed — so it was independently verified:

| Source | Reported resolution | DPI-aware? |
|---|---|---|
| `mss` (this experiment) | 1920 × 1080 | Yes (physical pixels) |
| `pyautogui.size()` (this experiment) | 1920 × 1080 | Yes (physical pixels) |
| WMI `Win32_VideoController` (native resolution, ground truth) | 1920 × 1080 | N/A — hardware-reported |
| Registry `AppliedDPI` (WindowMetrics) | 120 (= 125% of 96 base DPI) | — |
| .NET `Screen.PrimaryScreen.Bounds` from a plain PowerShell process | **1536 × 864** | **No** — virtualized/logical pixels |

**Finding:** Windows is scaling the display at 125%. A process that is
**not** DPI-aware (the ad-hoc PowerShell check above) is silently handed a
*virtualized* logical resolution (1536×864 = 1920÷1.25 × 1080÷1.25) instead
of the true 1920×1080 physical resolution. **`mss` and `pyautogui` were
both confirmed to report the true physical 1920×1080 resolution**, matching
the WMI-reported hardware resolution exactly — i.e., both libraries are
already DPI-aware on this machine and agree with each other and with
ground truth.

**Why this matters:** if either library had instead reported the
virtualized 1536×864 space, screenshots and mouse coordinates would be in
different coordinate systems and every later grounding/click experiment
would silently misfire by a consistent ~1.25x factor. That is not the case
here — but this must be re-verified on every new machine/monitor
configuration this POC is run on, since DPI-awareness behavior can differ
by OS version, per-monitor settings, or how the Python interpreter itself
was launched (e.g., via a shortcut with a DPI-awareness manifest override).
This check is cheap and should be re-run (`get_environment_info()`/SC-004)
before trusting grounding results on any new machine.

## Multi-Monitor Findings (actual, measured)

- `mss` reports **1 monitor** on this machine (`sct.monitors` has 2
  entries: index `0` = combined virtual desktop, index `1` = the single
  physical monitor — both report identical 1920×1080 geometry here since
  there's only one display).
- No multi-monitor automation was built. **Recommendation: restrict this
  POC to the primary monitor (mss index 1)** for now — this is already the
  module's default and was not changed. This keeps grounding coordinates
  unambiguous; multi-monitor support (monitor selection logic, coordinate
  offsets for non-primary monitors) is out of scope until/unless a real
  multi-monitor test environment is available to validate it against.

## Test Cases

| ID | Description |
|---|---|
| SC-001 | Single full-screen capture |
| SC-002 | PNG file validation (exists, opens, size matches) |
| SC-003 | Five sequential captures — repeated-capture reliability |
| SC-004 | MSS dimensions vs PyAutoGUI dimensions |
| SC-005 | Monitor enumeration |
| SC-006 | Timestamp / unique filename verification |
| SC-007 | Capture while Outlook is visible (presence only, no analysis) |

## Actual Test Results

Source: `results/raw/rnd001_screen_capture_results.json` (run on
2026-08-27).

| Test | Result | Observation |
|---|---|---|
| SC-001 | PASS | Captured 1920×1080 PNG, file created |
| SC-002 | PASS | File re-opened and verified with Pillow, dimensions matched |
| SC-003 | PASS | 5/5 sequential captures succeeded, all 5 filenames unique |
| SC-004 | PASS | mss 1920×1080 == pyautogui 1920×1080 |
| SC-005 | PASS | 1 monitor enumerated, geometry consistent with virtual desktop |
| SC-006 | PASS | Two captures ~1.1s apart produced unique filenames and strictly increasing timestamps |
| SC-007 | SKIPPED | `OUTLOOK.EXE` was not running on this machine at test time (verified via `tasklist`) — not run, not faked as a pass |

A pytest regression suite (`tests/test_screen_capture.py`, 4 tests) covering
single capture, repeated-capture uniqueness, environment info, and
timestamp parsing was also run and **passed 4/4** using an isolated
temp directory.

One captured screenshot was visually inspected directly (not just measured)
and confirmed to be an uncorrupted, accurate capture of the live desktop at
the time of capture.

## Failures / Observations

- No capture failures occurred across 17 total captures taken during this
  experiment (1 + 5 + 5 + 2 + 4 pytest = 17).
- The initial DPI check using `mss.mss()` triggered a `DeprecationWarning`
  (mss recommends `mss.MSS()`); the code was updated to use `mss.MSS()`
  before recording final results, so the results above are from the
  warning-free version.
- SC-007 could not be executed as a genuine test because Outlook was not
  open on this development machine at test time. This is recorded as
  `SKIPPED` with the reason stated, per the project rule against
  fabricating results. It should be re-run once Outlook is available
  (naturally will happen as part of RND-002, which requires Outlook open
  anyway to build the screenshot dataset).

## Limitations

- Only the primary monitor is currently targeted (see Multi-Monitor
  Findings) — this is an intentional, documented restriction, not an
  oversight.
- DPI/coordinate-space equivalence was verified on **this one machine**
  only, at 125% scaling with a single 1920×1080 monitor. It is not yet
  verified at 100%/150% scaling or on a different machine, and should not
  be assumed to hold universally — re-check via SC-004 on any new target
  machine.
- SC-007 (Outlook-visible capture) was skipped, not passed — visual capture
  of an actual Outlook window has not yet been confirmed.

## Conclusion

**Yes** — screen capture is reliable enough to proceed to RND-002. Across
17 real captures, every capture succeeded, every file validated correctly
with Pillow, filenames were reliably unique, and — most importantly for
this project — `mss` and `pyautogui` were independently confirmed to agree
on the true physical screen resolution (1920×1080) even though Windows is
actively applying 125% display scaling underneath them. The coordinate
system is therefore trustworthy on this machine: a pixel coordinate read
from an `mss` screenshot can be passed directly to `pyautogui` without
conversion.

## Next Step

**RND-002 — Controlled Outlook Screenshot Dataset.** Manually prepare
Outlook in the ~10 states described in the project brief (inbox, email
opened, reply visible, reply editor open, maximized, resized, different
email, long/short body, popup present) and capture each with this module,
building the ground-truth dataset that RND-004 (Screen Understanding) and
RND-005 (UI Grounding) will be measured against. This will also be the
first opportunity to actually run SC-007 for real.
