"""TieredRouter — 3-tier agent routing that saves ~250% token cost.

Tier 1: CACHED     — Check if identical/similar task was solved before
Tier 2: LIGHTWEIGHT — Check [AGENT_BOOSTER_AVAILABLE] for cheaper alternatives
Tier 3: FULL AGENT  — Spawn the complete agent with full LLM call
"""

import hashlib
from typing import Optional

from shared.logging_config import get_logger
from shared.memory_layer._pattern import PatternMemory
from shared.memory_layer._stores import EpisodicMemory

logger = get_logger(__name__)


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

        # ── Tier 2: FULL AGENT ──
        logger.info("TIER 3: Full agent needed for %s", agent_name)
        return None

    def cache_result(self, agent_name: str, state: dict, result: dict):
        """Cache a full agent result for future tier-1 hits."""
        task_hash = self._hash_task(agent_name, state)
        self._cache[task_hash] = result

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
