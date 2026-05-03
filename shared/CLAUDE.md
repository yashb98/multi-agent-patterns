# Shared Modules

Cross-cutting utilities used by all systems. Dependency flows ONE WAY: systems import from shared/, never the reverse.

## Key Modules

| Module | Purpose |
|--------|---------|
| `agents.py` | get_llm(), agent nodes (researcher, writer, reviewer, risk_aware_reviewer, fact_checker), smart_llm_call() |
| `code_graph/` | AST-based CodeGraph package — index Python, build call graph, compute risk scores (0-1). Submodules: `_algorithms.py`, `_indexer.py`, `_risk.py` |
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
| ~~`nlp_classifier.py`~~ | Lives in `jobpulse/nlp_classifier.py`, NOT shared/ |
| `logging_config.py` | Structured logging with run IDs (RunIdFilter) |
| `prompts/` | Prompt registry package — PromptRegistry system (get_prompt, list_prompts, reload_registry) with YAML templates |

## CodeGraph System

The code review intelligence layer:
1. **Index** — `CodeGraph.index_directory()` parses Python AST into SQLite (nodes + edges)
2. **Score** — `compute_risk_score()` weights security keywords, fan-in, test coverage, size
3. **Review** — `risk_aware_reviewer_node()` injects top-risk functions into reviewer prompt
4. **Visualize** — `export_code_graph_mermaid()` / `export_code_graph_dot()` with risk heatmap

## Code Exploration — MCP Tools First (10-250x faster than Grep)
MCP tools query pre-indexed SQLite (1-28ms). Grep scans 581 files every time (350-750ms).
- `find_symbol` — locate definition | `callers_of` / `callees_of` — call graph
- `impact_analysis` — blast radius | `risk_report` — high-risk functions
- `module_summary` — module overview | `semantic_search` — find by meaning
- `recent_changes` — git log + graph context
- Grep/Glob only for non-Python files or raw regex in configs
- Never use Explore agents for code understanding — they can't access MCP tools

## Memory Layer (shared/memory_layer/)
3-engine hybrid: SQLite (truth) + Qdrant (vectors) + Neo4j (graph).
- `_sqlite_store.py` — Source of truth CRUD
- `_qdrant_store.py` — Filtered HNSW vector search
- `_neo4j_store.py` — Graph traversal + signals
- `_embedder.py` — Voyage 3 Large + MiniLM fallback
- `_linker.py` — Autonomous graph linking (A-MEM)
- `_forgetting.py` — 6-signal decay + lifecycle promotion
- `_query.py` — QueryRouter picks engine(s) per query
- `_sync.py` — 3-engine reconciliation
- `_manager.py` — MemoryManager facade (single entry point)
All memory access goes through MemoryManager — never query engines directly.

## Cognitive Reasoning (shared/cognitive/)
4-level graduated escalation: L0 Memory Recall → L1 Single Shot → L2 Reflexion → L3 Tree of Thought.
- `CognitiveEngine.think(task, domain, stakes)` — single entry point
- EscalationClassifier picks level via heuristic (memory → novelty → stakes)
- StrategyComposer assembles prompts from templates + anti-patterns
- Budget caps: 20 L2/hour, 5 L3/hour, $0.50/hour. Kill switch: COGNITIVE_ENABLED=false
- Full docs: `shared/cognitive/CLAUDE.md`

## Optimization Engine (shared/optimization/)
Continuous learning & optimization — Pillar 3 of 6.
- Signal-driven: learning loops emit → aggregator detects patterns → policy acts → tracker measures
- `OptimizationEngine` facade: `get_optimization_engine()` returns shared singleton
- Signal types: correction | failure | success | adaptation | score_change | rollback
- Wrap learning actions with `before_learning_action()` / `after_learning_action()`
- TrajectoryStore logs structured action sequences, exports ShareGPT JSONL
- DomainStats feeds CognitiveEngine's EscalationClassifier with success rates
- Kill switch: `OPTIMIZATION_ENABLED=false` makes engine full no-op
- Full docs: `shared/optimization/CLAUDE.md`

## Additional Modules (not in table above)

| Module | Purpose |
|--------|---------|
| `code_intelligence/` | Unified code intelligence facade wrapping CodeGraph + HybridSearch (MCP backend). Submodules: `_indexer.py`, `_queries.py`, `_search.py`, `_analytics.py` |
| `tools/` | Tool implementations: WebSearchTool, GmailTool, TelegramTool, DiscordTool, LinkedInTool, BrowserTool, TerminalTool |
| `evals/` | Deterministic agent evaluation harness (CanonicalFlowCase, run_canonical_flow_evals) |
| `model_costs/` | JSON-based model pricing data for cost tracking |
| `hybrid_search.py` | FTS5 + vector similarity merged via Reciprocal Rank Fusion |
| `circuit_breaker.py` | Prevents cascading external service failures |
| `safe_fetch.py` | HTTP fetch with SSRF guardrails |
| `prompt_defense.py` | Prompt injection defense with XML markers |
| `pii.py` | PII wrappers for prompt construction and leak auditing |
| `profile_store.py` | ProfileStore for applicant profile data |
| `convergence.py` | Unified convergence controller for all patterns |
| `dynamic_agent_factory.py` | DynamicAgentFactory + AgentTemplate for runtime agent creation |
| `persona_evolution.py` | PersonaEvolver for prompt evolution |
| `prompt_optimizer.py` | DSPy + GEPA prompt optimization |
| `reranker.py` | Cross-encoder reranker (ms-marco-MiniLM) |
| `explainability.py` | DecisionExplainer for human-readable decision audit |
| `self_healing.py` | Database health and memory desync detection |
| `searxng_client.py` | SearXNG self-hosted metasearch client |
| `telegram_client.py` | Centralized Telegram Bot API client |
| `alerting.py` | Pipeline alerting via Telegram |
| `llm_fallback.py` | Multi-provider LLM fallback chain |
| `db.py` | Shared SQLite connection utility (get_pooled_db_conn) |
| `paths.py` | Project-level path constants (DATA_DIR) |

## Sub-Module Documentation
- `shared/cognitive/CLAUDE.md` — 4-level cognitive engine
- `shared/memory_layer/CLAUDE.md` — 5-tier memory with 3 storage engines
- `shared/optimization/CLAUDE.md` — Continuous learning & optimization
- `shared/adversarial/CLAUDE.md` — Adversarial evaluation framework, red-teaming
- `shared/execution/CLAUDE.md` — Durable execution, event sourcing, checkpointing
- `shared/governance/CLAUDE.md` — Security, score validation, policy engine, API auth

## Rules
- NEVER import from patterns/, jobpulse/, or mindgraph_app/
- NEVER instantiate ChatOpenAI directly — always use get_llm() from agents.py
- NEVER use resilient_llm_call() in new code — use smart_llm_call() (streams when enabled)
- All new shared utilities go here, not duplicated across systems
