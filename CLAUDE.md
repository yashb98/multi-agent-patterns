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
python -m jobpulse.runner job-apply-next  # Apply next N jobs from queue
python -m jobpulse.runner job-process-url # Full pipeline on a URL
python -m jobpulse.runner job-scan        # Scan all platforms for jobs
python -m jobpulse.runner optimize        # Run optimization engine
python -m jobpulse.runner learning-report # Show learning system status
python -m jobpulse.runner skill-verify    # Sync verified skills from Notion
python -m jobpulse.runner restart         # Restart daemon
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

**On error — OPRAL loop (Observe → Plan → Reason → Act → Learn):**
1. **Observe** — Capture the error in context: logs, DOM state, DB state, which agent failed and where
2. **Plan** — Trace via MCP (`find_symbol`, `callers_of`). Identify root cause, not symptoms. Determine which DB/system needs the fix
3. **Reason** — Why did this fail? Is it a one-off or a pattern? Which learning system should prevent recurrence?
4. **Act** — Fix surgically. Re-run same real data. Route fix to correct DB: fill issue → `CorrectionCapture` + `AgentRulesDB` | quirk → `GotchasDB` | answer → screening cache | nav → `NavigationLearner`
5. **Learn** — Emit `adaptation` signal via `OptimizationEngine`. Verify learning persisted (query DB). Confirm the agent handles this case autonomously on next run. Every error makes the system smarter — if an error can recur, the fix is incomplete.

**Verify 2 self-adaptation layers after every application:**
1. **Correction → Rule → Consumption** — `CorrectionCapture` → `AgentRulesDB` → `NativeFormFiller` consumes
2. **Strategy Reflection** — `strategy_reflector` → `TrajectoryStore` + `ExperienceMemory`

(Cognitive escalation runs *in-line during form fill*, not post-apply: see `native_form_filler._escalate_fill` for failed field fills (`domain="form_recovery"`/`"form_navigation"`). Navigator-level cognitive escalation was removed in the 2026-05-07 audit because no `ThinkResult`→`PageAction` translator exists, and cognitive does not yet emit `adaptation` signals to `OptimizationEngine` on escalation — see `pipeline-bugs.md` S6 W-1.)

## Database Wiring Status
27 DBs active with data, 19 wired but empty (code exists, not yet firing in production), 5 dead/legacy (51 total .db files in data/). Newly wired tables: `application_outcomes`, `company_reliability`, `gate_effectiveness` (in applications.db via post_apply_hook + gate4_quality), `performance_snapshots`, `cognitive_outcomes` (in optimization.db via optimize() + CognitiveEngine.think()). 11 dead 0-byte databases cleaned up. When touching any pipeline code, verify the relevant DB actually receives data — query it after a run.

## Critical Rules
- **OPRAL on every error** — Observe → Plan → Reason → Act → Learn. Every error must make the system smarter. If an error can recur, the fix is incomplete.
- **Real data + wiring verification** — Every new feature tested with real URLs/APIs/DBs (never mocks or stale data), then verified end-to-end that all downstream systems fire (hooks, signals, DB writes, learning chains). Not wired = not done.
- **No PII in source code** — ALL personal data (name, email, address, screening answers, skills, links, DEI) retrieved from databases at runtime, never hardcoded. Full policy: `.claude/rules/pii-policy.md`
- New intents via handler_registry.py + intent_registry.py + command_router.py (both dispatchers consume via get_handler_map())
- Always HTTPS for external APIs | Tests NEVER touch data/*.db — use tmp_path
- Never rewrite a file without checking `callers_of` (or Grep) for all function names used by other modules
- Log errors to `.claude/mistakes.md` | Full rules in `.claude/rules/`
- Use `semantic_search` to retrieve detailed rules/docs on demand — they're all indexed
- **Security wall bypass** — Playwright auto-bypass first (6 stages: auto-wait, human simulation, Turnstile click, reload ×2), THEN human fallback via Telegram (MANDATORY). Never abort without asking human. Full spec: `.claude/rules/jobs.md`
- **Semantic page reasoning** — When DOM classifier confidence is low, `page_analysis/page_reasoner.py` uses LLM to understand the page and recommend actions (dismiss_dialog, click_apply, fill_form, etc.). Cached per domain. Navigator executes the recommended action.

## Dispatch
Enhanced Swarm (default). `JOBPULSE_SWARM=false` for flat dispatcher.


## Infrastructure

### Docker Services
- `docker-compose.memory.yml` — Qdrant (port 6333) + Neo4j (port 7687) for memory layer
- `docker-compose.searxng.yml` — SearXNG metasearch (port 8888)

### Scripts (`scripts/`)
- `install_cron.py` — Install/update full crontab (marker-based merge)
- `setup_integrations.py` — First-run setup for Google OAuth, Notion, GitHub, Telegram
- `migrate_*.py` — Database migrations (run once per schema change)
- `update_stats.py` — Refresh stats line in CLAUDE.md
- `apply_live_with_review.py` — Live apply with human review
- `test_pipeline_live.py` — Live pipeline testing

### GitHub Actions (`.github/workflows/`)
Failover layer on top of local daemon + cron:
- `health-check.yml` — Every 10 min watchdog
- `telegram-poll.yml` — Every 5 min backup Telegram polling (8AM-10PM)
- `gmail-check.yml` — 1/3/5 PM backup Gmail recruiter checks
- `morning-briefing.yml` + `failover-briefing.yml` — Backup morning briefing
- `agent-readiness.yml` — Daily regression suite + PR checks

### Cron Schedule (15 distinct task types, 22 cron entries via `scripts/install_cron.py`)
3 AM profile sync | 7/1/7 PM full job scan (Reed + LinkedIn + Indeed) | 7:57 AM arXiv | 8:03 AM briefing | 9 AM follow-ups | 9/12/3 PM calendar | 10 AM/4:30 PM quick scan (same 3 platforms) | 1/3/5 PM Gmail | Hourly :15 optimize cycle | Sun 7 AM archive | Sun 8 PM weekly report | Sun 9 PM learning-maintenance | Mon 8:33 AM papers | Every 10 min health | Every 3 hrs daemon restart
> 2 AM overnight scan (Glassdoor + TotalJobs) removed 2026-05-04 — both platform scanners were deleted from `PLATFORM_SCANNERS` and the runner has no `job-scan-slow` handler. Added 2026-05-04: hourly `optimize` and Sunday-night `learning-maintenance` per `shared/optimization/CLAUDE.md`.

### Data Directory (`data/`)
62 SQLite databases, JSON configs, fonts, locks, and runtime artifacts. Key files:
- `profile_seed.json` — Profile DB seed data
- `skill_synonyms.json` — 36K+ skill synonym mappings
- `job_search_config.json` — Search configuration
- `fonts/` — ReportLab CV fonts (Lato, Raleway, Spectral)
- `locks/` — Mutex locks (apply, runner, scan_window)
- `applications/` — Per-application data snapshots

### Logs Directory (`logs/`)
24 log files, one per agent/subsystem. RotatingFileHandler: 5MB max, 5 backups (e.g., `jobpulse.log` → `jobpulse.log.5`).
Key logs: `daemon-stdout.log`/`daemon-stderr.log` (daemon output), `jobs.log` (application pipeline), `jobpulse.log` (main agent loop), `multi-listener.log` (Telegram bots), `health.log` (watchdog).
Config: `shared/logging_config.py`. All loggers via `get_logger(__name__)`.

### Dependencies (`requirements.txt`)
46 packages. Core: `langchain-core`, `langgraph`, `openai`, `python-dotenv`. Google: `google-api-python-client`, `google-auth-oauthlib`. Optional (commented): `playwright`, `dspy-ai`.
Setup: `python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`

## Stats
~173,500 LOC | 815 Python files | 56 databases | 4431 tests | 4 dashboards | 5 Telegram bots | 3 platforms
> Auto-updated by pre-commit hook. Manual: `python scripts/update_stats.py`

## Module Context (loaded when working in that directory)
- `jobpulse/CLAUDE.md` — Agents, dispatch, Telegram, extension engine, application orchestrator
- `patterns/CLAUDE.md` — 6 LangGraph orchestration patterns
- `mindgraph_app/CLAUDE.md` — Code Review Graph, risk scoring, Mermaid/DOT viz
- `shared/CLAUDE.md` — Cross-cutting utilities, NLP, fact-checker
- `shared/cognitive/CLAUDE.md` — 4-level cognitive engine: memory recall, single shot, reflexion, tree of thought
- `shared/memory_layer/CLAUDE.md` — 5-tier memory (STM/Episodic/Semantic/Procedural/Pattern) with 3 engines (SQLite/Qdrant/Neo4j)
- `shared/optimization/CLAUDE.md` — Continuous learning: signal bus, aggregator, tracker, policy, trajectories
- `shared/adversarial/CLAUDE.md` — Adversarial evaluation framework, red-teaming, robustness testing
- `shared/execution/CLAUDE.md` — Durable execution, event sourcing, checkpointing
- `shared/governance/CLAUDE.md` — Security, score validation, policy engine, API auth
- `.claude/rules/` — Domain-specific rules (jobs, jobpulse, jobpulse-agents, orchestration-agents, patterns, shared, testing, error-handling, pii-policy, seven-principles)
