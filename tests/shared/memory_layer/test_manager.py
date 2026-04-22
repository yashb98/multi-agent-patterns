import pytest
from unittest.mock import MagicMock

from shared.memory_layer._entries import MemoryEntry, MemoryTier, Lifecycle
from shared.memory_layer._query import MemoryQuery
from shared.memory_layer._manager import MemoryManager


@pytest.fixture
def sqlite_store(tmp_path):
    from shared.memory_layer._sqlite_store import SQLiteStore
    return SQLiteStore(str(tmp_path / "test.db"))


@pytest.fixture
def qdrant_mock():
    mock = MagicMock()
    mock.has_point.return_value = False
    mock.upsert.return_value = None
    mock.search.return_value = []
    mock.search_all_tiers.return_value = []
    mock.delete.return_value = None
    return mock


@pytest.fixture
def neo4j_mock():
    mock = MagicMock()
    mock.get_node.return_value = None
    mock.create_node.return_value = None
    mock._available = True
    mock.expand.return_value = []
    mock.domain_neighbors.return_value = []
    mock.degree.return_value = 0
    mock.count_similar.return_value = 0
    mock.avg_downstream_score.return_value = 0.0
    return mock


@pytest.fixture
def embedder_mock():
    mock = MagicMock()
    mock.embed.return_value = [0.1] * 1024
    mock.dims = 1024
    return mock


@pytest.fixture
def manager(tmp_path, sqlite_store, qdrant_mock, neo4j_mock, embedder_mock):
    return MemoryManager(
        storage_dir=str(tmp_path),
        sqlite_store=sqlite_store,
        qdrant=qdrant_mock,
        neo4j=neo4j_mock,
        embedder=embedder_mock,
    )


class TestMemoryManager:
    def test_store_memory_writes_to_all_engines(self, manager, sqlite_store, qdrant_mock, neo4j_mock):
        mid = manager.store_memory(
            tier=MemoryTier.EPISODIC, domain="test",
            content="Greenhouse uses React inputs", score=7.0,
        )
        manager.flush_secondary_sync(timeout=1.0)
        assert mid is not None
        assert sqlite_store.get_by_id(mid) is not None
        assert qdrant_mock.upsert.called
        assert neo4j_mock.create_node.called

    def test_query_exact_lookup(self, manager, sqlite_store, make_memory):
        entry = make_memory(content="test exact lookup")
        sqlite_store.insert(entry)
        results = manager.query(MemoryQuery(memory_id=entry.memory_id))
        assert len(results) == 1
        assert results[0].memory_id == entry.memory_id

    def test_health_reports_all_engines(self, manager):
        report = manager.health()
        assert "sqlite" in report
        assert "qdrant" in report
        assert "neo4j" in report

    def test_startup_runs_reconciliation(self, manager, sqlite_store, qdrant_mock, make_memory):
        sqlite_store.insert(make_memory(content="unsynced"))
        manager.startup()
        manager.flush_secondary_sync(timeout=1.0)
        assert qdrant_mock.upsert.called or qdrant_mock.has_point.called

    def test_pin_memory(self, manager, sqlite_store):
        mid = manager.store_memory(
            tier=MemoryTier.EPISODIC, domain="test",
            content="important fact", score=9.0,
        )
        manager.pin_memory(mid)
        entry = sqlite_store.get_by_id(mid)
        assert entry is not None

    def test_get_context_returns_string(self, manager):
        result = manager.get_context_for_agent("researcher", "test topic", "test")
        assert isinstance(result, str)
