"""MemoryManager — unified interface for all memory systems.

Single point of contact for agents and the orchestrator. Instead of
each agent querying five different memory stores, they call
memory_manager.get_context(agent, topic, domain) and receive a single,
formatted context package.
"""

import os
import hashlib
from typing import Optional
from datetime import datetime

from typing import TYPE_CHECKING

from shared.logging_config import get_logger
from shared.memory_layer._entries import (
    EpisodicEntry, ProceduralEntry, PatternEntry,
    MemoryEntry, MemoryTier, Lifecycle,
)
from shared.memory_layer._stores import (
    ShortTermMemory, EpisodicMemory, SemanticMemory, ProceduralMemory,
)
from shared.memory_layer._pattern import PatternMemory
from shared.memory_layer._router import TieredRouter
from shared.paths import DATA_DIR

if TYPE_CHECKING:
    from shared.memory_layer._embedder import MemoryEmbedder
    from shared.memory_layer._neo4j_store import Neo4jStore
    from shared.memory_layer._qdrant_store import QdrantStore
    from shared.memory_layer._query import MemoryQuery
    from shared.memory_layer._sqlite_store import SQLiteStore

logger = get_logger(__name__)

# Default persistent storage directory — survives process restarts
_DEFAULT_STORAGE_DIR = str(DATA_DIR / "agent_memory")


class MemoryManager:
    """
    Unified interface for all memory systems.

    This is the SINGLE POINT OF CONTACT for agents and the orchestrator.
    Instead of each agent querying five different memory stores,
    they call memory_manager.get_context(agent, topic, domain)
    and receive a single, formatted context package.

    USAGE:
        memory = MemoryManager()

        # Before agent runs: get relevant context
        context = memory.get_context_for_agent(
            agent_name="researcher",
            topic="quantum computing",
            domain="physics"
        )
        enhanced_prompt = f"{base_prompt}\\n\\n{context}"

        # After agent runs: record what happened
        memory.record_step("researcher", "Gathered 15 sources on quantum ML")

        # After full run completes: store episode
        memory.record_episode(topic, score, iterations, ...)

        # Learn from the run
        memory.learn_fact("physics", "Quantum advantage demonstrated in 2024")
        memory.learn_procedure("physics", "Always cite Nature/Science for quantum claims")
    """

    def __init__(
        self,
        storage_dir: str = _DEFAULT_STORAGE_DIR,
        sqlite_store: "SQLiteStore | None" = None,
        qdrant: "QdrantStore | None" = None,
        neo4j: "Neo4jStore | None" = None,
        embedder: "MemoryEmbedder | None" = None,
    ):
        self.storage_dir = storage_dir
        os.makedirs(storage_dir, exist_ok=True)

        # Old JSON-based stores (always initialised for backwards compat)
        self.short_term = ShortTermMemory(max_size=30)
        self.episodic = EpisodicMemory(
            storage_path=os.path.join(storage_dir, "episodic.json")
        )
        self.semantic = SemanticMemory(
            storage_path=os.path.join(storage_dir, "semantic.json")
        )
        self.procedural = ProceduralMemory(
            storage_path=os.path.join(storage_dir, "procedural.json")
        )
        self.patterns = PatternMemory(
            storage_path=os.path.join(storage_dir, "patterns.json")
        )
        self.router = TieredRouter(self.patterns, self.episodic)

        # New 3-engine system (optional — None means old-style JSON mode)
        self._sqlite = sqlite_store
        self._qdrant = qdrant
        self._neo4j = neo4j
        self._embedder = embedder
        self._sync = None
        self._linker = None
        self._forgetting = None

        if sqlite_store:
            from shared.memory_layer._sync import SyncService
            from shared.memory_layer._linker import AutonomousLinker
            from shared.memory_layer._forgetting import ForgettingEngine
            self._sync = SyncService(sqlite_store, qdrant, neo4j, embedder)
            self._linker = AutonomousLinker(neo4j=neo4j)
            self._forgetting = ForgettingEngine(neo4j=neo4j)

    def get_context_for_agent(
        self,
        agent_name: str,
        topic: str,
        domain: str = "",
    ) -> str:
        """
        Build a complete memory context package for an agent.

        This is called BEFORE each agent execution. It assembles
        relevant memories from all stores into a single string
        that gets injected into the agent's system prompt.

        Different agents get different memory slices:
        - Researcher: episodic (what worked before) + semantic (domain facts)
        - Writer: procedural (writing strategies) + short-term (recent steps)
        - Reviewer: episodic (past scores) + semantic (quality standards)
        """
        sections = []

        # Short-term: always include recent context
        stm = self.short_term.format_for_prompt(n=5)
        if stm:
            sections.append(stm)

        # Episodic: relevant past runs
        if agent_name in ("researcher", "reviewer", "task_analysis"):
            epi = self.episodic.format_for_prompt(topic, domain, n=3)
            if epi:
                sections.append(epi)

        # Semantic: domain knowledge
        if agent_name in ("researcher", "writer", "reviewer", "fact_checker"):
            sem = self.semantic.format_for_prompt(domain, n=5)
            if sem:
                sections.append(sem)

        # Procedural: proven strategies
        if agent_name in ("writer", "researcher"):
            proc = self.procedural.format_for_prompt(domain, n=3)
            if proc:
                sections.append(proc)

        # Experiential: GRPO-learned patterns from successful past runs
        # All agents benefit from these — they capture cross-agent winning patterns
        try:
            from shared.experiential_learning import get_shared_experience_memory
            exp_mem = get_shared_experience_memory()
            exp_context = exp_mem.format_for_prompt(domain or agent_name, n=2)
            if exp_context:
                sections.append(exp_context)
        except Exception:
            pass  # ExperienceMemory is optional — never block agent execution

        if not sections:
            return ""

        return "\n\n".join(sections)

    # ── Operational Principle #1: Memory before action ──

    def search_patterns(
        self, topic: str, domain: str = ""
    ) -> tuple[Optional[PatternEntry], float]:
        """
        MUST be called before starting any task.
        Returns (pattern, score). If score > 0.7, reuse the pattern.
        """
        return self.patterns.search(topic, domain)

    # ── Operational Principle #4: Learn after success ──

    def learn_from_success(
        self,
        topic: str,
        domain: str,
        agents_used: list[str],
        routing_decisions: list[str],
        final_score: float,
        iterations: int,
        strengths: list[str],
        output_summary: str,
    ):
        """
        MUST be called after any run with score >= 7.0.
        Stores the pattern in the 'patterns' namespace for future reuse.
        """
        self.patterns.store(
            topic=topic, domain=domain, agents_used=agents_used,
            routing_decisions=routing_decisions, final_score=final_score,
            iterations=iterations, strengths=strengths,
            output_summary=output_summary,
        )

    def record_step(
        self, agent: str, summary: str, score: float = None
    ):
        """Record a step in short-term memory (within current run)."""
        self.short_term.add(agent, "step", summary, score)

    def record_episode(
        self,
        topic: str,
        final_score: float,
        iterations: int,
        pattern_used: str,
        agents_used: list[str],
        strengths: list[str],
        weaknesses: list[str],
        output_summary: str,
        duration_seconds: float = 0,
        total_llm_calls: int = 0,
        domain: str = "",
    ):
        """Record a completed run as an episodic memory."""
        episode = EpisodicEntry(
            run_id=hashlib.md5(
                f"{topic}{datetime.now().isoformat()}".encode()
            ).hexdigest()[:10],
            topic=topic,
            timestamp=datetime.now().isoformat(),
            final_score=final_score,
            iterations=iterations,
            pattern_used=pattern_used,
            agents_used=agents_used,
            strengths=strengths,
            weaknesses=weaknesses,
            output_summary=output_summary[:500],
            duration_seconds=duration_seconds,
            total_llm_calls=total_llm_calls,
            domain=domain,
        )
        self.episodic.store(episode)
        logger.info("Stored episode: %s (score: %.1f)", topic, final_score)

    def learn_fact(self, domain: str, fact: str, run_id: str = "manual"):
        """Add or reinforce a semantic fact."""
        self.semantic.learn(domain, fact, run_id)

    def learn_procedure(
        self,
        domain: str,
        strategy: str,
        context: str = "",
        score: float = 7.0,
        source: str = "runtime",
    ):
        """Store a successful procedure."""
        proc = ProceduralEntry(
            procedure_id=hashlib.md5(
                f"{domain}{strategy[:50]}".encode()
            ).hexdigest()[:10],
            domain=domain,
            strategy=strategy,
            context=context,
            success_rate=1.0 if score >= 7.0 else 0.5,
            times_used=1,
            avg_score_when_used=score,
            source=source,
            created_at=datetime.now().isoformat(),
        )
        self.procedural.store(proc)

    def get_procedural_entries(self, domain: str) -> list[ProceduralEntry]:
        """Retrieve procedural templates for a domain (cognitive engine API)."""
        return self.procedural.recall(domain)

    def get_episodic_entries(self, domain: str) -> list[EpisodicEntry]:
        """Retrieve episodic memories for a domain (cognitive engine API)."""
        return self.episodic.recall("", domain)

    def start_new_session(self):
        """Clear short-term memory for a new session."""
        self.short_term.clear()

    def get_memory_report(self) -> str:
        """Generate a comprehensive memory status report."""
        lines = [
            "Memory System Report",
            "=" * 50,
            f"\nShort-term memory: {len(self.short_term.buffer)} entries "
            f"(max {self.short_term.buffer.maxlen})",
            f"Episodic memory: {len(self.episodic.episodes)} episodes "
            f"(max {self.episodic.max_episodes})",
            f"Semantic memory: {len(self.semantic.facts)} facts "
            f"(max {self.semantic.max_facts})",
            f"Procedural memory: {len(self.procedural.procedures)} procedures "
            f"(max {self.procedural.max_procedures})",
        ]

        # Domain breakdown
        domains = set()
        for ep in self.episodic.episodes:
            if ep.domain:
                domains.add(ep.domain)
        for f in self.semantic.facts.values():
            domains.add(f.domain)

        if domains:
            lines.append(f"\nDomains covered: {', '.join(sorted(domains))}")
            for domain in sorted(domains):
                stats = self.episodic.get_domain_stats(domain)
                if stats["runs"] > 0:
                    lines.append(
                        f"  {domain}: {stats['runs']} runs, "
                        f"avg score {stats.get('avg_score', 0):.1f}"
                    )

        # Lifecycle stats from new engine
        if self._sqlite:
            total = self._sqlite.count()
            lines.append(f"\n3-Engine Memory: {total} entries in SQLite")
            for lc in Lifecycle:
                entries = self._sqlite.query_by_lifecycle(lc, limit=0)
                # count via a direct query instead
            lines.append("Engines: SQLite (active)")
            if self._qdrant:
                lines.append("  + Qdrant (active)")
            if self._neo4j:
                lines.append("  + Neo4j (active)")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # New 3-engine methods
    # ------------------------------------------------------------------

    def store_memory(
        self,
        tier: MemoryTier,
        domain: str,
        content: str,
        score: float = 0.0,
        confidence: float = 0.7,
        payload: dict | None = None,
    ) -> str:
        """Write a memory to all 3 engines. Returns memory_id."""
        entry = MemoryEntry.create(
            tier=tier, domain=domain, content=content,
            score=score, confidence=confidence, payload=payload,
        )

        if self._sqlite:
            self._sqlite.insert(entry)

        if self._sync:
            self._sync.sync_to_secondary(entry)

        return entry.memory_id

    def query(self, query: "MemoryQuery") -> list[MemoryEntry]:
        """Routed retrieval across engines."""
        from shared.memory_layer._query import QueryRouter, Engine, Step

        if not self._sqlite:
            return []

        router = QueryRouter(
            qdrant_available=self._qdrant is not None,
            neo4j_available=self._neo4j is not None,
        )
        plan = router.route(query)

        # Exact lookup
        if query.memory_id:
            entry = self._sqlite.get_by_id(query.memory_id)
            return [entry] if entry else []

        memory_ids: set[str] = set()

        # Vector search
        if Step.VECTOR_SEARCH in plan.steps and self._qdrant and self._embedder:
            vec = self._embedder.embed(query.semantic_query)
            tiers = query.tiers or [MemoryTier.EPISODIC, MemoryTier.SEMANTIC, MemoryTier.PROCEDURAL]
            for tier in tiers:
                results = self._qdrant.search(tier, vec, top_k=query.top_k)
                memory_ids.update(mid for mid, _ in results)

        # FTS fallback
        if Step.FTS_SEARCH in plan.steps:
            for entry in self._sqlite.query_active(min_decay=query.min_decay_score):
                if query.semantic_query and query.semantic_query.lower() in entry.content.lower():
                    memory_ids.add(entry.memory_id)

        # Graph expansion
        if Step.GRAPH_EXPAND in plan.steps and self._neo4j and memory_ids:
            expanded = self._neo4j.expand(list(memory_ids), depth=query.graph_depth)
            memory_ids.update(expanded)

        # Domain cluster
        if Step.DOMAIN_CLUSTER in plan.steps and self._neo4j and query.domain:
            neighbors = self._neo4j.domain_neighbors(query.domain, limit=query.top_k)
            memory_ids.update(neighbors)

        # Hydrate from SQLite
        results = []
        for mid in memory_ids:
            entry = self._sqlite.get_by_id(mid)
            if entry and entry.decay_score >= query.min_decay_score:
                if not query.tiers or entry.tier in query.tiers:
                    results.append(entry)

        results.sort(key=lambda e: e.decay_score, reverse=True)
        return results[:query.top_k]

    def pin_memory(self, memory_id: str) -> None:
        """Mark a memory as pinned (never auto-deleted)."""
        if not self._sqlite:
            return
        entry = self._sqlite.get_by_id(memory_id)
        if entry:
            entry.payload["pinned"] = True
            self._sqlite.insert(entry)  # upsert

    def startup(self) -> dict:
        """Verify engines and run reconciliation."""
        stats = {}
        if self._sync:
            stats = self._sync.reconcile()
        return stats

    def health(self) -> dict:
        """Engine status and counts."""
        report = {
            "sqlite": "active" if self._sqlite else "not configured",
            "qdrant": "active" if self._qdrant else "not configured",
            "neo4j": "active" if self._neo4j else "not configured",
        }
        if self._sqlite:
            report["sqlite_count"] = self._sqlite.count()
        return report


# ---------------------------------------------------------------------------
# Shared singleton factory — mirrors get_shared_experience_memory() pattern
# ---------------------------------------------------------------------------

_shared_manager: "MemoryManager | None" = None


def get_shared_memory_manager(storage_dir: str | None = None) -> "MemoryManager":
    """Return (or create) the shared MemoryManager singleton.

    All pattern modules should call this instead of constructing their own
    MemoryManager() — ensures all agents share the same episodic, semantic,
    and procedural memory across a process lifetime.

    Args:
        storage_dir: Override storage path (use tmp_path in tests).
    """
    global _shared_manager
    if _shared_manager is None:
        path = storage_dir or _DEFAULT_STORAGE_DIR
        _shared_manager = MemoryManager(storage_dir=path)
        logger.info("Shared MemoryManager initialised at %s", path)
    return _shared_manager


def reset_shared_memory_manager() -> None:
    """Reset the shared singleton. Used for test isolation."""
    global _shared_manager
    _shared_manager = None
