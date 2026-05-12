"""Tests for `shared.memory_layer._embedder` — Audit 2026-05-10 / Slice S10 / TP-17.

The embedder previously silently fell back to MiniLM (384-dim) when BGE-M3
returned an HTTP 500. Combined with the screening_semantic_cache's
1024-dim Qdrant collection, this produced silent dim-mismatch: writes
were rejected (good), but lookups returned 0 results without alarm —
silently degrading cache hit-rate to 0%.

Per `dimensions.md → A9`, the fallback must be either removed or made
loud-fail. This slice adds: (a) retry-with-backoff for transient
errors, (b) circuit-breaker that raises `EmbedderUnavailableError`
after N consecutive persistent failures.
"""
from __future__ import annotations

import urllib.error
from unittest.mock import patch

import pytest


def test_embedder_unavailable_error_is_runtime_subclass():
    """`EmbedderUnavailableError` should be importable and a RuntimeError
    subclass so existing `except RuntimeError` catches still work."""
    from shared.memory_layer._embedder import EmbedderUnavailableError

    assert issubclass(EmbedderUnavailableError, RuntimeError)


def test_embed_bge_retries_on_transient_failure_then_succeeds():
    """A transient HTTPError (e.g. one-shot 500) is retried; after 1 retry
    success returns normally. No exception, no fallback engaged."""
    from shared.memory_layer._embedder import MemoryEmbedder

    embedder = MemoryEmbedder(primary="bge", fallback="minilm")
    call_count = {"n": 0}

    def fake_embed(texts):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("Ollama 500 (transient)")
        return [[0.1] * 1024 for _ in texts]

    with patch.object(embedder, "_embed_bge", side_effect=fake_embed):
        result = embedder.embed_batch(["hello"])

    assert call_count["n"] == 2  # 1 fail + 1 retry
    assert len(result) == 1
    assert len(result[0]) == 1024


def test_embed_bge_falls_back_to_minilm_after_all_retries_when_circuit_closed():
    """After max_retries persistent failures within a single embed_batch,
    the embedder still falls back to MiniLM (graceful degradation while the
    circuit is closed) — but logs at ERROR level. Loud-fail kicks in only
    after consecutive batch-level failures."""
    from shared.memory_layer._embedder import MemoryEmbedder

    # Reset the class-level circuit breaker so prior tests don't leak state
    MemoryEmbedder._consecutive_failures = 0

    embedder = MemoryEmbedder(primary="bge", fallback="minilm")

    def always_fail(texts):
        raise RuntimeError("Ollama persistent 500")

    with patch.object(embedder, "_embed_bge", side_effect=always_fail), \
         patch.object(embedder, "_embed_minilm", return_value=[[0.0] * 384]):
        result = embedder.embed_batch(["hello"])

    # Returns MiniLM result on first persistent failure (circuit still closed)
    assert len(result[0]) == 384


def test_circuit_breaker_raises_after_n_consecutive_persistent_failures():
    """After N consecutive batch-level failures (each itself failing all
    retries), the circuit opens and `EmbedderUnavailableError` is raised
    instead of silently falling back. Caller MUST handle the exception."""
    from shared.memory_layer._embedder import (
        EmbedderUnavailableError,
        MemoryEmbedder,
        _CIRCUIT_THRESHOLD,
    )

    # Reset breaker
    MemoryEmbedder._consecutive_failures = 0

    embedder = MemoryEmbedder(primary="bge", fallback="minilm")

    def always_fail(texts):
        raise RuntimeError("Ollama down")

    with patch.object(embedder, "_embed_bge", side_effect=always_fail), \
         patch.object(embedder, "_embed_minilm", return_value=[[0.0] * 384]):
        # First N-1 batches fall back gracefully (circuit still closed)
        for _ in range(_CIRCUIT_THRESHOLD - 1):
            embedder.embed_batch(["t"])
        # Nth batch trips the breaker → raises
        with pytest.raises(EmbedderUnavailableError):
            embedder.embed_batch(["t"])


def test_circuit_breaker_resets_on_success():
    """A successful BGE-M3 call resets the consecutive-failure counter,
    so transient outages don't accumulate toward the trip threshold."""
    from shared.memory_layer._embedder import MemoryEmbedder

    MemoryEmbedder._consecutive_failures = 0
    embedder = MemoryEmbedder(primary="bge", fallback="minilm")

    fail_then_succeed = [
        RuntimeError("flaky"),
        RuntimeError("flaky"),
        [[0.1] * 1024],  # success
    ]

    def fake_embed(texts):
        item = fail_then_succeed.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    with patch.object(embedder, "_embed_bge", side_effect=fake_embed), \
         patch.object(embedder, "_embed_minilm", return_value=[[0.0] * 384]):
        # First batch: 1 fail + 1 retry → success on retry, counter resets
        result = embedder.embed_batch(["t"])

    assert len(result[0]) == 1024
    assert MemoryEmbedder._consecutive_failures == 0


def test_explicit_minilm_primary_does_not_engage_circuit():
    """Callers that explicitly opt into `primary='minilm'` (e.g. for
    always-local development without Ollama) bypass the BGE retry +
    circuit-breaker entirely."""
    from shared.memory_layer._embedder import MemoryEmbedder

    MemoryEmbedder._consecutive_failures = 99  # pretend circuit is tripped

    embedder = MemoryEmbedder(primary="minilm")
    with patch.object(embedder, "_embed_minilm", return_value=[[0.5] * 384]):
        result = embedder.embed_batch(["t"])

    # Returns MiniLM result without going through BGE / circuit logic
    assert len(result[0]) == 384


def test_no_retry_on_non_transient_error():
    """Errors that aren't network/timeout (e.g. ValueError from malformed
    response) shouldn't be retried — they'll just fail again."""
    from shared.memory_layer._embedder import MemoryEmbedder

    MemoryEmbedder._consecutive_failures = 0
    embedder = MemoryEmbedder(primary="bge", fallback="minilm")
    call_count = {"n": 0}

    def fake_embed(texts):
        call_count["n"] += 1
        raise ValueError("malformed response — not a transient error")

    with patch.object(embedder, "_embed_bge", side_effect=fake_embed), \
         patch.object(embedder, "_embed_minilm", return_value=[[0.0] * 384]):
        result = embedder.embed_batch(["t"])

    # ValueError is NOT a retried error type — only one call
    assert call_count["n"] == 1
    # Falls back to MiniLM (graceful) since the circuit is still closed
    assert len(result[0]) == 384
