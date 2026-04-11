"""Unified convergence controller for all 4 orchestration patterns.

Before this module, each pattern implemented its own convergence logic:
- hierarchical   — inline if/elif in supervisor_node_rule_based
- peer_debate    — convergence_check() node with patience counter
- dynamic_swarm  — should_continue_swarm() routing function
- enhanced_swarm — enhanced_convergence() with adaptive threshold

All share the same dual-gate semantics but with subtle inconsistencies
(different thresholds, missing patience counter in some, etc.).
ConvergenceController centralises the logic so all patterns behave the
same way and changes propagate automatically.

CONVERGENCE RULES (in priority order):
  1. Dual gate: quality_score >= QUALITY_THRESHOLD AND accuracy_passed → CONVERGED
  2. Patience: no score improvement for `patience` rounds → CONVERGED (avoids wasted iterations)
  3. Safety valve: iteration >= max_iterations → MAX_ITERATIONS (never infinite loop)
  → Otherwise: CONTINUE

Usage (in any pattern node or routing function)::

    from shared.convergence import ConvergenceController

    _convergence = ConvergenceController()   # module-level singleton

    # In a routing function:
    decision = _convergence.check(state)
    return "finish" if decision.should_stop else "continue"

    # Or with full context:
    decision = _convergence.check(state, max_iterations=3, patience=2)
    logger.info("Convergence: %s — %s", decision.outcome, decision.reason)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from shared.logging_config import get_logger

logger = get_logger(__name__)

import os as _os
_is_local = _os.environ.get("LLM_PROVIDER", "openai") == "local"

# ── Thresholds (from .claude/rules/patterns.md) ─────────────────────────────
QUALITY_THRESHOLD = 8.0    # review_score must reach this
ACCURACY_THRESHOLD = 9.5   # accuracy_score must reach this
_DEFAULT_MAX_ITER = 5 if _is_local else 3  # more iterations when compute is free


class Outcome(str, Enum):
    CONVERGED = "converged"        # Both gates passed
    PATIENCE = "patience"          # No improvement for N rounds
    MAX_ITERATIONS = "max_iter"    # Safety valve hit
    CONTINUE = "continue"          # Keep going


@dataclass
class ConvergenceDecision:
    """Result of a single convergence check."""
    outcome: Outcome
    reason: str
    quality_score: float
    accuracy_score: float
    iteration: int

    @property
    def should_stop(self) -> bool:
        return self.outcome != Outcome.CONTINUE

    def __str__(self) -> str:
        return f"{self.outcome.value}: {self.reason}"


class ConvergenceController:
    """Unified convergence logic for all 4 orchestration patterns.

    Stateful: tracks score history across calls to detect stagnation
    (patience counter). Create one instance per pattern run or reuse a
    module-level singleton and call reset() between runs.

    Args:
        quality_threshold: Minimum review_score to pass quality gate (default 8.0).
        accuracy_threshold: Minimum accuracy_score to pass accuracy gate (default 9.5).
        max_iterations: Hard iteration cap — safety valve only, not primary stop (default 3).
        patience: Stop after this many rounds with < min_improvement delta (default 2).
        min_improvement: Minimum score delta to count as "improvement" (default 0.3).
    """

    def __init__(
        self,
        quality_threshold: float = QUALITY_THRESHOLD,
        accuracy_threshold: float = ACCURACY_THRESHOLD,
        max_iterations: int = _DEFAULT_MAX_ITER,
        patience: int = 2,
        min_improvement: float = 0.3,
    ):
        self.quality_threshold = quality_threshold
        self.accuracy_threshold = accuracy_threshold
        self.max_iterations = max_iterations
        self.patience = patience
        self.min_improvement = min_improvement
        self._score_history: list[float] = []

    def reset(self) -> None:
        """Reset score history. Call between independent runs."""
        self._score_history.clear()

    def check(self, state: dict[str, Any]) -> ConvergenceDecision:
        """Evaluate convergence for the current state.

        Args:
            state: LangGraph AgentState dict (or any dict with the standard keys).

        Returns:
            ConvergenceDecision — check .should_stop to decide whether to loop.
        """
        quality_score: float = float(state.get("review_score", 0.0))
        accuracy_score: float = float(state.get("accuracy_score", 0.0))
        quality_ok: bool = bool(state.get("review_passed", False)) and quality_score >= self.quality_threshold
        accuracy_ok: bool = bool(state.get("accuracy_passed", False)) and accuracy_score >= self.accuracy_threshold
        iteration: int = int(state.get("iteration", 0))

        # Track score history for patience counter
        self._score_history.append(quality_score)

        # ── Priority 1: Dual gate ──────────────────────────────────────────
        if quality_ok and accuracy_ok:
            reason = (
                f"Both gates passed — quality={quality_score:.1f}/{self.quality_threshold}, "
                f"accuracy={accuracy_score:.1f}/{self.accuracy_threshold}"
            )
            logger.info("Convergence → CONVERGED: %s", reason)
            return ConvergenceDecision(
                outcome=Outcome.CONVERGED,
                reason=reason,
                quality_score=quality_score,
                accuracy_score=accuracy_score,
                iteration=iteration,
            )

        # ── Priority 2: Safety valve ───────────────────────────────────────
        if iteration >= self.max_iterations:
            reason = (
                f"Max iterations ({self.max_iterations}) reached — "
                f"quality={quality_score:.1f}, accuracy={accuracy_score:.1f}"
            )
            logger.info("Convergence → MAX_ITERATIONS: %s", reason)
            return ConvergenceDecision(
                outcome=Outcome.MAX_ITERATIONS,
                reason=reason,
                quality_score=quality_score,
                accuracy_score=accuracy_score,
                iteration=iteration,
            )

        # ── Priority 3: Patience counter ──────────────────────────────────
        if len(self._score_history) >= self.patience + 1:
            recent = self._score_history[-(self.patience + 1):]
            improvements = [
                recent[i + 1] - recent[i] for i in range(len(recent) - 1)
            ]
            if all(delta < self.min_improvement for delta in improvements):
                reason = (
                    f"No score improvement for {self.patience} rounds "
                    f"(scores: {[f'{s:.1f}' for s in recent]}) — stopping early"
                )
                logger.info("Convergence → PATIENCE: %s", reason)
                return ConvergenceDecision(
                    outcome=Outcome.PATIENCE,
                    reason=reason,
                    quality_score=quality_score,
                    accuracy_score=accuracy_score,
                    iteration=iteration,
                )

        # ── Default: continue ──────────────────────────────────────────────
        missing = []
        if not quality_ok:
            missing.append(
                f"quality {quality_score:.1f}/{self.quality_threshold}"
                + (" (not passed)" if not state.get("review_passed") else "")
            )
        if not accuracy_ok:
            missing.append(
                f"accuracy {accuracy_score:.1f}/{self.accuracy_threshold}"
                + (" (not checked)" if not state.get("accuracy_passed") else "")
            )
        reason = f"Round {iteration}/{self.max_iterations} — still needs: {', '.join(missing)}"
        logger.info("Convergence → CONTINUE: %s", reason)
        return ConvergenceDecision(
            outcome=Outcome.CONTINUE,
            reason=reason,
            quality_score=quality_score,
            accuracy_score=accuracy_score,
            iteration=iteration,
        )
