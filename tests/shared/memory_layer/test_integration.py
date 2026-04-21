"""Integration tests — end-to-end flows through MemoryManager.

SQLite is real (via tmp_path). Qdrant, Neo4j, and embedder are mocked.
"""
import threading
import pytest
from unittest.mock import MagicMock

from shared.memory_layer._entries import MemoryEntry, MemoryTier, Lifecycle, EdgeType
from shared.memory_layer._query import MemoryQuery
from shared.memory_layer._manager import MemoryManager
from shared.memory_layer._linker import classify_relationship


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

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
    mock.batch_create_edges.return_value = None
    mock.create_edge.return_value = None
    mock.mark_label.return_value = None
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


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestIntegration:

    def test_full_lifecycle_stm_to_ltm(self, manager, sqlite_store):
        """Store memory, access 3x → MTM, access 7 more → LTM."""
        mid = manager.store_memory(
            tier=MemoryTier.EPISODIC,
            domain="research",
            content="Lifecycle promotion flow test",
            score=8.0,
        )
        entry = sqlite_store.get_by_id(mid)
        assert entry is not None
        assert entry.lifecycle == Lifecycle.STM

        # 3 accesses promote STM → MTM (STM_TO_MTM_ACCESSES = 3)
        for _ in range(3):
            sqlite_store.touch(mid)

        entry = sqlite_store.get_by_id(mid)
        assert entry.access_count >= 3

        # Manually promote to MTM (as ForgettingEngine.evaluate_single() would)
        sqlite_store.update_lifecycle(mid, Lifecycle.MTM)

        # 10 total accesses + 5 validations promote MTM → LTM
        for _ in range(7):
            sqlite_store.touch(mid)

        entry = sqlite_store.get_by_id(mid)
        assert entry.access_count >= 10

        # Set validation count in payload to satisfy MTM_TO_LTM_VALIDATIONS = 5
        entry.payload["times_validated"] = 5
        sqlite_store.insert(entry)

        # Evaluate via ForgettingEngine
        from shared.memory_layer._forgetting import ForgettingEngine
        engine = ForgettingEngine(neo4j=None)
        actions = engine.evaluate_single(entry)

        assert actions.get("promote_to") == Lifecycle.LTM

    def test_full_lifecycle_ltm_to_cold(self, sqlite_store, make_memory):
        """LTM memory with low score + many similar copies → decay below LTM_COLD_DECAY → COLD."""
        from shared.memory_layer._forgetting import ForgettingEngine, LTM_COLD_DECAY
        from datetime import datetime, timedelta

        # Build a neo4j mock that reports 3+ similar entries (drives uniqueness=0.3)
        neo4j = MagicMock()
        neo4j.count_similar.return_value = 3   # uniqueness = 0.3
        neo4j.degree.return_value = 0           # no connectivity bonus
        neo4j.avg_downstream_score.return_value = 0.0

        entry = make_memory(
            tier=MemoryTier.SEMANTIC,
            domain="archive",
            content="Old fact that should decay to cold",
            score=0.5,           # quality = 0.5/7 ≈ 0.07
            lifecycle=Lifecycle.LTM,
            access_count=0,
        )
        # Simulate entry that was last accessed 500 hours ago
        entry.last_accessed = datetime.now() - timedelta(hours=500)
        entry.created_at = datetime.now() - timedelta(hours=1000)

        sqlite_store.insert(entry)

        engine = ForgettingEngine(neo4j=neo4j)
        # Verify the computed decay is actually below LTM_COLD_DECAY=0.25
        decay = engine.compute_decay(entry)
        assert decay < LTM_COLD_DECAY, f"Expected decay < {LTM_COLD_DECAY}, got {decay}"

        # Entry is not PROTECTED (protection requires count_similar==0 → but we have 3)
        actions = engine.evaluate_single(entry)
        assert actions.get("demote_to") == Lifecycle.COLD

    def test_autonomous_linking_on_write(self, sqlite_store, qdrant_mock, neo4j_mock, embedder_mock, make_memory):
        """Episodic new entry linked to existing semantic in same domain → PRODUCED edge.

        Rule 4 in classify_relationship:
          new_tier == EPISODIC and existing_tier == SEMANTIC and same_domain → PRODUCED
        So the new (linker perspective) entry must be EPISODIC.
        """
        from shared.memory_layer._linker import AutonomousLinker

        # Pre-existing semantic fact stored first
        semantic_entry = make_memory(
            tier=MemoryTier.SEMANTIC,
            domain="nlp",
            content="BERT fine-tuning improves domain classification by 12%",
            score=7.5,
        )
        sqlite_store.insert(semantic_entry)

        # New episodic entry arrives — linker discovers it relates to the semantic fact
        episodic_entry = make_memory(
            tier=MemoryTier.EPISODIC,
            domain="nlp",
            content="Completed experiment on BERT fine-tuning",
            score=8.0,
        )
        sqlite_store.insert(episodic_entry)

        linker = AutonomousLinker(neo4j=neo4j_mock)
        # Pass semantic_entry as the neighbor (existing); episodic_entry is the new one
        neighbors = [(semantic_entry, 0.9)]
        edges = linker.link_with_neighbors(episodic_entry, neighbors)

        # classify_relationship(new=EPISODIC, existing=SEMANTIC, same_domain=True) → PRODUCED
        edge_types = {e[2] for e in edges}
        assert EdgeType.PRODUCED.value in edge_types

    def test_contradiction_resolves(self, sqlite_store, neo4j_mock, make_memory):
        """Contradicting facts: weaker one loses confidence; loser_id returned."""
        from shared.memory_layer._linker import AutonomousLinker

        # Fact A — high strength (confidence=0.9, score=9.0)
        fact_a = make_memory(
            tier=MemoryTier.SEMANTIC,
            domain="physics",
            content="Speed of light is 3e8 m/s",
            score=9.0,
            confidence=0.9,
        )
        sqlite_store.insert(fact_a)

        # Fact B — lower strength (confidence=0.5, score=5.0)
        fact_b = make_memory(
            tier=MemoryTier.SEMANTIC,
            domain="physics",
            content="Speed of light is approximately 3e8 m/s in vacuum",
            score=5.0,
            confidence=0.5,
        )
        sqlite_store.insert(fact_b)

        linker = AutonomousLinker(neo4j=neo4j_mock)
        result = linker.handle_contradiction(fact_a, fact_b)

        # Fact B is the loser (lower strength)
        assert result["loser_id"] == fact_b.memory_id
        # Confidence should have decreased by 0.2
        assert result["new_confidence"] == pytest.approx(fact_b.confidence - 0.2, abs=1e-6)
        # CONTRADICTS edge should have been created
        assert neo4j_mock.create_edge.called

    def test_graph_expanded_retrieval(self, tmp_path, sqlite_store, qdrant_mock, neo4j_mock, embedder_mock):
        """Qdrant returns 2 IDs; Neo4j expand adds 3 more → 5 total results."""
        from shared.memory_layer._sqlite_store import SQLiteStore

        # Insert 5 distinct entries
        entries = []
        for i in range(5):
            e = MemoryEntry.create(
                tier=MemoryTier.EPISODIC,
                domain="expansion",
                content=f"Memory entry number {i}",
                score=7.0,
            )
            sqlite_store.insert(e)
            entries.append(e)

        # Qdrant returns first 2 as vector search hits
        qdrant_ids = [entries[0].memory_id, entries[1].memory_id]
        qdrant_mock.search.return_value = [(mid, 0.95) for mid in qdrant_ids]

        # Neo4j expand returns the other 3
        neo4j_expand_ids = [entries[2].memory_id, entries[3].memory_id, entries[4].memory_id]
        neo4j_mock.expand.return_value = neo4j_expand_ids

        manager = MemoryManager(
            storage_dir=str(tmp_path),
            sqlite_store=sqlite_store,
            qdrant=qdrant_mock,
            neo4j=neo4j_mock,
            embedder=embedder_mock,
        )

        results = manager.query(MemoryQuery(
            semantic_query="Memory entry",
            graph_depth=1,
            top_k=10,
        ))

        result_ids = {r.memory_id for r in results}
        assert result_ids == {e.memory_id for e in entries}

    def test_revival_after_tombstoning(self, manager, sqlite_store):
        """Store → tombstone → revive → entry accessible again."""
        mid = manager.store_memory(
            tier=MemoryTier.EPISODIC,
            domain="test",
            content="Entry to be revived",
            score=7.5,
        )
        assert sqlite_store.get_by_id(mid) is not None

        sqlite_store.tombstone(mid)
        assert sqlite_store.get_by_id(mid) is None  # hidden from reads

        sqlite_store.revive(mid)
        revived = sqlite_store.get_by_id(mid)
        assert revived is not None
        assert not revived.is_tombstoned

        # Also queryable through manager
        results = manager.query(MemoryQuery(memory_id=mid))
        assert len(results) == 1
        assert results[0].memory_id == mid

    def test_agent_context_enriched(self, manager):
        """Store diverse memories → context string is non-empty and non-trivial."""
        # Store memories using new 3-engine path
        for i in range(3):
            manager.store_memory(
                tier=MemoryTier.EPISODIC,
                domain="research",
                content=f"Research finding {i}: important result",
                score=8.0,
            )

        # Old-style episodic records also fill context
        manager.record_episode(
            topic="quantum ML benchmarks",
            final_score=8.5,
            iterations=3,
            pattern_used="hierarchical",
            agents_used=["researcher", "writer"],
            strengths=["thorough"], weaknesses=[],
            output_summary="Benchmark showed 20% improvement",
            domain="research",
        )
        manager.learn_fact("research", "Transformers outperform RNNs on long sequences")

        context = manager.get_context_for_agent("researcher", "quantum ML", "research")
        assert isinstance(context, str)
        assert len(context) > 0

    def test_concurrent_memory_access(self, manager, sqlite_store):
        """10 threads simultaneously store memories — no errors, correct count."""
        errors = []
        memory_ids = []
        lock = threading.Lock()

        def store_one(idx):
            try:
                mid = manager.store_memory(
                    tier=MemoryTier.EPISODIC,
                    domain="concurrent",
                    content=f"Concurrent entry {idx}",
                    score=7.0,
                )
                with lock:
                    memory_ids.append(mid)
            except Exception as exc:
                with lock:
                    errors.append(exc)

        threads = [threading.Thread(target=store_one, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Thread errors: {errors}"
        assert len(memory_ids) == 10
        # All 10 entries visible in SQLite
        assert sqlite_store.count() >= 10
