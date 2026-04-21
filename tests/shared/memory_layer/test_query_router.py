import pytest
from shared.memory_layer._query import MemoryQuery, QueryRouter, Step, Engine
from shared.memory_layer._entries import MemoryTier


@pytest.fixture
def router():
    return QueryRouter(qdrant_available=True, neo4j_available=True)


class TestQueryRouter:
    def test_exact_lookup_routes_to_sqlite(self, router):
        q = MemoryQuery(memory_id="abc123")
        plan = router.route(q)
        assert plan.engines == [Engine.SQLITE]

    def test_semantic_query_routes_to_qdrant(self, router):
        q = MemoryQuery(semantic_query="form filling")
        plan = router.route(q)
        assert Engine.QDRANT in plan.engines
        assert Step.VECTOR_SEARCH in plan.steps

    def test_graph_depth_adds_neo4j(self, router):
        q = MemoryQuery(semantic_query="form filling", graph_depth=2)
        plan = router.route(q)
        assert Engine.NEO4J in plan.engines
        assert Step.GRAPH_EXPAND in plan.steps

    def test_domain_only_routes_to_neo4j(self, router):
        q = MemoryQuery(domain="greenhouse")
        plan = router.route(q)
        assert Engine.NEO4J in plan.engines
        assert Step.DOMAIN_CLUSTER in plan.steps

    def test_fallback_to_sqlite_when_qdrant_down(self):
        router = QueryRouter(qdrant_available=False, neo4j_available=True)
        q = MemoryQuery(semantic_query="test")
        plan = router.route(q)
        assert Engine.QDRANT not in plan.engines
        assert Engine.SQLITE in plan.engines
        assert Step.FTS_SEARCH in plan.steps

    def test_fallback_skips_graph_when_neo4j_down(self):
        router = QueryRouter(qdrant_available=True, neo4j_available=False)
        q = MemoryQuery(semantic_query="test", graph_depth=2)
        plan = router.route(q)
        assert Engine.NEO4J not in plan.engines
        assert Step.GRAPH_EXPAND not in plan.steps

    def test_min_decay_score_filter_applied(self, router):
        q = MemoryQuery(semantic_query="test", min_decay_score=0.3)
        plan = router.route(q)
        assert plan.min_decay_score == 0.3

    def test_tier_filter_applied(self, router):
        q = MemoryQuery(semantic_query="test", tiers=[MemoryTier.SEMANTIC])
        plan = router.route(q)
        assert plan.tier_filter == [MemoryTier.SEMANTIC]
