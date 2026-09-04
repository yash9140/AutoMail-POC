"""Evaluation-only grounding-strategy experiments (2026-09-03).

Compares 4 candidate OUTLOOK_SEARCH grounding strategies against the
same real screenshots via the real Anthropic API — see run_comparison.py
for the CLI. Nothing here is imported by, or modifies, app/outlook/
launch.py or any runtime prompt/schema. Strategy A is the current
runtime baseline (unchanged prompt/schema, imported read-only from
app.vision.models / the real prompt file path). Strategies B, C, D are
new prompt/schema variants that live entirely under this directory.
"""
