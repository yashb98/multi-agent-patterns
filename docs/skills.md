# Skills

Learnable capabilities: GRPO experiential learning, persona evolution, and prompt optimization.

## 1. Training-Free GRPO (Experiential Learning)

**File:** `shared/experiential_learning.py`
**Based on:** arXiv:2510.08191

### How It Works

Traditional RLHF updates model weights. Training-Free GRPO learns in **prompt space** — model weights stay frozen:

```
1. GENERATE GROUP  → Run same agent N times at different temperatures
2. SCORE           → Evaluate all N outputs
3. EXTRACT ADVANTAGE → LLM analyzes WHY the best output won
4. STORE EXPERIENCE  → Save the "why" as a learned pattern
5. INJECT           → Future runs get relevant experiences in prompts
```

The model doesn't change — but prompts get smarter over time.

### Key Classes

- `ExperienceMemory`: Stores learned patterns with relevance-based retrieval
- `TrainingFreeGRPO`: Main class with `group_sample_and_learn()` method
- `_extract_semantic_advantage()`: LLM analyzes WHY best output was better (not just scores)

### Usage

```python
from shared.experiential_learning import TrainingFreeGRPO, ExperienceMemory

memory = ExperienceMemory()
grpo = TrainingFreeGRPO(memory)

best_output, score = grpo.group_sample_and_learn(
    agent_fn=writer_node,
    state=current_state,
    n_samples=4,
    scorer_fn=review_and_score
)
```

## 2. Persona Evolution

**File:** `shared/persona_evolution.py`

### The Search-Synthesise-Compress Loop

Agent prompts evolve through iterative cycles:

```
SEARCH     → Inject fresh domain knowledge
SYNTHESISE → Merge with existing persona
COMPRESS   → Distill to essential instructions
VALIDATE   → Score performance
CONVERGE or LOOP
```

### Key Classes

- `PersonaEvolver`: Evolves agent prompts across cycles
- `PersonaSnapshot`: Records persona state at each evolution cycle

### Convergence Detection

- **Patience counter**: N cycles without improvement → stop
- **Rollback**: If current persona < best, revert to best
- **Fresh knowledge injection**: Each cycle gets new context (prevents overfitting)

## 3. DSPy/GEPA Prompt Optimization

**File:** `shared/prompt_optimizer.py`

### Three Optimization Backends

| Backend | Approach | Best For |
|---------|----------|----------|
| DSPy + GEPA | Textual feedback + reflective evolution on failures | Recommended default |
| DSPy + MIPROv2 | Automated prompt tuning with instruction generation | When GEPA unavailable |
| LLM Meta-Optimization | LLM rewrites own prompts from performance data | Fallback (no dependencies) |

### Usage

```python
from shared.prompt_optimizer import PromptOptimizer

optimizer = PromptOptimizer(backend="gepa")
result = optimizer.optimize(
    current_prompt=WRITER_PROMPT,
    examples=training_examples,
    metric=quality_scorer
)
# result.optimized_prompt, result.improvement_pct
```

### How GEPA Works

1. Collects failure trajectories (inputs + bad outputs + feedback)
2. LLM reflects on WHY failures happened
3. Evolves prompt instructions to avoid failure patterns
4. Validates improvement on held-out examples
