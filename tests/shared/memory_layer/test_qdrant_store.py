import math
import time
import pytest

from shared.memory_layer._entries import MemoryTier, Lifecycle
from shared.memory_layer._qdrant_store import QdrantStore


def _make_vector(seed: float, dims: int = 1024) -> list[float]:
    import hashlib
    h = hashlib.sha256(str(seed).encode()).digest()
    raw = [(h[i % len(h)] + i) % 256 / 255.0 * 2 - 1 for i in range(dims)]
    norm = math.sqrt(sum(x * x for x in raw))
    return [x / norm for x in raw]


@pytest.fixture
def store():
    s = QdrantStore(location=":memory:", dims=1024)
    s.ensure_collections()
    return s


class TestQdrantStore:
    def test_upsert_and_search(self, store):
        target = _make_vector(1.0)
        store.upsert("id1", MemoryTier.EPISODIC, target, {"domain": "test", "score": 7.0})
        store.upsert("id2", MemoryTier.EPISODIC, _make_vector(999.0), {"domain": "test", "score": 5.0})
        results = store.search(MemoryTier.EPISODIC, target, top_k=1)
        assert len(results) >= 1
        assert results[0][0] == "id1"

    def test_collection_per_tier(self, store):
        vec = _make_vector(1.0)
        store.upsert("id1", MemoryTier.EPISODIC, vec, {"domain": "test"})
        results = store.search(MemoryTier.PROCEDURAL, vec, top_k=10)
        assert len(results) == 0

    def test_filtered_search_by_domain(self, store):
        for i in range(5):
            store.upsert(f"a{i}", MemoryTier.SEMANTIC, _make_vector(float(i)), {"domain": "physics"})
        for i in range(5):
            store.upsert(f"b{i}", MemoryTier.SEMANTIC, _make_vector(float(i + 100)), {"domain": "cooking"})
        results = store.search(
            MemoryTier.SEMANTIC, _make_vector(0.0), top_k=10,
            filters={"domain": "physics"},
        )
        assert all(r[0].startswith("a") for r in results)

    def test_filtered_search_by_score(self, store):
        for i, score in enumerate([3.0, 5.0, 7.0, 9.0]):
            store.upsert(f"id{i}", MemoryTier.EPISODIC, _make_vector(float(i)), {"domain": "test", "score": score})
        results = store.search(
            MemoryTier.EPISODIC, _make_vector(0.0), top_k=10,
            min_score=7.0,
        )
        ids = {r[0] for r in results}
        assert "id0" not in ids
        assert "id1" not in ids

    def test_filtered_search_by_lifecycle(self, store):
        store.upsert("stm1", MemoryTier.EPISODIC, _make_vector(1.0), {"domain": "test", "lifecycle": "stm"})
        store.upsert("cold1", MemoryTier.EPISODIC, _make_vector(2.0), {"domain": "test", "lifecycle": "cold"})
        results = store.search(
            MemoryTier.EPISODIC, _make_vector(1.0), top_k=10,
            filters={"lifecycle": "stm"},
        )
        ids = {r[0] for r in results}
        assert "stm1" in ids
        assert "cold1" not in ids

    def test_similarity_ordering(self, store):
        base = _make_vector(1.0)
        close = _make_vector(1.001)
        far = _make_vector(999.0)
        store.upsert("close", MemoryTier.EPISODIC, close, {"domain": "test"})
        store.upsert("far", MemoryTier.EPISODIC, far, {"domain": "test"})
        results = store.search(MemoryTier.EPISODIC, base, top_k=2)
        assert results[0][0] == "close"

    def test_cross_tier_search(self, store):
        vec = _make_vector(1.0)
        store.upsert("ep1", MemoryTier.EPISODIC, vec, {"domain": "test"})
        store.upsert("pr1", MemoryTier.PROCEDURAL, vec, {"domain": "test"})
        results = store.search_all_tiers(vec, top_k=5)
        ids = {r[0] for r in results}
        assert "ep1" in ids
        assert "pr1" in ids

    def test_delete_by_id(self, store):
        vec = _make_vector(1.0)
        store.upsert("id1", MemoryTier.EPISODIC, vec, {"domain": "test"})
        store.delete("id1", MemoryTier.EPISODIC)
        results = store.search(MemoryTier.EPISODIC, vec, top_k=1)
        assert len(results) == 0

    def test_cosine_threshold(self, store):
        store.upsert("id1", MemoryTier.EPISODIC, _make_vector(1.0), {"domain": "test"})
        far_vec = _make_vector(999.0)
        results = store.search(MemoryTier.EPISODIC, far_vec, top_k=1, score_threshold=0.95)
        assert len(results) == 0

    def test_10k_vectors_performance(self, store):
        for i in range(10000):
            store.upsert(f"id{i}", MemoryTier.EPISODIC, _make_vector(float(i)), {"domain": "test"})
        query = _make_vector(5000.0)
        start = time.monotonic()
        results = store.search(MemoryTier.EPISODIC, query, top_k=10)
        elapsed_ms = (time.monotonic() - start) * 1000
        assert len(results) > 0
        assert elapsed_ms < 100
