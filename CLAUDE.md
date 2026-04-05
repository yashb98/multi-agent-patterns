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
python -m jobpulse.runner api-server    # Start HTTP API server for extension backend
python -m jobpulse.runner install-native-host  # Install Native Messaging host manifest for Chrome
python -m jobpulse.runner ralph-test   # Dry-run Ralph Loop self-healing test
```

## Code Intelligence (use for ALL code exploration)
MCP tools are 10-250x faster than Grep (1-28ms vs 350-750ms, pre-indexed SQLite).
- `find_symbol` — locate definition | `callers_of` / `callees_of` — call graph
- `impact_analysis` — blast radius | `risk_report` — high-risk functions
- `semantic_search` — find code AND docs by meaning (all .md files are indexed)
- `module_summary` — module overview | `recent_changes` — git log + graph
- Grep/Glob only for non-Python files or raw regex in configs
- **Never use Explore agents for code understanding** — they can't access MCP, burn 50-100k tokens

| System | What |
|--------|------|
| Orchestration | 4 LangGraph patterns (hierarchical, peer debate, dynamic swarm, enhanced swarm) |
| JobPulse | 10+ agents: Gmail, Calendar, GitHub, Notion, Budget, arXiv, Jobs — 24/7 via Telegram |
| Job Autopilot | 5-gate pre-screen → scan → hybrid skill extract → tailor CV → ATS score → apply/queue |
| Skill Graph | Nightly 3am GitHub sync → CodeGraph skill/project graph → recruiter-grade pre-screen |
| CodeGraph | AST-based code analysis, semantic search, fast retrieval, risk scoring, Mermaid/DOT visualization, review prioritization |
| NLP Classifier | 3-tier: regex → embeddings (5ms) → LLM fallback. 250+ examples, 41 intents |
| Chrome Extension Engine | MV3 extension — autonomous scanning + applying via Alarms API + HTTP API to Python backend |
| Form Intelligence | 5-tier: Pattern (free) → Semantic Cache (free) → Gemini Nano (free) → LLM ($0.002) → Vision ($0.01) |
| Application Orchestrator | Cookie dismiss → hybrid page detect → SSO/login/signup → Gmail verify → multi-page fill → submit |
| Ralph Loop | Self-healing retry: try → screenshot → diagnose → fix → retry. Learned fixes persist to SQLite |

## Critical Rules
- Update BOTH dispatcher.py AND swarm_dispatcher.py for new intents
- Always HTTPS for external APIs | Tests NEVER touch data/*.db — use tmp_path
- Never rewrite a file without checking `callers_of` (or Grep) for all function names used by other modules
- Log errors to `.claude/mistakes.md` | Full rules in `.claude/rules/`
- Use `semantic_search` to retrieve detailed rules/docs on demand — they're all indexed

## Rules

- Read @.claude/mistakes.md before every session
- Log errors immediately to `.claude/mistakes.md`
- Full constraints in `docs/rules.md`

## Code Intelligence (MCP tools — use FIRST for code exploration)
- Before spawning Explore agents or doing multi-step Grep/Glob searches, use CodeGraph MCP tools
- `find_symbol` → find any function/class definition (replaces grep for "where is X defined?")
- `callers_of` → who calls this function (replaces grep for "who uses X?")
- `callees_of` → what does this function call
- `impact_analysis` → blast radius of a change (replaces multi-file grep walks)
- `risk_report` → high-risk functions that need careful review
- `semantic_search` → find code by meaning, not just text
- `module_summary` → overview of a module's structure
- `recent_changes` → what changed recently
- When briefing subagents, ALWAYS include: "Use MCP tools (find_symbol, callers_of, callees_of, impact_analysis, semantic_search) before falling back to Grep/Glob"
- One MCP call replaces 5-15 Grep/Glob/Read calls and saves 10-50k tokens per exploration

## Do NOT (extracted from production incidents)
- NEVER update only one dispatcher — always update BOTH dispatcher.py AND swarm_dispatcher.py for new intents
- NEVER use http:// for external APIs — always HTTPS (arXiv HTTP→HTTPS redirect burned rate limit)
- NEVER let tests touch production DBs in data/*.db — always patch DB_PATH to tmp_path
- NEVER wait for Telegram replies in Claude Code sessions — poll the API directly
- NEVER use GitHub Events API for commit counting — use Commits API per-repo
- NEVER rewrite a file without first grepping for all function names used by other modules
- NEVER use == for date filtering on pushed_at — use >= or < comparisons
- NEVER assume Whisper output is lowercase — strip trailing punctuation before regex matching
- NEVER add Playwright back — all browser automation goes through the Chrome extension
- NEVER call adapter.fill_and_submit() directly — use _call_fill_and_submit() to handle sync/async bridging
- NEVER patch ATS_ACCOUNT_PASSWORD on jobpulse.account_manager — patch on jobpulse.config (local import inside create_account())

## 5 Telegram Bots

| Bot | Intents | Fallback |
|-----|---------|----------|
| Main | Tasks, calendar, briefing, remote control | Default |
| Budget | Expenses, income, savings, recurring | `TELEGRAM_BUDGET_BOT_TOKEN` |
| Research | arXiv, trending, CodeGraph | `TELEGRAM_RESEARCH_BOT_TOKEN` |
| Jobs | Scan, apply, reject, stats | `TELEGRAM_JOBS_BOT_TOKEN` |
| Alert | Send-only (gmail alerts, interviews) | `TELEGRAM_ALERT_BOT_TOKEN` |

All fall back to `TELEGRAM_BOT_TOKEN` if dedicated token not set.

## Env Vars

**Required:** `OPENAI_API_KEY`

**Telegram:** `TELEGRAM_BOT_TOKEN` `TELEGRAM_CHAT_ID` + optional per-bot tokens (BUDGET, RESEARCH, JOBS, ALERT)

**Platforms:** `SLACK_BOT_TOKEN` `DISCORD_BOT_TOKEN` `DISCORD_USER_ID`

**Notion:** `NOTION_API_KEY` `NOTION_TASKS_DB_ID` `NOTION_RESEARCH_DB_ID` `NOTION_PARENT_PAGE_ID` `NOTION_APPLICATIONS_DB_ID`

**Jobs:** `REED_API_KEY` `GITHUB_TOKEN` `JOB_AUTOPILOT_AUTO_SUBMIT=false` `JOB_AUTOPILOT_MAX_DAILY=10`

**AI:** `JOBPULSE_SWARM=true` `CONVERSATION_MODEL=gpt-5o-mini` `RLM_BACKEND=openai` `RLM_ROOT_MODEL=gpt-5o-mini` `RLM_MAX_BUDGET=0.10`

**Extension Engine:** `ATS_ACCOUNT_PASSWORD` (required for account creation) `GMAIL_VERIFY_TIMEOUT=120` `PAGE_STABLE_TIMEOUT_MS=3000` `API_SERVER_PORT=8790` (HTTP API for extension backend)

## Stats
~72,500 LOC | 287 Python files | 18 databases | 1683 tests | 4 dashboards | 5 Telegram bots | 3 platforms
> Auto-updated by pre-commit hook. Manual: `python scripts/update_stats.py`

## Chrome Extension Engine

Extension-only architecture. No Playwright. Chrome MV3 extension communicates via Native Messaging (bootstrap) + HTTP API (runtime).

**Architecture:**
```
Python Backend ←— Native Messaging (bootstrap) + HTTP API —→ Chrome Extension
  api_server.py        service_worker.js (Alarms API scheduler)
  ext_adapter.py       content.js (page scanner + form filler)
  ext_models.py        sidepanel.js (real-time dashboard)
  form_intelligence.py
  semantic_cache.py
  state_machines/      (platform-specific: greenhouse, lever, linkedin, indeed, workday, generic)
```

**How to use:**
1. `python -m jobpulse.runner install-native-host` — install Native Messaging host manifest
2. Load `extension/` as unpacked Chrome extension
3. `python -m jobpulse.runner api-server` — start HTTP API backend
4. Extension autonomously scans and applies via Alarms API scheduler

**5-Tier Form Intelligence** (`form_intelligence.py`):
| Tier | Source | Cost | Latency |
|------|--------|------|---------|
| 1 | Pattern match (regex) | Free | <1ms |
| 2 | Semantic cache (embeddings) | Free | ~5ms |
| 3 | Gemini Nano (Chrome AI) | Free | ~500ms |
| 4 | LLM API (GPT-4.1-mini) | $0.002 | ~1s |
| 5 | Vision (screenshot → GPT-4o-mini) | $0.01 | ~3s |

**Key files:**
- `extension/manifest.json` — MV3 manifest (permissions, service worker, content scripts)
- `extension/service_worker.js` — Alarms API scheduler, Native Messaging bootstrap, HTTP API client
- `extension/content.js` — DOM scanner, form filler, Gemini Nano local inference
- `extension/sidepanel.html` — Real-time application dashboard
- `jobpulse/api_server.py` — HTTP API server for extension backend
- `jobpulse/ext_adapter.py` — BaseATSAdapter implementation routing through extension
- `jobpulse/ext_models.py` — Pydantic models (ExtCommand, PageSnapshot, FieldAnswer)
- `jobpulse/form_intelligence.py` — 5-tier answer resolver
- `jobpulse/semantic_cache.py` — Embedding-based answer cache (sentence-transformers + SQLite)
- `jobpulse/vision_tier.py` — Screenshot-based field analysis via GPT-4o-mini
- `jobpulse/state_machines/` — Platform state machines (greenhouse, lever, linkedin, etc.)
- `jobpulse/application_orchestrator.py` — Full application lifecycle orchestrator
- `jobpulse/page_analyzer.py` — Hybrid DOM+Vision page type detection
- `jobpulse/cookie_dismisser.py` — Cookie banner detection and dismissal
- `jobpulse/account_manager.py` — SQLite credential store per ATS domain
- `jobpulse/gmail_verify.py` — Gmail verification email polling + link extraction
- `jobpulse/navigation_learner.py` — Per-domain navigation sequence learning (SQLite)
- `jobpulse/sso_handler.py` — SSO button detection and click (Google/LinkedIn/Microsoft/Apple)

**Application Orchestrator** (`application_orchestrator.py`):
Manages the full external application lifecycle:
1. Dismiss cookie banners before any detection
2. Hybrid page detection: DOM analysis (free) + Vision LLM fallback ($0.003 when unsure)
3. SSO detection: "Sign in with Google/LinkedIn" → clicks SSO, skips account creation
4. Account creation: `ATS_ACCOUNT_PASSWORD` env var, stores credentials per domain in SQLite
5. Gmail verification: exponential polling (1s→2s→4s→...→32s), extracts verify links from HTML
6. Navigation learning: saves successful sequences per domain, replays on repeat visits (zero cost)
7. Multi-page form filling: state machine with Next button detection, progress tracking, stuck detection

**Ralph Loop** (`ralph_loop/`): Self-healing retry wrapper. On failure: screenshot → diagnose (vision or heuristic) → save fix to SQLite → retry. Max 5 iterations. Works with extension adapter.

## Docs

- @.claude/mistakes.md — **READ FIRST**
- `docs/rules.md` — Constraints, rate limits, anti-detection
- `docs/agents.md` — All agents, NLP, budget, salary, A/B testing, Telegram commands, API endpoints
- `docs/skills.md` — GRPO, persona evolution, RLM, prompt optimization
- `docs/subagents.md` — Dynamic agent factory
- `docs/hooks.md` — Process trails, memory, logging, export

## Module Context (loaded when working in that directory)
- `jobpulse/CLAUDE.md` — Agents, dispatch, Telegram, extension engine, application orchestrator
- `patterns/CLAUDE.md` — 4 LangGraph orchestration patterns
- `mindgraph_app/CLAUDE.md` — Code Review Graph, risk scoring, Mermaid/DOT viz
- `shared/CLAUDE.md` — Cross-cutting utilities, NLP, fact-checker
- `.claude/rules/` — Domain-specific rules (jobs, testing, patterns, shared, frontend, error-handling)
