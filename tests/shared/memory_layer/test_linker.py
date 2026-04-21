import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime

from shared.memory_layer._entries import MemoryEntry, MemoryTier, Lifecycle, EdgeType
from shared.memory_layer._linker import AutonomousLinker, classify_relationship


# Use make_memory from conftest.py for creating test entries


class MockQdrant:
    """Simple mock that returns configurable search results."""
    def __init__(self):
        self.stored = {}  # memory_id -> (tier, vector)

    def search(self, tier, query_vector, top_k=10, score_threshold=None, **kwargs):
        return list(self.stored.get(tier, {}).items())[:top_k]

    def search_all_tiers(self, query_vector, top_k=10, score_threshold=0.75):
        results = []
        for tier_results in self.stored.values():
            results.extend(tier_results.items())
        return results[:top_k]


class MockNeo4j:
    """Simple mock for Neo4j graph operations."""
    def __init__(self):
        self._nodes = {}
        self._edges = []
        self._available = True

    def create_edge(self, src, tgt, edge_type, properties=None):
        if not any(e[:3] == (src, tgt, edge_type) for e in self._edges):
            self._edges.append((src, tgt, edge_type, properties or {}))

    def batch_create_edges(self, edges):
        count = 0
        for src, tgt, etype, props in edges:
            if not any(e[:3] == (src, tgt, etype) for e in self._edges):
                self._edges.append((src, tgt, etype, props))
                count += 1
        return count

    def domain_neighbors(self, domain, limit=20):
        return [nid for nid, n in self._nodes.items() if n.get("domain") == domain][:limit]

    def degree(self, memory_id):
        return sum(1 for s, t, _, _ in self._edges if s == memory_id or t == memory_id)

    @property
    def edges(self):
        return self._edges


@pytest.fixture
def neo4j():
    return MockNeo4j()


class TestClassifyRelationship:
    def test_same_tier_high_similarity_creates_similar_to(self):
        result = classify_relationship(
            new_tier=MemoryTier.EPISODIC, existing_tier=MemoryTier.EPISODIC,
            similarity=0.9, same_domain=True, new_score=7.0, existing_score=6.0,
        )
        assert result == EdgeType.SIMILAR_TO

    def test_episode_to_fact_creates_produced(self):
        result = classify_relationship(
            new_tier=MemoryTier.EPISODIC, existing_tier=MemoryTier.SEMANTIC,
            similarity=0.6, same_domain=True, new_score=7.0, existing_score=7.0,
        )
        assert result == EdgeType.PRODUCED

    def test_episode_to_procedure_creates_taught(self):
        result = classify_relationship(
            new_tier=MemoryTier.EPISODIC, existing_tier=MemoryTier.PROCEDURAL,
            similarity=0.6, same_domain=True, new_score=7.0, existing_score=7.0,
        )
        assert result == EdgeType.TAUGHT

    def test_experience_to_episode_creates_extracted_from(self):
        result = classify_relationship(
            new_tier=MemoryTier.EXPERIENCE, existing_tier=MemoryTier.EPISODIC,
            similarity=0.5, same_domain=False, new_score=7.0, existing_score=7.0,
        )
        assert result == EdgeType.EXTRACTED_FROM

    def test_higher_score_procedure_creates_supersedes(self):
        result = classify_relationship(
            new_tier=MemoryTier.PROCEDURAL, existing_tier=MemoryTier.PROCEDURAL,
            similarity=0.85, same_domain=True, new_score=9.0, existing_score=6.0,
        )
        assert result == EdgeType.SUPERSEDES

    def test_cross_tier_creates_related_to(self):
        result = classify_relationship(
            new_tier=MemoryTier.SEMANTIC, existing_tier=MemoryTier.PROCEDURAL,
            similarity=0.8, same_domain=False, new_score=7.0, existing_score=7.0,
        )
        assert result == EdgeType.RELATED_TO

    def test_low_similarity_returns_none(self):
        result = classify_relationship(
            new_tier=MemoryTier.EPISODIC, existing_tier=MemoryTier.EPISODIC,
            similarity=0.3, same_domain=True, new_score=7.0, existing_score=7.0,
        )
        assert result is None


class TestAutonomousLinker:
    def test_linking_creates_edges(self, neo4j, make_memory):
        linker = AutonomousLinker(neo4j=neo4j)
        new_entry = make_memory(tier=MemoryTier.EPISODIC, domain="test", content="new")
        existing = make_memory(tier=MemoryTier.SEMANTIC, domain="test", content="existing")
        neighbors = [(existing, 0.8)]
        linker.link_with_neighbors(new_entry, neighbors)
        assert len(neo4j.edges) > 0

    def test_linking_is_idempotent(self, neo4j, make_memory):
        linker = AutonomousLinker(neo4j=neo4j)
        new_entry = make_memory(tier=MemoryTier.EPISODIC, domain="test", content="new")
        existing = make_memory(tier=MemoryTier.SEMANTIC, domain="test", content="existing")
        neighbors = [(existing, 0.8)]
        linker.link_with_neighbors(new_entry, neighbors)
        count1 = len(neo4j.edges)
        linker.link_with_neighbors(new_entry, neighbors)
        count2 = len(neo4j.edges)
        assert count1 == count2

    def test_contradiction_decays_confidence(self, neo4j, make_memory):
        linker = AutonomousLinker(neo4j=neo4j)
        new_fact = make_memory(tier=MemoryTier.SEMANTIC, domain="test", content="Workday has 3 pages", score=8.0, confidence=0.9)
        old_fact = make_memory(tier=MemoryTier.SEMANTIC, domain="test", content="Workday has 5 pages", score=6.0, confidence=0.8)
        result = linker.handle_contradiction(new_fact, old_fact)
        assert result["loser_id"] == old_fact.memory_id
        assert result["new_confidence"] < old_fact.confidence

    def test_contradiction_tombstones_weak_fact(self, neo4j, make_memory):
        linker = AutonomousLinker(neo4j=neo4j)
        strong = make_memory(tier=MemoryTier.SEMANTIC, score=9.0, confidence=0.9, content="strong")
        weak = make_memory(tier=MemoryTier.SEMANTIC, score=3.0, confidence=0.15, content="weak")
        result = linker.handle_contradiction(strong, weak)
        assert result["tombstone"] is True
