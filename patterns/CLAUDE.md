# Orchestration Patterns

4 LangGraph patterns for multi-agent coordination.

## Patterns
- hierarchical.py — Supervisor routes to workers + fact-checker
- peer_debate.py — Agents cross-critique each round + fact-check
- dynamic_swarm.py — Task queue + runtime re-analysis
- enhanced_swarm.py — GRPO + persona + RLM + fact-check (production)

## Convergence
Dual gate: quality score >= 8.0/10 AND factual accuracy >= 9.5/10.
Max 3 iterations. Fallback: accept best draft.

## Rules
- Agents are stateless functions — no instance variables, no side effects
- Never return full AgentState — only fields that changed
- Never mutate `topic` after initialization
- Review scores are floats 0-10, threshold 8.0
- Output files go to outputs/ as markdown
