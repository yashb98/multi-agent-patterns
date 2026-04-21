"""Tests for get_llm max_tokens default."""

import shared.agents as _agents_mod
from shared.agents import get_llm


def _reset_provider_cache(monkeypatch):
    """Reset cached provider state so _ensure_provider re-evaluates env vars."""
    monkeypatch.setattr(_agents_mod, "_LLM_PROVIDER", None)
    monkeypatch.setattr(_agents_mod, "_is_local", None)
    monkeypatch.setattr(_agents_mod, "_use_fallback_models", None)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-for-testing")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)


def test_get_llm_has_max_tokens_default(monkeypatch):
    """get_llm() must set max_tokens to prevent unbounded output."""
    _reset_provider_cache(monkeypatch)
    llm = get_llm()
    assert llm.max_tokens is not None
    assert llm.max_tokens == 4096


def test_get_llm_respects_explicit_max_tokens(monkeypatch):
    """Callers can override max_tokens."""
    _reset_provider_cache(monkeypatch)
    llm = get_llm(max_tokens=1024)
    assert llm.max_tokens == 1024
