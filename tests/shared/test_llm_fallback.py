"""Tests for multi-provider LLM fallback."""

import pytest
from unittest.mock import patch, MagicMock
from shared.llm_fallback import FallbackLLM, ProviderError


def test_primary_provider_works():
    """When primary succeeds, no fallback needed."""
    fb = FallbackLLM(providers=["openai", "anthropic"])
    with patch.object(fb, "_call_openai", return_value="Hello"):
        result = fb.invoke("test prompt")
        assert result == "Hello"


def test_fallback_on_primary_failure():
    """When primary fails, falls back to secondary."""
    fb = FallbackLLM(providers=["openai", "anthropic"])
    with patch.object(fb, "_call_openai", side_effect=ProviderError("503")):
        with patch.object(fb, "_call_anthropic", return_value="Fallback response"):
            result = fb.invoke("test prompt")
            assert result == "Fallback response"


def test_all_providers_fail():
    """When all providers fail, raises last error."""
    fb = FallbackLLM(providers=["openai", "anthropic"])
    with patch.object(fb, "_call_openai", side_effect=ProviderError("503")):
        with patch.object(fb, "_call_anthropic", side_effect=ProviderError("500")):
            with pytest.raises(ProviderError):
                fb.invoke("test prompt")
