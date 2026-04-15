"""Tests for get_llm max_tokens default."""

from shared.agents import get_llm


def test_get_llm_has_max_tokens_default():
    """get_llm() must set max_tokens to prevent unbounded output."""
    llm = get_llm()
    assert llm.max_tokens is not None
    assert llm.max_tokens == 4096


def test_get_llm_respects_explicit_max_tokens():
    """Callers can override max_tokens."""
    llm = get_llm(max_tokens=1024)
    assert llm.max_tokens == 1024
