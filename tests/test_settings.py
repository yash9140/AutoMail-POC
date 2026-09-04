"""app/config/settings.py unit tests — every constant migrated from the
scattered RND-stage modules must keep its EXACT original value (this is
a pure reorganization, not a behavior change), and get_provider()/
estimate_cost() must behave like the duplicated helpers they replace.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402


def test_launch_timing_constants_unchanged():
    assert settings.WINDOWS_KEY_STABILIZE_SECONDS == 0.9
    assert settings.SEARCH_TYPE_STABILIZE_SECONDS == 1.2
    assert settings.TYPE_INTERVAL_SECONDS == 0.03
    assert settings.MOVE_DURATION_SECONDS == 0.6
    assert settings.OUTLOOK_LAUNCH_INITIAL_WAIT_SECONDS == 2.0
    assert settings.OUTLOOK_LAUNCH_POLL_INTERVAL_SECONDS == 1.0
    assert settings.OUTLOOK_LAUNCH_TIMEOUT_SECONDS == 18.0
    assert settings.READINESS_INITIAL_WAIT_SECONDS == 6.0
    assert settings.READINESS_RETRY_WAIT_SECONDS == 3.0
    assert settings.MAX_READINESS_ATTEMPTS == 2


def test_find_email_constants_unchanged():
    assert settings.POST_CLICK_INITIAL_WAIT_SECONDS == 1.5
    assert settings.POST_CLICK_RETRY_WAIT_SECONDS == 1.0
    assert settings.MAX_EMAIL_OPEN_VERIFICATION_ATTEMPTS == 2
    assert settings.LEFT_SIDEBAR_MAX_X_FRACTION == 0.17


def test_reply_draft_constants_unchanged():
    assert settings.FOCUS_SETTLE_DELAY_SECONDS == 0.6
    assert settings.REPLY_EDITOR_INITIAL_WAIT_SECONDS == 1.5
    assert settings.REPLY_EDITOR_RETRY_WAIT_SECONDS == 1.0
    assert settings.MAX_REPLY_EDITOR_VERIFICATION_ATTEMPTS == 2
    assert settings.POST_TYPING_WAIT_SECONDS == 1.5
    assert settings.DRAFT_MIN_LENGTH == 5
    assert settings.PLACEHOLDER_MARKERS == ("[insert", "TODO", "lorem ipsum", "<placeholder>", "XXX")


def test_consolidated_confidence_threshold_matches_both_original_values():
    # GROUNDING_CONFIDENCE_THRESHOLD and EMAIL_GROUNDING_CONFIDENCE_THRESHOLD
    # were both 0.6 in the pre-refactor code — consolidating them into one
    # named constant changes nothing behaviorally.
    assert settings.VISION_CONFIDENCE_THRESHOLD == 0.6


def test_outlook_ready_timeout_alias_matches_launch_timeout():
    assert settings.OUTLOOK_READY_TIMEOUT == settings.OUTLOOK_LAUNCH_TIMEOUT_SECONDS


def test_declared_for_later_phase_constants_exist():
    # Not yet referenced by any step module this phase — just declared
    # so later phases have one place to add their policy to.
    assert settings.MAX_MESSAGE_LIST_SCROLL_ATTEMPTS > 0
    assert settings.MAX_EMAIL_BODY_SCROLL_ATTEMPTS > 0
    assert settings.MAX_REPLY_SEARCH_SCROLL_ATTEMPTS > 0
    assert settings.PROVIDER_RETRY_COUNT >= 0
    assert settings.SEND_CLICK_MAX == 1


def test_get_provider_raises_without_configured_key(monkeypatch, tmp_path):
    empty_env = tmp_path / ".env"
    empty_env.write_text("", encoding="utf-8")
    monkeypatch.setattr(settings, "PROJECT_ROOT", tmp_path)
    monkeypatch.setenv("AI_PROVIDER", "anthropic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    try:
        settings.get_provider()
        assert False, "expected SystemExit"
    except SystemExit:
        pass


def test_get_provider_constructs_anthropic_provider(monkeypatch, tmp_path):
    from app.vision.providers.anthropic_provider import AnthropicProvider

    empty_env = tmp_path / ".env"
    empty_env.write_text("", encoding="utf-8")
    monkeypatch.setattr(settings, "PROJECT_ROOT", tmp_path)
    monkeypatch.setenv("AI_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-sonnet-5")
    provider, model = settings.get_provider()
    assert model == "claude-sonnet-5"
    assert provider.model == "claude-sonnet-5"
    assert isinstance(provider, AnthropicProvider)
    assert provider.provider_name == "anthropic"


def test_get_provider_constructs_gemini_provider(monkeypatch, tmp_path):
    from app.vision.providers.gemini_provider import GeminiProvider

    empty_env = tmp_path / ".env"
    empty_env.write_text("", encoding="utf-8")
    monkeypatch.setattr(settings, "PROJECT_ROOT", tmp_path)
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.6-flash")
    provider, model = settings.get_provider()
    assert model == "gemini-3.6-flash"
    assert provider.model == "gemini-3.6-flash"
    assert isinstance(provider, GeminiProvider)
    assert provider.provider_name == "gemini"


def test_get_provider_raises_without_configured_gemini_key(monkeypatch, tmp_path):
    empty_env = tmp_path / ".env"
    empty_env.write_text("", encoding="utf-8")
    monkeypatch.setattr(settings, "PROJECT_ROOT", tmp_path)
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    try:
        settings.get_provider()
        assert False, "expected SystemExit"
    except SystemExit:
        pass


def test_get_ai_provider_name_defaults_to_anthropic_when_unset(monkeypatch, tmp_path):
    empty_env = tmp_path / ".env"
    empty_env.write_text("", encoding="utf-8")
    monkeypatch.setattr(settings, "PROJECT_ROOT", tmp_path)
    monkeypatch.delenv("AI_PROVIDER", raising=False)
    assert settings.get_ai_provider_name() == "anthropic"


def test_get_ai_provider_name_reads_explicit_value(monkeypatch, tmp_path):
    empty_env = tmp_path / ".env"
    empty_env.write_text("", encoding="utf-8")
    monkeypatch.setattr(settings, "PROJECT_ROOT", tmp_path)
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    assert settings.get_ai_provider_name() == "gemini"


def test_get_ai_provider_name_rejects_unsupported_value(monkeypatch, tmp_path):
    empty_env = tmp_path / ".env"
    empty_env.write_text("", encoding="utf-8")
    monkeypatch.setattr(settings, "PROJECT_ROOT", tmp_path)
    monkeypatch.setenv("AI_PROVIDER", "openai")
    try:
        settings.get_ai_provider_name()
        assert False, "expected SystemExit"
    except SystemExit:
        pass


def test_get_provider_never_reads_gemini_or_openai_env_vars_in_anthropic_mode(monkeypatch, tmp_path):
    """No fallback to Gemini/OpenAI while AI_PROVIDER=anthropic —
    get_provider() must not even look at those env vars, let alone
    construct those providers, even when they ARE configured (as they
    are in this project's real .env)."""
    empty_env = tmp_path / ".env"
    empty_env.write_text("", encoding="utf-8")
    monkeypatch.setattr(settings, "PROJECT_ROOT", tmp_path)
    monkeypatch.setenv("AI_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-sonnet-5")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-fake-key")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.6-flash")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-fake-key")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o")
    provider, model = settings.get_provider()
    assert model == "claude-sonnet-5"
    assert provider.provider_name == "anthropic"


def test_get_provider_never_reads_anthropic_or_openai_env_vars_in_gemini_mode(monkeypatch, tmp_path):
    """Mirror of the above for AI_PROVIDER=gemini."""
    empty_env = tmp_path / ".env"
    empty_env.write_text("", encoding="utf-8")
    monkeypatch.setattr(settings, "PROJECT_ROOT", tmp_path)
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-fake-key")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.6-flash")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-fake-key")
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-sonnet-5")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-fake-key")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o")
    provider, model = settings.get_provider()
    assert model == "gemini-3.6-flash"
    assert provider.provider_name == "gemini"
