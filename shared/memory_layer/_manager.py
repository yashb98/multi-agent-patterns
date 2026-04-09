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

from shared.logging_config import get_logger
from shared.memory_layer._entries import EpisodicEntry, ProceduralEntry, PatternEntry
from shared.memory_layer._stores import (
    ShortTermMemory, EpisodicMemory, SemanticMemory, ProceduralMemory,
)
from shared.memory_layer._pattern import PatternMemory
from shared.memory_layer._router import TieredRouter

logger = get_logger(__name__)


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

    def __init__(self, storage_dir: str = "/tmp/agent_memory"):
        self.storage_dir = storage_dir
        os.makedirs(storage_dir, exist_ok=True)

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

        return "\n".join(lines)
