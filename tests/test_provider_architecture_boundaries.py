"""Structural test (2026-09-05, Claude-primary/Gemini-fallback provider
architecture): business/runtime modules must depend on VisionService,
never construct a concrete AnthropicProvider/GeminiProvider directly.

Provider construction is isolated to ONE composition root:
app.vision.service.get_vision_service() (and the legacy, still-supported
app.config.settings.get_provider()). Everything downstream — app/
outlook/, app/playbook/, app/automation/, app/safety/, app/workers/ —
must depend only on the provider-neutral VisionService/VisionProvider
interfaces, never import or call AnthropicProvider(...)/GeminiProvider(...)
themselves. This is what makes Claude and Gemini truly interchangeable:
if any business module could construct a provider directly, it could
also branch on which one it built, silently reintroducing a second
Outlook flow.

Same "read source, assert string absence" technique already used by
test_provider_migration.py::test_I_openai_never_constructed_by_get_provider
and test_import_boundaries.py. No real Vision/API calls, no pyautogui,
no live Outlook interaction — pure static source-text scanning.
"""

import inspect
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1] / "app"

# The ONE allowed construction site for each concrete provider class.
ALLOWED_CONSTRUCTION_MODULES = {
    "app.config.settings",  # legacy get_provider() — single-provider, no fallback
    "app.vision.service",   # get_vision_service() — the new composition root
}

BUSINESS_PACKAGES = ("outlook", "playbook", "automation", "safety", "workers", "controllers", "ui")


def _iter_business_py_files():
    for package in BUSINESS_PACKAGES:
        package_dir = APP_DIR / package
        if package_dir.is_dir():
            yield from package_dir.rglob("*.py")


def test_business_modules_never_construct_a_concrete_provider_directly():
    offenders = []
    for py_file in _iter_business_py_files():
        source = py_file.read_text(encoding="utf-8")
        if "AnthropicProvider(" in source or "GeminiProvider(" in source:
            offenders.append(str(py_file.relative_to(APP_DIR.parent)))
    assert not offenders, (
        "These business modules construct a concrete provider directly — provider "
        "construction must stay isolated to the composition root "
        f"(app.vision.service.get_vision_service()): {offenders}"
    )


def test_business_modules_never_import_concrete_provider_classes():
    """Stricter than the construction check above: importing the class
    at all (even unused) is itself a smell that invites a future
    provider-name branch. app/vision/service.py and app/config/settings.py
    are the only modules allowed to know these classes exist."""
    offenders = []
    for py_file in _iter_business_py_files():
        source = py_file.read_text(encoding="utf-8")
        if "from app.vision.providers.anthropic_provider import" in source:
            offenders.append(f"{py_file.relative_to(APP_DIR.parent)}: imports AnthropicProvider")
        if "from app.vision.providers.gemini_provider import" in source:
            offenders.append(f"{py_file.relative_to(APP_DIR.parent)}: imports GeminiProvider")
    assert not offenders, offenders


def test_only_the_composition_root_modules_construct_concrete_providers():
    """Positive-side check: confirm the construction sites we expect to
    exist actually do — guards against this whole test file silently
    passing because the construction moved somewhere unlabeled."""
    settings_source = inspect.getsource(__import__("app.config.settings", fromlist=["x"]))
    service_source = inspect.getsource(__import__("app.vision.service", fromlist=["x"]))
    assert "AnthropicProvider(" in settings_source and "GeminiProvider(" in settings_source
    assert "AnthropicProvider(" in service_source and "GeminiProvider(" in service_source


def test_outlook_modules_contain_no_provider_name_string_branching():
    """app/outlook/*.py must never branch on "gemini"/"anthropic"/"claude"
    literal strings — the ONE historical exception (launch.py's
    OUTLOOK_SEARCH dispatch) was removed in the 2026-09-05 unification;
    this pins that it stays removed. app/config/settings.py and app/
    vision/service.py are the composition root and are exempt — they
    are INFRASTRUCTURE selecting which provider to construct, not
    Outlook business logic branching on which one is active."""
    offenders = []
    for py_file in (APP_DIR / "outlook").rglob("*.py"):
        source = py_file.read_text(encoding="utf-8")
        for needle in ('"gemini"', "'gemini'", '"anthropic"', "'anthropic'", '"claude"', "'claude'"):
            if needle in source:
                offenders.append(f"{py_file.relative_to(APP_DIR.parent)}: contains {needle}")
    assert not offenders, (
        "app/outlook/*.py must stay provider-neutral — found literal provider-name "
        f"references: {offenders}"
    )


def test_playbook_state_machine_has_no_provider_specific_states():
    from app.playbook.states import PlaybookState

    for state in PlaybookState:
        name = state.value.lower()
        assert "claude" not in name and "gemini" not in name and "anthropic" not in name, (
            f"PlaybookState.{state.name} looks provider-specific — playbook states must "
            "represent workflow/business state only, never which provider is active."
        )


def test_business_dirs_actually_scanned():
    """Guards against the scans above silently passing because
    BUSINESS_PACKAGES resolved to the wrong paths after a future move."""
    found_any = False
    for package in BUSINESS_PACKAGES:
        if (APP_DIR / package).is_dir():
            found_any = True
    assert found_any, f"None of {BUSINESS_PACKAGES} found under {APP_DIR} — path resolution is broken"
