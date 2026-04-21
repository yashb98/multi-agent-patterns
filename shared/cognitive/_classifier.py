"""Escalation classifier — 3-step heuristic for cognitive level selection."""

from shared.logging_config import get_logger
from shared.cognitive._budget import ThinkLevel, BudgetTracker

logger = get_logger(__name__)

STAKES_REGISTRY = {
    "high": [
        "job_application", "cv_generation", "cover_letter",
        "financial_transaction", "form_submission",
    ],
    "medium": [
        "email_classification", "calendar_scheduling",
        "screening_answers", "jd_analysis",
    ],
    "low": [
        "briefing_synthesis", "github_trending", "arxiv_ranking",
        "budget_categorization", "task_management",
    ],
}

# Thresholds for "strong" templates
_MIN_TIMES_USED = 3
_MIN_AVG_SCORE = 8.0
_MIN_SUCCESS_RATE = 0.8

# Self-improvement thresholds
_L0_SKIP_THRESHOLD = 0.95
_L1_HARD_DOMAIN_THRESHOLD = 0.50


class EscalationClassifier:
    """Picks the cognitive reasoning level via heuristic checks.

    3-step cascade: memory check → novelty check → stakes check.
    Budget tracker clamps the result if over budget.
    """

    def __init__(self, memory_manager, budget_tracker: BudgetTracker):
        self._memory = memory_manager
        self._budget = budget_tracker
        self._domain_stats: dict[str, dict] = {}

    def classify(self, task: str, domain: str, stakes: str) -> ThinkLevel:
        # Step 0a: check optimization engine DomainStats override
        try:
            from shared.optimization import get_optimization_engine
            opt_stats = get_optimization_engine().get_domain_stats(domain, domain)
            if opt_stats.forced_level is not None and opt_stats.sample_size >= 20:
                level = ThinkLevel(opt_stats.forced_level)
                logger.debug(
                    "Optimization override: %s forced to %s (n=%d)",
                    domain, level.name, opt_stats.sample_size,
                )
                return self._budget.clamp(level)
            if opt_stats.l0_success_rate >= 0.95 and opt_stats.sample_size >= 20:
                logger.debug(
                    "Optimization L0 fast-path: %s success=%.0f%% (n=%d)",
                    domain, opt_stats.l0_success_rate * 100, opt_stats.sample_size,
                )
                return self._budget.clamp(ThinkLevel.L0_MEMORY)
        except Exception:
            pass  # degrade gracefully — fall through to local stats

        # Step 0b: check classifier self-improvement memory
        stats = self._domain_stats.get(domain)
        if stats and stats.get("l0_success_rate", 0) >= _L0_SKIP_THRESHOLD \
           and stats.get("sample_size", 0) >= 10:
            logger.debug("Classifier memory: %s is easy (L0 %.0f%%) → L0",
                         domain, stats["l0_success_rate"] * 100)
            return self._budget.clamp(ThinkLevel.L0_MEMORY)

        hard_domain = stats and stats.get("l1_escalation_rate", 0) >= _L1_HARD_DOMAIN_THRESHOLD \
                      and stats.get("sample_size", 0) >= 10

        # Step 1: MEMORY CHECK — look for procedural templates
        procs = self._memory.get_procedural_entries(domain) \
            if hasattr(self._memory, "get_procedural_entries") else []

        if procs:
            strong = [p for p in procs
                      if p.avg_score_when_used >= _MIN_AVG_SCORE
                      and p.times_used >= _MIN_TIMES_USED
                      and p.success_rate >= _MIN_SUCCESS_RATE]
            if strong and not hard_domain:
                return self._budget.clamp(ThinkLevel.L0_MEMORY)
            # Weak templates exist
            if hard_domain:
                return self._budget.clamp(ThinkLevel.L2_REFLEXION)
            return self._budget.clamp(ThinkLevel.L1_SINGLE)

        # Step 2: NOVELTY CHECK — any memory about this domain?
        episodic = self._memory.get_episodic_entries(domain) \
            if hasattr(self._memory, "get_episodic_entries") else []

        if episodic:
            return self._budget.clamp(ThinkLevel.L1_SINGLE)

        # Step 3: STAKES CHECK — completely novel domain
        resolved_stakes = self._resolve_stakes(domain, stakes)
        if resolved_stakes == "high":
            return self._budget.clamp(ThinkLevel.L3_TREE_OF_THOUGHT)
        elif resolved_stakes == "medium":
            return self._budget.clamp(ThinkLevel.L2_REFLEXION)
        else:
            return self._budget.clamp(ThinkLevel.L1_SINGLE)

    def should_escalate(
        self, current_level: ThinkLevel, score: float, task: str, domain: str,
    ) -> tuple[bool, ThinkLevel]:
        if current_level == ThinkLevel.L0_MEMORY and score < 6.0:
            return True, ThinkLevel.L1_SINGLE
        if current_level == ThinkLevel.L1_SINGLE and score < 7.0:
            return True, ThinkLevel.L2_REFLEXION
        if current_level == ThinkLevel.L2_REFLEXION and score < 5.0:
            return True, ThinkLevel.L3_TREE_OF_THOUGHT
        return False, current_level

    def update_domain_stats(self, domain: str, level: ThinkLevel, escalated: bool):
        stats = self._domain_stats.setdefault(domain, {
            "l0_success_rate": 0.0, "l1_escalation_rate": 0.0,
            "l0_total": 0, "l0_success": 0,
            "l1_total": 0, "l1_escalated": 0,
            "sample_size": 0,
        })
        stats["sample_size"] = stats.get("sample_size", 0) + 1
        if level == ThinkLevel.L0_MEMORY:
            stats["l0_total"] += 1
            if not escalated:
                stats["l0_success"] += 1
            stats["l0_success_rate"] = stats["l0_success"] / max(stats["l0_total"], 1)
        elif level == ThinkLevel.L1_SINGLE:
            stats["l1_total"] += 1
            if escalated:
                stats["l1_escalated"] += 1
            stats["l1_escalation_rate"] = stats["l1_escalated"] / max(stats["l1_total"], 1)

        # Persist classifier accuracy to MemoryManager as SEMANTIC tier
        if stats["sample_size"] % 10 == 0:
            self._persist_domain_stats(domain, stats)

    def _persist_domain_stats(self, domain: str, stats: dict):
        """Save classifier accuracy to MemoryManager for cross-session persistence."""
        try:
            summary = (
                f"{domain}: L0 success {stats['l0_success_rate']:.0%}, "
                f"L1 escalation {stats['l1_escalation_rate']:.0%}, "
                f"n={stats['sample_size']}"
            )
            self._memory.learn_fact(
                domain="cognitive_classifier",
                fact=summary,
                run_id=f"classifier_{domain}",
            )
        except Exception as e:
            logger.debug("Failed to persist classifier stats: %s", e)

    def load_persisted_stats(self):
        """Load classifier accuracy from MemoryManager on init.

        Called by CognitiveEngine.__init__ to restore cross-session stats.
        Parses SEMANTIC tier entries with domain='cognitive_classifier'.
        """
        try:
            sem = getattr(self._memory, "semantic", None)
            if sem is None:
                return
            facts = getattr(sem, "facts", None)
            if not isinstance(facts, dict):
                return
            for _fact_id, entry in facts.items():
                if getattr(entry, "domain", "") == "cognitive_classifier":
                    self._parse_persisted_fact(getattr(entry, "fact", ""))
        except Exception as e:
            logger.debug("Failed to load persisted classifier stats: %s", e)

    def _parse_persisted_fact(self, fact: str):
        """Parse a persisted classifier fact string back into domain stats."""
        import re
        match = re.match(r"(\S+): L0 success (\d+)%, L1 escalation (\d+)%, n=(\d+)", fact)
        if match:
            domain = match.group(1)
            self._domain_stats[domain] = {
                "l0_success_rate": int(match.group(2)) / 100,
                "l1_escalation_rate": int(match.group(3)) / 100,
                "l0_total": 0, "l0_success": 0,
                "l1_total": 0, "l1_escalated": 0,
                "sample_size": int(match.group(4)),
            }

    @staticmethod
    def _resolve_stakes(domain: str, explicit_stakes: str) -> str:
        # Explicit stakes always take priority
        if explicit_stakes in ("high", "medium", "low"):
            return explicit_stakes
        # Fall back to registry lookup
        for level, domains in STAKES_REGISTRY.items():
            if domain in domains:
                return level
        return "medium"
