"""OptimizationPolicy — decides actions based on aggregated insights."""

import sqlite3
import time
from dataclasses import dataclass
from typing import Optional

from shared.logging_config import get_logger
from shared.optimization._aggregator import AggregatedInsight

logger = get_logger(__name__)

_CONFIDENCE_THRESHOLD_FOR_LLM = 0.6


@dataclass
class OptimizationBudget:
    max_rollbacks_per_hour: int = 3
    max_rule_generations_per_hour: int = 10
    max_llm_policy_calls_per_hour: int = 5
    cooldown_after_rollback_minutes: int = 30


@dataclass
class PolicyAction:
    action_type: str
    target: str
    domain: str
    evidence: str
    confidence: float


class OptimizationPolicy:
    """Rule-based policy with CognitiveEngine fallback for novel decisions."""

    def __init__(self, memory_manager=None, cognitive_engine=None,
                 budget: Optional[OptimizationBudget] = None,
                 db_path: Optional[str] = None):
        self._memory = memory_manager
        self._cognitive = cognitive_engine
        self._budget = budget or OptimizationBudget()
        self._db_path = db_path
        self._rollback_count = 0
        self._rule_gen_count = 0
        self._llm_call_count = 0
        self._window_start = time.monotonic()
        self._last_rollback_time = 0.0
        if db_path:
            self._init_budget_table()
            self._load_budget_state()

    def _init_budget_table(self):
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS budget_state (
                    key TEXT PRIMARY KEY,
                    value REAL NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)

    def _load_budget_state(self):
        try:
            with sqlite3.connect(self._db_path) as conn:
                rows = conn.execute("SELECT key, value FROM budget_state").fetchall()
            state = {r[0]: r[1] for r in rows}
            self._rollback_count = int(state.get("rollback_count", 0))
            self._rule_gen_count = int(state.get("rule_gen_count", 0))
            self._llm_call_count = int(state.get("llm_call_count", 0))
            self._last_rollback_time = state.get("last_rollback_time", 0.0)
            saved_window = state.get("window_start", 0.0)
            if saved_window > 0:
                elapsed = time.monotonic() - (time.time() - saved_window)
                if elapsed < 3600:
                    self._window_start = time.monotonic() - (time.time() - saved_window)
        except Exception:
            pass

    def _save_budget_state(self):
        if not self._db_path:
            return
        ts = time.time()
        items = [
            ("rollback_count", self._rollback_count),
            ("rule_gen_count", self._rule_gen_count),
            ("llm_call_count", self._llm_call_count),
            ("last_rollback_time", self._last_rollback_time),
            ("window_start", ts - (time.monotonic() - self._window_start)),
        ]
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self._db_path) as conn:
            for key, val in items:
                conn.execute(
                    "INSERT OR REPLACE INTO budget_state (key, value, updated_at) VALUES (?, ?, ?)",
                    (key, val, now),
                )

    def _maybe_reset_window(self):
        if time.monotonic() - self._window_start >= 3600:
            self._window_start = time.monotonic()
            self._rollback_count = 0
            self._rule_gen_count = 0
            self._llm_call_count = 0
            self._save_budget_state()

    def _in_cooldown(self) -> bool:
        if self._last_rollback_time == 0:
            return False
        elapsed = time.monotonic() - self._last_rollback_time
        return elapsed < self._budget.cooldown_after_rollback_minutes * 60

    def decide(self, insight: AggregatedInsight) -> list[PolicyAction]:
        self._maybe_reset_window()
        actions: list[PolicyAction] = []

        if insight.pattern_type == "systemic_failure":
            actions.extend(self._handle_systemic(insight))
        elif insight.pattern_type == "regression":
            actions.extend(self._handle_regression(insight))
        elif insight.pattern_type == "persona_drift":
            actions.extend(self._handle_drift(insight))
        elif insight.pattern_type == "platform_change":
            actions.append(PolicyAction(
                action_type="alert_human",
                target="telegram",
                domain=insight.domain,
                evidence=insight.evidence,
                confidence=insight.confidence,
            ))
        elif insight.pattern_type == "redundant":
            actions.append(PolicyAction(
                action_type="merge_actions",
                target="coordinator",
                domain=insight.domain,
                evidence=insight.evidence,
                confidence=insight.confidence,
            ))
        elif insight.pattern_type == "success_streak":
            actions.extend(self._handle_success_streak(insight))
        elif insight.pattern_type == "adaptation_worked":
            actions.extend(self._handle_adaptation_worked(insight))

        if not actions and insight.confidence < _CONFIDENCE_THRESHOLD_FOR_LLM:
            actions.append(PolicyAction(
                action_type="alert_human",
                target="telegram",
                domain=insight.domain,
                evidence=f"Low confidence ({insight.confidence:.2f}): {insight.evidence}",
                confidence=insight.confidence,
            ))

        return actions

    async def decide_async(self, insight: AggregatedInsight) -> list[PolicyAction]:
        self._maybe_reset_window()
        actions = self.decide(insight)
        if insight.confidence < _CONFIDENCE_THRESHOLD_FOR_LLM and self._cognitive:
            if self._llm_call_count < self._budget.max_llm_policy_calls_per_hour:
                self._llm_call_count += 1
                self._save_budget_state()
                context_parts = [
                    f"Pattern: {insight.pattern_type}",
                    f"Domain: {insight.domain}",
                    f"Evidence: {insight.evidence}",
                    f"Confidence: {insight.confidence:.2f}",
                    f"Recommended: {insight.recommended_action}",
                ]
                if actions:
                    context_parts.append(
                        f"Rule-based actions so far: "
                        f"{', '.join(a.action_type for a in actions)}"
                    )
                task_str = (
                    "Decide the best optimization action.\n"
                    + "\n".join(context_parts)
                )
                result = await self._cognitive.think(
                    task=task_str,
                    domain="optimization",
                    stakes="medium",
                )
                # CognitiveEngine.think() returns score=None whenever no
                # scorer is supplied and the chosen level is L1 / L2 / L3
                # with the LLM scoring fallback unavailable. Coalesce to
                # 0.0 so the PolicyAction is still emitted with a usable
                # confidence floor instead of crashing. S6 audit M-B.
                confidence = (result.score or 0.0) / 10.0
                actions.append(PolicyAction(
                    action_type="cognitive_decision",
                    target=result.answer,
                    domain=insight.domain,
                    evidence=f"CognitiveEngine: {result.answer[:200]}",
                    confidence=confidence,
                ))
        return actions

    def _handle_systemic(self, insight: AggregatedInsight) -> list[PolicyAction]:
        actions = []
        if self._rule_gen_count < self._budget.max_rule_generations_per_hour:
            self._rule_gen_count += 1
            self._save_budget_state()
            actions.append(PolicyAction(
                action_type="generate_insight",
                target="semantic_memory",
                domain=insight.domain,
                evidence=insight.evidence,
                confidence=insight.confidence,
            ))
        if insight.confidence >= 0.85:
            actions.append(PolicyAction(
                action_type="escalate_cognitive",
                target="escalation_classifier",
                domain=insight.domain,
                evidence=insight.evidence,
                confidence=insight.confidence,
            ))
        return actions

    def _handle_regression(self, insight: AggregatedInsight) -> list[PolicyAction]:
        actions = []
        if (self._rollback_count >= self._budget.max_rollbacks_per_hour
                or self._in_cooldown()):
            actions.append(PolicyAction(
                action_type="alert_human",
                target="telegram",
                domain=insight.domain,
                evidence=f"Budget/cooldown: {insight.evidence}",
                confidence=insight.confidence,
            ))
            return actions

        self._rollback_count += 1
        self._last_rollback_time = time.monotonic()
        self._save_budget_state()
        actions.append(PolicyAction(
            action_type="rollback",
            target=insight.recommended_action,
            domain=insight.domain,
            evidence=insight.evidence,
            confidence=insight.confidence,
        ))
        actions.append(PolicyAction(
            action_type="demote_memory",
            target="memory_manager",
            domain=insight.domain,
            evidence=insight.evidence,
            confidence=insight.confidence,
        ))
        actions.append(PolicyAction(
            action_type="escalate_cognitive",
            target="escalation_classifier",
            domain=insight.domain,
            evidence=insight.evidence,
            confidence=insight.confidence,
        ))
        return actions

    def _handle_drift(self, insight: AggregatedInsight) -> list[PolicyAction]:
        return [
            PolicyAction(
                action_type="rollback_persona",
                target="persona_evolution",
                domain=insight.domain,
                evidence=insight.evidence,
                confidence=insight.confidence,
            ),
            PolicyAction(
                action_type="pause_loop",
                target="persona_evolution",
                domain=insight.domain,
                evidence=insight.evidence,
                confidence=insight.confidence,
            ),
        ]

    def _handle_success_streak(self, insight: AggregatedInsight) -> list[PolicyAction]:
        """Reward domains that are working well — positive reinforcement."""
        actions = []
        # Pin the strategy in memory so it survives forgetting sweeps
        actions.append(PolicyAction(
            action_type="promote_strategy",
            target="memory_manager",
            domain=insight.domain,
            evidence=insight.evidence,
            confidence=insight.confidence,
        ))
        # If confidence is high, also generate an insight so other agents can learn
        if insight.confidence >= 0.8:
            actions.append(PolicyAction(
                action_type="generate_insight",
                target="semantic_memory",
                domain=insight.domain,
                evidence=f"[POSITIVE] {insight.evidence}",
                confidence=insight.confidence,
            ))
        return actions

    def _handle_adaptation_worked(self, insight: AggregatedInsight) -> list[PolicyAction]:
        """Reinforce an adaptation that correlated with success."""
        actions = []
        actions.append(PolicyAction(
            action_type="reinforce_adaptation",
            target="agent_rules",
            domain=insight.domain,
            evidence=insight.evidence,
            confidence=insight.confidence,
        ))
        # Freeze this adaptation from future rollbacks
        actions.append(PolicyAction(
            action_type="freeze_baseline",
            target="memory_manager",
            domain=insight.domain,
            evidence=insight.evidence,
            confidence=insight.confidence,
        ))
        return actions

    def promote_memory(self, memory_id: str):
        if self._memory:
            self._memory.promote(memory_id)

    def demote_memory(self, memory_id: str, check_pinned: bool = False):
        if check_pinned and self._memory and memory_id in getattr(self._memory, "_pinned", []):
            logger.info("Skipping demote — memory %s is PINNED", memory_id)
            return
        if self._memory:
            self._memory.demote(memory_id)

    def resolve_contradiction(self, new_id: str, old_id: str,
                              new_stronger: bool = True):
        if self._memory and new_stronger:
            self._memory.contradict(old_id)
