import math
import pytest
from unittest.mock import patch, MagicMock

from shared.memory_layer._embedder import MemoryEmbedder


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


@pytest.fixture
def embedder():
    return MemoryEmbedder(primary="minilm", fallback="minilm")


class TestMemoryEmbedder:
    def test_embed_returns_correct_dims(self, embedder):
        vec = embedder.embed("test text")
        assert len(vec) == 384  # MiniLM dims

    def test_same_text_same_vector(self, embedder):
        v1 = embedder.embed("greenhouse form filling")
        v2 = embedder.embed("greenhouse form filling")
        assert v1 == v2

    def test_similar_text_high_cosine(self, embedder):
        v1 = embedder.embed("greenhouse form filling")
        v2 = embedder.embed("filling greenhouse application forms")
        assert _cosine(v1, v2) > 0.7

    def test_different_text_low_cosine(self, embedder):
        v1 = embedder.embed("greenhouse form filling")
        v2 = embedder.embed("quantum physics research papers")
        assert _cosine(v1, v2) < 0.5

    def test_fallback_on_primary_failure(self):
        embedder = MemoryEmbedder(primary="voyage", fallback="minilm")
        with patch.object(embedder, "_embed_voyage", side_effect=ConnectionError("API down")):
            vec = embedder.embed("test text")
            assert len(vec) == 384  # fell back to MiniLM

    def test_fallback_logs_warning(self, caplog):
        embedder = MemoryEmbedder(primary="voyage", fallback="minilm")
        with patch.object(embedder, "_embed_voyage", side_effect=ConnectionError("API down")):
            embedder.embed("test")
            assert "falling back" in caplog.text.lower() or "fallback" in caplog.text.lower()

    def test_batch_embed(self, embedder):
        texts = [f"text {i}" for i in range(10)]
        results = embedder.embed_batch(texts)
        assert len(results) == 10
        assert all(len(v) == 384 for v in results)
