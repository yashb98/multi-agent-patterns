# Orchestration Patterns

4 LangGraph patterns for multi-agent coordination, all sharing CodeGraph-powered review and SQLite-backed experiential learning.

## Patterns
- hierarchical.py — Supervisor routes to workers + fact-checker
- peer_debate.py — Agents cross-critique each round + fact-check + experiential learning
- dynamic_swarm.py — Task queue + runtime re-analysis + experiential learning
- enhanced_swarm.py — GRPO + persona + parallel candidates + experiential learning (production)

## Cross-Cutting Infrastructure (all 4 patterns)

| Feature | Module | What |
|---------|--------|------|
| Risk-Aware Review | `shared/agents.py:risk_aware_reviewer_node` | CodeGraph AST analysis → risk scoring → prioritized review prompt |
| Experiential Learning | `shared/experiential_learning.py` | SQLite-backed ExperienceMemory shared across patterns and restarts |
| State Pruning | `shared/state.py:prune_state()` | Prevents unbounded list growth between iterations |
| Streaming Output | `shared/streaming.py:smart_llm_call()` | Token-by-token output when `STREAM_LLM_OUTPUT=1` |
| Structured Logging | `shared/logging_config.py` | Run IDs correlate all logs per execution |
| LLM Retry | `shared/llm_retry.py` | Exponential backoff on 429/5xx/timeout |
| Visualization | `shared/graph_visualizer.py` | Mermaid/DOT topology diagrams for all 4 patterns |
## Convergence
Dual gate: quality score >= 8.0/10 AND factual accuracy >= 9.5/10.
Max 3 iterations. Fallback: accept best draft.

## Code Exploration — Use MCP Tools First
Use CodeGraph MCP tools for ALL code exploration. Never use raw Grep/Glob.
- `find_symbol` — locate any function/class definition
- `callers_of` / `callees_of` — trace call chains
- `impact_analysis` — blast radius of a change
- `semantic_search` — find code by meaning
- `grep_search` — ripgrep + code graph enrichment for literal/regex/TODO search with risk ranking
One MCP call replaces 5-15 Grep/Glob/Read calls. Brief subagents to do the same.

## Rules
- Agents are stateless functions — no instance variables, no side effects
- Never return full AgentState — only fields that changed
- Never mutate `topic` after initialization
- Review scores are floats 0-10, threshold 8.0
- Use smart_llm_call() (not resilient_llm_call) for new LLM calls
- Output files go to outputs/ as markdown
