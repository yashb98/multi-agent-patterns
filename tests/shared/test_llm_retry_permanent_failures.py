"""Wiring test: permanent-failure patterns short-circuit retry.

Live regression on 2026-05-05: Ollama returned HTTP 500 with
"model requires more system memory (21.9 GiB) than is available (19.6 GiB)".
The retry layer treated it as transient (it's a 5xx) and burned 4 retries
× 5-15s before the cloud-fallback layer kicked in. Across many LLM calls
per application, that's 5-10+ minutes lost to known-permanent failures.

This fix adds a permanent-failure fast-path: when the error message
contains one of the known permanent patterns (memory exhaustion, model
not found, bad API key, etc.), is_retryable_error returns False
immediately and the caller can fall back without retry-induced delay.
"""
from __future__ import annotations
import pytest


def test_ollama_oom_500_is_not_retryable():
    """Ollama OOM is permanent for this session — must not retry."""
    from shared.llm_retry import is_retryable_error

    err = Exception(
        "Error code: 500 - {'error': {'message': 'model requires more "
        "system memory (21.9 GiB) than is available (19.6 GiB)', "
        "'type': 'api_error'}}"
    )
    assert is_retryable_error(err) is False


def test_generic_500_is_still_retryable():
    """A plain 500 without OOM/permanent markers is still retryable."""
    from shared.llm_retry import is_retryable_error
    err = Exception("Error code: 500 - server temporarily unavailable")
    assert is_retryable_error(err) is True


def test_429_rate_limit_remains_retryable():
    """Rate-limit must still retry (transient by definition)."""
    from shared.llm_retry import is_retryable_error
    err = Exception("Error code: 429 - rate limit exceeded")
    assert is_retryable_error(err) is True


def test_model_not_found_is_not_retryable():
    """Ollama 'model not found' is permanent until model is pulled."""
    from shared.llm_retry import is_retryable_error
    err = Exception(
        "Error code: 500 - {'error': {'message': 'model not found, try pulling it first'}}"
    )
    assert is_retryable_error(err) is False


def test_bad_api_key_is_not_retryable():
    """Auth failures must not retry — they need operator action."""
    from shared.llm_retry import is_retryable_error
    err = Exception("Invalid API key provided: sk-...")
    assert is_retryable_error(err) is False


def test_context_length_is_not_retryable():
    """Context-length is fixed by truncation, not retry."""
    from shared.llm_retry import is_retryable_error
    err = Exception("This model's maximum context length is 8192 tokens")
    assert is_retryable_error(err) is False


def test_timeout_remains_retryable():
    """Network timeouts remain transient and retryable."""
    from shared.llm_retry import is_retryable_error
    err = Exception("Request timed out after 30s")
    assert is_retryable_error(err) is True
