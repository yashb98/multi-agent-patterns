"""
Training-Free GRPO: Experiential Learning for Agents
=====================================================

Based on the Training-Free GRPO paper (arXiv:2510.08191), this module
implements experiential learning WITHOUT updating model weights.

CORE IDEA:
Instead of RL weight updates, we:
1. Generate a GROUP of outputs for the same input
2. SCORE each output using the reviewer
3. Extract REASONING PATTERNS from the best outputs
4. Store these patterns as "experiential knowledge"
5. Inject this knowledge into future prompts as few-shot context

This is "reinforcement learning" in the prompt space, not weight space.
The model's weights stay frozen. What changes is the CONTEXT it receives.

WHY THIS WORKS:
When you show an LLM "here's what worked well and why" as part of
its context, it naturally mimics those successful patterns. This is
essentially in-context learning driven by the agent's own experience.

ARCHITECTURE:
    Task arrives
         ↓
    Generate G completions (group sampling)
         ↓
    Score each with reviewer
         ↓
    Rank by score
         ↓
    Extract "what made the best ones good" (semantic advantage)
         ↓
    Store in Experience Memory
         ↓
    Next task: inject top experiences into prompt
"""

import json
import sqlite3
from typing import Callable, Optional
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from langchain_core.messages import SystemMessage, HumanMessage

from shared.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class Experience:
    """A single learned experience from a group rollout."""
    task_description: str
    successful_pattern: str  # What worked and why
    score: float
    domain: str
    timestamp: str = ""
    last_accessed: str = ""  # Updated on every retrieve() hit

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if not self.last_accessed:
            self.last_accessed = self.timestamp

    def touch(self):
        """Update last_accessed to now (for LRU tracking)."""
        self.last_accessed = datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class GRPOConfig:
    """Configuration for Training-Free GRPO."""
    group_size: int = 4           # Number of completions per group
    top_k: int = 2                # How many top completions to learn from
    max_experiences: int = 20     # Max experiences to store
    experiences_per_prompt: int = 3  # How many to inject into each prompt
    temperature_spread: list = field(
        default_factory=lambda: [0.3, 0.5, 0.7, 0.9]
    )  # Different temperatures for diversity


class ExperienceMemory:
    """
    SQLite-backed experience memory with LRU eviction.

    Persists learned experiences across process restarts.
    Eviction strategy: quality * 0.6 + recency * 0.4.

    Args:
        max_size: Maximum experiences to store.
        db_path: SQLite database path. Use ":memory:" for tests.
    """

    def __init__(self, max_size: int = 20, db_path: str = "data/experience_memory.db"):
        self.max_size = max_size
        self.db_path = db_path

        # Ensure directory exists for file-backed DBs
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._init_schema()
        self._load_cache()

    def _init_schema(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS experiences (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_description TEXT NOT NULL,
                successful_pattern TEXT NOT NULL,
                score REAL NOT NULL,
                domain TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                last_accessed TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_exp_domain ON experiences(domain);
            CREATE INDEX IF NOT EXISTS idx_exp_score ON experiences(score DESC);
        """)
        self.conn.commit()

    def _load_cache(self):
        """Load experiences from SQLite into in-memory cache."""
        rows = self.conn.execute(
            "SELECT * FROM experiences ORDER BY score DESC"
        ).fetchall()
        self.experiences = [
            Experience(
                task_description=r["task_description"],
                successful_pattern=r["successful_pattern"],
                score=r["score"],
                domain=r["domain"],
                timestamp=r["timestamp"],
                last_accessed=r["last_accessed"],
            )
            for r in rows
        ]
        if self.experiences:
            logger.info("Loaded %d experiences from %s", len(self.experiences), self.db_path)

    def _eviction_score(self, exp: Experience) -> float:
        """Compute combined score for eviction ranking.

        Higher = more worth keeping. Combines quality (60%) with recency (40%).
        """
        quality = exp.score / 10.0

        try:
            last = datetime.strptime(exp.last_accessed, "%Y-%m-%d %H:%M:%S")
            hours_since = (datetime.now() - last).total_seconds() / 3600.0
        except (ValueError, TypeError):
            hours_since = 999.0

        recency = max(0.0, 1.0 - hours_since / 168.0)
        return quality * 0.6 + recency * 0.4

    def add(self, experience: Experience):
        """Add an experience, evicting the least valuable if at capacity."""
        self.experiences.append(experience)

        # Persist to SQLite
        self.conn.execute(
            "INSERT INTO experiences (task_description, successful_pattern, score, domain, timestamp, last_accessed) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (experience.task_description, experience.successful_pattern,
             experience.score, experience.domain,
             experience.timestamp, experience.last_accessed),
        )

        if len(self.experiences) > self.max_size:
            self.experiences.sort(key=self._eviction_score, reverse=True)
            evicted = self.experiences[self.max_size:]
            self.experiences = self.experiences[:self.max_size]

            # Remove evicted from SQLite
            for exp in evicted:
                self.conn.execute(
                    "DELETE FROM experiences WHERE task_description=? AND timestamp=?",
                    (exp.task_description, exp.timestamp),
                )

        self.conn.commit()
        logger.debug("Stored experience (domain=%s, score=%.1f)", experience.domain, experience.score)

    def retrieve(self, domain: str, n: int = 3) -> list[Experience]:
        """Retrieve top-N experiences for a domain. Updates last_accessed (LRU)."""
        relevant = [e for e in self.experiences if e.domain == domain]
        if not relevant:
            relevant = list(self.experiences)

        relevant.sort(key=lambda e: e.score, reverse=True)
        results = relevant[:n]

        # Touch retrieved experiences
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for exp in results:
            exp.touch()
            self.conn.execute(
                "UPDATE experiences SET last_accessed=? WHERE task_description=? AND timestamp=?",
                (now, exp.task_description, exp.timestamp),
            )
        self.conn.commit()

        return results

    def format_for_prompt(self, domain: str, n: int = 3) -> str:
        """Format experiences as injectable prompt context."""
        experiences = self.retrieve(domain, n)

        if not experiences:
            return ""

        lines = ["## Learned Patterns From Previous Successes\n"]
        for i, exp in enumerate(experiences, 1):
            lines.append(f"### Pattern {i} (score: {exp.score:.1f}/10)")
            lines.append(f"Task: {exp.task_description}")
            lines.append(f"What worked: {exp.successful_pattern}")
            lines.append("")

        lines.append("Apply these successful patterns to the current task.\n")
        return "\n".join(lines)

    def __len__(self):
        return len(self.experiences)

    def close(self):
        """Close the SQLite connection."""
        self.conn.close()


class TrainingFreeGRPO:
    """
    Training-Free GRPO implementation for multi-agent systems.
    
    For each agent execution, this system:
    1. Generates multiple candidate outputs (group sampling)
    2. Scores them using the evaluator
    3. Extracts semantic advantages (what made the best ones good)
    4. Stores experiences for future use
    5. Injects relevant experiences into future prompts
    
    USAGE:
        grpo = TrainingFreeGRPO(llm, config)
        
        # Learn from a group of outputs
        best_output, experience = grpo.group_sample_and_learn(
            system_prompt="You are a researcher...",
            user_message="Research quantum computing",
            evaluator_fn=score_research,
            domain="quantum_computing"
        )
        
        # Enhance future prompts with learned experiences
        enhanced_prompt = grpo.enhance_prompt(
            base_prompt="You are a researcher...",
            domain="quantum_computing"
        )
    """
    
    def __init__(self, llm, config: Optional[GRPOConfig] = None):
        self.llm = llm
        self.config = config or GRPOConfig()
        self.memory = ExperienceMemory(max_size=self.config.max_experiences)
    
    def group_sample_and_learn(
        self,
        system_prompt: str,
        user_message: str,
        evaluator_fn: Callable,
        domain: str,
    ) -> tuple:
        """
        Core GRPO loop: generate group, score, learn, return best.
        
        Parameters:
            system_prompt: The agent's system prompt
            user_message: The task/query for the agent
            evaluator_fn: Function(output_text) -> float (score 0-10)
            domain: Domain identifier for experience retrieval
        
        Returns:
            (best_output: str, experience: Experience)
        
        HOW IT WORKS:
        1. We generate G completions at different temperatures
           (diversity is crucial — same temp gives similar outputs)
        2. Each completion is scored by the evaluator
        3. We extract WHY the best ones scored higher (semantic advantage)
        4. This "why" becomes an Experience stored in memory
        5. Future prompts are enhanced with these experiences
        """
        logger.info("GRPO: Generating %d candidates...", self.config.group_size)

        # Inject existing experiences into the prompt
        experience_context = self.memory.format_for_prompt(
            domain, self.config.experiences_per_prompt
        )

        enhanced_system = system_prompt
        if experience_context:
            enhanced_system = f"{system_prompt}\n\n{experience_context}"

        # STEP 1: Group sampling — generate multiple completions IN PARALLEL
        from shared.parallel_executor import parallel_grpo_candidates

        model_name = self.llm.model_name if hasattr(self.llm, 'model_name') else "gpt-4.1-mini"
        temps = [
            self.config.temperature_spread[i % len(self.config.temperature_spread)]
            for i in range(self.config.group_size)
        ]

        def make_variant(temp):
            from langchain_openai import ChatOpenAI
            return ChatOpenAI(model=model_name, temperature=temp, request_timeout=30.0)

        candidates = parallel_grpo_candidates(make_variant, enhanced_system, user_message, temps)
        
        # STEP 2: Score each candidate
        scored = []
        for i, candidate in enumerate(candidates):
            score = evaluator_fn(candidate)
            scored.append((score, candidate, i))
            logger.info("Candidate %d: score=%.1f, length=%d words",
                       i + 1, score, len(candidate.split()))
        
        # STEP 3: Rank and select
        scored.sort(key=lambda x: x[0], reverse=True)
        best_score, best_output, best_idx = scored[0]
        worst_score, worst_output, _ = scored[-1]
        
        logger.info("Best: candidate %d (%.1f), Worst: %.1f",
                    best_idx + 1, best_score, worst_score)
        
        # STEP 4: Extract semantic advantage
        # This is the key innovation — we ask the LLM WHY the best
        # output was better, extracting transferable patterns
        if best_score > worst_score + 0.5:  # Meaningful difference
            experience = self._extract_semantic_advantage(
                best_output, worst_output, best_score, domain, user_message
            )
            self.memory.add(experience)
            logger.info("Learned new pattern: %s...", experience.successful_pattern[:80])
        else:
            experience = None
            logger.info("Scores too close — no clear pattern to extract")
        
        return best_output, experience
    
    def _extract_semantic_advantage(
        self,
        best_output: str,
        worst_output: str,
        best_score: float,
        domain: str,
        task: str,
    ) -> Experience:
        """
        Extract WHY the best output was better than the worst.
        
        This is the "group relative semantic advantage" from the paper.
        Instead of a numerical advantage (score difference), we extract
        a textual explanation of what made the best output succeed.
        
        This textual pattern is MORE INFORMATIVE than a number because
        it tells future runs HOW to be better, not just THAT one was better.
        """
        extraction_prompt = f"""Compare these two outputs for the same task and explain 
what SPECIFIC techniques, patterns, or approaches made Output A score higher.

TASK: {task[:300]}

OUTPUT A (score: {best_score:.1f}/10):
{best_output[:1000]}

OUTPUT B (lower scoring):
{worst_output[:1000]}

Extract 2-3 SPECIFIC, TRANSFERABLE patterns from Output A that could be 
applied to future tasks. Focus on methodology, not content.

Format as a concise paragraph (3-4 sentences max). Focus on actionable patterns."""
        
        response = self.llm.invoke([
            SystemMessage(content="Extract winning patterns from successful outputs."),
            HumanMessage(content=extraction_prompt)
        ])
        
        pattern = response.content.strip()
        
        return Experience(
            task_description=task[:200],
            successful_pattern=pattern,
            score=best_score,
            domain=domain,
        )
    
    def enhance_prompt(self, base_prompt: str, domain: str) -> str:
        """
        Enhance a base prompt with learned experiences.
        
        Call this before each agent execution to inject experiential
        knowledge into the agent's system prompt.
        """
        experience_context = self.memory.format_for_prompt(
            domain, self.config.experiences_per_prompt
        )
        
        if not experience_context:
            return base_prompt
        
        return f"{base_prompt}\n\n{experience_context}"
    
    def get_learning_report(self) -> str:
        """Generate a report of what the system has learned."""
        lines = [
            "Training-Free GRPO Learning Report",
            "=" * 40,
            f"Experiences stored: {len(self.memory)}",
            ""
        ]
        
        for i, exp in enumerate(self.memory.experiences, 1):
            lines.append(f"Experience {i} (score: {exp.score:.1f}):")
            lines.append(f"  Domain: {exp.domain}")
            lines.append(f"  Pattern: {exp.successful_pattern[:100]}...")
            lines.append("")
        
        return "\n".join(lines)
