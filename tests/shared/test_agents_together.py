"""Tests for Together AI provider support in shared/agents.py."""
import os
import pytest
from unittest.mock import patch

from shared import agents


def test_resolve_provider_together(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "together")
    monkeypatch.setenv("TOGETHER_API_KEY", "test-key")
    # Reset all three cached provider globals (matches established precedent in test_agents_real.py)
    agents._LLM_PROVIDER = None
    agents._is_local = None
    agents._use_fallback_models = None
    assert agents._resolve_provider() == "together"


def test_get_model_name_together(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "together")
    monkeypatch.setenv("TOGETHER_API_KEY", "test-key")
    monkeypatch.setenv("TOGETHER_MODEL", "Qwen/Qwen3-30B-A3B-Instruct")
    # Reset all three cached provider globals (matches established precedent in test_agents_real.py)
    agents._LLM_PROVIDER = None
    agents._is_local = None
    agents._use_fallback_models = None
    assert agents.get_model_name() == "Qwen/Qwen3-30B-A3B-Instruct"


def test_make_together_llm_uses_openai_base(monkeypatch):
    monkeypatch.setenv("TOGETHER_API_KEY", "test-key")
    llm = agents._make_together_llm(
        temperature=0.3, model="Qwen/Qwen3-30B-A3B-Instruct",
        timeout=30.0, max_tokens=2000,
    )
    assert "together.xyz" in str(llm.openai_api_base)
    assert llm.model_name == "Qwen/Qwen3-30B-A3B-Instruct"
