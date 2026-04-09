"""Data classes for all memory entry types.

Each entry type represents a different tier of the five-tier memory architecture:
- ShortTermEntry — single step in current session
- EpisodicEntry — complete record of a past run
- SemanticEntry — accumulated domain knowledge
- ProceduralEntry — learned strategy/procedure
- PatternEntry — reusable execution pattern from a successful run
"""

import hashlib
from typing import Optional
from dataclasses import dataclass
from datetime import datetime

from shared.logging_config import get_logger

logger = get_logger(__name__)


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
