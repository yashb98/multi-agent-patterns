# Cognitive Reasoning Engines

4-level graduated escalation system. Any agent opts in via 3 lines.

## Levels

| Level | Name | When | LLM Calls | Cost |
|-------|------|------|-----------|------|
| L0 | Memory Recall | Strong templates in memory (score>=8, used>=3) | 0 | ~$0 |
| L1 | Single Shot | Weak templates or partial memory | 1 | ~$0.001 |
| L2 | Reflexion | Novel + medium stakes, or L1 scored poorly | 2-3 | ~$0.005 |
| L3 | Tree of Thought | Novel + high stakes, or L2 failed | 6-12 | ~$0.02-0.05 |

## Usage

```python
from shared.cognitive import CognitiveEngine

engine = CognitiveEngine(memory_manager, agent_name="my_agent")
result = await engine.think(task="...", domain="...", stakes="medium")
# result.answer, result.score, result.level, result.cost
await engine.flush()  # batch-write strategy templates to memory
```

## Modules

| Module | Purpose |
|--------|---------|
| `_budget.py` | ThinkLevel enum, CognitiveBudget, BudgetTracker |
| `_classifier.py` | EscalationClassifier — 3-step heuristic (memory → novelty → stakes) |
| `_strategy.py` | StrategyComposer, ComposedPrompt — template retrieval + prompt assembly |
| `_prompts.py` | Prompt templates for critique, branching, scoring |
| `_reflexion.py` | ReflexionLoop — L2 try/critique/retry |
| `_tree_of_thought.py` | TreeOfThought — L3 branch/score/prune/extend |
| `_engine.py` | CognitiveEngine — single entry point |

## Rules

- ALL LLM calls go through get_llm() / smart_llm_call()
- Critiques use gpt-4.1-nano (~$0.0002/call)
- Budget caps: 20 L2/hour, 5 L3/hour, $0.50/hour max
- Strategy templates stored via MemoryManager.learn_procedure()
- Failure patterns stored via MemoryManager.record_episode()
- Call flush() at end of agent run to persist pending writes
