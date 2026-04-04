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
python -m jobpulse.runner ext-bridge   # Start Chrome extension WebSocket bridge (ws://localhost:8765)
python -m jobpulse.runner ralph-test   # Dry-run Ralph Loop self-healing test
```

## Architecture

| System | What |
|--------|------|
| Orchestration | 4 LangGraph patterns (hierarchical, peer debate, dynamic swarm, enhanced swarm) |
| JobPulse | 10+ agents: Gmail, Calendar, GitHub, Notion, Budget, arXiv, Jobs — 24/7 via Telegram |
| Job Autopilot | 5-gate pre-screen → scan → hybrid skill extract → tailor CV → ATS score → apply/queue |
| Skill Graph | Nightly 3am GitHub sync → MindGraph skill/project graph → recruiter-grade pre-screen |
| MindGraph | Entity extraction, GraphRAG retrieval, Three.js 3D visualization |
| NLP Classifier | 3-tier: regex → embeddings (5ms) → LLM fallback. 250+ examples, 41 intents |
| Chrome Extension Engine | MV3 extension + WebSocket bridge. Replaces Playwright when `APPLICATION_ENGINE=extension` |
| Form Intelligence | 5-tier: Pattern (free) → Semantic Cache (free) → Gemini Nano (free) → LLM ($0.002) → Vision ($0.01) |
| Application Orchestrator | Cookie dismiss → hybrid page detect → SSO/login/signup → Gmail verify → multi-page fill → submit |
| Ralph Loop | Self-healing retry: try → screenshot → diagnose → fix → retry. Learned fixes persist to SQLite |

**Dispatch mode:** Enhanced Swarm (`JOBPULSE_SWARM=false` to revert to flat)

## Rules

- Read @.claude/mistakes.md before every session
- Log errors immediately to `.claude/mistakes.md`
- Full constraints in `docs/rules.md`

## Do NOT (extracted from production incidents)
- NEVER update only one dispatcher — always update BOTH dispatcher.py AND swarm_dispatcher.py for new intents
- NEVER use http:// for external APIs — always HTTPS (arXiv HTTP→HTTPS redirect burned rate limit)
- NEVER let tests touch production DBs in data/*.db — always patch DB_PATH to tmp_path
- NEVER wait for Telegram replies in Claude Code sessions — poll the API directly
- NEVER use GitHub Events API for commit counting — use Commits API per-repo
- NEVER rewrite a file without first grepping for all function names used by other modules
- NEVER use == for date filtering on pushed_at — use >= or < comparisons
- NEVER assume Whisper output is lowercase — strip trailing punctuation before regex matching
- NEVER call adapter.fill_and_submit() directly — use _call_fill_and_submit() to handle sync/async bridging
- NEVER patch APPLICATION_ENGINE on the ats_adapters module — patch on jobpulse.config (it's imported inside the function)
- NEVER patch ATS_ACCOUNT_PASSWORD on jobpulse.account_manager — patch on jobpulse.config (local import inside create_account())

## 5 Telegram Bots

| Bot | Intents | Fallback |
|-----|---------|----------|
| Main | Tasks, calendar, briefing, remote control | Default |
| Budget | Expenses, income, savings, recurring | `TELEGRAM_BUDGET_BOT_TOKEN` |
| Research | arXiv, trending, MindGraph | `TELEGRAM_RESEARCH_BOT_TOKEN` |
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

**Extension Engine:** `APPLICATION_ENGINE=extension` (default: `playwright`) `EXT_BRIDGE_HOST=localhost` `EXT_BRIDGE_PORT=8765` `ATS_ACCOUNT_PASSWORD` (required for account creation) `GMAIL_VERIFY_TIMEOUT=120` `PAGE_STABLE_TIMEOUT_MS=3000`

## Stats

~61,500 LOC | 273 Python files | 15 databases | 1196 tests | 4 dashboards | 5 Telegram bots | 3 platforms

> Auto-updated by pre-commit hook. Manual: `python scripts/update_stats.py`

## Chrome Extension Engine

Replaces Playwright-based automation with a Chrome MV3 extension communicating via WebSocket.

**Architecture:**
```
Python Backend ←— WebSocket (ws://localhost:8765) —→ Chrome Extension
  ext_bridge.py        service_worker.js
  ext_adapter.py       content.js (page scanner + form filler)
  ext_models.py        sidepanel.js (real-time dashboard)
  form_intelligence.py
  semantic_cache.py
  state_machines/      (platform-specific: greenhouse, lever, linkedin, indeed, workday, generic)
```

**How to use:**
1. `python -m jobpulse.runner ext-bridge` — start WebSocket bridge
2. Load `extension/` as unpacked Chrome extension
3. `export APPLICATION_ENGINE=extension` — route applications through extension
4. All existing commands (job-scan, ralph-test) now use the extension adapter

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
- `extension/service_worker.js` — WebSocket client, state machine executor, 20s heartbeat
- `extension/content.js` — DOM scanner, form filler, Gemini Nano local inference
- `extension/sidepanel.html` — Real-time application dashboard
- `jobpulse/ext_bridge.py` — WebSocket server, command dispatch, connection lifecycle
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

**Ralph Loop** (`ralph_loop/`): Self-healing retry wrapper. On failure: screenshot → diagnose (vision or heuristic) → save fix to SQLite → retry. Max 5 iterations. Works with both Playwright and extension adapters.

## Docs

- @.claude/mistakes.md — **READ FIRST**
- `docs/rules.md` — Constraints, rate limits, anti-detection
- `docs/agents.md` — All agents, NLP, budget, salary, A/B testing, Telegram commands, API endpoints
- `docs/skills.md` — GRPO, persona evolution, RLM, prompt optimization
- `docs/subagents.md` — Dynamic agent factory
- `docs/hooks.md` — Process trails, memory, logging, export

## Module Context (loaded when working in that directory)
- `jobpulse/CLAUDE.md` — JobPulse agents, dispatch, Telegram
- `patterns/CLAUDE.md` — 4 LangGraph orchestration patterns
- `mindgraph_app/CLAUDE.md` — Knowledge graph, GraphRAG, 3D viz
- `shared/CLAUDE.md` — Cross-cutting utilities, NLP, fact-checker
- `extension/` — Chrome MV3 extension (service worker, content script, side panel, state machines)
- `jobpulse/state_machines/` — Platform state machines for extension engine
- `jobpulse/ralph_loop/` — Self-healing applicator (diagnoser, pattern store, CLI output)
