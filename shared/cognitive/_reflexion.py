"""Reflexion Loop — Level 2 cognitive reasoning: try/critique/retry."""

from dataclasses import dataclass, field
from typing import Callable, Optional

from shared.logging_config import get_logger
from shared.cognitive._prompts import CRITIQUE_PROMPT

logger = get_logger(__name__)

_GENERATE_COST = 0.001
_CRITIQUE_COST = 0.0002


async def _llm_generate(prompt: str, model: str = None) -> str:
    from shared.agents import get_llm
    from shared.streaming import smart_llm_call
    from langchain_core.messages import HumanMessage
    llm = get_llm(model=model or "gpt-4.1-mini", temperature=0.3, timeout=30.0)
    messages = [HumanMessage(content=prompt)]
    response = smart_llm_call(llm, messages)
    return response.content


@dataclass
class ReflexionResult:
    answer: str
    score: float | None
    attempts: int
    critiques: list[str] = field(default_factory=list)
    strategy_template: str = ""
    cost: float = 0.0


class ReflexionLoop:
    """Sequential self-correction: try, critique what went wrong, retry."""

    def __init__(self, memory_manager, agent_name: str):
        self._memory = memory_manager
        self._agent_name = agent_name

    async def run(
        self,
        task: str,
        domain: str,
        initial_prompt: str,
        max_attempts: int = 3,
        score_threshold: float = 7.0,
        scorer: Optional[Callable] = None,
    ) -> ReflexionResult:
        best_answer = ""
        best_score: float | None = None
        critiques: list[str] = []
        total_cost = 0.0
        attempt = 0

        # Retrieve failure patterns for this domain
        failure_context = self._get_failure_context(domain)

        prompt = initial_prompt
        for attempt in range(1, max_attempts + 1):
            # Generate
            answer = await _llm_generate(prompt)
            total_cost += _GENERATE_COST

            # Score
            score = scorer(answer) if scorer else await self._llm_score(task, answer)

            if best_score is None or (score is not None and score > best_score):
                best_answer = answer
                best_score = score

            score_display = f"{score:.1f}" if score is not None else "None"
            logger.info(
                "Reflexion attempt %d/%d: score=%s (threshold=%.1f)",
                attempt, max_attempts, score_display, score_threshold,
            )

            if score is not None and score >= score_threshold:
                break

            if attempt >= max_attempts:
                break

            # Critique (using nano — cheap)
            score_for_prompt = f"{score:.1f}" if score is not None else "N/A"
            critique_prompt = CRITIQUE_PROMPT.format(
                task=task, output=answer, score=score_for_prompt, threshold=score_threshold,
            )
            critique = await _llm_generate(critique_prompt, model="gpt-4.1-nano")
            total_cost += _CRITIQUE_COST
            critiques.append(critique)

            # Build retry prompt with critique + failure patterns
            prompt = f"{initial_prompt}\n\n## Previous attempt (score: {score})\n{answer}"
            prompt += f"\n\n## Critique\n{critique}"
            if failure_context:
                prompt += f"\n\n## Known failure patterns for this domain\n{failure_context}"

        # Store learnings
        if best_score is not None and best_score >= score_threshold:
            self._store_success(task, domain, best_answer, best_score)
        else:
            self._store_failure(task, domain, best_answer, best_score, critiques)

        return ReflexionResult(
            answer=best_answer,
            score=best_score,
            attempts=attempt,
            critiques=critiques,
            strategy_template=(
                best_answer[:200]
                if best_score is not None and best_score >= score_threshold
                else ""
            ),
            cost=total_cost,
        )

    def _get_failure_context(self, domain: str) -> str:
        if not hasattr(self._memory, "get_episodic_entries"):
            return ""
        episodes = self._memory.get_episodic_entries(domain)
        failures = [e for e in episodes if e.final_score < 5.0]
        if not failures:
            return ""
        lines = []
        for f in failures[:3]:
            if f.weaknesses:
                lines.append(f"- {f.weaknesses[0]}")
        return "\n".join(lines)

    def _store_success(self, task: str, domain: str, answer: str, score: float):
        strategy = f"For '{task[:50]}' tasks: {answer[:150]}"
        # STRATEGY_PAYLOAD fields encoded in context for pre-Pillar-1 compatibility
        payload_context = (
            f"agent_name={self._agent_name}|trigger={task[:50]}|"
            f"times_used=1|times_succeeded=1|success_rate=1.0|"
            f"avg_score={score:.1f}|source=reflexion"
        )
        self._memory.learn_procedure(
            domain=domain,
            strategy=strategy,
            context=payload_context,
            score=score,
            source=self._agent_name,
        )

    def _store_failure(
        self,
        task: str,
        domain: str,
        answer: str,
        score: float | None,
        critiques: list[str],
    ):
        critique_text = critiques[-1] if critiques else "No critique available"
        self._memory.record_episode(
            topic=task[:100],
            final_score=score if score is not None else 0.0,
            iterations=len(critiques) + 1,
            pattern_used="reflexion",
            agents_used=[self._agent_name],
            strengths=[],
            weaknesses=[critique_text],
            output_summary=answer[:200],
            domain=domain,
        )

    async def _llm_score(self, task: str, output: str) -> float | None:
        from shared.cognitive._prompts import SCORING_PROMPT
        prompt = SCORING_PROMPT.format(task=task, output=output)
        result = await _llm_generate(prompt, model="gpt-4.1-nano")
        try:
            return float(result.strip())
        except ValueError:
            return None
