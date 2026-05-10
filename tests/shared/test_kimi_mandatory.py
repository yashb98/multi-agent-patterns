"""Kimi (Moonshot) is the mandatory cloud LLM provider.

The OpenAI key is exhausted (per the 2026-05-09 plan); every cloud
LLM call must route through Kimi via its OpenAI-compatible endpoint.
``shared/agents.get_openai_client`` and ``_make_openai_llm`` honour
``KimiAI_API_KEY`` and rewrite OpenAI-style model names like
``gpt-5-mini`` → ``moonshot-v1-auto`` so existing callers don't 4xx
against the Moonshot endpoint.
"""

from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def reload_agents(monkeypatch):
    """Reload shared.agents so module-level _MODEL_OVERRIDE etc. honour
    the fresh env vars the test sets."""

    import shared.agents as _agents

    def _reload():
        return importlib.reload(_agents)

    yield _reload
    importlib.reload(_agents)


def test_kimi_key_routes_client_to_moonshot_endpoint(monkeypatch, reload_agents):
    monkeypatch.setenv("KimiAI_API_KEY", "kimi-test-key")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("LLM_MODEL_OVERRIDE", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    agents = reload_agents()

    client = agents.get_openai_client(timeout=5.0)
    assert "moonshot.ai" in str(client.base_url), (
        f"Expected Kimi endpoint, got {client.base_url}"
    )
    assert client.api_key == "kimi-test-key"


def test_kimi_remaps_openai_default_model(monkeypatch, reload_agents):
    monkeypatch.setenv("KimiAI_API_KEY", "kimi-test-key")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("LLM_MODEL_OVERRIDE", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    agents = reload_agents()

    assert agents.get_model_name("gpt-5-mini") == "moonshot-v1-auto"
    assert agents.get_model_name("gpt-4o") == "moonshot-v1-auto"
    # Already-Kimi names pass through.
    assert agents.get_model_name("kimi-k2.6") == "kimi-k2.6"


def test_kimi_unset_falls_back_to_openai_when_key_present(monkeypatch, reload_agents):
    monkeypatch.delenv("KimiAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "openai-test-key")
    monkeypatch.delenv("LLM_MODEL_OVERRIDE", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    agents = reload_agents()

    client = agents.get_openai_client(timeout=5.0)
    assert client.api_key == "openai-test-key"
    # Stays on default OpenAI base url.
    assert "moonshot" not in str(client.base_url).lower()


def test_no_credentials_raises(monkeypatch, reload_agents):
    monkeypatch.delenv("KimiAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    agents = reload_agents()

    with pytest.raises(RuntimeError, match="No LLM credentials configured"):
        agents.get_openai_client(timeout=5.0)


def test_local_provider_unchanged_by_kimi_key(monkeypatch, reload_agents):
    """LLM_PROVIDER=local still uses Ollama regardless of KimiAI_API_KEY —
    the local-LLM path is independent of cloud credentials."""

    monkeypatch.setenv("KimiAI_API_KEY", "kimi-test-key")
    monkeypatch.setenv("LLM_PROVIDER", "local")
    agents = reload_agents()

    client = agents.get_openai_client(timeout=5.0)
    assert client.api_key == "ollama"
    assert "moonshot" not in str(client.base_url).lower()


def test_kimi_remap_helper_passthrough_for_kimi_names():
    from shared.agents import _kimi_remap_model
    for name in ("kimi-k2.6", "moonshot-v1-auto", "kimi-k3-pro", "K2.6"):
        assert _kimi_remap_model(name) == name
