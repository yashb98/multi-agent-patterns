# Skills

Learnable capabilities: GRPO, persona evolution, RLM integration, prompt optimization.

## 1. Training-Free GRPO (Experiential Learning)

**File:** `shared/experiential_learning.py` + `jobpulse/swarm_dispatcher.py`

### How It Works

Learns in **prompt space** — model weights stay frozen:

```
1. GENERATE GROUP  → Run agent N times at different temps
2. SCORE           → Evaluate all N outputs
3. EXTRACT ADVANTAGE → LLM analyzes WHY best won
4. STORE EXPERIENCE  → Save the "why" as learned pattern
5. INJECT           → Future runs get experiences in prompts
```

### In JobPulse

- Budget classification: 3 candidates, pick best category match
- Briefing synthesis: 3 candidates, pick most actionable briefing
- Experiences stored in `swarm_experience.db` per intent

## 2. Persona Evolution

**File:** `shared/persona_evolution.py` + `jobpulse/persona_evolution.py`

### The Search-Synthesise-Compress Loop

```
SEARCH     → Look at recent experiences for this agent
SYNTHESISE → Merge learnings with current prompt
COMPRESS   → Distill to essential instructions (< 200 words)
VALIDATE   → Score performance
CONVERGE or LOOP
```

### In JobPulse

Three agents evolve their prompts over time:

| Agent | Base Prompt | After 4 weeks (example) |
|-------|------------|------------------------|
| gmail_agent | "Classify into 4 categories" | + "Skip Workday auto-rejections. Prioritize person names over noreply@" |
| budget_agent | "Match to 17 categories" | + "Coffee/lunch = Eating out, not Groceries. Amazon = check context" |
| briefing_synth | "Lead with urgent items" | + "Interviews always first. Yash prefers short bullet points" |

Evolved prompts stored in `persona_prompts` table (swarm_experience.db).

## 3. RLM (Recursive Language Model)

**Package:** `rlms>=0.1.0` (from `rlm import RLM`)

### What RLM Does

Normal LLM: sees all context at once, gets overwhelmed on large inputs.
RLM: root model writes **code** that processes context in chunks via sub-LM calls.

```
RLM = LLM that writes programs that call other LLMs
```

### Where It's Used

| Location | Trigger | What It Does |
|----------|---------|-------------|
| `retriever.py:deep_query()` | subgraph > 10K chars | Breaks graph into chunks by type, summarizes each, synthesizes answer |
| `swarm_dispatcher.py:rlm_synthesize()` | briefing data > 5K chars | Splits sections, summarizes, combines into actionable briefing |
| Morning briefing | > 20 events/day | Recursive categorize → prioritize → synthesize |

### Configuration

```env
RLM_BACKEND=openai
RLM_ROOT_MODEL=gpt-4o-mini
RLM_MAX_ITERATIONS=10
RLM_MAX_BUDGET=0.10      # $ cap per query
```

### Cost

~$0.02-0.05 per deep query (1 root call + 3-8 sub-LM calls).
Only activates on large contexts — small queries use direct LLM ($0.001).

## 4. DSPy/GEPA Prompt Optimization

**File:** `shared/prompt_optimizer.py`

| Backend | Approach | Best For |
|---------|----------|----------|
| DSPy + GEPA | Textual feedback + reflective evolution | Recommended |
| DSPy + MIPROv2 | Automated prompt tuning | When GEPA unavailable |
| LLM Meta-Optimization | LLM rewrites own prompts | Fallback |
