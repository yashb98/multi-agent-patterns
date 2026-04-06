# Rules: Shared Modules (shared/**/*)

## Dependency Direction
shared/ modules MUST NOT import from patterns/, jobpulse/, or mindgraph_app/.
Dependency flows one way: systems → shared. Never the reverse.

## get_llm()
All LLM instantiation goes through get_llm() in shared/agents.py.
Never call ChatOpenAI() or similar constructors directly anywhere in the codebase.

## smart_llm_call()
All new LLM calls should use smart_llm_call() from shared/streaming.py (re-exported via shared/agents.py).
It auto-switches between streaming and non-streaming based on STREAM_LLM_OUTPUT env var.
Do NOT use resilient_llm_call() in new code.

## CodeGraph (shared/code_graph.py)
AST-based code intelligence used by risk_aware_reviewer_node:
- Index Python files into SQLite graph (nodes + edges)
- Risk scoring: security keywords, fan-in, test coverage, function size
- Impact radius: BFS blast radius for changed files
- Tests must use `:memory:` SQLite — never file-backed in tests
- **MCP tools available:** find_symbol, callers_of, callees_of, impact_analysis, risk_report, semantic_search, module_summary, recent_changes, grep_search
- `grep_search` — ripgrep subprocess + code graph enrichment. Use for literal strings, regex, TODOs, config values. Returns matches ranked by risk with enclosing function context.
- ALWAYS use MCP tools instead of Grep/Glob for code exploration — one MCP call replaces 5-15 search calls

## Graph Visualizer (shared/graph_visualizer.py)
Export CodeGraph and LangGraph pattern topologies:
- Mermaid flowcharts with risk heatmap coloring
- DOT format for Graphviz rendering
- All 4 pattern topologies defined in PATTERN_TOPOLOGIES dict

## Experiential Learning (shared/experiential_learning.py)
SQLite-backed ExperienceMemory shared across all 4 patterns:
- DB path: data/experience_memory.db (tests must use tmp_path)
- LRU eviction: quality * 0.6 + recency * 0.4
- All patterns inject learned experiences into prompts and extract from high-scoring runs

## NLP Classifier (shared/nlp_classifier.py)
3-tier pipeline: regex (instant) → semantic embeddings (5ms) → LLM fallback ($0.001).
- When adding intents: add regex patterns first, then embedding examples, then LLM gets it for free.
- 250+ examples across 41 intents.
- Strip trailing punctuation before classification (Whisper adds ".", "!", "?").

## Fact Checker (shared/fact_checker.py)
Unified module used by both patterns/ and jobpulse/.
- 3-level verification: research notes → external (Semantic Scholar, web search) → cache
- Honest scoring: abstract-only verification = 0.5 (5.0/10), not 1.0
- Human-readable explanations required for every verification result
- Cache in data/verified_facts.db — tests must use tmp_path
