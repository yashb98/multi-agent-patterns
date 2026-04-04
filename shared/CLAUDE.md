# Shared Modules

Cross-cutting utilities used by all systems. Dependency flows ONE WAY: systems import from shared/, never the reverse.

## Key Modules

| Module | Purpose |
|--------|---------|
| `agents.py` | get_llm(), agent nodes (researcher, writer, reviewer, risk_aware_reviewer, fact_checker), smart_llm_call() |
| `code_graph.py` | AST-based CodeGraph — index Python, build call graph, compute risk scores (0-1) |
| `graph_visualizer.py` | Mermaid/DOT export for CodeGraph + LangGraph pattern topologies |
| `streaming.py` | Streaming LLM output — StreamCallback protocol, smart_llm_call() auto-switch |
| `llm_retry.py` | Exponential backoff retry for 429/5xx/timeout (3 retries, 2s base) |
| `parallel_executor.py` | ThreadPoolExecutor for concurrent LLM calls + GRPO candidate generation |
| `context_compression.py` | Tiktoken token counting, message truncation, context budget checks |
| `cost_tracker.py` | Per-call cost estimation + aggregation (MODEL_COSTS dict) |
| `agentic_loop.py` | stop_reason-based agentic loop with tool dispatch |
| `state.py` | AgentState TypedDict + prune_state() for iteration hygiene |
| `experiential_learning.py` | SQLite-backed ExperienceMemory + Training-Free GRPO |
| `fact_checker.py` | 3-level verification (research notes → web search → cache) |
| `nlp_classifier.py` | 3-tier intent classification (regex → embeddings → LLM fallback) |
| `logging_config.py` | Structured logging with run IDs (RunIdFilter) |
| `prompts.py` | System prompt constants for all agents |

## CodeGraph System

The code review intelligence layer:
1. **Index** — `CodeGraph.index_directory()` parses Python AST into SQLite (nodes + edges)
2. **Score** — `compute_risk_score()` weights security keywords, fan-in, test coverage, size
3. **Review** — `risk_aware_reviewer_node()` injects top-risk functions into reviewer prompt
4. **Visualize** — `export_code_graph_mermaid()` / `export_code_graph_dot()` with risk heatmap

## Code Exploration — Use MCP Tools First
Before using Grep/Glob to explore code, use CodeGraph MCP tools:
- `find_symbol` — locate any function/class definition
- `callers_of` / `callees_of` — trace call chains
- `impact_analysis` — blast radius of a change
- `risk_report` — high-risk functions needing careful review
- `module_summary` — overview of a module's structure
- `semantic_search` — find code by meaning
- `recent_changes` — what changed recently
One MCP call replaces 5-15 Grep/Glob/Read calls. Brief subagents to do the same.

## Rules
- NEVER import from patterns/, jobpulse/, or mindgraph_app/
- NEVER instantiate ChatOpenAI directly — always use get_llm() from agents.py
- NEVER use resilient_llm_call() in new code — use smart_llm_call() (streams when enabled)
- All new shared utilities go here, not duplicated across systems
