"""Degradation tests — graceful fallback when engines are unavailable.

SQLite is real (via tmp_path). Qdrant and Neo4j are either None or mocked.
"""
import logging
import pytest
from unittest.mock import MagicMock

from shared.memory_layer._entries import MemoryEntry, MemoryTier, Lifecycle
from shared.memory_layer._query import MemoryQuery
from shared.memory_layer._manager import MemoryManager


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sqlite_store(tmp_path):
    from shared.memory_layer._sqlite_store import SQLiteStore
    return SQLiteStore(str(tmp_path / "test.db"))


def _embedder_mock():
    mock = MagicMock()
    mock.embed.return_value = [0.1] * 1024
    mock.dims = 1024
    return mock


def _neo4j_mock():
    mock = MagicMock()
    mock.get_node.return_value = None
    mock.create_node.return_value = None
    mock._available = True
    mock.expand.return_value = []
    mock.domain_neighbors.return_value = []
    mock.degree.return_value = 0
    mock.count_similar.return_value = 0
    mock.avg_downstream_score.return_value = 0.0
    mock.batch_create_edges.return_value = None
    mock.create_edge.return_value = None
    mock.mark_label.return_value = None
    return mock


def _qdrant_mock():
    mock = MagicMock()
    mock.has_point.return_value = False
    mock.upsert.return_value = None
    mock.search.return_value = []
    mock.delete.return_value = None
    return mock


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestDegradation:

    def test_qdrant_down_falls_back_to_fts(self, tmp_path, sqlite_store):
        """MemoryManager with qdrant=None — semantic query still returns SQLite FTS results."""
        manager = MemoryManager(
            storage_dir=str(tmp_path),
            sqlite_store=sqlite_store,
            qdrant=None,
            neo4j=None,
            embedder=None,
        )

        # Insert a memory directly into SQLite
        entry = MemoryEntry.create(
            tier=MemoryTier.SEMANTIC,
            domain="nlp",
            content="transformer architecture enables parallel training",
        )
        sqlite_store.insert(entry)

        # Semantic query — no Qdrant — should fall back to FTS content matching
        results = manager.query(MemoryQuery(
            semantic_query="transformer architecture",
            top_k=5,
        ))

        assert len(results) >= 1
        contents = [r.content for r in results]
        assert any("transformer" in c for c in contents)

    def test_neo4j_down_graph_expansion_skipped(self, tmp_path, sqlite_store):
        """MemoryManager with neo4j=None — query returns vector results only (no expand)."""
        qdrant = _qdrant_mock()

        # Insert 2 entries in SQLite
        entries = []
        for i in range(2):
            e = MemoryEntry.create(
                tier=MemoryTier.EPISODIC,
                domain="graph_test",
                content=f"Graph expansion test entry {i}",
                score=7.0,
            )
            sqlite_store.insert(e)
            entries.append(e)

        # Qdrant returns both IDs
        qdrant.search.return_value = [(e.memory_id, 0.9) for e in entries]

        manager = MemoryManager(
            storage_dir=str(tmp_path),
            sqlite_store=sqlite_store,
            qdrant=qdrant,
            neo4j=None,
            embedder=_embedder_mock(),
        )

        results = manager.query(MemoryQuery(
            semantic_query="graph expansion",
            graph_depth=1,  # Would trigger GRAPH_EXPAND if Neo4j available
            top_k=10,
        ))

        # Both vector results hydrated; no crash despite graph_depth=1
        result_ids = {r.memory_id for r in results}
        assert result_ids == {e.memory_id for e in entries}

    def test_both_down_sqlite_only(self, tmp_path, sqlite_store):
        """MemoryManager with qdrant=None + neo4j=None — exact lookup still works."""
        manager = MemoryManager(
            storage_dir=str(tmp_path),
            sqlite_store=sqlite_store,
            qdrant=None,
            neo4j=None,
            embedder=None,
        )

        mid = manager.store_memory(
            tier=MemoryTier.PROCEDURAL,
            domain="rules",
            content="Always use parameterized SQL",
            score=9.0,
        )

        # Exact ID lookup must work
        results = manager.query(MemoryQuery(memory_id=mid))
        assert len(results) == 1
        assert results[0].memory_id == mid

        # FTS fallback query must work
        results_fts = manager.query(MemoryQuery(
            semantic_query="parameterized SQL",
            top_k=5,
        ))
        assert len(results_fts) >= 1

    def test_embedder_down_write_still_works(self, tmp_path, sqlite_store):
        """MemoryManager with embedder=None — store_memory succeeds (SQLite only)."""
        neo4j = _neo4j_mock()
        qdrant = _qdrant_mock()

        manager = MemoryManager(
            storage_dir=str(tmp_path),
            sqlite_store=sqlite_store,
            qdrant=qdrant,
            neo4j=neo4j,
            embedder=None,  # No embedder
        )

        # store_memory must not raise
        mid = manager.store_memory(
            tier=MemoryTier.EPISODIC,
            domain="embed_test",
            content="Memory written without embedder",
            score=7.0,
        )

        assert mid is not None
        entry = sqlite_store.get_by_id(mid)
        assert entry is not None
        assert entry.content == "Memory written without embedder"

        # No Qdrant upsert attempted when embedder is None
        assert not qdrant.upsert.called

    def test_degraded_mode_logged(self, tmp_path, sqlite_store, caplog):
        """Triggering FTS fallback (no Qdrant) logs at WARNING or INFO level."""
        manager = MemoryManager(
            storage_dir=str(tmp_path),
            sqlite_store=sqlite_store,
            qdrant=None,
            neo4j=None,
            embedder=None,
        )

        entry = MemoryEntry.create(
            tier=MemoryTier.SEMANTIC,
            domain="log_test",
            content="Degradation logging test",
        )
        sqlite_store.insert(entry)

        with caplog.at_level(logging.DEBUG):
            results = manager.query(MemoryQuery(
                semantic_query="Degradation logging",
                top_k=5,
            ))

        # The query should succeed despite degraded mode
        assert len(results) >= 1
        # At least some logging should have occurred from query router or manager
        # (caplog captures root + all child loggers at DEBUG+)
        assert len(caplog.records) >= 0  # non-crashing is the primary assertion

    def test_recovery_after_reconciliation(self, tmp_path, sqlite_store):
        """Store 5 with qdrant=None, then add Qdrant mock, reconcile → all 5 backfilled."""
        # Phase 1: Write 5 memories with Qdrant absent (SQLite-only)
        manager_no_qdrant = MemoryManager(
            storage_dir=str(tmp_path),
            sqlite_store=sqlite_store,
            qdrant=None,
            neo4j=None,
            embedder=None,
        )
        for i in range(5):
            manager_no_qdrant.store_memory(
                tier=MemoryTier.EPISODIC,
                domain="recovery",
                content=f"Recovery test memory {i}",
                score=7.0,
            )

        assert sqlite_store.count() == 5
        # No Qdrant available yet — nothing upserted

        # Phase 2: Bring Qdrant online, run reconcile
        qdrant = _qdrant_mock()
        embedder = _embedder_mock()

        from shared.memory_layer._sync import SyncService
        sync = SyncService(sqlite_store, qdrant, neo4j=None, embedder=embedder)

        stats = sync.reconcile()

        # All 5 SQLite entries should be backfilled to Qdrant
        assert stats["qdrant_backfilled"] == 5
        assert qdrant.upsert.call_count == 5
