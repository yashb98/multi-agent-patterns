"""Tree of Thought — Level 3 cognitive reasoning: branch/score/prune/extend."""

import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from shared.logging_config import get_logger
from shared.cognitive._prompts import BRANCH_STRATEGIES, EXTENSION_PROMPT

logger = get_logger(__name__)

_GENERATE_COST = 0.001
_SCORE_COST = 0.0002


async def _llm_generate(prompt: str, model: str = None) -> str:
    from shared.agents import get_llm
    from shared.streaming import smart_llm_call
    from langchain_core.messages import HumanMessage
    llm = get_llm(model=model or "gpt-4.1-mini", temperature=0.7, timeout=30.0)
    messages = [HumanMessage(content=prompt)]
    response = smart_llm_call(llm, messages)
    return response.content


@dataclass
class Branch:
    branch_id: str
    reasoning: str
    output: str
    score: float
    depth: int = 0


@dataclass
class ToTResult:
    winner: Branch
    all_branches: list[Branch] = field(default_factory=list)
    strategy_template: str = ""
    pruned_count: int = 0
    cost: float = 0.0


class TreeOfThought:
    """Parallel exploration of structurally different reasoning approaches.

    Uses GRPO's parallel generation machinery for initial branches,
    then adds structured prompting + pruning/extension on top.
    """

    def __init__(self, memory_manager, agent_name: str):
        self._memory = memory_manager
        self._agent_name = agent_name

    def _generate_branches_via_grpo(
        self, system_prompt: str, task: str, strategies: list[str],
    ) -> list[str]:
        """Use parallel generation with per-strategy prompts for branch diversity."""
        try:
            from shared.agents import get_llm
            from langchain_core.messages import HumanMessage
            from concurrent.futures import ThreadPoolExecutor

            temps = [0.3 + i * 0.15 for i in range(len(strategies))]
            prompts = [
                f"{system_prompt}\n\n## Reasoning approach\n{s}\n\n## Task\n{task}"
                for s in strategies
            ]

            def generate_one(i: int) -> str:
                llm = get_llm(model="gpt-4.1-mini", temperature=temps[i], timeout=30.0)
                result = llm.invoke([HumanMessage(content=prompts[i])])
                return result.content if result else ""

            with ThreadPoolExecutor(max_workers=len(strategies)) as pool:
                return list(pool.map(generate_one, range(len(strategies))))
        except (ImportError, Exception):
            return []

    async def explore(
        self,
        task: str,
        domain: str,
        context: str,
        num_branches: int = 4,
        prune_threshold: float = 5.0,
        extend_top_n: int = 2,
        scorer: Optional[Callable] = None,
    ) -> ToTResult:
        total_cost = 0.0
        all_branches: list[Branch] = []

        # Step 1: Generate initial branches with structurally different prompts
        strategies = BRANCH_STRATEGIES[:num_branches]
        while len(strategies) < num_branches:
            strategies.append(f"Try a creative approach #{len(strategies) + 1}.")

        # Try GRPO parallel generation first, fall back to sequential
        grpo_outputs = self._generate_branches_via_grpo(context, task, strategies)

        for i, strategy in enumerate(strategies):
            if i < len(grpo_outputs) and grpo_outputs[i]:
                output = grpo_outputs[i]
            else:
                prompt = f"{context}\n\n## Reasoning approach\n{strategy}\n\n## Task\n{task}"
                output = await _llm_generate(prompt)
            total_cost += _GENERATE_COST

            score = scorer(output) if scorer else 5.0
            total_cost += _SCORE_COST

            branch = Branch(
                branch_id=f"b{i}", reasoning=strategy,
                output=output, score=score, depth=0,
            )
            all_branches.append(branch)

        # Step 2: Prune below threshold
        surviving = [b for b in all_branches if b.score >= prune_threshold]
        pruned_count = len(all_branches) - len(surviving)

        if not surviving:
            surviving = sorted(all_branches, key=lambda b: b.score, reverse=True)[:1]
            pruned_count = len(all_branches) - 1

        # Step 3: Extend top N surviving branches
        surviving.sort(key=lambda b: b.score, reverse=True)
        to_extend = surviving[:extend_top_n]

        for parent in to_extend:
            ext_prompt = EXTENSION_PROMPT.format(reasoning=parent.output)
            ext_prompt = f"{context}\n\n{ext_prompt}\n\n## Task\n{task}"
            ext_output = await _llm_generate(ext_prompt)
            total_cost += _GENERATE_COST

            ext_score = scorer(ext_output) if scorer else 5.0
            total_cost += _SCORE_COST

            ext_branch = Branch(
                branch_id=f"{parent.branch_id}_ext", reasoning=ext_prompt,
                output=ext_output, score=ext_score, depth=1,
            )
            all_branches.append(ext_branch)

        # Step 4: Pick winner
        winner = max(all_branches, key=lambda b: b.score)

        # Extract strategy template from winner
        strategy_template = f"Winning approach for '{task[:50]}': {winner.output[:150]}"

        # Only store when winner exceeds quality threshold
        if winner.score >= 7.0:
            payload_context = (
                f"agent_name={self._agent_name}|trigger={task[:50]}|"
                f"times_used=1|times_succeeded=1|success_rate=1.0|"
                f"avg_score={winner.score:.1f}|source=tot|"
                f"pruned_count={pruned_count}|branches={len(all_branches)}"
            )
            self._memory.learn_procedure(
                domain=domain,
                strategy=strategy_template,
                context=payload_context,
                score=winner.score,
                source=self._agent_name,
            )

        return ToTResult(
            winner=winner,
            all_branches=all_branches,
            strategy_template=strategy_template,
            pruned_count=pruned_count,
            cost=total_cost,
        )
