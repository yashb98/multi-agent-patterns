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
python -m jobpulse.runner chrome-pw     # Launch Chrome with CDP for Playwright
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

## Eight Engineering Principles (MANDATORY)
Every feature, function, and file MUST satisfy all 8 principles. Full checklist: `.claude/rules/seven-principles.md`
1. **System Design** — Clear boundaries, no import-time side effects, no duplicated logic
2. **Tool & Contract Design** — Typed interfaces, centralized LLM factories, consistent return types
3. **Retrieval Engineering** — Connection pooling, no N+1, cached lookups, lazy loading
4. **Reliability Engineering** — Resource cleanup in finally, guarded LLM calls, bounded loops
5. **Security & Safety** — No PII in source (all personal data from DBs at runtime), no injection vectors, SSRF protection, parameterized SQL
6. **Evaluation & Observability** — Cost tracking on all LLM calls, decision logging, structured errors
7. **Product Thinking** — Dry-run-first, confirm_application(), OS-aware paths, user-actionable errors
8. **Dynamic Over Hardcoded** — All pipeline values resolved at runtime, never hardcoded for specific forms/platforms. No regex for semantic work — use LLM/embeddings/semantic matching instead

## Live Pipeline Observation (MANDATORY)

All applications run the real live pipeline. No mocks, no headless, no silent runs.

**Claude's role: Orchestrator, not doer.** When running the pipeline, invoke the actual AI agents (`job-apply-next`, `apply_job()`, `ApplicationOrchestrator`, `NativeFormFiller`). Observe their output, diagnose failures, direct corrections — but let the agents execute. Don't bypass agents by writing ad-hoc Playwright scripts — that skips the learning loop (CorrectionCapture, AgentRulesDB, strategy_reflector never fire). The agents can only learn from runs they actually performed.

**Visibility:** Browser always headed — human watches live. No screenshots needed (human sees it). Logs to stdout; cron streams to Telegram. On ambiguity: STOP, tell human.

**Observe each step — let the system pick the best approach dynamically, capture everything for learning:**
1. **Pre-Screen** — Gates 0-4 on real JD. Log which gate passed/killed and why. Capture skill match data.
2. **CV/CL** — Dynamic profile sync + generation. Log matched skills, projects, role profile selection.
3. **Form Fill** — System dynamically resolves: field discovery method, container scoping, option matching strategy, timing, screening answers. Log every decision + outcome per field (what was tried, what worked, what failed) so agents learn which approach works best per domain/platform.
4. **Dry Run** — Human reviews live. Every mismatch = correction signal with before/after values.
5. **Submit** — Rate limiter + mutex + `confirm_application()` (mandatory)
6. **Learning** — Verify ALL fire and capture maximum data: `post_apply_hook` → `CorrectionCapture` → `AgentRulesDB` → `strategy_reflector` → `OptimizationEngine` signals → `AgentPerformanceDB`. Each system stores what worked AND what didn't — failures are learning data too.

**On error — Diagnose → Fix → Test → Teach → Verify:**
- Trace via MCP (`find_symbol`, `callers_of`). Fix surgically. Re-run same real data.
- Route fix to correct DB: fill issue → `CorrectionCapture` + `AgentRulesDB` | quirk → `GotchasDB` | answer → screening cache | nav → `NavigationLearner`
- Always emit `adaptation` signal + log trajectory step via `OptimizationEngine`
- Verify learning persisted (query DB)

**Verify 3 self-adaptation layers after every application:**
1. **Correction → Rule → Consumption** — `CorrectionCapture` → `AgentRulesDB` → `NativeFormFiller` consumes
2. **Strategy Reflection** — `strategy_reflector` → `TrajectoryStore` + `ExperienceMemory`
3. **Cognitive Escalation** — `CognitiveEngine` (L0→L3) + `OptimizationEngine` → `EscalationClassifier`

## Critical Rules
- **Real data + wiring verification** — Every new feature tested with real URLs/APIs/DBs (never mocks or stale data), then verified end-to-end that all downstream systems fire (hooks, signals, DB writes, learning chains). Not wired = not done.
- **No PII in source code** — ALL personal data (name, email, address, screening answers, skills, links, DEI) retrieved from databases at runtime, never hardcoded. Full policy: `.claude/rules/pii-policy.md`
- Update BOTH dispatcher.py AND swarm_dispatcher.py for new intents
- Always HTTPS for external APIs | Tests NEVER touch data/*.db — use tmp_path
- Never rewrite a file without checking `callers_of` (or Grep) for all function names used by other modules
- Log errors to `.claude/mistakes.md` | Full rules in `.claude/rules/`
- Use `semantic_search` to retrieve detailed rules/docs on demand — they're all indexed

## Dispatch
Enhanced Swarm (default). `JOBPULSE_SWARM=false` for flat dispatcher.

## Stats
~145,500 LOC | 684 Python files | 58 databases | 3426 tests | 5 dashboards | 5 Telegram bots | 3 platforms
> Auto-updated by pre-commit hook. Manual: `python scripts/update_stats.py`

## Module Context (loaded when working in that directory)
- `jobpulse/CLAUDE.md` — Agents, dispatch, Telegram, extension engine, application orchestrator
- `patterns/CLAUDE.md` — 4 LangGraph orchestration patterns
- `mindgraph_app/CLAUDE.md` — Code Review Graph, risk scoring, Mermaid/DOT viz
- `shared/CLAUDE.md` — Cross-cutting utilities, NLP, fact-checker
- `shared/cognitive/CLAUDE.md` — 4-level cognitive engine: memory recall, single shot, reflexion, tree of thought
- `shared/memory_layer/CLAUDE.md` — 3-engine memory: SQLite (truth) + Qdrant (vectors) + Neo4j (graph)
- `shared/optimization/CLAUDE.md` — Continuous learning: signal bus, aggregator, tracker, policy, trajectories
- `.claude/rules/` — Domain-specific rules (jobs, testing, patterns, shared, frontend, error-handling)
