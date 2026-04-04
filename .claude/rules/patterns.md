# Rules: Orchestration Patterns (patterns/**/*)

## Convergence
- Dual gate: quality >= 8.0 AND accuracy >= 9.5. Both must pass.
- Max 3 iterations for hierarchical/dynamic_swarm.
- Peer debate uses patience counter (score improvement < threshold).
- Enhanced swarm uses experience-aware adaptive threshold.

## Agent Design
- Agents are stateless functions. No instance variables. No side effects.
- Never return full AgentState — only the fields that changed.
- Never mutate `topic` after initialization — it's the immutable input.
- Review scores are floats 0.0-10.0. Never use integers.

## LLM Usage
- Never instantiate ChatOpenAI directly — use get_llm() from shared/agents.py.
- Use smart_llm_call() for new LLM calls (auto-streams when STREAM_LLM_OUTPUT=1).

## Code Review (CodeGraph)
- All 4 patterns use risk_aware_reviewer_node (shared/agents.py).
- CodeGraph indexes draft code via AST, scores risk 0-1, injects top-risk functions into review prompt.
- Visualization: export_pattern_mermaid() for pattern topology, export_code_graph_mermaid() for dependency graphs.

## Experiential Learning
- All 4 patterns share ExperienceMemory (SQLite: data/experience_memory.db).
- High-scoring runs (>= 7.0) extract learnings at convergence/finish nodes.
- Learned patterns injected into agent prompts for future runs.

## State Pruning
- prune_state() called at convergence/routing points in all patterns.
- Limits: research_notes=3, agent_history=20, token_usage=30.

## Fact Checking
- Uses shared/fact_checker.py (unified module, not pattern-specific).
- Claim types: benchmark, date, attribution, comparison, technical.
- Scoring: VERIFIED +1.0, INACCURATE -2.0, EXAGGERATED -1.0, UNVERIFIED -0.5/-1.5.
- SQLite cache at data/verified_facts.db for instant reuse.
