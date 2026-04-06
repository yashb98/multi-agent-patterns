# JobPulse — Multi-Agent Automation System

Production autonomous agent system: LangGraph + OpenAI + Enhanced Swarm + RLM.

## Quick Reference

```bash
python -m jobpulse.runner multi-bot    # Start all 5 Telegram bots
python -m jobpulse.runner stop         # Stop all daemons
python -m jobpulse.runner webhook      # API server (port 8080, Swagger at /docs)
python -m jobpulse.runner briefing     # Morning digest
python -m jobpulse.runner export       # Full data backup
python -m jobpulse.runner profile-sync # Refresh skill/project graph (3am cron)
python -m jobpulse.runner skill-gaps   # Show top missing skills + export CSV
python -m jobpulse.runner ext-bridge   # Start Chrome extension WebSocket bridge
python -m jobpulse.runner ralph-test <url>   # Test single URL via Ralph Loop
python -m jobpulse.runner ralph-test --live  # Scrape fresh URLs + test via extension
```

## Code Intelligence (use for ALL code exploration)
MCP tools are 10-250x faster than Grep (1-28ms vs 350-750ms, pre-indexed SQLite).
- `find_symbol` — locate definition | `callers_of` / `callees_of` — call graph
- `impact_analysis` — blast radius | `risk_report` — high-risk functions
- `semantic_search` — find code AND docs by meaning (all .md files are indexed)
- `module_summary` — module overview | `recent_changes` — git log + graph
- Grep/Glob only for non-Python files or raw regex in configs
- **Never use Explore agents for code understanding** — they can't access MCP, burn 50-100k tokens
- Brief subagents (general-purpose type): "Use MCP tools (find_symbol, callers_of, semantic_search) — never raw Grep/Glob"

## Critical Rules
- Update BOTH dispatcher.py AND swarm_dispatcher.py for new intents
- Always HTTPS for external APIs | Tests NEVER touch data/*.db — use tmp_path
- Never rewrite a file without checking `callers_of` (or Grep) for all function names used by other modules
- Log errors to `.claude/mistakes.md` | Full rules in `.claude/rules/`
- Use `semantic_search` to retrieve detailed rules/docs on demand — they're all indexed

## Dispatch
Enhanced Swarm (default). `JOBPULSE_SWARM=false` for flat dispatcher.

## Stats
~140,500 LOC | 579 Python files | 18 databases | 1610 tests | 4 dashboards | 5 Telegram bots | 3 platforms
> Auto-updated by pre-commit hook. Manual: `python scripts/update_stats.py`

## Module Context (loaded when working in that directory)
- `jobpulse/CLAUDE.md` — Agents, dispatch, Telegram, extension engine, application orchestrator
- `patterns/CLAUDE.md` — 4 LangGraph orchestration patterns
- `mindgraph_app/CLAUDE.md` — Code Review Graph, risk scoring, Mermaid/DOT viz
- `shared/CLAUDE.md` — Cross-cutting utilities, NLP, fact-checker
- `.claude/rules/` — Domain-specific rules (jobs, testing, patterns, shared, frontend, error-handling)
