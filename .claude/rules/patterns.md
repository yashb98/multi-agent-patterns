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

## Fact Checking
- Uses shared/fact_checker.py (unified module, not pattern-specific).
- Claim types: benchmark, date, attribution, comparison, technical.
- Scoring: VERIFIED +1.0, INACCURATE -2.0, EXAGGERATED -1.0, UNVERIFIED -0.5/-1.5.
- SQLite cache at data/verified_facts.db for instant reuse.
