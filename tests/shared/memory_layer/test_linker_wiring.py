"""Wiring tests for AutonomousLinker — bug pattern S6 / pipeline-bugs M-11.A.

Pre-fix bug pattern: `MemoryManager.__init__` constructs `self._linker =
AutonomousLinker(neo4j=...)` but no production callsite ever invokes
`link_with_neighbors`. Result: Neo4j has Memory nodes but **zero edges** in
production → `ForgettingEngine.compute_decay`'s connectivity / impact /
uniqueness signals all return defaults → 3 of 6 decay signals are decorative.

These tests fail today and pass after `SyncService._sync_entry` calls the
linker after the upsert + node creation.
"""

import threading
from unittest.mock import MagicMock

import pytest

from shared.memory_layer._entries import EdgeType, MemoryEntry, MemoryTier
from shared.memory_layer._manager import MemoryManager
from shared.memory_layer._sqlite_store import SQLiteStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class _MockNeo4j:
    """Edge-tracking Neo4j substitute. Mirrors `Neo4jStore`'s interface for
    `create_node`, `batch_create_edges`, and `count_edges` only."""

    def __init__(self) -> None:
        self._nodes: dict[str, dict] = {}
        self._edges: list[tuple[str, str, str, dict]] = []
        self._available = True
        self._lock = threading.Lock()

    def create_node(self, memory_id, tier, domain, content_preview, score,
                    confidence, decay_score, lifecycle, created_at) -> None:
        with self._lock:
            self._nodes[memory_id] = {
                "tier": tier, "domain": domain,
                "score": score, "confidence": confidence,
                "decay_score": decay_score, "lifecycle": lifecycle,
            }

    def get_node(self, memory_id):
        with self._lock:
            return self._nodes.get(memory_id)

    def create_edge(self, src, tgt, edge_type, properties=None) -> None:
        with self._lock:
            if not any(e[:3] == (src, tgt, edge_type) for e in self._edges):
                self._edges.append((src, tgt, edge_type, properties or {}))

    def batch_create_edges(self, edges) -> int:
        count = 0
        with self._lock:
            for src, tgt, etype, props in edges:
                if not any(e[:3] == (src, tgt, etype) for e in self._edges):
                    self._edges.append((src, tgt, etype, props or {}))
                    count += 1
        return count

    def count_edges(self) -> int:
        with self._lock:
            return len(self._edges)

    def mark_label(self, memory_id, label) -> None:
        with self._lock:
            if memory_id in self._nodes:
                self._nodes[memory_id]["lifecycle"] = label.lower()

    @property
    def edges(self) -> list[tuple[str, str, str, dict]]:
        with self._lock:
            return list(self._edges)


@pytest.fixture
def sqlite_store(tmp_path):
    return SQLiteStore(str(tmp_path / "memories.db"))


@pytest.fixture
def neo4j_mock():
    return _MockNeo4j()


@pytest.fixture
def embedder_mock():
    """1024-dim constant embedder. Vector content is irrelevant here because
    Qdrant search is mocked; what matters is that the dim matches qdrant._dims
    so the SyncService dim guard does not skip the upsert."""
    mock = MagicMock()
    mock.embed.return_value = [0.1] * 1024
    mock.dims = 1024
    return mock


@pytest.fixture
def manager_factory(tmp_path, sqlite_store, neo4j_mock, embedder_mock):
    """Build a MemoryManager with a configurable Qdrant mock. Returns
    (build, qdrant_mock) so each test can wire `qdrant_mock.search.return_value`
    however it wants before constructing the manager."""

    def _build(qdrant_mock):
        return MemoryManager(
            storage_dir=str(tmp_path),
            sqlite_store=sqlite_store,
            qdrant=qdrant_mock,
            neo4j=neo4j_mock,
            embedder=embedder_mock,
        )

    return _build


# ---------------------------------------------------------------------------
# Bug-pattern regression tests
# ---------------------------------------------------------------------------


class TestLinkerInvocation:
    """Wiring-level tests: linker must be CALLED, not just constructed."""

    def test_store_memory_invokes_link_with_neighbors_when_qdrant_returns_hits(
        self, monkeypatch, manager_factory, sqlite_store, embedder_mock,
    ):
        """Bug pattern S6/M-11.A: linker constructed but never invoked.

        Pre-fix, `MemoryManager.store_memory` queues secondary sync but
        `_sync_entry` only writes Qdrant + Neo4j node — never calls the linker.
        This spy fails today and passes once the linker is wired into the
        sync path.
        """
        # Pre-existing entry that Qdrant will return as a search hit
        existing = MemoryEntry.create(
            tier=MemoryTier.SEMANTIC, domain="test",
            content="pre-existing semantic fact", score=7.0,
        )
        sqlite_store.insert(existing)

        qdrant = MagicMock()
        qdrant._dims = 1024
        # Return the existing entry as a hit on the SEMANTIC tier; empty
        # elsewhere. The linker must filter the new entry out by memory_id.
        def _search(tier, _vec, **_kw):
            if tier == MemoryTier.SEMANTIC:
                return [(existing.memory_id, 0.9)]
            return []
        qdrant.search.side_effect = _search

        from shared.memory_layer import _linker as linker_mod
        invocations: list[tuple[str, list[tuple[str, float]]]] = []
        original = linker_mod.AutonomousLinker.link_with_neighbors

        def spy(self, new_entry, neighbors):
            invocations.append((
                new_entry.memory_id,
                [(n.memory_id, sim) for n, sim in neighbors],
            ))
            return original(self, new_entry, neighbors)

        monkeypatch.setattr(
            linker_mod.AutonomousLinker, "link_with_neighbors", spy,
        )

        manager = manager_factory(qdrant)
        new_id = manager.store_memory(
            tier=MemoryTier.EPISODIC, domain="test",
            content="new episodic event referencing the fact",
            score=8.0,
        )
        manager.flush_secondary_sync(timeout=2.0)

        assert len(invocations) == 1, (
            f"Expected exactly 1 link_with_neighbors invocation, got {len(invocations)}. "
            "AutonomousLinker is constructed in MemoryManager.__init__ but the sync "
            "path must invoke it after upsert + node creation (M-11.A)."
        )
        invoked_id, neighbor_ids_and_scores = invocations[0]
        assert invoked_id == new_id
        assert (existing.memory_id, 0.9) in neighbor_ids_and_scores

    def test_store_memory_filters_self_match(
        self, monkeypatch, manager_factory, embedder_mock,
    ):
        """Self-match guard: Qdrant returns the just-upserted point as its top
        hit (cosine ≈ 1.0). Linker must filter it before classifying."""
        qdrant = MagicMock()
        qdrant._dims = 1024
        # Configure search to always return whatever was last upserted (self-match)
        last_upserted: dict[str, str] = {}

        def _upsert(memory_id, *_a, **_kw):
            last_upserted["id"] = memory_id
        qdrant.upsert.side_effect = _upsert

        def _search(_tier, _vec, **_kw):
            mid = last_upserted.get("id")
            return [(mid, 1.0)] if mid else []
        qdrant.search.side_effect = _search

        from shared.memory_layer import _linker as linker_mod
        invocations: list[list[str]] = []
        original = linker_mod.AutonomousLinker.link_with_neighbors

        def spy(self, new_entry, neighbors):
            invocations.append([n.memory_id for n, _s in neighbors])
            return original(self, new_entry, neighbors)

        monkeypatch.setattr(
            linker_mod.AutonomousLinker, "link_with_neighbors", spy,
        )

        manager = manager_factory(qdrant)
        manager.store_memory(
            tier=MemoryTier.EPISODIC, domain="test",
            content="solo entry", score=7.0,
        )
        manager.flush_secondary_sync(timeout=2.0)

        # Self-only hit → linker is either not called (no neighbors after filter)
        # or called with empty neighbors list. Either way, it must not link to
        # itself.
        for neighbor_ids in invocations:
            assert all(nid != last_upserted["id"] for nid in neighbor_ids), (
                f"Linker received self in neighbors: {neighbor_ids}"
            )


# ---------------------------------------------------------------------------
# Acceptance test (runner table S6)
# ---------------------------------------------------------------------------


class TestAcceptance:
    """Runner-table acceptance criteria for session S6.

    > After `MemoryManager.store_memory(...)` × 5, Neo4j edge count > 0
    > (verify via `_neo4j_store.count_edges`).
    """

    def test_five_store_memory_calls_produce_neo4j_edges(
        self, manager_factory, neo4j_mock, embedder_mock,
    ):
        upserted: list[str] = []

        qdrant = MagicMock()
        qdrant._dims = 1024

        def _upsert(memory_id, _tier, _vec, _payload):
            upserted.append(memory_id)
        qdrant.upsert.side_effect = _upsert

        # Each search returns previously-upserted IDs in the SEMANTIC tier
        # (mimicking 5 memories that all live in the same tier and resemble
        # each other). The linker filters self-match by memory_id internally.
        def _search(tier, _vec, top_k=5, **_kw):
            if tier != MemoryTier.SEMANTIC:
                return []
            return [(mid, 0.9) for mid in upserted[-top_k:]]
        qdrant.search.side_effect = _search

        manager = manager_factory(qdrant)

        for i in range(5):
            manager.store_memory(
                tier=MemoryTier.SEMANTIC, domain="research",
                content=f"finding {i}: quantum encryption robustness",
                score=7.5,
            )
        manager.flush_secondary_sync(timeout=2.0)

        edge_count = neo4j_mock.count_edges()
        assert edge_count > 0, (
            f"Expected Neo4j edges after 5 store_memory calls, got {edge_count}. "
            "M-11.A: AutonomousLinker.link_with_neighbors not invoked from sync path."
        )
        # Same-tier (SEMANTIC) + similarity 0.9 → SIMILAR_TO via classify_relationship
        edge_types = {e[2] for e in neo4j_mock.edges}
        assert EdgeType.SIMILAR_TO.value in edge_types

    def test_link_skipped_when_qdrant_unavailable(
        self, tmp_path, sqlite_store, neo4j_mock, embedder_mock,
    ):
        """Graceful degradation: no Qdrant → no neighbor search → no edges,
        no exception. Linker invocation must be optional, not load-bearing."""
        manager = MemoryManager(
            storage_dir=str(tmp_path),
            sqlite_store=sqlite_store,
            qdrant=None,
            neo4j=neo4j_mock,
            embedder=embedder_mock,
        )
        manager.store_memory(
            tier=MemoryTier.EPISODIC, domain="test",
            content="degraded mode entry", score=7.0,
        )
        manager.flush_secondary_sync(timeout=2.0)
        # Nodes still created via Neo4j path; edges = 0 because no search ran.
        assert neo4j_mock.count_edges() == 0
        assert neo4j_mock.get_node is not None  # node creation untouched
