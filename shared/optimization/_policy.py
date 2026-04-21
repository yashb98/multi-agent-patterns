"""OptimizationPolicy — decides actions based on aggregated insights."""

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
                 budget: Optional[OptimizationBudget] = None):
        self._memory = memory_manager
        self._cognitive = cognitive_engine
        self._budget = budget or OptimizationBudget()
        self._rollback_count = 0
        self._rule_gen_count = 0
        self._llm_call_count = 0
        self._window_start = time.monotonic()
        self._last_rollback_time = 0.0

    def _maybe_reset_window(self):
        if time.monotonic() - self._window_start >= 3600:
            self._window_start = time.monotonic()
            self._rollback_count = 0
            self._rule_gen_count = 0
            self._llm_call_count = 0

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
                actions.append(PolicyAction(
                    action_type="cognitive_decision",
                    target=result.answer,
                    domain=insight.domain,
                    evidence=f"CognitiveEngine: {result.answer[:200]}",
                    confidence=result.score / 10.0,
                ))
        return actions

    def _handle_systemic(self, insight: AggregatedInsight) -> list[PolicyAction]:
        actions = []
        if self._rule_gen_count < self._budget.max_rule_generations_per_hour:
            self._rule_gen_count += 1
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
