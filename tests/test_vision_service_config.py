"""app/vision/service.py composition-root tests: get_vision_service(),
get_primary_vision_provider_name(), get_fallback_vision_provider_name()
— the Claude-primary/Gemini-fallback configuration layer (2026-09-05).

Lives in app/vision/service.py rather than app/config/settings.py
specifically to avoid a circular import (app.fallback.recovery, which
this module already imports, itself imports app.config.settings for
PROVIDER_RETRY_COUNT — see service.py's own comment on this). app.config
.settings.get_provider() (single-provider, no fallback) is left
completely untouched and is tested separately in test_settings.py; none
of its own tests needed to change as a result of this file's addition.

Same env-isolation pattern test_settings.py already uses: an empty
tmp_path/.env plus monkeypatch.setenv/delenv, so these tests never read
the project's real .env or make a real API call.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.vision import service as vision_service  # noqa: E402


def _isolate_env(monkeypatch, tmp_path) -> None:
    empty_env = tmp_path / ".env"
    empty_env.write_text("", encoding="utf-8")
    monkeypatch.setattr(vision_service, "PROJECT_ROOT", tmp_path)
    # get_primary_vision_provider_name()'s backward-compat fallback calls
    # settings.get_ai_provider_name(), which does its OWN load_dotenv()
    # against settings.PROJECT_ROOT — must be isolated too, or a real
    # .env sitting next to this repo (which legitimately has
    # PRIMARY_VISION_PROVIDER/FALLBACK_VISION_PROVIDER set) leaks in via
    # dotenv's "don't override an already-set var, but do set unset ones"
    # default behavior.
    monkeypatch.setattr(settings, "PROJECT_ROOT", tmp_path)


# --- Backward compatibility: only AI_PROVIDER set ---

def test_primary_falls_back_to_AI_PROVIDER_when_PRIMARY_VISION_PROVIDER_unset(monkeypatch, tmp_path):
    _isolate_env(monkeypatch, tmp_path)
    monkeypatch.delenv("PRIMARY_VISION_PROVIDER", raising=False)
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    assert vision_service.get_primary_vision_provider_name() == "gemini"


def test_fallback_is_none_when_FALLBACK_VISION_PROVIDER_unset(monkeypatch, tmp_path):
    _isolate_env(monkeypatch, tmp_path)
    monkeypatch.delenv("FALLBACK_VISION_PROVIDER", raising=False)
    monkeypatch.setenv("AI_PROVIDER", "anthropic")
    assert vision_service.get_fallback_vision_provider_name() is None


def test_get_vision_service_matches_todays_zero_fallback_behavior_with_only_AI_PROVIDER_set(monkeypatch, tmp_path):
    from app.vision.providers.gemini_provider import GeminiProvider

    _isolate_env(monkeypatch, tmp_path)
    monkeypatch.delenv("PRIMARY_VISION_PROVIDER", raising=False)
    monkeypatch.delenv("FALLBACK_VISION_PROVIDER", raising=False)
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.6-flash")

    service, model = vision_service.get_vision_service()
    assert model == "gemini-3.6-flash"
    assert isinstance(service.primary, GeminiProvider)
    assert service.fallback is None


# --- New explicit primary+fallback configuration ---

def test_get_vision_service_constructs_claude_primary_gemini_fallback(monkeypatch, tmp_path):
    from app.vision.providers.anthropic_provider import AnthropicProvider
    from app.vision.providers.gemini_provider import GeminiProvider

    _isolate_env(monkeypatch, tmp_path)
    monkeypatch.setenv("PRIMARY_VISION_PROVIDER", "anthropic")
    monkeypatch.setenv("FALLBACK_VISION_PROVIDER", "gemini")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-anthropic-key")
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-sonnet-5")
    monkeypatch.setenv("GEMINI_API_KEY", "fake-gemini-key")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.6-flash")

    service, primary_model = vision_service.get_vision_service()
    assert primary_model == "claude-sonnet-5"
    assert isinstance(service.primary, AnthropicProvider)
    assert isinstance(service.fallback, GeminiProvider)


def test_fallback_same_as_primary_means_no_self_fallback(monkeypatch, tmp_path):
    _isolate_env(monkeypatch, tmp_path)
    monkeypatch.setenv("PRIMARY_VISION_PROVIDER", "anthropic")
    monkeypatch.setenv("FALLBACK_VISION_PROVIDER", "anthropic")
    assert vision_service.get_fallback_vision_provider_name() is None


def test_get_vision_service_fallback_none_when_configured_equal_to_primary(monkeypatch, tmp_path):
    _isolate_env(monkeypatch, tmp_path)
    monkeypatch.setenv("PRIMARY_VISION_PROVIDER", "gemini")
    monkeypatch.setenv("FALLBACK_VISION_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.6-flash")

    service, _ = vision_service.get_vision_service()
    assert service.fallback is None


# --- Isolation: each provider's env vars are read only when actually selected ---

def test_get_vision_service_never_reads_unconfigured_third_providers_key(monkeypatch, tmp_path):
    """PRIMARY=anthropic, FALLBACK=gemini — OpenAI is never selected as
    either, so this must never even look at an OPENAI_* var, let alone
    construct that provider."""
    _isolate_env(monkeypatch, tmp_path)
    monkeypatch.setenv("PRIMARY_VISION_PROVIDER", "anthropic")
    monkeypatch.setenv("FALLBACK_VISION_PROVIDER", "gemini")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-sonnet-5")
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.6-flash")
    monkeypatch.setenv("OPENAI_API_KEY", "should-never-be-read")

    service, _ = vision_service.get_vision_service()
    assert service.primary.provider_name == "anthropic"
    assert service.fallback.provider_name == "gemini"


# --- Error handling: missing keys / unsupported names ---

def test_get_vision_service_raises_when_fallback_key_missing(monkeypatch, tmp_path):
    _isolate_env(monkeypatch, tmp_path)
    monkeypatch.setenv("PRIMARY_VISION_PROVIDER", "anthropic")
    monkeypatch.setenv("FALLBACK_VISION_PROVIDER", "gemini")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-sonnet-5")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_MODEL", raising=False)

    try:
        vision_service.get_vision_service()
        assert False, "expected SystemExit"
    except SystemExit:
        pass


def test_get_primary_vision_provider_name_rejects_unsupported_value(monkeypatch, tmp_path):
    _isolate_env(monkeypatch, tmp_path)
    monkeypatch.setenv("PRIMARY_VISION_PROVIDER", "openai")
    try:
        vision_service.get_primary_vision_provider_name()
        assert False, "expected SystemExit"
    except SystemExit:
        pass


def test_get_fallback_vision_provider_name_rejects_unsupported_value(monkeypatch, tmp_path):
    _isolate_env(monkeypatch, tmp_path)
    monkeypatch.setenv("PRIMARY_VISION_PROVIDER", "anthropic")
    monkeypatch.setenv("FALLBACK_VISION_PROVIDER", "openai")
    try:
        vision_service.get_fallback_vision_provider_name()
        assert False, "expected SystemExit"
    except SystemExit:
        pass
