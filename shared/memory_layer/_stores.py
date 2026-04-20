"""Core memory stores — ShortTerm, Episodic, Semantic, Procedural.

Each store handles persistence (JSON file) and retrieval for one tier
of the five-tier memory architecture.
"""

import json
import os
import hashlib
import tempfile
from datetime import datetime
from collections import deque

from shared.logging_config import get_logger
from shared.memory_layer._entries import (
    ShortTermEntry, EpisodicEntry, SemanticEntry, ProceduralEntry,
)

logger = get_logger(__name__)


def _atomic_json_write(path: str, data) -> None:
    """Write JSON atomically via temp file + rename to prevent corruption."""
    dir_name = os.path.dirname(path) or "."
    os.makedirs(dir_name, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


class ShortTermMemory:
    """
    Sliding window of recent steps in the current session.

    Agents use this to maintain context within a multi-step workflow.
    The Reviewer can see "the Writer just revised section 3 based on
    my feedback" without needing to re-read the entire state.

    Window size is configurable. Default 20 steps.
    """

    def __init__(self, max_size: int = 20):
        self.buffer: deque[ShortTermEntry] = deque(maxlen=max_size)

    def add(self, agent: str, action: str, summary: str, score: float = None):
        self.buffer.append(ShortTermEntry(
            agent=agent,
            action=action,
            summary=summary,
            score=score,
            timestamp=datetime.now().strftime("%H:%M:%S"),
        ))

    def get_recent(self, n: int = 5) -> list[ShortTermEntry]:
        return list(self.buffer)[-n:]

    def format_for_prompt(self, n: int = 5) -> str:
        recent = self.get_recent(n)
        if not recent:
            return ""
        lines = ["## Recent activity in this session\n"]
        for entry in recent:
            score_str = f" (score: {entry.score:.1f})" if entry.score else ""
            lines.append(f"- [{entry.timestamp}] {entry.agent}: {entry.summary}{score_str}")
        return "\n".join(lines)

    def clear(self):
        self.buffer.clear()


class EpisodicMemory:
    """
    Records of complete past runs.

    After each run completes, an episode is stored with:
    - What the task was
    - How well it went (score, iterations)
    - What worked and what didn't
    - Which pattern and agents were used

    Before a new run starts, relevant episodes are retrieved
    so agents can learn from past experience with similar tasks.

    Persistence: JSON file in dev, PostgreSQL in production.
    """

    def __init__(self, storage_path: str = None, max_episodes: int = 200):
        self.storage_path = storage_path or "/tmp/agent_episodic_memory.json"
        self.max_episodes = max_episodes
        self.episodes: list[EpisodicEntry] = []
        self._load()

    def store(self, episode: EpisodicEntry):
        """Store a completed episode."""
        self.episodes.append(episode)
        # Evict oldest if over capacity
        if len(self.episodes) > self.max_episodes:
            self.episodes.sort(key=lambda e: e.timestamp, reverse=True)
            self.episodes = self.episodes[:self.max_episodes]
        self._save()

    def recall(
        self, topic: str, domain: str = "", n: int = 3
    ) -> list[EpisodicEntry]:
        """Retrieve the N most relevant episodes for a topic."""
        scored = [
            (ep, ep.relevance_score(topic, domain))
            for ep in self.episodes
        ]
        scored.sort(key=lambda x: x[1], reverse=True)
        return [ep for ep, score in scored[:n] if score > 0]

    def format_for_prompt(self, topic: str, domain: str = "", n: int = 3) -> str:
        """Format relevant episodes as injectable prompt context."""
        episodes = self.recall(topic, domain, n)
        if not episodes:
            return ""

        lines = ["## Lessons from similar past tasks\n"]
        for ep in episodes:
            lines.append(f"### Task: {ep.topic} (score: {ep.final_score:.1f}/10)")
            if ep.strengths:
                lines.append(f"What worked: {'; '.join(ep.strengths[:3])}")
            if ep.weaknesses:
                lines.append(f"What to avoid: {'; '.join(ep.weaknesses[:3])}")
            lines.append(f"Pattern: {ep.pattern_used}, {ep.iterations} iterations")
            lines.append("")

        return "\n".join(lines)

    def get_domain_stats(self, domain: str) -> dict:
        """Get aggregate statistics for a domain."""
        domain_eps = [
            ep for ep in self.episodes
            if domain.lower() in ep.domain.lower()
        ]
        if not domain_eps:
            return {"runs": 0}

        scores = [ep.final_score for ep in domain_eps]
        return {
            "runs": len(domain_eps),
            "avg_score": sum(scores) / len(scores),
            "best_score": max(scores),
            "avg_iterations": sum(ep.iterations for ep in domain_eps) / len(domain_eps),
            "most_used_pattern": max(
                set(ep.pattern_used for ep in domain_eps),
                key=lambda p: sum(1 for ep in domain_eps if ep.pattern_used == p)
            ),
        }

    def _save(self):
        try:
            data = [
                {
                    "run_id": ep.run_id, "topic": ep.topic,
                    "timestamp": ep.timestamp, "final_score": ep.final_score,
                    "iterations": ep.iterations, "pattern_used": ep.pattern_used,
                    "agents_used": ep.agents_used,
                    "strengths": ep.strengths, "weaknesses": ep.weaknesses,
                    "output_summary": ep.output_summary,
                    "duration_seconds": ep.duration_seconds,
                    "total_llm_calls": ep.total_llm_calls,
                    "domain": ep.domain,
                }
                for ep in self.episodes
            ]
            _atomic_json_write(self.storage_path, data)
        except Exception as e:
            logger.warning("Failed to save episodic memory: %s", e)

    def _load(self):
        try:
            if os.path.exists(self.storage_path):
                with open(self.storage_path, "r") as f:
                    data = json.load(f)
                self.episodes = [EpisodicEntry(**d) for d in data]
        except Exception as e:
            logger.warning("Failed to load episodic memory: %s", e)
            self.episodes = []


class SemanticMemory:
    """
    Accumulated domain knowledge that persists across all runs.

    Unlike episodic memory (which records events), semantic memory
    stores FACTS and PATTERNS that have been validated across
    multiple episodes.

    Example:
    - "In AI articles, always include benchmark comparisons" (validated 8 times)
    - "Readers prefer code examples in Python over pseudocode" (validated 5 times)
    - "The transformer architecture was introduced in 2017" (fact, high confidence)

    Facts gain confidence when multiple runs validate them.
    Facts lose confidence when runs contradict them.
    This creates a self-correcting knowledge base.

    Persistence: JSON file in dev, Qdrant vector store in production.
    """

    def __init__(self, storage_path: str = None, max_facts: int = 500):
        self.storage_path = storage_path or "/tmp/agent_semantic_memory.json"
        self.max_facts = max_facts
        self.facts: dict[str, SemanticEntry] = {}  # fact_id → entry
        self._load()

    def learn(self, domain: str, fact: str, run_id: str, confidence: float = 0.7):
        """
        Add or reinforce a piece of knowledge.

        If the fact already exists (fuzzy match), reinforce it.
        If it's new, store it with initial confidence.
        """
        fact_id = self._make_id(domain, fact)

        if fact_id in self.facts:
            # Reinforce existing knowledge
            existing = self.facts[fact_id]
            existing.times_validated += 1
            existing.confidence = min(1.0, existing.confidence + 0.05)
            if run_id not in existing.source_runs:
                existing.source_runs.append(run_id)
            existing.last_used = datetime.now().isoformat()
        else:
            # New knowledge
            self.facts[fact_id] = SemanticEntry(
                fact_id=fact_id,
                domain=domain,
                fact=fact,
                confidence=confidence,
                source_runs=[run_id],
                times_validated=1,
                times_contradicted=0,
                created_at=datetime.now().isoformat(),
                last_used=datetime.now().isoformat(),
            )

        self._save()

    def contradict(self, domain: str, fact: str):
        """Record that a fact was contradicted by a run."""
        fact_id = self._make_id(domain, fact)
        if fact_id in self.facts:
            entry = self.facts[fact_id]
            entry.times_contradicted += 1
            entry.confidence = max(0.0, entry.confidence - 0.1)
            # Remove facts that fall below reliability threshold
            if entry.reliability < 0.3:
                del self.facts[fact_id]
            self._save()

    def recall(self, domain: str, n: int = 10) -> list[SemanticEntry]:
        """Retrieve the most reliable facts for a domain."""
        domain_facts = [
            f for f in self.facts.values()
            if domain.lower() in f.domain.lower() and f.reliability > 0.5
        ]
        domain_facts.sort(key=lambda f: f.reliability * f.confidence, reverse=True)
        return domain_facts[:n]

    def format_for_prompt(self, domain: str, n: int = 5) -> str:
        """Format domain knowledge as injectable prompt context."""
        facts = self.recall(domain, n)
        if not facts:
            return ""

        lines = ["## Domain knowledge (validated across past runs)\n"]
        for f in facts:
            reliability = f"({f.times_validated}x validated"
            if f.times_contradicted > 0:
                reliability += f", {f.times_contradicted}x contradicted"
            reliability += ")"
            lines.append(f"- {f.fact} {reliability}")

        lines.append("\nApply this knowledge where relevant.\n")
        return "\n".join(lines)

    def _make_id(self, domain: str, fact: str) -> str:
        """Create a stable ID for a fact (enables deduplication)."""
        # Simple hash-based ID — in production, use embeddings for fuzzy match
        key = f"{domain.lower().strip()}:{fact.lower().strip()[:100]}"
        return hashlib.md5(key.encode()).hexdigest()[:12]

    def _save(self):
        try:
            data = {
                fid: {
                    "fact_id": f.fact_id, "domain": f.domain, "fact": f.fact,
                    "confidence": f.confidence, "source_runs": f.source_runs,
                    "times_validated": f.times_validated,
                    "times_contradicted": f.times_contradicted,
                    "created_at": f.created_at, "last_used": f.last_used,
                }
                for fid, f in self.facts.items()
            }
            _atomic_json_write(self.storage_path, data)
        except Exception as e:
            logger.debug("Failed to save semantic memory: %s", e)

    def _load(self):
        try:
            if os.path.exists(self.storage_path):
                with open(self.storage_path, "r") as f:
                    data = json.load(f)
                self.facts = {
                    fid: SemanticEntry(**d) for fid, d in data.items()
                }
        except Exception as e:
            logger.debug("Failed to load semantic memory: %s", e)
            self.facts = {}


class ProceduralMemory:
    """
    Learned strategies and procedures.

    This is where GRPO experiences, evolved personas, and optimised
    prompts are stored in a unified way. Each entry describes a
    STRATEGY that worked well in a specific context.

    Agents receive relevant procedures before execution so they
    can apply proven approaches rather than starting from scratch.

    Persistence: JSON file in dev, Redis in production.
    """

    def __init__(self, storage_path: str = None, max_procedures: int = 100):
        self.storage_path = storage_path or "/tmp/agent_procedural_memory.json"
        self.max_procedures = max_procedures
        self.procedures: list[ProceduralEntry] = []
        self._load()

    def store(self, procedure: ProceduralEntry):
        """Store a new procedure or update an existing one."""
        # Check for duplicate (same strategy in same domain)
        for i, existing in enumerate(self.procedures):
            if (existing.domain == procedure.domain
                    and existing.strategy[:50] == procedure.strategy[:50]):
                # Update existing
                existing.times_used += 1
                existing.avg_score_when_used = (
                    (existing.avg_score_when_used * (existing.times_used - 1)
                     + procedure.avg_score_when_used)
                    / existing.times_used
                )
                existing.success_rate = (
                    (existing.success_rate * (existing.times_used - 1)
                     + procedure.success_rate)
                    / existing.times_used
                )
                self._save()
                return

        self.procedures.append(procedure)
        if len(self.procedures) > self.max_procedures:
            # Evict least successful
            self.procedures.sort(
                key=lambda p: p.success_rate * p.times_used, reverse=True
            )
            self.procedures = self.procedures[:self.max_procedures]
        self._save()

    def recall(self, domain: str, context: str = "", n: int = 5) -> list[ProceduralEntry]:
        """Retrieve best procedures for a domain and context."""
        relevant = [
            p for p in self.procedures
            if domain.lower() in p.domain.lower()
        ]
        if not relevant:
            relevant = self.procedures  # Fallback to all

        relevant.sort(
            key=lambda p: p.success_rate * p.avg_score_when_used,
            reverse=True
        )
        return relevant[:n]

    def format_for_prompt(self, domain: str, n: int = 3) -> str:
        """Format procedures as injectable prompt context."""
        procedures = self.recall(domain, n=n)
        if not procedures:
            return ""

        lines = ["## Proven strategies from past experience\n"]
        for p in procedures:
            lines.append(
                f"- Strategy: {p.strategy} "
                f"(success rate: {p.success_rate:.0%}, "
                f"avg score: {p.avg_score_when_used:.1f}, "
                f"used {p.times_used}x)"
            )
            if p.context:
                lines.append(f"  When to use: {p.context}")

        lines.append("\nApply the most relevant strategy.\n")
        return "\n".join(lines)

    def _save(self):
        try:
            data = [
                {
                    "procedure_id": p.procedure_id, "domain": p.domain,
                    "strategy": p.strategy, "context": p.context,
                    "success_rate": p.success_rate, "times_used": p.times_used,
                    "avg_score_when_used": p.avg_score_when_used,
                    "source": p.source, "created_at": p.created_at,
                }
                for p in self.procedures
            ]
            _atomic_json_write(self.storage_path, data)
        except Exception as e:
            logger.debug("Failed to save procedural memory: %s", e)

    def _load(self):
        try:
            if os.path.exists(self.storage_path):
                with open(self.storage_path, "r") as f:
                    data = json.load(f)
                self.procedures = [ProceduralEntry(**d) for d in data]
        except Exception as e:
            logger.debug("Failed to load procedural memory: %s", e)
            self.procedures = []
