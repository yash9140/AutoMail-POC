"""Structural import-boundary check (2026-09-04 structure reorganization).

Confirms `app/` — the live runtime — never imports from `benchmarks/`,
`tests/`, `results/`, or `debug/`. `rnd/` is a DELIBERATE, documented
exception (see rnd/README.md and docs/decisions/
STRUCTURE_REORGANIZATION.md) — several app/outlook/*.py modules reuse
frozen Pydantic models and prompt files from rnd/ by original design, so
it is not checked here.

No real Vision/API calls, no pyautogui, no live Outlook interaction —
pure static source-text scanning, same technique as
test_app_ui.py::test_no_pyautogui_actions_occur_outside_the_named_outlook_modules.
"""

import re
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1] / "app"

FORBIDDEN_NAMESPACES = ("benchmarks", "tests", "results", "debug", "research")

IMPORT_PATTERN = re.compile(
    r"^\s*(?:from|import)\s+(" + "|".join(FORBIDDEN_NAMESPACES) + r")\b",
    re.MULTILINE,
)


def test_app_source_never_imports_forbidden_namespaces():
    offenders = []
    for py_file in APP_DIR.rglob("*.py"):
        source = py_file.read_text(encoding="utf-8")
        for match in IMPORT_PATTERN.finditer(source):
            line_no = source.count("\n", 0, match.start()) + 1
            offenders.append(f"{py_file.relative_to(APP_DIR.parent)}:{line_no}: {match.group(0).strip()}")
    assert not offenders, (
        "app/ must never import from benchmarks/, tests/, results/, debug/, "
        "or research/ — found:\n" + "\n".join(offenders)
    )


def test_app_dir_actually_has_python_files():
    """Guards against the scan above silently passing because APP_DIR
    resolved to the wrong path after a future move."""
    assert list(APP_DIR.rglob("*.py")), f"No .py files found under {APP_DIR} — path resolution is broken"
