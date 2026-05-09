"""MemoryEmbedder — BGE-M3 (Ollama) primary, MiniLM fallback. Voyage available."""

import json as _json
import os
import urllib.error
import urllib.request

from shared.logging_config import get_logger

logger = get_logger(__name__)

_BGE_DIMS = 1024
_VOYAGE_DIMS = 1024
_MINILM_DIMS = 384
_minilm_model = None


def _get_minilm():
    global _minilm_model
    if _minilm_model is None:
        from sentence_transformers import SentenceTransformer
        _minilm_model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
    return _minilm_model


class MemoryEmbedder:
    """Triple-mode embedder with automatic fallback.

    Modes:
      - bge:    BGE-M3 served by local Ollama (1024 dims). Default.
      - voyage: Voyage 3 Large via voyageai SDK (1024 dims).
      - minilm: sentence-transformers MiniLM-L6-v2 (384 dims). Always-local fallback.
    """

    def __init__(
        self,
        primary: str = "bge",
        fallback: str = "minilm",
    ):
        self._primary = primary
        self._fallback = fallback
        self._voyage_client = None

    @property
    def dims(self) -> int:
        if self._primary == "bge":
            return _BGE_DIMS
        if self._primary == "voyage":
            return _VOYAGE_DIMS
        return _MINILM_DIMS

    def _get_voyage(self):
        if self._voyage_client is None:
            import voyageai
            self._voyage_client = voyageai.Client(
                api_key=os.environ.get("VOYAGE_API_KEY", ""),
            )
        return self._voyage_client

    def _embed_voyage(self, texts: list[str]) -> list[list[float]]:
        client = self._get_voyage()
        result = client.embed(texts, model="voyage-3-large")
        return result.embeddings

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

    def _run_fallback(self, texts: list[str]) -> list[list[float]]:
        if self._fallback == "voyage":
            return self._embed_voyage(texts)
        if self._fallback == "bge":
            return self._embed_bge(texts)
        return self._embed_minilm(texts)

    def embed(self, text: str) -> list[float]:
        return self.embed_batch([text])[0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if self._primary == "bge":
            try:
                return self._embed_bge(texts)
            except (urllib.error.URLError, OSError, RuntimeError, TimeoutError) as e:
                logger.warning("BGE-M3 embed failed, falling back to %s: %s", self._fallback, e)
                return self._run_fallback(texts)
        if self._primary == "voyage":
            try:
                return self._embed_voyage(texts)
            except Exception as e:
                logger.warning("Voyage embed failed, falling back to %s: %s", self._fallback, e)
                return self._run_fallback(texts)
        return self._embed_minilm(texts)


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
