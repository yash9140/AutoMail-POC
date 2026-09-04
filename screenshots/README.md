# screenshots/ — live runtime output + historical R&D dataset

**`raw/` is not purely historical — it's the live runtime's active
screenshot output directory right now.** `app/automation/screen_capture.py
::capture_screen()` writes every screenshot taken during a real POC run
(every Vision call, live or R&D) to `screenshots/raw/`. `rnd/capture/
screen_capture.py` (the frozen R&D original) writes to the same path.
This is why `screenshots/` was not relocated during the 2026-09-04
structure cleanup — moving it would require changing a live runtime
path constant, not just moving files.

- `raw/` — every captured screenshot, live runtime output mixed with
  historical R&D captures (not separable without a runtime code change).
- `annotated/` — R&D ground-truth annotation overlays, written by
  `rnd/experiments/annotate_ground_truth.py` and `generate_annotated_
  previews.py`. Historical only.
- `app/` — current POC UI screenshots (Login/Permissions/Dashboard),
  captured by `scripts/capture_app_screenshots.py` for documentation.

`raw/*` and `annotated/*` are gitignored (see `.gitignore`) except for
`.gitkeep` placeholders — the files present in a working copy are local
run history, not tracked evidence.
