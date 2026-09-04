# results/ — preserved historical run evidence

Raw, per-test machine-readable results and aggregated reports from the
R&D experiments (RND-001 through RND-009D). Never modified after being
written — historical failures are kept as POC evidence, not cleaned up.

- `raw/provider_responses/<experiment>/` — one JSON file per Vision API
  call made during that experiment, verbatim.
- `raw/*_pending/` — in-progress/incomplete result sets from experiments
  that were re-run (kept alongside the final result, not overwritten).
- `reports/` — aggregated per-experiment summary reports.

Written only by `rnd/experiments/*.py` and `rnd/capture/
run_rnd001_tests.py` — never read or written by the live `app/`
runtime. Not moved during the 2026-09-04 structure cleanup; its
substructure is already self-consistent with the layout those scripts
expect.
