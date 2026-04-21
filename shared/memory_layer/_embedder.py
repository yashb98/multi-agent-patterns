"""MemoryEmbedder — Voyage 3 Large primary, MiniLM fallback."""

import os
from typing import Optional

from shared.logging_config import get_logger

logger = get_logger(__name__)

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
    """Dual-mode embedder with automatic fallback."""

    def __init__(
        self,
        primary: str = "voyage",
        fallback: str = "minilm",
    ):
        self._primary = primary
        self._fallback = fallback
        self._voyage_client = None

    @property
    def dims(self) -> int:
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

    def embed(self, text: str) -> list[float]:
        return self.embed_batch([text])[0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if self._primary == "voyage":
            try:
                return self._embed_voyage(texts)
            except Exception as e:
                logger.warning("Voyage embed failed, falling back to MiniLM: %s", e)
                return self._embed_minilm(texts)
        return self._embed_minilm(texts)
