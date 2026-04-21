"""QueryRouter — picks engine(s) and steps based on query type."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from shared.memory_layer._entries import MemoryTier


class Engine(str, Enum):
    SQLITE = "sqlite"
    QDRANT = "qdrant"
    NEO4J = "neo4j"


class Step(str, Enum):
    VECTOR_SEARCH = "vector_search"
    FTS_SEARCH = "fts_search"
    GRAPH_EXPAND = "graph_expand"
    DOMAIN_CLUSTER = "domain_cluster"
    HYDRATE = "hydrate"
    DEDUPLICATE = "deduplicate"


@dataclass
class MemoryQuery:
    memory_id: str | None = None
    semantic_query: str | None = None
    domain: str | None = None
    tiers: list[MemoryTier] | None = None
    graph_depth: int = 0
    top_k: int = 10
    min_decay_score: float = 0.1
    min_confidence: float = 0.0


@dataclass
class RetrievalPlan:
    engines: list[Engine] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)
    tier_filter: list[MemoryTier] | None = None
    min_decay_score: float = 0.1
    top_k: int = 10
    graph_depth: int = 0


class QueryRouter:
    def __init__(self, qdrant_available: bool = True, neo4j_available: bool = True):
        self._qdrant = qdrant_available
        self._neo4j = neo4j_available

    def route(self, query: MemoryQuery) -> RetrievalPlan:
        plan = RetrievalPlan(
            tier_filter=query.tiers,
            min_decay_score=query.min_decay_score,
            top_k=query.top_k,
            graph_depth=query.graph_depth,
        )

        if query.memory_id:
            plan.engines = [Engine.SQLITE]
            plan.steps = [Step.HYDRATE]
            return plan

        if query.semantic_query:
            if self._qdrant:
                plan.engines.append(Engine.QDRANT)
                plan.steps.append(Step.VECTOR_SEARCH)
            else:
                plan.engines.append(Engine.SQLITE)
                plan.steps.append(Step.FTS_SEARCH)

            if query.graph_depth > 0 and self._neo4j:
                plan.engines.append(Engine.NEO4J)
                plan.steps.append(Step.GRAPH_EXPAND)
                plan.steps.append(Step.DEDUPLICATE)

            plan.engines.append(Engine.SQLITE)
            plan.steps.append(Step.HYDRATE)
            return plan

        if query.domain and not query.semantic_query:
            if self._neo4j:
                plan.engines.append(Engine.NEO4J)
                plan.steps.append(Step.DOMAIN_CLUSTER)
            plan.engines.append(Engine.SQLITE)
            plan.steps.append(Step.HYDRATE)
            return plan

        plan.engines = [Engine.SQLITE]
        plan.steps = [Step.HYDRATE]
        return plan
