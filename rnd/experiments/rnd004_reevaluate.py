"""Re-scores an existing RND-004 results file against the current
evaluator logic, WITHOUT calling Gemini again. Used when the local
evaluator itself had a bug (e.g. the "compose" keyword false-positive
fixed after the 2026-08-28 run) — the raw predictions already collected
are real API data; only the grading of them needs to be redone.

Run (from outlook-vision-poc/):
    python rnd/experiments/rnd004_reevaluate.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from rnd.experiments.rnd004_screen_understanding import (  # noqa: E402
    KNOWN_ABSENT_CONTROLS,
    REPORT_PATH,
    RESULTS_PATH,
    build_report,
    expected_relevant_controls_for,
    load_required_cases,
)
from rnd.metrics.evaluator import evaluate_case  # noqa: E402
from rnd.models.screen_understanding import RND004CaseResult  # noqa: E402


def main() -> None:
    existing = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
    cases_by_id = {c["test_id"]: c for c in load_required_cases()}

    rescored: list[RND004CaseResult] = []
    for raw in existing["cases"]:
        test_id = raw["test_id"]
        case = cases_by_id[test_id]
        expected_action = case["expected"].get("recommended_action")
        expected_controls = expected_relevant_controls_for(case)
        known_absent = KNOWN_ABSENT_CONTROLS.get(test_id, [])

        eval_fields = evaluate_case(
            expected_application=case["expected"]["application"],
            expected_state=case["expected"]["state"],
            expected_action=expected_action,
            expected_relevant_controls=expected_controls,
            predicted_application=raw.get("predicted_application"),
            predicted_state=raw.get("predicted_state"),
            predicted_relevant_controls=raw.get("predicted_relevant_controls"),
            predicted_action=raw.get("predicted_action"),
            predicted_target=raw.get("predicted_target"),
            known_absent_controls=known_absent,
        )
        failure_types = list(eval_fields.pop("failure_types"))
        if not raw["schema_valid"]:
            failure_types.append("INVALID_SCHEMA")

        merged = {**raw, **eval_fields, "failure_types": failure_types}
        rescored.append(RND004CaseResult.model_validate(merged))

    existing["cases"] = [json.loads(r.model_dump_json()) for r in rescored]
    existing["reevaluated_note"] = (
        "Re-scored after fixing a local evaluator bug (normalize_action's 'compose' keyword "
        "false-positive) — raw model predictions are UNCHANGED from the original run; only "
        "local grading was redone. No new Gemini calls were made."
    )
    RESULTS_PATH.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    REPORT_PATH.write_text(build_report(rescored), encoding="utf-8")
    print(f"Re-scored {len(rescored)} cases from existing raw predictions (no new API calls).")
    print(f"Updated: {RESULTS_PATH.relative_to(PROJECT_ROOT)}")
    print(f"Updated: {REPORT_PATH.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
