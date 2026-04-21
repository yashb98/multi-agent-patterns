"""Agent Awareness Loop -- cross-pillar wiring.

TaskPreFlight queries Pillars 1-4 before execution.
ConfidenceTracker monitors real-time confidence during execution.
TaskPostFlight records outcomes to Pillars 1, 3, 4.
TaskRunner wraps any agent function with the full loop.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from shared.execution._event_store import EventStore
from shared.execution._verifier import VerifyResult
from shared.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class TaskPlan:
    """Pre-flight plan with confidence, strategy, and escalation hints."""

    confidence: float
    strategy: list
    anti_patterns: list
    cognitive_level: str
    start_tier: int
    escalation_hints: list = field(default_factory=list)


@dataclass
class Decision:
    """After-action decision: continue or escalate."""

    action: str  # "continue" or "escalate"
    target: str | None = None
    reason: str = ""


class ConfidenceTracker:
    """Monitors real-time confidence during task execution."""

    def __init__(self, plan: TaskPlan) -> None:
        self.confidence = plan.confidence
        self.hints = plan.escalation_hints
        self.events: list[dict] = []

    def after_action(self, event: dict, verify: VerifyResult) -> Decision:
        """Update confidence based on verification result. Returns decision."""
        self.events.append(event)

        if verify.field_mismatch:
            self.confidence -= 0.2
        if verify.unexpected_element:
            self.confidence -= 0.3
        if verify.all_ok:
            self.confidence += 0.05

        # Check escalation hints (custom callables)
        for hint in self.hints:
            if callable(getattr(hint, "matches", None)) and hint.matches(
                event, self.confidence
            ):
                return Decision("escalate", hint.action, hint.reason)

        if self.confidence < 0.4:
            return Decision("escalate", "rescue", f"confidence {self.confidence:.2f}")

        return Decision("continue")


class TaskPreFlight:
    """Queries Pillars 1-4 before execution to build a TaskPlan."""

    def __init__(
        self,
        memory,
        cognitive,
        optimization,
        event_store: EventStore,
    ) -> None:
        self._memory = memory
        self._cognitive = cognitive
        self._optimization = optimization
        self._store = event_store

    def prepare(self, task: dict) -> TaskPlan:
        """Build a TaskPlan from memory, cognitive assessment, and optimization stats."""
        domain = task.get("input", {}).get("domain", "")
        platform = task.get("input", {}).get("platform", "")
        skill_id = task.get("skill_id", "")

        # Pillar 1: Memory recall
        memories: list = []
        if self._memory:
            try:
                memories = self._memory.recall(
                    query=f"{platform} {domain} {skill_id}",
                    tiers=["procedural", "episodic"],
                    limit=5,
                )
            except Exception:
                memories = []

        # Cold start: no memories and no relevant events
        if not memories:
            recent = self._store.query(
                stream_prefix=f"form:{platform}:{domain}" if platform else None,
                event_types=["form.mistake_detected", "form.rescue_used"],
                limit=10,
            )
            if not recent:
                return TaskPlan(
                    confidence=0.5,
                    strategy=[],
                    anti_patterns=[],
                    cognitive_level="L1",
                    start_tier=1,
                    escalation_hints=[],
                )

        # Pillar 2: Cognitive assessment
        assessment_confidence = 0.5
        cognitive_level = "L1"
        if self._cognitive:
            try:
                assessment = self._cognitive.assess(
                    task=f"{skill_id} on {platform}:{domain}",
                    domain=skill_id,
                    memories=memories,
                    recent_failure_count=0,
                )
                assessment_confidence = assessment.confidence
                cognitive_level = assessment.recommended_level
            except Exception:
                pass

        # Pillar 3: Optimization stats (informational, doesn't change plan)
        if self._optimization:
            try:
                self._optimization.get_domain_stats(skill_id, platform)
            except Exception:
                pass

        start_tier = 1 if assessment_confidence > 0.7 and memories else 2

        return TaskPlan(
            confidence=assessment_confidence,
            strategy=memories,
            anti_patterns=[],
            cognitive_level=cognitive_level,
            start_tier=start_tier,
            escalation_hints=[],
        )


class TaskPostFlight:
    """Records outcomes to Pillars 1, 3, 4 after execution."""

    def __init__(self, memory, optimization, event_store: EventStore) -> None:
        self._memory = memory
        self._optimization = optimization
        self._store = event_store

    def complete(self, task: dict, result: dict, events: list[dict]) -> None:
        """Record task outcome to memory, optimization, and event store."""
        success = result.get("success", False)

        # Pillar 1: Memory learning
        if self._memory:
            try:
                if success:
                    self._memory.learn_procedure(
                        domain=task.get("skill_id", "unknown"),
                        strategy=f"Completed {task.get('skill_id')}",
                        context=(
                            f"{task.get('input', {}).get('platform', '')}:"
                            f"{task.get('input', {}).get('domain', '')}"
                        ),
                        score=0.8,
                        source=task.get("input", {}).get("platform", "unknown"),
                    )
                else:
                    self._memory.store_episodic(
                        content=f"Failed: {result.get('failure_reason', 'unknown')}",
                        context=task.get("skill_id", ""),
                        tags=["failure"],
                    )
            except Exception as e:
                logger.debug("Post-flight memory store failed: %s", e)

        # Pillar 3: Optimization signal
        if self._optimization:
            try:
                self._optimization.emit_signal(
                    signal_type="success" if success else "failure",
                    domain=task.get("skill_id", ""),
                    source=task.get("input", {}).get("platform", ""),
                    payload={"task_id": task.get("task_id")},
                )
            except Exception as e:
                logger.debug("Post-flight optimization signal failed: %s", e)

        # Pillar 4: Event store
        self._store.emit(
            stream_id=f"task:{task.get('task_id', 'unknown')}",
            event_type="task.post_flight_done",
            payload={"success": success},
        )


class TaskRunner:
    """Wraps any agent function with the full awareness loop."""

    def __init__(
        self,
        agent_fn,
        memory,
        cognitive,
        optimization,
        event_store: EventStore,
    ) -> None:
        self.agent_fn = agent_fn
        self.preflight = TaskPreFlight(memory, cognitive, optimization, event_store)
        self.postflight = TaskPostFlight(memory, optimization, event_store)
        self._store = event_store

    async def run(self, task: dict) -> dict:
        """Execute agent with pre-flight, timeout, and post-flight."""
        plan = self.preflight.prepare(task)
        tracker = ConfidenceTracker(plan)

        try:
            result = await asyncio.wait_for(
                self.agent_fn(task, plan, tracker),
                timeout=task.get("timeout_s", 120),
            )
        except asyncio.TimeoutError:
            self._store.emit(
                f"task:{task.get('task_id', 'unknown')}",
                "task.timed_out",
                {"timeout_s": task.get("timeout_s")},
            )
            result = {"success": False, "failure_reason": "timeout"}
        except Exception as e:
            result = {"success": False, "failure_reason": str(e)}

        self.postflight.complete(task, result, tracker.events)
        return result
