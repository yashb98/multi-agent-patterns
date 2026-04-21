"""CognitiveEngine — single entry point for 4-level cognitive reasoning."""

import os
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from shared.logging_config import get_logger
from shared.cognitive._budget import ThinkLevel, CognitiveBudget, BudgetTracker
from shared.cognitive._classifier import EscalationClassifier
from shared.cognitive._strategy import StrategyComposer, ComposedPrompt
from shared.cognitive._reflexion import ReflexionLoop
from shared.cognitive._tree_of_thought import TreeOfThought

logger = get_logger(__name__)

_DEFAULT_SCORE_THRESHOLD = float(os.getenv("COGNITIVE_SCORE_THRESHOLD", "7.0"))
_GENERATE_COST = 0.001


async def _llm_generate(prompt: str, model: str = None) -> str:
    from shared.agents import get_llm
    from shared.streaming import smart_llm_call
    from langchain_core.messages import HumanMessage
    llm = get_llm(model=model or "gpt-4.1-mini", temperature=0.3, timeout=30.0)
    messages = [HumanMessage(content=prompt)]
    response = smart_llm_call(llm, messages)
    return response.content


@dataclass
class ThinkResult:
    answer: str
    score: float
    level: ThinkLevel
    cost: float
    latency_ms: float
    strategy_stored: bool = False
    escalated_from: Optional[ThinkLevel] = None
    attempts: int = 1
    composed_prompt: Optional[ComposedPrompt] = None


class CognitiveEngine:
    """The single entry point for cognitive reasoning.

    Usage:
        engine = CognitiveEngine(memory_manager, agent_name="my_agent")
        result = await engine.think(task="...", domain="...", stakes="medium")
        await engine.flush()
    """

    def __init__(
        self,
        memory_manager,
        agent_name: str,
        budget: Optional[CognitiveBudget] = None,
        prompt_resolver: Optional[Callable] = None,
    ):
        self._memory = memory_manager
        self._agent_name = agent_name
        self._prompt_resolver = prompt_resolver
        bgt = budget or CognitiveBudget.from_env()
        self._budget_tracker = BudgetTracker(bgt)
        self._classifier = EscalationClassifier(memory_manager, self._budget_tracker)
        self._classifier.load_persisted_stats()
        self._composer = StrategyComposer()
        self._reflexion = ReflexionLoop(memory_manager, agent_name)
        self._tot = TreeOfThought(memory_manager, agent_name)
        self._pending_writes: list[dict] = []
        self._level_counts: dict[str, int] = {}
        self._total_cost = 0.0
        self._total_calls = 0

    async def think(
        self,
        task: str,
        domain: str,
        stakes: str = "medium",
        scorer: Optional[Callable] = None,
        force_level: Optional[ThinkLevel] = None,
    ) -> ThinkResult:
        start = time.monotonic()
        self._total_calls += 1

        # 1. Classify
        try:
            level = force_level if force_level is not None else \
                self._classifier.classify(task, domain, stakes)
        except Exception as e:
            logger.warning("Classification failed, falling back to L1: %s", e)
            level = ThinkLevel.L1_SINGLE
        original_level = level

        # 2. Compose prompt from strategy templates
        try:
            composed = self._composer.compose(
                task, domain, self._agent_name, self._memory,
                prompt_resolver=self._prompt_resolver,
            )
        except Exception as e:
            logger.warning("Composition failed, using base prompt: %s", e)
            composed = ComposedPrompt(text=task)

        # 3. Execute at classified level
        result = await self._execute(level, task, domain, composed, scorer)

        # 4. Post-execution: auto-escalate if score too low
        if result.score < 6.0 and level < ThinkLevel.L3_TREE_OF_THOUGHT:
            should, next_level = self._classifier.should_escalate(
                level, result.score, task, domain,
            )
            if should and self._budget_tracker.allows(next_level):
                escalated_result = await self._execute(
                    next_level, task, domain, composed, scorer,
                )
                escalated_result.escalated_from = original_level
                escalated_result.level = next_level
                elapsed = (time.monotonic() - start) * 1000
                escalated_result.latency_ms = elapsed
                self._classifier.update_domain_stats(domain, original_level, escalated=True)
                self._record_level(next_level, escalated_result.cost)
                return escalated_result

        elapsed = (time.monotonic() - start) * 1000
        result.latency_ms = elapsed
        result.composed_prompt = composed
        self._classifier.update_domain_stats(domain, level, escalated=False)
        self._record_level(level, result.cost)

        # L1 successes get queued for batch-write via flush()
        if result.level == ThinkLevel.L1_SINGLE and result.score >= _DEFAULT_SCORE_THRESHOLD:
            self._pending_writes.append({
                "domain": domain,
                "strategy": f"For '{task[:50]}' tasks: {result.answer[:150]}",
                "context": f"agent_name={self._agent_name}|trigger={task[:50]}|source=l1_success",
                "score": result.score,
                "source": self._agent_name,
            })

        return result

    async def _execute(
        self,
        level: ThinkLevel,
        task: str,
        domain: str,
        composed: ComposedPrompt,
        scorer: Optional[Callable],
    ) -> ThinkResult:
        if level == ThinkLevel.L0_MEMORY:
            return self._execute_l0(task, domain, composed, scorer)
        elif level == ThinkLevel.L1_SINGLE:
            return await self._execute_l1(task, composed, scorer)
        elif level == ThinkLevel.L2_REFLEXION:
            return await self._execute_l2(task, domain, composed, scorer)
        elif level == ThinkLevel.L3_TREE_OF_THOUGHT:
            return await self._execute_l3(task, domain, composed, scorer)
        return await self._execute_l1(task, composed, scorer)

    def _execute_l0(
        self, task: str, domain: str, composed: ComposedPrompt, scorer: Optional[Callable],
    ) -> ThinkResult:
        if composed.templates_used:
            procs = self._memory.get_procedural_entries(domain) \
                if hasattr(self._memory, "get_procedural_entries") else []
            for p in procs:
                if getattr(p, "procedure_id", "") in composed.templates_used:
                    answer = p.strategy
                    score = scorer(answer) if scorer else p.avg_score_when_used
                    return ThinkResult(
                        answer=answer, score=score,
                        level=ThinkLevel.L0_MEMORY, cost=0.0, latency_ms=0,
                    )
        # No template match — return low score to trigger auto-escalation to L1
        return ThinkResult(
            answer="", score=0.0,
            level=ThinkLevel.L0_MEMORY, cost=0.0, latency_ms=0,
        )

    async def _execute_l1(
        self, task: str, composed: ComposedPrompt, scorer: Optional[Callable],
    ) -> ThinkResult:
        answer = await _llm_generate(composed.text)
        score = scorer(answer) if scorer else 5.0
        return ThinkResult(
            answer=answer, score=score,
            level=ThinkLevel.L1_SINGLE, cost=_GENERATE_COST, latency_ms=0,
        )

    async def _execute_l2(
        self, task: str, domain: str, composed: ComposedPrompt,
        scorer: Optional[Callable],
    ) -> ThinkResult:
        ref_result = await self._reflexion.run(
            task=task, domain=domain, initial_prompt=composed.text,
            score_threshold=_DEFAULT_SCORE_THRESHOLD, scorer=scorer,
        )
        return ThinkResult(
            answer=ref_result.answer, score=ref_result.score,
            level=ThinkLevel.L2_REFLEXION, cost=ref_result.cost,
            latency_ms=0, attempts=ref_result.attempts,
            strategy_stored=ref_result.score >= _DEFAULT_SCORE_THRESHOLD,
        )

    async def _execute_l3(
        self, task: str, domain: str, composed: ComposedPrompt,
        scorer: Optional[Callable],
    ) -> ThinkResult:
        tot_result = await self._tot.explore(
            task=task, domain=domain, context=composed.text, scorer=scorer,
        )
        return ThinkResult(
            answer=tot_result.winner.output, score=tot_result.winner.score,
            level=ThinkLevel.L3_TREE_OF_THOUGHT, cost=tot_result.cost,
            latency_ms=0, strategy_stored=True,
        )

    def _record_level(self, level: ThinkLevel, cost: float):
        name = level.name
        self._level_counts[name] = self._level_counts.get(name, 0) + 1
        self._total_cost += cost
        self._budget_tracker.record(level, cost)

    def report(self) -> dict:
        return {
            "agent_name": self._agent_name,
            "total_calls": self._total_calls,
            "level_counts": dict(self._level_counts),
            "total_cost": round(self._total_cost, 4),
            "budget": self._budget_tracker.report(),
        }

    def think_sync(
        self,
        task: str,
        domain: str,
        stakes: str = "medium",
        scorer: Optional[Callable] = None,
        force_level: Optional[ThinkLevel] = None,
    ) -> ThinkResult:
        """Synchronous wrapper around think() for non-async agents."""
        import asyncio
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(
                    asyncio.run,
                    self.think(task, domain, stakes, scorer, force_level),
                ).result()
        return asyncio.run(self.think(task, domain, stakes, scorer, force_level))

    async def flush(self):
        for write in self._pending_writes:
            try:
                self._memory.learn_procedure(**write)
            except Exception as e:
                logger.warning("Failed to flush strategy template: %s", e)
        self._pending_writes.clear()

    def flush_sync(self):
        """Synchronous wrapper around flush() for non-async agents."""
        import asyncio
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(asyncio.run, self.flush()).result()
        else:
            asyncio.run(self.flush())


def get_cognitive_engine(
    agent_name: str,
    budget: Optional[CognitiveBudget] = None,
    prompt_resolver: Optional[Callable] = None,
) -> CognitiveEngine:
    """Factory that creates a CognitiveEngine with the shared MemoryManager."""
    from shared.memory_layer import get_shared_memory_manager
    memory = get_shared_memory_manager()
    return CognitiveEngine(
        memory_manager=memory,
        agent_name=agent_name,
        budget=budget,
        prompt_resolver=prompt_resolver,
    )
