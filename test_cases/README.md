# test_cases/ — controlled test-email definitions

Shared fixture data used by **both** the live-runtime automated tests
(`tests/test_find_email.py`, `tests/test_outlook_reply.py`) and the
historical R&D dataset-validation scripts (`rnd/experiments/
annotate_ground_truth.py`, `validate_outlook_dataset.py`, and others).
Kept at the repo root — not moved into `tests/fixtures/` — because
relocating it would break the `rnd/experiments/*.py` scripts' own
hardcoded `PROJECT_ROOT`-relative paths, which are frozen/never edited.

- `outlook/dataset_manifest.json` — the RND-002 controlled Outlook
  screenshot dataset manifest.
- `outlook/test_emails.json` — test-email definitions (sender/subject
  pairs) used for grounding/matching test fixtures.
