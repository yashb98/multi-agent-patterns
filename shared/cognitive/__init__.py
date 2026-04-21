"""Cognitive Reasoning Engines — 4-level graduated escalation.

Opt-in toolkit for agents. Any agent adds cognitive abilities via:

    from shared.cognitive import CognitiveEngine

    engine = CognitiveEngine(memory_manager, agent_name="my_agent")
    result = await engine.think(task="...", domain="...", stakes="medium")
    await engine.flush()

Levels:
    L0 Memory Recall  — strategy templates found, zero LLM calls
    L1 Single Shot    — one LLM call with composed prompt
    L2 Reflexion      — try/critique/retry with failure memory
    L3 Tree of Thought — parallel branch exploration via GRPO
"""

from shared.cognitive._budget import (  # noqa: F401
    ThinkLevel,
    CognitiveBudget,
    BudgetTracker,
)
from shared.cognitive._strategy import (  # noqa: F401
    StrategyComposer,
    ComposedPrompt,
)
from shared.cognitive._reflexion import ReflexionResult  # noqa: F401
from shared.cognitive._tree_of_thought import ToTResult, Branch  # noqa: F401
from shared.cognitive._engine import CognitiveEngine, ThinkResult  # noqa: F401
