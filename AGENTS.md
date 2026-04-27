# AGENTS.md — Multi-Agent Orchestration + JobPulse + Knowledge MindGraph

> This file is the source of truth for AI coding agents working on this project. Read it first. Every directory may contain its own `CLAUDE.md` with deeper module context — check those when working in a specific area.

---

## 1. Project Overview

This is a production autonomous agent system combining three integrated subsystems:

1. **Orchestration Engine** (`patterns/`) — Six LangGraph multi-agent patterns (Hierarchical, Peer Debate, Dynamic Swarm, Enhanced Swarm, Plan-and-Execute, Map-Reduce) with mandatory fact-checking and quality gates.
2. **JobPulse Daily Automation** (`jobpulse/`) — 15+ autonomous agents running 24/7 via macOS daemon / cron / GitHub Actions. Agents include Gmail classifier, Calendar, GitHub, arXiv digest, Notion tasks, Budget tracker, Salary tracker, Briefing, Job Autopilot (4-gate pre-screen + ATS apply), Skill Graph Sync, and more.
3. **Knowledge MindGraph** (`mindgraph_app/`) — LLM-based entity/relation extraction, SQLite knowledge graph, GraphRAG retrieval, and a Three.js 3D visualization frontend.

Remote control is provided via Telegram (5 dedicated bots), Slack, and Discord adapters. The system features NLP 3-tier intent classification, multi-provider LLM fallback (OpenAI → Anthropic → Gemini), circuit breakers, cost enforcement, and a 3-engine memory layer (SQLite + Qdrant + Neo4j).

---

## 2. Technology Stack

| Layer | Technology |
|-------|------------|
| **Language** | Python 3.12+ (primary), JavaScript/React (frontend) |
| **Agent Framework** | LangGraph, LangChain, OpenAI Agents SDK |
| **LLM Providers** | OpenAI (primary), Anthropic, Google Gemini, Ollama (local fallback) |
| **Frontend** | React 19, Vite 6, Three.js, `@react-three/fiber`, `@react-three/drei` |
| **Backend API** | FastAPI + Uvicorn (MindGraph) |
| **Databases** | SQLite (primary truth), Qdrant (vectors), Neo4j (graph) |
| **Browser Automation** | Playwright (CDP mode for ATS adapters) |
| **PDF Generation** | ReportLab (CV / cover letter) |
| **Task Orchestration** | macOS launchd plist, cron, GitHub Actions |
| **Search** | SearXNG self-hosted meta-search (+ optional Tor proxy) |
| **Testing** | pytest, pytest-cov, testcontainers[neo4j] |
| **Deployment** | Vercel (frontend), local daemon (backend) |

---

## 3. Project Structure & Module Divisions

```
├── jobpulse/                    # Daily automation agents + CLI runner
│   ├── runner.py                # Main CLI entry point (daemon, briefing, apply, etc.)
│   ├── config.py                # Centralized env-var loading
│   ├── dispatcher.py            # Flat intent dispatcher
│   ├── swarm_dispatcher.py      # Enhanced Swarm dispatcher (GRPO + personas)
│   ├── command_router.py        # NLP 3-tier classifier → intent routing
│   ├── telegram_agent.py        # Telegram long-polling daemon
│   ├── platforms/               # Slack, Discord, Telegram adapters
│   ├── application_orchestrator_pkg/  # Playwright-based external apply engine
│   ├── ats_adapters/            # Platform strategies (LinkedIn, Greenhouse, Workday, etc.)
│   │   ├── strategy.py          # BasePlatformStrategy ABC + registry + get_strategy()
│   │   ├── linkedin.py          # Container hint: .jobs-easy-apply-modal, field range 3-10
│   │   ├── greenhouse.py        # Container hint: #application, field range 3-15
│   │   ├── workday.py           # Field range 3-20, hydration wait 10s
│   │   └── generic.py           # Fallback: no hint, field range 1-30
│   ├── cv_templates/            # ReportLab PDF generators
│   ├── form_engine/             # Adaptive form-filling pipeline
│   │   ├── field_scanner.py     # 3-tier container resolution + scan validation gate
│   │   ├── field_mapper.py      # Deterministic seed_mapping + LLM fallback + semantic option matching
│   │   ├── field_resolver.py    # Label→profile_key lookup tables + screening prompt builders
│   │   ├── semantic_matcher.py  # 5-tier option matching (exact→alias→numeric→token→substring)
│   │   ├── engine.py            # Main fill orchestrator
│   │   └── text/select/radio/checkbox/file_filler.py  # Widget-specific fillers
│   └── <agent>.py               # gmail, calendar, github, arxiv, budget, etc.
├── patterns/                    # 6 LangGraph orchestration patterns
│   ├── enhanced_swarm.py        # Production pattern (used by JobPulse)
│   ├── hierarchical.py
│   ├── peer_debate.py
│   ├── dynamic_swarm.py
│   ├── plan_and_execute.py
│   └── map_reduce.py
├── shared/                      # Cross-cutting utilities (ONE-WAY dependency)
│   ├── agents.py                # get_llm(), smart_llm_call(), agent nodes
│   ├── code_intelligence/       # AST-based code graph + MCP tools
│   ├── code_intel_cli.py        # CLI wrapper for code graph queries
│   ├── code_intel_mcp.py        # MCP server exposing 20+ code tools
│   ├── memory_layer/            # 3-engine memory (SQLite/Qdrant/Neo4j)
│   ├── cognitive/               # 4-level cognitive reasoning engine
│   ├── optimization/            # Continuous learning / signal bus
│   ├── fact_checker.py          # 3-level multi-source fact verification
│   ├── nlp_classifier.py        # Intent classification pipeline
│   ├── cost_tracker.py          # Per-call cost estimation
│   ├── llm_retry.py             # Exponential backoff + circuit breaker
│   ├── logging_config.py        # Structured logging with run IDs
│   └── …
├── mindgraph_app/               # Knowledge graph + FastAPI + 3D viz
│   ├── main.py                  # Uvicorn entry point
│   ├── api.py                   # FastAPI routes
│   ├── extractor.py             # LLM entity/relation extraction
│   ├── retriever.py             # GraphRAG retrieval
│   ├── storage.py               # SQLite KG storage
│   └── codegraph_api.py         # CodeGraph visualization routes
├── frontend/                    # React + Three.js 3D frontend
│   ├── src/App.jsx
│   ├── package.json
│   └── vite.config.js
├── tests/                       # pytest suite mirroring source tree
│   ├── conftest.py              # Shared fixtures, JOBPULSE_TEST_MODE=1
│   ├── jobpulse/
│   ├── patterns/
│   ├── shared/
│   └── papers/
├── scripts/                     # Setup, migration, benchmark, daemon install
├── docs/                        # Feature design docs, architecture, rules
├── data/                        # SQLite DBs, JSON configs, exports
├── logs/                        # Agent logs (.log + rotated .log.1)
├── static/                      # Dashboard HTML (health, analytics, processes)
└── .github/workflows/           # GitHub Actions (health check, morning briefing, etc.)
```

**Dependency Rule:** `shared/` NEVER imports from `jobpulse/`, `patterns/`, or `mindgraph_app/`. All other modules may import from `shared/`.

---

## 4. Build, Run & Test Commands

### Installation
```bash
pip install -r requirements.txt
cp .env.example .env          # Fill in API keys
python scripts/setup_integrations.py
```

### Running the System
```bash
# Telegram daemon (single bot)
python -m jobpulse.runner daemon

# All 5 Telegram bots
python -m jobpulse.runner multi-bot

# Multi-platform listeners (Telegram + Slack + Discord)
python -m jobpulse.runner multi

# Morning briefing (one-shot)
python -m jobpulse.runner briefing

# Job scan + apply pipeline (one-shot or cron)
python -m jobpulse.runner job-scan
python -m jobpulse.runner job-apply-next [N] [YYYY-MM-DD]

# MindGraph API server
python -m mindgraph_app.main          # http://localhost:8000

# Three.js 3D frontend
cd frontend && npm install && npm run dev   # http://localhost:3000
```

### Testing
```bash
# Full suite
python -m pytest tests/ -v

# With coverage
python -m pytest tests/ -v --cov

# Filter by keyword
python -m pytest tests/ -v -k "budget"
python -m pytest tests/ -v -k "dispatch"
python -m pytest tests/ -v -k "fact"

# JobPulse only
python -m pytest tests/ -v -k "jobpulse"

# Live API tests (skipped by default)
python -m pytest tests/ -v -m live
```

### Comparing Orchestration Patterns
```bash
python run_all.py "Your topic here"
```

### macOS Daemon (auto-start on login)
```bash
./scripts/install_daemon.sh install
```

### Docker Services (optional)
```bash
# Memory stack (Qdrant + Neo4j)
docker compose -f docker-compose.memory.yml up -d

# SearXNG meta-search
docker compose -f docker-compose.searxng.yml up -d
```

---

## 5. Code Style & Engineering Principles

### Mandatory 7 Principles
Every feature, function, and file MUST satisfy all 7 principles. Full checklist: `.claude/rules/seven-principles.md`.

1. **System Design** — Clear boundaries, no import-time side effects, no duplicated logic, functions under 100 lines.
2. **Tool & Contract Design** — Typed interfaces, centralized LLM factories (`get_llm()` / `smart_llm_call()`), consistent return types (TypedDict/dataclass), no bare `dict` returns.
3. **Retrieval Engineering** — Connection pooling, no N+1, cached lookups, lazy loading, parameterized SQL.
4. **Reliability Engineering** — `try/finally` for Playwright, `with` for SQLite, retry + timeout on LLM calls, bounded loops, circuit breakers.
5. **Security & Safety** — No PII in source, no string interpolation in `page.evaluate()`, SSRF protection, parameterized SQL, `0o600` on credential files, prompt injection defense.
6. **Evaluation & Observability** — Cost tracking on all LLM calls, decision logging, structured errors, no silent failures.
7. **Product Thinking** — Dry-run-first, `confirm_application()` on successful submits, user-actionable errors, OS-aware paths.

### Additional Style Rules
- Match existing style exactly. Do not "improve" adjacent code, comments, or formatting.
- Remove imports/variables/functions that **your** changes made unused — do not touch pre-existing dead code.
- State assumptions explicitly. If multiple interpretations exist, present them.
- Minimum code that solves the problem. Nothing speculative.
- Never instantiate `ChatOpenAI`, `OpenAI`, or `litellm.completion()` directly — always use `get_llm()` from `shared/agents.py`.
- All new shared utilities go in `shared/`, never duplicated across systems.

### Adaptive Form Engine Rules
- **Container resolution** is 3-tier: Learned (FormExperienceDB) → Auto-detect (common ancestor JS) → Strategy hint. Never scan full page without trying scoping first.
- **Timing** is adaptive: `FormExperienceDB.get_timing()` returns measured values, `_get_adaptive_page_delay()` derives delays. Set `FAST_FILL=true` for zero delays in Claude Code sessions.
- **Option matching** uses `semantic_option_match()` from `form_engine/semantic_matcher.py` — never hardcode option text. 5-tier cascade: exact → canonical aliases → numeric range → token overlap → substring.
- **Fill failures** are classified via `_classify_fill_failure()`: no_field, blocked, wrong_value, readonly, unknown. Each class triggers a different recovery path.
- **Strategy classes** in `ats_adapters/` define `form_container_hint()`, `expected_field_range()`, `screening_defaults()`, `normalize_label()`. Add a new strategy when a platform's form structure is known.

---

## 6. Testing Strategy

### Database Isolation (CRITICAL)
Tests **MUST NEVER** touch production databases in `data/*.db`. Always use `tmp_path` fixtures or monkeypatch DB paths.

- `conftest.py` sets `JOBPULSE_TEST_MODE=1` before any imports touch storage modules.
- Use the `in_memory_db` fixture for SQLite tests.
- Use `mock_openai`, `mock_telegram`, `mock_event_logger`, `mock_process_trail` fixtures to prevent external side effects.

### Test Organization
- Tests mirror source structure: `tests/jobpulse/` ↔ `jobpulse/`, `tests/patterns/` ↔ `patterns/`, `tests/shared/` ↔ `shared/`.
- Use `@pytest.mark.slow` for integration tests.
- Use `@pytest.mark.live` for tests requiring live API access (skipped unless `-m live` is passed).

### Goal-Driven Testing
- Transform "fix the bug" → write a test that reproduces it, then make it pass.
- Transform "add validation" → write tests for invalid inputs, then make them pass.
- Transform "refactor X" → ensure tests pass before and after.
- Do not add error handling or test coverage for scenarios that can't happen.

---

## 7. Security Considerations

- **Credential Storage:** API keys live in `.env` (gitignored). ATS account passwords are encrypted at rest using Fernet (stored in SQLite, never plaintext).
- **File Permissions:** Run `scripts/fix_file_perms.sh` to ensure credential files are `0o600`.
- **Prompt Injection:** All LLM prompts use boundary markers + `shared/prompt_defense.py` to strip injection-relevant tags including `agent_output`.
- **SSRF Protection:** `mindgraph_app/api.py` validates URL scheme + host before fetching external resources.
- **Command Injection:** Telegram remote shell uses an allowlist (`_is_allowed()` in dispatcher). `rm -rf`, `sudo`, `shutdown` are auto-blocked.
- **Playwright Safety:** Never pass user input directly into `page.evaluate()` JavaScript — use Playwright's typed argument passing.
- **API Defaults:** `get_llm()` sets `max_tokens=4096` to prevent unbounded output (OWASP LLM10).
- **ToolExecutor:** Sandboxed with deny-by-default approval and sliding-window rate limits.

---

## 8. Database Safety

- Production DBs live in `data/*.db` — **NEVER modify or touch these directly**.
- All SQLite connections MUST use WAL mode (prevents `SQLITE_BUSY` with concurrent bots).
- All test fixtures must use `tmp_path` or monkeypatch DB paths.
- Never query SQLite/Qdrant/Neo4j directly — always go through `MemoryManager` (same principle as `get_llm()` for LLM calls).

---

## 9. Code Exploration — Use CLI, Not Grep/Glob

You do NOT have access to MCP tools directly. Instead, use the Code Intelligence CLI via Bash for all code exploration:

```bash
python shared/code_intel_cli.py find_symbol <name>        # Locate function/class definition
python shared/code_intel_cli.py callers_of <name>          # Who calls this function?
python shared/code_intel_cli.py callees_of <name>          # What does this function call?
python shared/code_intel_cli.py impact_analysis <file>     # Blast radius of a change
python shared/code_intel_cli.py risk_report [top_n]        # High-risk functions
python shared/code_intel_cli.py module_summary <file>      # Module overview
python shared/code_intel_cli.py semantic_search "<query>"  # Find code by meaning (~4s)
python shared/code_intel_cli.py dead_code [top_n]          # Unreachable functions
python shared/code_intel_cli.py recent_changes [n]         # Git log + graph context
```

**Rules:**
- ALWAYS use `python shared/code_intel_cli.py` via Bash instead of Grep/Glob for Python code queries.
- These queries take ~50ms (vs 350–750ms for grep) and return richer data (risk scores, call graph context).
- Use Grep/Glob ONLY for non-Python files, raw regex in configs, or when the CLI doesn't cover your query.
- NEVER use `python -m shared.code_intel_cli` (triggers heavy imports, 60× slower).

---

## 10. Memory System

Before agent execution, call `memory_manager.get_context_for_agent(agent_name, topic, domain)` to get relevant context. After execution, call `memory_manager.store_memory(tier, domain, content, score)` for any learned facts, procedures, or notable outcomes.

Never query SQLite/Qdrant/Neo4j directly — always go through `MemoryManager`. Same principle as `get_llm()` for LLM calls.

The memory layer lives in `shared/memory_layer/`:
- `_sqlite_store.py` — Source of truth CRUD.
- `_qdrant_store.py` — Filtered HNSW vector search.
- `_neo4j_store.py` — Graph traversal + signals.
- `_manager.py` — `MemoryManager` facade (single entry point).

---

## 11. Cognitive Reasoning (opt-in)

Use `CognitiveEngine.think(task, domain, stakes)` for tasks that benefit from self-improving reasoning. Call `flush()` (or `flush_sync()`) at end of run to persist strategy templates.

Import: `from shared.cognitive import CognitiveEngine`

Kill switch: `COGNITIVE_ENABLED=false` disables everywhere, falling back to direct LLM.

---

## 12. Dual Dispatcher Rule

When investigating intents or dispatch logic, check **BOTH** `jobpulse/dispatcher.py` AND `jobpulse/swarm_dispatcher.py`. New intents MUST be added to both files.

`JOBPULSE_SWARM=true` (default) uses Enhanced Swarm. `JOBPULSE_SWARM=false` uses flat dispatcher.

---

## 13. Environment & Configuration

Key env vars (see `.env.example` for full list):

| Variable | Purpose |
|----------|---------|
| `OPENAI_API_KEY` | Required unless using local LLM |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | Main Telegram bot |
| `TELEGRAM_*_BOT_TOKEN` / `TELEGRAM_*_CHAT_ID` | Dedicated bots (Budget, Research, Alert, Jobs) — optional, fallback to main |
| `NOTION_API_KEY` / `NOTION_TASKS_DB_ID` / `NOTION_RESEARCH_DB_ID` | Notion integration |
| `GOOGLE_OAUTH_CLIENT_ID` / `GOOGLE_OAUTH_CLIENT_SECRET` | Gmail + Calendar OAuth2 |
| `GITHUB_TOKEN` | GitHub agent + profile sync |
| `CONVERSATION_MODEL` | Chat model (default: `gpt-5-mini`) |
| `LLM_PROVIDER_FALLBACK` | Comma-separated chain: `openai,anthropic,gemini` |
| `JOBPULSE_SWARM` | `true` = Enhanced Swarm, `false` = flat dispatcher |
| `JOB_AUTOPILOT_AUTO_SUBMIT` | `false` = manual review before applying |
| `ATS_ACCOUNT_PASSWORD` | Fernet-encrypted ATS login password |
| `COGNITIVE_ENABLED` | `true` / `false` kill switch |
| `OPTIMIZATION_ENABLED` | `true` / `false` kill switch |
| `FAST_FILL` | `true` = zero page delays (Claude Code sessions) |

Config is centralized in `jobpulse/config.py`. Never read `os.getenv()` outside `config.py`.

---

## 14. Deployment & Runtime Architecture

### Local Daemon (Primary)
- macOS daemon via `scripts/com.jobpulse.daemon.plist` or `./scripts/install_daemon.sh install`.
- Cron schedules defined in `scripts/install_cron.py`.
- Logs rotate to `logs/jobpulse.log.1`.

### GitHub Actions (Backup / Watchdog)
- `.github/workflows/morning-briefing.yml` — Fallback nudge if local daemon misses the 8:03am briefing.
- `.github/workflows/health-check.yml` — Watchdog every 10 minutes; alerts via Telegram if bot is unreachable.
- `.github/workflows/gmail-check.yml` — Periodic Gmail check backup.
- `.github/workflows/failover-briefing.yml` — Failover briefing with degraded content.

### Dashboards
- `http://localhost:8000/health.html` — Daemon status, agent success rates, API rate limits, errors, data export.
- `http://localhost:8000/analytics.html` — GRPO scores, persona drift, cost estimates, daily trends (Chart.js).
- `http://localhost:8000/processes.html` — Agent process trail viewer (step-by-step audit).
- `http://localhost:3000` — Three.js 3D neural/galaxy visualization (primary frontend).

### Vercel
- Frontend deploys to Vercel via `vercel --prod`.
- Build output is `static/3d/` (configured in `frontend/vite.config.js`).

---

## 15. Module-Specific Context

When working in a subdirectory, read its local `CLAUDE.md` for deeper guidance:

| Directory | Context Doc |
|-----------|-------------|
| `jobpulse/` | `jobpulse/CLAUDE.md` — Agents, dispatch, Telegram, extension engine, application orchestrator |
| `patterns/` | `patterns/CLAUDE.md` — 6 LangGraph orchestration patterns |
| `mindgraph_app/` | `mindgraph_app/CLAUDE.md` — Code Review Graph, risk scoring, Mermaid/DOT viz |
| `shared/` | `shared/CLAUDE.md` — Cross-cutting utilities, NLP, fact-checker |
| `shared/cognitive/` | `shared/cognitive/CLAUDE.md` — 4-level cognitive engine |
| `shared/memory_layer/` | `shared/memory_layer/CLAUDE.md` — 3-engine memory architecture |
| `shared/optimization/` | `shared/optimization/CLAUDE.md` — Continuous learning signal bus |

Domain-specific rules also live in `.claude/rules/`:
- `seven-principles.md` — Mandatory engineering checklist
- `jobs.md` — Job autopilot rules
- `jobpulse.md` / `jobpulse-agents.md` — Agent-specific rules
- `patterns.md` / `orchestration-agents.md` — Pattern rules
- `shared.md` — Shared module rules
- `testing.md` — Testing rules
- `error-handling.md` — Error handling conventions
- `frontend.md` — Frontend rules

Use `python shared/code_intel_cli.py semantic_search "<query>"` to retrieve detailed rules on demand — all `.md` files are indexed with embeddings.
