import pytest
from unittest.mock import MagicMock, call
from datetime import datetime

from shared.memory_layer._entries import MemoryEntry, MemoryTier, Lifecycle
from shared.memory_layer._sync import SyncService


@pytest.fixture
def sqlite_store(tmp_path):
    from shared.memory_layer._sqlite_store import SQLiteStore
    return SQLiteStore(str(tmp_path / "test.db"))


@pytest.fixture
def qdrant_mock():
    mock = MagicMock()
    mock.has_point.return_value = False
    mock.upsert.return_value = None
    mock.delete.return_value = None
    return mock


@pytest.fixture
def neo4j_mock():
    mock = MagicMock()
    mock.get_node.return_value = None
    mock.create_node.return_value = None
    mock.mark_label.return_value = None
    mock._available = True
    return mock


@pytest.fixture
def embedder_mock():
    mock = MagicMock()
    mock.embed.return_value = [0.1] * 1024
    mock.dims = 1024
    return mock


@pytest.fixture
def sync(sqlite_store, qdrant_mock, neo4j_mock, embedder_mock):
    return SyncService(
        sqlite=sqlite_store,
        qdrant=qdrant_mock,
        neo4j=neo4j_mock,
        embedder=embedder_mock,
        start_background=False,
    )


class TestSyncService:
    def test_reconcile_backfills_qdrant(self, sync, sqlite_store, qdrant_mock, make_memory):
        for i in range(5):
            sqlite_store.insert(make_memory(content=f"entry {i}"))
        sync.reconcile()
        assert qdrant_mock.upsert.call_count == 5

    def test_reconcile_backfills_neo4j(self, sync, sqlite_store, neo4j_mock, make_memory):
        for i in range(5):
            sqlite_store.insert(make_memory(content=f"entry {i}"))
        sync.reconcile()
        assert neo4j_mock.create_node.call_count == 5

    def test_reconcile_skips_already_synced(self, sync, sqlite_store, qdrant_mock, neo4j_mock, make_memory):
        entry = make_memory(content="already synced")
        sqlite_store.insert(entry)
        qdrant_mock.has_point.return_value = True
        neo4j_mock.get_node.return_value = {"memory_id": entry.memory_id}
        sync.reconcile()
        assert qdrant_mock.upsert.call_count == 0
        assert neo4j_mock.create_node.call_count == 0

    def test_tombstone_propagation(self, sync, sqlite_store, qdrant_mock, neo4j_mock, make_memory):
        entry = make_memory(content="to be tombstoned")
        sqlite_store.insert(entry)
        sync.propagate_tombstone(entry.memory_id, entry.tier)
        qdrant_mock.delete.assert_called_once()
        neo4j_mock.mark_label.assert_called_once()

    def test_sync_single_writes_to_both(self, sync, qdrant_mock, neo4j_mock, make_memory):
        entry = make_memory(content="new memory")
        sync.sync_to_secondary(entry)
        assert qdrant_mock.upsert.call_count == 1
        assert neo4j_mock.create_node.call_count == 1

    def test_background_queue_flushes_pending_work(
        self, sqlite_store, qdrant_mock, neo4j_mock, embedder_mock, make_memory,
    ):
        bg_sync = SyncService(
            sqlite=sqlite_store,
            qdrant=qdrant_mock,
            neo4j=neo4j_mock,
            embedder=embedder_mock,
            start_background=True,
        )
        entry = make_memory(content="queued secondary sync")
        bg_sync.sync_to_secondary(entry, background=True)
        bg_sync.flush(timeout=1.0)
        assert qdrant_mock.upsert.call_count >= 1
        assert neo4j_mock.create_node.call_count >= 1
        bg_sync.shutdown()
