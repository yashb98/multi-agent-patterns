import pytest
from unittest.mock import MagicMock, patch

from shared.memory_layer._entries import MemoryTier, Lifecycle, EdgeType
from shared.memory_layer._neo4j_store import Neo4jStore


class MockNeo4jStore(Neo4jStore):
    """In-memory mock that simulates Neo4j graph operations for unit tests."""

    def __init__(self):
        self._nodes: dict[str, dict] = {}
        self._edges: list[tuple[str, str, str, dict]] = []
        self._available = True

    def create_node(self, memory_id, tier, domain, content_preview, score, confidence,
                    decay_score, lifecycle, created_at):
        self._nodes[memory_id] = {
            "memory_id": memory_id, "tier": tier, "domain": domain,
            "content_preview": content_preview, "score": score,
            "confidence": confidence, "decay_score": decay_score,
            "lifecycle": lifecycle, "created_at": created_at,
        }

    def get_node(self, memory_id):
        return self._nodes.get(memory_id)

    def create_edge(self, source_id, target_id, edge_type, properties=None):
        if not any(e[0] == source_id and e[1] == target_id and e[2] == edge_type
                   for e in self._edges):
            self._edges.append((source_id, target_id, edge_type, properties or {}))

    def expand(self, memory_ids, depth=1, exclude_labels=None):
        exclude = set(exclude_labels or [])
        visited = set(memory_ids)
        frontier = set(memory_ids)
        for _ in range(depth):
            next_frontier = set()
            for node_id in frontier:
                for src, tgt, _, _ in self._edges:
                    neighbor = tgt if src == node_id else (src if tgt == node_id else None)
                    if neighbor and neighbor not in visited:
                        node = self._nodes.get(neighbor, {})
                        if node.get("lifecycle") not in exclude:
                            next_frontier.add(neighbor)
                            visited.add(neighbor)
            frontier = next_frontier
        return list(visited)

    def domain_neighbors(self, domain, limit=20):
        return [nid for nid, n in self._nodes.items()
                if n.get("domain") == domain and n.get("lifecycle") != "archived"][:limit]

    def degree(self, memory_id):
        return sum(1 for s, t, _, _ in self._edges if s == memory_id or t == memory_id)

    def avg_downstream_score(self, memory_id):
        downstream = [t for s, t, _, _ in self._edges if s == memory_id]
        if not downstream:
            return 0.0
        scores = [self._nodes[d]["score"] for d in downstream if d in self._nodes]
        return sum(scores) / len(scores) if scores else 0.0

    def count_similar(self, memory_id):
        return sum(1 for s, t, tp, _ in self._edges
                   if tp == "SIMILAR_TO" and (s == memory_id or t == memory_id))

    def mark_label(self, memory_id, label):
        if memory_id in self._nodes:
            self._nodes[memory_id]["lifecycle"] = label.lower()

    def batch_create_edges(self, edges):
        count = 0
        for src, tgt, etype, props in edges:
            if not any(e[0] == src and e[1] == tgt and e[2] == etype for e in self._edges):
                self._edges.append((src, tgt, etype, props))
                count += 1
        return count

    def verify(self):
        return self._available


@pytest.fixture
def store():
    return MockNeo4jStore()


class TestNeo4jStore:
    def test_create_node_and_retrieve(self, store):
        store.create_node("n1", "episodic", "test", "preview", 7.0, 0.8, 1.0, "stm", "2026-01-01")
        node = store.get_node("n1")
        assert node is not None
        assert node["memory_id"] == "n1"
        assert node["domain"] == "test"

    def test_create_edge(self, store):
        store.create_node("n1", "episodic", "test", "a", 7.0, 0.8, 1.0, "stm", "2026-01-01")
        store.create_node("n2", "semantic", "test", "b", 8.0, 0.9, 1.0, "stm", "2026-01-01")
        store.create_edge("n1", "n2", "SIMILAR_TO", {"similarity": 0.9})
        assert store.degree("n1") == 1

    def test_graph_expand_1_hop(self, store):
        store.create_node("a", "episodic", "t", "a", 7.0, 0.8, 1.0, "stm", "2026-01-01")
        store.create_node("b", "semantic", "t", "b", 7.0, 0.8, 1.0, "stm", "2026-01-01")
        store.create_node("c", "procedural", "t", "c", 7.0, 0.8, 1.0, "stm", "2026-01-01")
        store.create_edge("a", "b", "PRODUCED")
        store.create_edge("b", "c", "RELATED_TO")
        result = store.expand(["a"], depth=1)
        assert set(result) == {"a", "b"}

    def test_graph_expand_2_hops(self, store):
        store.create_node("a", "episodic", "t", "a", 7.0, 0.8, 1.0, "stm", "2026-01-01")
        store.create_node("b", "semantic", "t", "b", 7.0, 0.8, 1.0, "stm", "2026-01-01")
        store.create_node("c", "procedural", "t", "c", 7.0, 0.8, 1.0, "stm", "2026-01-01")
        store.create_edge("a", "b", "PRODUCED")
        store.create_edge("b", "c", "RELATED_TO")
        result = store.expand(["a"], depth=2)
        assert set(result) == {"a", "b", "c"}

    def test_graph_expand_excludes_forgotten(self, store):
        store.create_node("a", "episodic", "t", "a", 7.0, 0.8, 1.0, "stm", "2026-01-01")
        store.create_node("b", "semantic", "t", "b", 7.0, 0.8, 1.0, "archived", "2026-01-01")
        store.create_node("c", "procedural", "t", "c", 7.0, 0.8, 1.0, "stm", "2026-01-01")
        store.create_edge("a", "b", "PRODUCED")
        store.create_edge("b", "c", "RELATED_TO")
        result = store.expand(["a"], depth=2, exclude_labels=["archived"])
        assert "c" not in result

    def test_domain_neighbors(self, store):
        for i in range(3):
            store.create_node(f"g{i}", "episodic", "greenhouse", f"g{i}", 7.0, 0.8, 1.0, "stm", "2026-01-01")
        for i in range(2):
            store.create_node(f"w{i}", "episodic", "workday", f"w{i}", 7.0, 0.8, 1.0, "stm", "2026-01-01")
        result = store.domain_neighbors("greenhouse")
        assert len(result) == 3

    def test_degree_count(self, store):
        store.create_node("hub", "episodic", "t", "hub", 7.0, 0.8, 1.0, "stm", "2026-01-01")
        for i in range(7):
            store.create_node(f"n{i}", "semantic", "t", f"n{i}", 7.0, 0.8, 1.0, "stm", "2026-01-01")
            store.create_edge("hub", f"n{i}", "PRODUCED")
        assert store.degree("hub") == 7

    def test_downstream_scores(self, store):
        store.create_node("a", "episodic", "t", "a", 5.0, 0.8, 1.0, "stm", "2026-01-01")
        store.create_node("b", "procedural", "t", "b", 8.0, 0.8, 1.0, "stm", "2026-01-01")
        store.create_node("c", "procedural", "t", "c", 6.0, 0.8, 1.0, "stm", "2026-01-01")
        store.create_edge("a", "b", "TAUGHT")
        store.create_edge("a", "c", "TAUGHT")
        assert store.avg_downstream_score("a") == 7.0

    def test_count_similar(self, store):
        store.create_node("target", "semantic", "t", "t", 7.0, 0.8, 1.0, "stm", "2026-01-01")
        for i in range(4):
            store.create_node(f"s{i}", "semantic", "t", f"s{i}", 7.0, 0.8, 1.0, "stm", "2026-01-01")
            store.create_edge("target", f"s{i}", "SIMILAR_TO")
        assert store.count_similar("target") == 4

    def test_platform_node_linking(self, store):
        store.create_node("proc1", "procedural", "greenhouse", "escape", 8.0, 0.9, 1.0, "stm", "2026-01-01")
        store.create_node("greenhouse", "platform", "greenhouse", "Greenhouse", 0.0, 1.0, 1.0, "ltm", "2026-01-01")
        store.create_edge("proc1", "greenhouse", "APPLIES_TO")
        result = store.expand(["greenhouse"], depth=1)
        assert "proc1" in result

    def test_batch_edge_creation(self, store):
        for i in range(20):
            store.create_node(f"n{i}", "episodic", "t", f"n{i}", 7.0, 0.8, 1.0, "stm", "2026-01-01")
        edges = [(f"n{i}", f"n{i+1}", "RELATED_TO", {}) for i in range(15)]
        count = store.batch_create_edges(edges)
        assert count == 15
