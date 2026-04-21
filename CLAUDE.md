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
```

## Code Intelligence (use for ALL code exploration)
MCP tools are 10-250x faster than Grep (1-28ms vs 350-750ms, pre-indexed SQLite).
- `find_symbol` — locate definition | `callers_of` / `callees_of` — call graph
- `impact_analysis` — blast radius | `risk_report` — high-risk functions
- `semantic_search` — find code AND docs by meaning (all .md files are indexed)
- `module_summary` — module overview | `recent_changes` — git log + graph
- Grep/Glob only for non-Python files or raw regex in configs
- **Never use Explore agents for code understanding** — they can't access MCP, burn 50-100k tokens

### Subagent Code Intelligence (auto-injected via AGENTS.md)
Subagents automatically get CLI instructions via `AGENTS.md` — no manual briefing needed.
CLI uses direct SQLite (~50ms) vs `python -m` path (~4s, heavy `shared/__init__.py` imports).

## Coding Principles

### Think Before Coding
- State assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them — don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.

### Simplicity First
- Minimum code that solves the problem. Nothing speculative.
- No features beyond what was asked. No abstractions for single-use code.
- No error handling for impossible scenarios. If 200 lines could be 50, rewrite it.

### Surgical Changes
- Don't "improve" adjacent code, comments, or formatting.
- Match existing style. Don't refactor things that aren't broken.
- Remove imports/variables/functions that YOUR changes made unused — don't touch pre-existing dead code.
- Every changed line should trace directly to the request.

### Goal-Driven Execution
- Transform tasks into verifiable goals with success criteria.
- For multi-step tasks, state a brief plan with verification checks per step.
- Loop until verified — weak criteria ("make it work") require clarification first.

## Seven Engineering Principles (MANDATORY)
Every feature, function, and file MUST satisfy all 7 principles. Full checklist: `.claude/rules/seven-principles.md`
1. **System Design** — Clear boundaries, no import-time side effects, no duplicated logic
2. **Tool & Contract Design** — Typed interfaces, centralized LLM factories, consistent return types
3. **Retrieval Engineering** — Connection pooling, no N+1, cached lookups, lazy loading
4. **Reliability Engineering** — Resource cleanup in finally, guarded LLM calls, bounded loops
5. **Security & Safety** — No PII in source, no injection vectors, SSRF protection, parameterized SQL
6. **Evaluation & Observability** — Cost tracking on all LLM calls, decision logging, structured errors
7. **Product Thinking** — Dry-run-first, confirm_application(), OS-aware paths, user-actionable errors

## Critical Rules
- Update BOTH dispatcher.py AND swarm_dispatcher.py for new intents
- Always HTTPS for external APIs | Tests NEVER touch data/*.db — use tmp_path
- Never rewrite a file without checking `callers_of` (or Grep) for all function names used by other modules
- Log errors to `.claude/mistakes.md` | Full rules in `.claude/rules/`
- Use `semantic_search` to retrieve detailed rules/docs on demand — they're all indexed

## Dispatch
Enhanced Swarm (default). `JOBPULSE_SWARM=false` for flat dispatcher.

## Stats
~98,500 LOC | 467 Python files | 32 databases | 2566 tests | 4 dashboards | 5 Telegram bots | 3 platforms
> Auto-updated by pre-commit hook. Manual: `python scripts/update_stats.py`

## Module Context (loaded when working in that directory)
- `jobpulse/CLAUDE.md` — Agents, dispatch, Telegram, extension engine, application orchestrator
- `patterns/CLAUDE.md` — 4 LangGraph orchestration patterns
- `mindgraph_app/CLAUDE.md` — Code Review Graph, risk scoring, Mermaid/DOT viz
- `shared/CLAUDE.md` — Cross-cutting utilities, NLP, fact-checker
- `.claude/rules/` — Domain-specific rules (jobs, testing, patterns, shared, frontend, error-handling)
