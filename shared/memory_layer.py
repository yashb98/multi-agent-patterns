"""
Memory Layer: The Agent's Brain
=================================

This module implements a five-tier memory architecture that gives
agents persistent, structured memory across runs, tasks, and domains.

THE FIVE MEMORY TYPES:
======================

1. WORKING MEMORY (already exists as AgentState)
   - Lives within a single graph execution
   - Cleared when the run ends
   - Analogy: Your scratch paper while solving a problem

2. SHORT-TERM MEMORY (conversation buffer)
   - Recent interactions within a session (last N messages/steps)
   - Sliding window that drops oldest entries
   - Analogy: What you remember from the last 5 minutes of a meeting

3. EPISODIC MEMORY (what happened before)
   - Complete records of past runs: topic, score, iterations, what worked/failed
   - Searchable by topic similarity, recency, and outcome
   - Analogy: Your diary of past projects

4. SEMANTIC MEMORY (accumulated knowledge)
   - Domain facts and patterns learned from many runs
   - Not tied to any specific episode — generalised knowledge
   - Analogy: Your textbook of things you've learned over time

5. PROCEDURAL MEMORY (how to do things)
   - Optimised prompts, successful strategies, effective tool sequences
   - This is where GRPO experiences and evolved personas live
   - Analogy: Your muscle memory for tasks you've done many times

HOW AGENTS USE MEMORY:
======================

Before each agent runs, the MemoryManager builds a CONTEXT PACKAGE:
- Relevant episodic memories ("last time we wrote about this topic...")
- Applicable semantic facts ("in this domain, always cite primary sources...")
- Procedural patterns ("the best approach for technical articles is...")
- Recent short-term context ("the reviewer just flagged section 3...")

This context is injected into the agent's prompt alongside the task.
The agent doesn't "query" memory — memory is PUSHED to the agent
based on relevance to the current task.

PERSISTENCE:
============
- Working memory: In-process (AgentState dict)
- Short-term: In-process (sliding window list)
- Episodic: JSON file (dev) / PostgreSQL (prod)
- Semantic: JSON file (dev) / Qdrant vector store (prod)
- Procedural: JSON file (dev) / Redis (prod)
"""

import json
import os
import hashlib
from typing import Optional, Any
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from collections import deque

from shared.logging_config import get_logger

logger = get_logger(__name__)


# ─── MEMORY ENTRIES ──────────────────────────────────────────────

@dataclass
class EpisodicEntry:
    """A record of a complete past run."""
    run_id: str
    topic: str
    timestamp: str
    final_score: float
    iterations: int
    pattern_used: str  # "hierarchical", "debate", "swarm", "enhanced"
    agents_used: list[str]
    strengths: list[str]     # What worked well
    weaknesses: list[str]    # What needed improvement
    output_summary: str      # First 500 chars of final output
    duration_seconds: float
    total_llm_calls: int
    domain: str

    def relevance_score(self, query_topic: str, query_domain: str) -> float:
        """Score how relevant this episode is to a new task."""
        score = 0.0
        # Topic word overlap
        query_words = set(query_topic.lower().split())
        topic_words = set(self.topic.lower().split())
        overlap = len(query_words & topic_words)
        score += overlap * 2.0
        # Domain match
        if query_domain and query_domain.lower() in self.domain.lower():
            score += 5.0
        # Recency bonus (episodes from last 7 days score higher)
        try:
            age = (datetime.now() - datetime.fromisoformat(self.timestamp)).days
            score += max(0, 3.0 - age * 0.3)
        except (ValueError, TypeError) as e:
            logger.debug("Failed to parse episode timestamp: %s", e)
        # Quality bonus (high-scoring episodes are more useful)
        score += self.final_score * 0.5
        return score


@dataclass
class SemanticEntry:
    """A piece of accumulated domain knowledge."""
    fact_id: str
    domain: str
    fact: str               # The knowledge itself
    confidence: float       # 0-1, how confident we are in this fact
    source_runs: list[str]  # Which runs contributed to this knowledge
    times_validated: int    # How many times this was confirmed
    times_contradicted: int # How many times this was challenged
    created_at: str
    last_used: str

    @property
    def reliability(self) -> float:
        """How reliable is this fact based on validation history."""
        total = self.times_validated + self.times_contradicted
        if total == 0:
            return self.confidence
        return self.times_validated / total


@dataclass
class ProceduralEntry:
    """A learned procedure or strategy."""
    procedure_id: str
    domain: str
    strategy: str           # Description of the approach
    context: str            # When to use this strategy
    success_rate: float     # Historical success rate
    times_used: int
    avg_score_when_used: float
    source: str             # "grpo", "persona_evolution", "prompt_optimizer"
    created_at: str


@dataclass
class ShortTermEntry:
    """A single step in the current session."""
    agent: str
    action: str
    summary: str
    score: Optional[float]
    timestamp: str


# ─── MEMORY STORES ───────────────────────────────────────────────

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
            os.makedirs(os.path.dirname(self.storage_path) or ".", exist_ok=True)
            with open(self.storage_path, "w") as f:
                json.dump(data, f, indent=2)
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
            os.makedirs(os.path.dirname(self.storage_path) or ".", exist_ok=True)
            with open(self.storage_path, "w") as f:
                json.dump(data, f, indent=2)
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
            os.makedirs(os.path.dirname(self.storage_path) or ".", exist_ok=True)
            with open(self.storage_path, "w") as f:
                json.dump(data, f, indent=2)
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


# ─── PATTERN MEMORY (Operational Principle #1 + #4) ─────────────
# "Memory before action" — search patterns before starting any task.
# "Learn after success" — store winning patterns after score >= 7.0.

@dataclass
class PatternEntry:
    """A reusable pattern learned from a successful run."""
    pattern_id: str
    topic: str
    domain: str
    agents_used: list[str]
    routing_decisions: list[str]  # sequence of supervisor decisions
    final_score: float
    iterations: int
    strengths: list[str]
    output_summary: str
    timestamp: str

    def relevance_score(self, query_topic: str, query_domain: str) -> float:
        """Score how relevant this pattern is to a new task. 0.0-1.0 normalized."""
        score = 0.0
        query_words = set(query_topic.lower().split())
        topic_words = set(self.topic.lower().split())
        overlap = len(query_words & topic_words)
        max_possible = max(len(query_words), 1)
        score += (overlap / max_possible) * 0.5
        if query_domain and query_domain.lower() in self.domain.lower():
            score += 0.3
        # Quality bonus
        score += min(self.final_score / 10.0, 1.0) * 0.2
        return min(score, 1.0)


class PatternMemory:
    """
    Stores and retrieves successful execution patterns.

    Uses hybrid search (FTS5 + vector similarity + RRF) for pattern retrieval
    alongside the existing word-overlap scoring. The hybrid search catches
    both exact keyword matches AND semantic similarity.

    OPERATIONAL PRINCIPLE #1: Memory before action.
    Before any task, call search(). If score > 0.7, reuse the pattern.

    OPERATIONAL PRINCIPLE #4: Learn after success.
    After any run with score >= 7.0, call store() to save the pattern.
    """

    def __init__(self, storage_path: str = None):
        self.storage_path = storage_path or "/tmp/agent_memory/patterns.json"
        self.patterns: list[PatternEntry] = []
        self._hybrid_search = None  # Lazy init
        self._load()
        self._rebuild_search_index()

    def _get_hybrid_search(self):
        """Lazy-init hybrid search index."""
        if self._hybrid_search is None:
            try:
                from shared.hybrid_search import HybridSearch
                self._hybrid_search = HybridSearch(":memory:")
            except ImportError:
                logger.debug("hybrid_search not available, using word overlap only")
        return self._hybrid_search

    def _rebuild_search_index(self):
        """Rebuild the FTS5 + vector index from current patterns."""
        hs = self._get_hybrid_search()
        if not hs:
            return
        for p in self.patterns:
            search_text = f"{p.topic} {p.domain} {' '.join(p.strengths)} {' '.join(p.agents_used)}"
            hs.add(p.pattern_id, search_text, {"topic": p.topic, "score": p.final_score})

    def search(self, topic: str, domain: str = "") -> tuple[Optional[PatternEntry], float]:
        """
        Search for a reusable pattern using hybrid search (FTS5 + vector + word overlap).

        Returns (best_pattern, score).
        If score > 0.7, the caller MUST reuse this pattern.
        If score <= 0.7, returns (None, score).
        """
        if not self.patterns:
            logger.info("No patterns stored yet — building from scratch")
            return None, 0.0

        # Primary: word-overlap scoring (existing approach)
        scored = [
            (p, p.relevance_score(topic, domain))
            for p in self.patterns
        ]
        scored.sort(key=lambda x: x[1], reverse=True)

        # Secondary: hybrid search boost (FTS5 + vector similarity via RRF)
        hs = self._get_hybrid_search()
        if hs and hs.count() > 0:
            query_text = f"{topic} {domain}".strip()
            hybrid_results = hs.query(query_text, top_k=5)
            hybrid_ids = {r["id"]: r["score"] for r in hybrid_results}

            # Boost word-overlap scores with hybrid search signal
            boosted = []
            for pattern, word_score in scored:
                hybrid_boost = hybrid_ids.get(pattern.pattern_id, 0.0)
                # Blend: 70% word overlap + 30% hybrid search
                combined = word_score * 0.7 + hybrid_boost * 100.0 * 0.3
                boosted.append((pattern, combined))
            boosted.sort(key=lambda x: x[1], reverse=True)
            scored = boosted

        best_pattern, best_score = scored[0]

        if best_score > 0.7:
            logger.info("REUSE pattern from '%s' (score: %.2f, original score: %s/10)",
                        best_pattern.topic, best_score, best_pattern.final_score)
            return best_pattern, best_score
        elif best_score > 0.4:
            logger.info("PARTIAL match from '%s' (score: %.2f) — use as starting point",
                        best_pattern.topic, best_score)
            return best_pattern, best_score
        else:
            logger.info("No good match (best: %.2f) — building from scratch", best_score)
            return None, best_score

    def store(self, topic: str, domain: str, agents_used: list[str],
              routing_decisions: list[str], final_score: float,
              iterations: int, strengths: list[str], output_summary: str):
        """
        Store a successful pattern. Only call when final_score >= 7.0.
        """
        if final_score < 7.0:
            logger.info("Score %s < 7.0 — not storing", final_score)
            return

        pattern = PatternEntry(
            pattern_id=hashlib.md5(
                f"{topic}{datetime.now().isoformat()}".encode()
            ).hexdigest()[:10],
            topic=topic,
            domain=domain,
            agents_used=agents_used,
            routing_decisions=routing_decisions,
            final_score=final_score,
            iterations=iterations,
            strengths=strengths,
            output_summary=output_summary[:500],
            timestamp=datetime.now().isoformat(),
        )
        self.patterns.append(pattern)
        # Keep top 50 by score
        if len(self.patterns) > 50:
            self.patterns.sort(key=lambda p: p.final_score, reverse=True)
            self.patterns = self.patterns[:50]
        self._save()

        # Index in hybrid search
        hs = self._get_hybrid_search()
        if hs:
            search_text = f"{topic} {domain} {' '.join(strengths)} {' '.join(agents_used)}"
            hs.add(pattern.pattern_id, search_text, {"topic": topic, "score": final_score})

        logger.info("Stored pattern: '%s' (score: %s/10)", topic, final_score)

    def _save(self):
        try:
            data = [
                {
                    "pattern_id": p.pattern_id, "topic": p.topic,
                    "domain": p.domain, "agents_used": p.agents_used,
                    "routing_decisions": p.routing_decisions,
                    "final_score": p.final_score, "iterations": p.iterations,
                    "strengths": p.strengths, "output_summary": p.output_summary,
                    "timestamp": p.timestamp,
                }
                for p in self.patterns
            ]
            os.makedirs(os.path.dirname(self.storage_path) or ".", exist_ok=True)
            with open(self.storage_path, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.warning("PatternMemory save failed: %s", e)

    def _load(self):
        try:
            if os.path.exists(self.storage_path):
                with open(self.storage_path, "r") as f:
                    data = json.load(f)
                self.patterns = [PatternEntry(**d) for d in data]
        except Exception as e:
            logger.debug("Failed to load pattern memory: %s", e)
            self.patterns = []


# ─── 3-TIER ROUTING (Operational Principle #5) ─────────────────
# "Check for [AGENT_BOOSTER_AVAILABLE] before spawning expensive agents."
# Route: cached → lightweight → full agent.

class TieredRouter:
    """
    3-tier agent routing that saves ~250% token cost.

    Tier 1: CACHED     — Check if identical/similar task was solved before
    Tier 2: LIGHTWEIGHT — Check [AGENT_BOOSTER_AVAILABLE] for cheaper alternatives
    Tier 3: FULL AGENT  — Spawn the complete agent with full LLM call

    Usage:
        router = TieredRouter(pattern_memory, episodic_memory)
        result = router.route(agent_name, state, agent_fn)
        # result is either a cached dict, a lightweight dict, or None (= run full agent)
    """

    AGENT_BOOSTER_AVAILABLE = False  # Set True when lightweight model is configured

    def __init__(self, pattern_memory: PatternMemory, episodic_memory: 'EpisodicMemory'):
        self.pattern_memory = pattern_memory
        self.episodic = episodic_memory
        self._cache: dict[str, dict] = {}  # task_hash → partial state result

    def route(self, agent_name: str, state: dict) -> Optional[dict]:
        """
        Attempt to resolve an agent task without a full LLM call.
        Returns partial state dict if resolved, None if full agent needed.
        """
        topic = state.get("topic", "")
        task_hash = self._hash_task(agent_name, state)

        # ── Tier 1: CACHED ──
        if task_hash in self._cache:
            logger.info("TIER 1 HIT: Returning cached result for %s", agent_name)
            return self._cache[task_hash]

        # ── Tier 2: LIGHTWEIGHT / BOOSTER ──
        if self.AGENT_BOOSTER_AVAILABLE:
            lightweight_result = self._try_lightweight(agent_name, state)
            if lightweight_result is not None:
                logger.info("TIER 2 HIT: Lightweight resolve for %s", agent_name)
                self._cache[task_hash] = lightweight_result
                return lightweight_result

        # ── Tier 3: FULL AGENT ──
        logger.info("TIER 3: Full agent needed for %s", agent_name)
        return None

    def cache_result(self, agent_name: str, state: dict, result: dict):
        """Cache a full agent result for future tier-1 hits."""
        task_hash = self._hash_task(agent_name, state)
        self._cache[task_hash] = result

    @classmethod
    def enable_booster(cls):
        """Enable tier-2 lightweight routing."""
        cls.AGENT_BOOSTER_AVAILABLE = True
        logger.info("[AGENT_BOOSTER_AVAILABLE] = True")

    @classmethod
    def disable_booster(cls):
        """Disable tier-2 lightweight routing."""
        cls.AGENT_BOOSTER_AVAILABLE = False

    def _try_lightweight(self, agent_name: str, state: dict) -> Optional[dict]:
        """
        Attempt lightweight resolution using episodic memory.
        For reviewers: reuse past review structure if same domain.
        For researchers: reuse past research if topic overlap > 80%.
        """
        topic = state.get("topic", "")
        episodes = self.episodic.recall(topic, n=1)
        if not episodes:
            return None

        best_ep = episodes[0]
        relevance = best_ep.relevance_score(topic, "")

        # Only use lightweight if very high relevance
        if relevance < 8.0:
            return None

        # For researcher: reuse research notes structure (agent still enriches)
        if agent_name == "researcher" and best_ep.strengths:
            return None  # Research always needs fresh data

        return None  # Conservative: only cache hits for now

    def _hash_task(self, agent_name: str, state: dict) -> str:
        """Create a cache key from agent name + relevant state fields."""
        key_parts = [
            agent_name,
            state.get("topic", ""),
            str(state.get("iteration", 0)),
            str(len(state.get("research_notes", []))),
            str(bool(state.get("review_feedback", ""))),
        ]
        key = "|".join(key_parts)
        return hashlib.md5(key.encode()).hexdigest()[:16]


# ─── UNIFIED MEMORY MANAGER ─────────────────────────────────────

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
        enhanced_prompt = f"{base_prompt}\n\n{context}"
        
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

    # ── Operational Principle #5: 3-tier routing ──

    def route_agent(self, agent_name: str, state: dict) -> Optional[dict]:
        """
        Try to resolve an agent task without a full LLM call.
        Returns partial state dict if resolved, None if full agent needed.
        Check [AGENT_BOOSTER_AVAILABLE] before spawning expensive agents.
        """
        return self.router.route(agent_name, state)

    def cache_agent_result(self, agent_name: str, state: dict, result: dict):
        """Cache a full agent result for future 3-tier routing hits."""
        self.router.cache_result(agent_name, state, result)

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
