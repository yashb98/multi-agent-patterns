import json
import threading
import pytest

from shared.logging_config import clear_trajectory_id, set_trajectory_id
from shared.memory_layer._entries import MemoryEntry, MemoryTier, Lifecycle
from shared.memory_layer._sqlite_store import SQLiteStore


@pytest.fixture
def store(tmp_path):
    return SQLiteStore(str(tmp_path / "test.db"))


class TestSQLiteStore:
    def test_insert_and_retrieve(self, store, make_memory):
        entry = make_memory(content="quantum computing research")
        store.insert(entry)
        result = store.get_by_id(entry.memory_id)
        assert result is not None
        assert result.memory_id == entry.memory_id
        assert result.content == "quantum computing research"
        assert result.tier == MemoryTier.EPISODIC

    def test_insert_creates_all_indexes(self, store):
        conn = store._get_conn()
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_mem_%'"
        ).fetchall()
        index_names = {r[0] for r in rows}
        assert {"idx_mem_tier", "idx_mem_domain", "idx_mem_decay", "idx_mem_lifecycle"} <= index_names

    def test_tier_views_filter_correctly(self, store, make_memory):
        store.insert(make_memory(tier=MemoryTier.SEMANTIC, content="fact 1"))
        store.insert(make_memory(tier=MemoryTier.SEMANTIC, content="fact 2"))
        tombstoned = make_memory(tier=MemoryTier.SEMANTIC, content="fact 3", is_tombstoned=True)
        store.insert(tombstoned)
        results = store.query_by_tier(MemoryTier.SEMANTIC)
        assert len(results) == 2

    def test_domain_filter(self, store, make_memory):
        for i in range(5):
            store.insert(make_memory(domain="physics", content=f"physics {i}"))
        for i in range(5):
            store.insert(make_memory(domain="cooking", content=f"cooking {i}"))
        results = store.query_by_domain("physics")
        assert len(results) == 5

    def test_lifecycle_filter(self, store, make_memory):
        store.insert(make_memory(lifecycle=Lifecycle.STM, content="stm"))
        store.insert(make_memory(lifecycle=Lifecycle.MTM, content="mtm"))
        store.insert(make_memory(lifecycle=Lifecycle.LTM, content="ltm"))
        results = store.query_by_lifecycle(Lifecycle.STM)
        assert len(results) == 1
        assert results[0].lifecycle == Lifecycle.STM

    def test_decay_score_ordering(self, store, make_memory):
        for score in [0.1, 0.9, 0.5, 0.3, 0.7]:
            store.insert(make_memory(decay_score=score, content=f"decay {score}"))
        results = store.query_by_decay_desc(limit=5)
        scores = [r.decay_score for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_tombstone_soft_delete(self, store, make_memory):
        entry = make_memory(content="will be deleted")
        store.insert(entry)
        store.tombstone(entry.memory_id)
        assert store.get_by_id(entry.memory_id) is None
        conn = store._get_conn()
        row = conn.execute(
            "SELECT is_tombstoned FROM memories WHERE memory_id=?",
            (entry.memory_id,),
        ).fetchone()
        assert row[0] == 1

    def test_payload_json_roundtrip(self, store, make_memory):
        payload = {"strengths": ["research", "writing"], "nested": {"key": 42}}
        entry = make_memory(payload=payload, content="payload test")
        store.insert(entry)
        result = store.get_by_id(entry.memory_id)
        assert result.payload == payload

    def test_update_access_metadata(self, store, make_memory):
        entry = make_memory(content="access tracking")
        store.insert(entry)
        old_accessed = entry.last_accessed
        store.touch(entry.memory_id)
        result = store.get_by_id(entry.memory_id)
        assert result.access_count == 1
        assert result.last_accessed > old_accessed

    def test_reads_persist_trajectory_context(self, store, make_memory):
        entry = make_memory(content="trajectory read")
        store.insert(entry)
        set_trajectory_id("traj_mem_read")
        try:
            result = store.get_by_id(entry.memory_id)
            assert result is not None
        finally:
            clear_trajectory_id()

        conn = store._get_conn()
        row = conn.execute(
            "SELECT trajectory_id, action FROM memory_access_log WHERE memory_id = ?",
            (entry.memory_id,),
        ).fetchone()
        assert row["trajectory_id"] == "traj_mem_read"
        assert row["action"] == "get_by_id"

    def test_concurrent_writes(self, store, make_memory):
        errors = []

        def writer(thread_id):
            try:
                for i in range(10):
                    store.insert(make_memory(content=f"thread {thread_id} entry {i}"))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(t,)) for t in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors
        assert store.count() == 100
