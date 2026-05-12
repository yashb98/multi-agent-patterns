"""MemoryEmbedder — BGE-M3 (Ollama) primary, MiniLM fallback.

Audit 2026-05-10 / Slice S10 / TP-17. Adds retry-with-backoff for transient
BGE-M3 failures + a circuit breaker that raises `EmbedderUnavailableError`
after N consecutive persistent failures. The previous silent MiniLM
fallback would write 384-dim vectors that mismatched the 1024-dim Qdrant
collections — the cache-layer dim guards refused the writes (good), but
lookups silently returned 0 results (bad), invisibly degrading hit-rate
to 0%. Per `dimensions.md → A9`, that fallback path is now loud-failed
under sustained outage; transient errors still degrade gracefully.
"""

import json as _json
import os
import time
import urllib.error
import urllib.request

from shared.logging_config import get_logger

logger = get_logger(__name__)

_BGE_DIMS = 1024
_MINILM_DIMS = 384
_minilm_model = None

# Circuit-breaker tunables.
_CIRCUIT_THRESHOLD = int(os.environ.get("MEMORY_EMBEDDER_CIRCUIT_THRESHOLD", "3"))
_RETRY_ATTEMPTS = int(os.environ.get("MEMORY_EMBEDDER_RETRY_ATTEMPTS", "3"))
_RETRY_BASE_SECONDS = float(os.environ.get("MEMORY_EMBEDDER_RETRY_BASE", "1.0"))

# Errors we'll retry on (transient network/timeout).
_TRANSIENT_ERRORS: tuple[type[BaseException], ...] = (
    urllib.error.URLError,
    OSError,
    RuntimeError,
    TimeoutError,
)


class EmbedderUnavailableError(RuntimeError):
    """BGE-M3 unreachable after retries + circuit-breaker threshold.

    Raised when the primary embedder has failed for N consecutive batches.
    Caller MUST handle this — silently falling back to a different-dim
    embedder corrupts Qdrant collections (per `dimensions.md → A9`).

    Subclasses RuntimeError so existing `except RuntimeError` handlers
    still catch it; new code should catch this specifically and decide
    whether to retry later, alert, or abort.
    """


def _get_minilm():
    global _minilm_model
    if _minilm_model is None:
        from sentence_transformers import SentenceTransformer
        _minilm_model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
    return _minilm_model


class MemoryEmbedder:
    """Dual-mode embedder with retry + circuit-breaker.

    Modes:
      - bge:    BGE-M3 served by local Ollama (1024 dims). Default.
      - minilm: sentence-transformers MiniLM-L6-v2 (384 dims). Always-local fallback.

    Failure handling (when `primary == "bge"`):
      1. Per-batch retry: up to `_RETRY_ATTEMPTS` attempts with exponential
         backoff on transient errors (URLError / OSError / RuntimeError /
         TimeoutError). Recoveries reset the consecutive-failure counter.
      2. Circuit breaker: tracks consecutive batch-level failures across
         the class. After `_CIRCUIT_THRESHOLD` consecutive persistent
         failures, raises `EmbedderUnavailableError` instead of silently
         degrading to MiniLM. Successful calls reset the counter.
      3. Below the circuit threshold, persistent batch failures still fall
         back to MiniLM (graceful degradation for transient outages) but
         emit ERROR-level structured logs so the engagement is observable.
    """

    # Class-level counter so all embedder instances share circuit state.
    # Reset to 0 on any successful BGE-M3 batch.
    _consecutive_failures: int = 0

    def __init__(
        self,
        primary: str = "bge",
        fallback: str = "minilm",
    ):
        self._primary = primary
        self._fallback = fallback

    @property
    def dims(self) -> int:
        if self._primary == "bge":
            return _BGE_DIMS
        return _MINILM_DIMS

    def _embed_minilm(self, texts: list[str]) -> list[list[float]]:
        model = _get_minilm()
        embeddings = model.encode(texts, normalize_embeddings=True)
        return [e.tolist() for e in embeddings]

    def _embed_bge(self, texts: list[str]) -> list[list[float]]:
        base = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
        model = os.environ.get("OLLAMA_EMBED_MODEL", "bge-m3:latest")
        timeout = float(os.environ.get("OLLAMA_EMBED_TIMEOUT", "60"))
        req = urllib.request.Request(
            f"{base}/api/embed",
            data=_json.dumps({"model": model, "input": texts}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = _json.loads(resp.read())
        embeddings = data.get("embeddings")
        if not embeddings:
            raise RuntimeError(f"Ollama /api/embed returned no embeddings (model={model})")
        return embeddings

    def _embed_bge_with_retry(self, texts: list[str]) -> list[list[float]]:
        """Retry BGE-M3 on transient errors. Returns embeddings on success;
        raises the last exception if all attempts fail."""
        last_exc: BaseException | None = None
        for attempt in range(_RETRY_ATTEMPTS):
            try:
                return self._embed_bge(texts)
            except _TRANSIENT_ERRORS as e:
                last_exc = e
                if attempt < _RETRY_ATTEMPTS - 1:
                    wait = _RETRY_BASE_SECONDS * (2 ** attempt)
                    logger.warning(
                        "BGE-M3 embed attempt %d/%d failed: %s — retrying in %.1fs",
                        attempt + 1, _RETRY_ATTEMPTS, e, wait,
                    )
                    time.sleep(wait)
        # Exhausted — let caller decide.
        assert last_exc is not None  # for type-checker; loop guarantees this
        raise last_exc

    def _run_fallback(self, texts: list[str]) -> list[list[float]]:
        if self._fallback == "bge":
            return self._embed_bge(texts)
        return self._embed_minilm(texts)

    def embed(self, text: str) -> list[float]:
        return self.embed_batch([text])[0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if self._primary != "bge":
            # Non-BGE primaries (e.g. explicit minilm-only mode) bypass the
            # BGE retry + circuit-breaker entirely.
            return self._embed_minilm(texts)

        try:
            result = self._embed_bge_with_retry(texts)
        except _TRANSIENT_ERRORS as e:
            # Persistent batch failure. Increment circuit-breaker counter
            # before deciding whether to fall back or loud-fail.
            type(self)._consecutive_failures += 1
            count = type(self)._consecutive_failures
            if count >= _CIRCUIT_THRESHOLD:
                logger.error(
                    "BGE-M3 unavailable — circuit breaker tripped after %d "
                    "consecutive batch failures (threshold=%d). Last error: %s. "
                    "Per dim A9 (audit 2026-05-10), refusing silent MiniLM "
                    "fallback to prevent dim-mismatch cache corruption.",
                    count, _CIRCUIT_THRESHOLD, e,
                )
                raise EmbedderUnavailableError(
                    f"BGE-M3 unreachable after {count} consecutive batches "
                    f"(last error: {e}). Caller must handle "
                    f"EmbedderUnavailableError; silent fallback to MiniLM-384 "
                    f"would corrupt 1024-dim Qdrant collections.",
                ) from e
            logger.error(
                "BGE-M3 embed failed after %d attempts (consecutive_failures=%d/%d) — "
                "falling back to %s [DIM MISMATCH RISK: %d != %d]: %s",
                _RETRY_ATTEMPTS, count, _CIRCUIT_THRESHOLD, self._fallback,
                _MINILM_DIMS, _BGE_DIMS, e,
            )
            return self._run_fallback(texts)
        except Exception as e:  # noqa: BLE001
            # Non-transient error (malformed response / type error / etc.).
            # Don't retry, don't trip the breaker — this is a different class
            # of failure than network outage. Fall back gracefully so a single
            # malformed Ollama response doesn't kill the whole pipeline.
            logger.error(
                "BGE-M3 embed failed with non-transient error: %s — "
                "falling back to %s without circuit-breaker increment",
                e, self._fallback,
            )
            return self._run_fallback(texts)

        # Success — reset breaker.
        if type(self)._consecutive_failures > 0:
            logger.info(
                "BGE-M3 recovered (was %d consecutive failures) — "
                "resetting circuit breaker.",
                type(self)._consecutive_failures,
            )
        type(self)._consecutive_failures = 0
        return result


_default_embedder: MemoryEmbedder | None = None


def embed_text(text: str) -> list[float]:
    """Module-level entry point for one-off embedding calls.

    Uses a lazily-constructed singleton MemoryEmbedder with the default
    configuration (BGE-M3 primary via Ollama, MiniLM fallback).
    """
    global _default_embedder
    if _default_embedder is None:
        _default_embedder = MemoryEmbedder()
    return _default_embedder.embed(text)
