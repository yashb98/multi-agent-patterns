# Rules: Shared Modules (shared/**/*)

## Coding Discipline
- Surgical changes only — don't refactor adjacent code or add unrequested features.
- No abstractions for single-use code. No speculative "flexibility" or "configurability."
- Remove only what YOUR changes made unused — don't touch pre-existing dead code.
- State assumptions explicitly before implementing. If multiple approaches exist, present them.

## No PII in Source (MANDATORY)
Personal information MUST NEVER be hardcoded — always retrieved from databases at runtime.
All profile data flows through proper data access layers (`get_profile()`, `ScreeningPipeline`, etc.).
Full policy: `.claude/rules/pii-policy.md`

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

## CodeGraph (shared/code_graph/)
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

## NLP Classifier (jobpulse/nlp_classifier.py)
3-tier pipeline: regex (instant) → semantic embeddings (5ms) → LLM fallback ($0.001).
- When adding intents: add embedding examples first (preferred), then LLM gets it for free. Regex tier is legacy — do NOT add new regex patterns. Migrate existing regex patterns to embedding examples when touching this file.
- 250+ examples across 41 intents.
- Strip trailing punctuation before classification (Whisper adds ".", "!", "?").

## No Regex for Semantic Work (MANDATORY)
Regex MUST NOT be used for classification, intent routing, question categorization, consent detection, field matching, or command parsing. Use dynamic approaches: LLM (with caching), embedding similarity, semantic matching, DOM/a11y inspection, or database-stored patterns.
- **Regex remains OK for**: text normalization (whitespace/punctuation), security sanitization (injection tag stripping), structural format validation (email/phone/date/URL), number extraction from known formats
- **Migration rule**: When touching a file with regex-based classification, migrate those patterns to dynamic in the same change
- This extends the "Dynamic Over Hardcoded" principle — regex patterns are a form of hardcoding that breaks on input variation

## Real Data + Wiring Verification (MANDATORY)
New shared features: test with real data (real embeddings, real DB queries, real API calls — never mocks or stale fixtures). Verify downstream consumers actually receive signals/data. If `OptimizationEngine` emits a signal, confirm the aggregator consumed it. If `MemoryManager` stores a fact, confirm retrieval returns it. Not wired = not done.

## OPRAL Error Loop (MANDATORY)
On every error in shared modules: **Observe** → **Plan** → **Reason** → **Act** → **Learn**. Trace root cause, fix with real data, route learning to the correct system (MemoryManager, OptimizationEngine, CognitiveEngine), verify the fix persists and prevents recurrence. Every error makes the system smarter.

## Memory Layer (shared/memory_layer/)
All memory access goes through MemoryManager — never query SQLite/Qdrant/Neo4j directly.
Same principle as get_llm() for LLM calls: single entry point, no direct engine access.

## Optimization Engine (shared/optimization/)
All optimization access goes through OptimizationEngine — never query data/optimization.db directly.
Same principle as MemoryManager and CognitiveEngine: single facade, no direct component access.
All learning loops MUST emit signals. All learning actions MUST use before/after measurement.

## Fact Checker (shared/fact_checker.py)
Unified module used by both patterns/ and jobpulse/.
- 3-level verification: research notes → external (Semantic Scholar, web search) → cache
- Honest scoring: abstract-only verification = 0.5 (5.0/10), not 1.0
- Human-readable explanations required for every verification result
- Cache in data/verified_facts.db — tests must use tmp_path
