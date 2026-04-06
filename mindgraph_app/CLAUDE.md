# Code Review Graph System

AST-based code analysis + risk scoring + dependency visualization. Replaces the old knowledge-only MindGraph with a production code intelligence layer.

## Architecture

```
Source Code → AST Parser → SQLite Graph (nodes + edges)
    → Risk Scoring → Review Prioritization → Mermaid/DOT Export
```

## Core Components

| Module | Path | Purpose |
|--------|------|---------|
| CodeGraph | `shared/code_graph.py` | AST indexing, call graph, risk scoring |
| Graph Visualizer | `shared/graph_visualizer.py` | Mermaid/DOT export, pattern topology diagrams |
| Risk-Aware Reviewer | `shared/agents.py:risk_aware_reviewer_node` | Injects risk context into LLM code review |

## CodeGraph Schema (SQLite)

**nodes** — functions, classes, methods extracted from Python AST
- `kind`: function, class, method
- `qualified_name`: `file_path::ClassName::method_name`
- `is_test`, `is_async`: flags for coverage + async detection

**edges** — dependency relationships
- `kind`: calls, imports, inherits, contains
- `source_qname` → `target_qname`

## Risk Scoring (0.0 — 1.0)

| Factor | Weight | Trigger |
|--------|--------|---------|
| Security keyword | +0.25 | auth, password, token, crypt, sql, jwt, etc. |
| Fan-in (callers) | +0.05/caller | High caller count = high blast radius |
| Cross-file callers | +0.10 | Dependencies span multiple files |
| No test coverage | +0.30 | Function has no corresponding test |
| Large function | +0.15 | >50 lines |

## Visualization Export

- `export_pattern_mermaid(name)` — 4 LangGraph pattern topologies (hierarchical, peer_debate, dynamic_swarm, enhanced_swarm)
- `export_code_graph_mermaid(graph, focus_file, max_nodes, show_risk)` — Dependency graph with risk heatmap
- `export_code_graph_dot(graph)` — Graphviz DOT format

Risk colors: Red (#ff6b6b) >= 0.7, Yellow (#ffd93d) >= 0.4, Green (#6bcb77) < 0.4

## Legacy MindGraph (still used by JobPulse)

The original entity/relation knowledge graph (`storage.py`, `extractor.py`, `retriever.py`) is still used by:
- `jobpulse/skill_graph_store.py` — Skill/project graph for job pre-screening
- `jobpulse/event_logger.py` — Agent action timeline
- `jobpulse/auto_extract.py` — Document entity extraction

These will be migrated to CodeGraph in a future phase. Do not delete `storage.py`, `extractor.py`, or `retriever.py` until migration is complete.

## Code Exploration — Use MCP Tools First
Use CodeGraph MCP tools for ALL code exploration. Never use raw Grep/Glob.
- `find_symbol` — locate any function/class definition
- `callers_of` / `callees_of` — trace call chains
- `impact_analysis` — blast radius of a change
- `risk_report` — high-risk functions needing careful review
- `semantic_search` — find code by meaning
- `grep_search` — ripgrep + code graph enrichment for literal/regex/TODO search with risk ranking
One MCP call replaces 5-15 Grep/Glob/Read calls. Brief subagents to do the same.

## Rules
- CodeGraph lives in `shared/` — all systems can import it
- Legacy storage layer in `mindgraph_app/storage.py` — jobpulse-only access
- Tests MUST use `:memory:` or `tmp_path` for SQLite (see mistakes.md: 2026-03-25)
- Never import from jobpulse/ or patterns/
