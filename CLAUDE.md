# Multi-Agent Orchestration + JobPulse Automation + Knowledge MindGraph

LangGraph + OpenAI + Enhanced Swarm + RLM. Production autonomous agent system.

## Commands

```bash
pip install -r requirements.txt
python run_all.py "topic"                 # Compare all 4 patterns
python -m jobpulse.runner daemon          # Start Telegram daemon (Enhanced Swarm)
python -m jobpulse.runner briefing        # Morning digest
python -m jobpulse.runner gmail           # Check recruiter emails
python -m jobpulse.runner calendar        # Today + tomorrow events
python -m jobpulse.runner weekly-report   # 7-day summary across all agents
python -m jobpulse.runner export          # Full data backup (tar.gz)
python -m jobpulse.runner webhook         # Start webhook server (port 8080)
python -m jobpulse.runner slack           # Start Slack listener
python -m jobpulse.runner discord         # Start Discord listener
python -m jobpulse.runner multi           # Start all platform listeners
./scripts/install_daemon.sh install       # Auto-start daemon on login
```

## Architecture (3 Systems)

**1. Orchestration Engine** — 4 LangGraph patterns: hierarchical, peer debate, dynamic swarm, enhanced swarm
**2. JobPulse Automation** — Gmail, Calendar, GitHub, Notion, Budget, Telegram agents running 24/7
**3. Knowledge MindGraph** — Entity extraction, GraphRAG retrieval, D3.js + Three.js visualization

**Current dispatch mode:** Enhanced Swarm (set `JOBPULSE_SWARM=false` in .env to revert to flat)

## Operational Principles

IMPORTANT: Non-negotiable. Violating any = log to `.claude/mistakes.md`.

1. **Memory before action** — Search memory/patterns before any task. Score > 0.7 = reuse.
2. **ORCHESTRATOR, not EXECUTOR** — Claude coordinates. Agents do the work.
3. **Enhanced Swarm for production** — Task analyzer → dynamic routing → GRPO sampling → persona evolution → RLM synthesis.
4. **Learn after success** — Store patterns scoring >= 7.0 to experience memory. Future runs benefit.
5. **3-Tier routing** — Cached → lightweight → full agent. Skip tiers only when lower fail.
6. **Commands return instantly** — Create records only. Never wait.

## Self-Correction Protocol

1. **Before every session**: Read @.claude/mistakes.md
2. **On error**: IMMEDIATELY append to `.claude/mistakes.md`
3. **Before committing**: Re-check mistakes log
4. **On user correction**: Log it, even if minor

## Telegram Commands

| You type | What happens |
|----------|-------------|
| "show tasks" | Notion → today's checklist |
| list of items | Notion → creates tasks |
| "mark X done" | Notion → fuzzy match + complete |
| "calendar" | Calendar → today + tomorrow |
| "check emails" | Gmail → scan + classify + alert |
| "commits" | GitHub → yesterday's activity |
| "trending" | GitHub → hot repos |
| "briefing" | Enhanced Swarm → 6-agent collect → RLM synthesis |
| "spent 15 on lunch" | Budget → classify → SQLite → Notion sync |
| "budget" | Budget → weekly summary |
| "weekly report" | All agents → 7-day summary |
| "export" | Full data backup (databases, personas, experiences) |
| voice message | Whisper transcription → intent classification → agent |
| "help" | Lists all commands |

## Env Vars

- `OPENAI_API_KEY` (required)
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
- `SLACK_BOT_TOKEN`, `SLACK_CHANNEL_ID`
- `DISCORD_BOT_TOKEN`, `DISCORD_CHANNEL_ID`, `DISCORD_USER_ID`
- `NOTION_API_KEY`, `NOTION_TASKS_DB_ID`, `NOTION_RESEARCH_DB_ID`
- `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`
- `JOBPULSE_SWARM=true` — Enhanced Swarm dispatch (false = flat)
- `RLM_BACKEND=openai`, `RLM_ROOT_MODEL=gpt-4o-mini`, `RLM_MAX_BUDGET=0.10`

## Dashboards

- `/health.html` — daemon status, agent success rates, API rate limits, errors, data export
- `/analytics.html` — usage trends, intent distribution, response times
- `/processes.html` — agent run timelines, step-by-step audit trails

## Logging

All modules use `shared/logging_config.py` — structured logging with per-module loggers via `get_logger(__name__)`. Logs written to `logs/` directory.

## Documentation

- @.claude/mistakes.md — MUST READ FIRST
- @docs/rules.md — Constraints, convergence, pattern selection
- @docs/agents.md — All agents (orchestration + JobPulse)
- @docs/skills.md — GRPO, persona evolution, RLM, prompt optimization
- @docs/subagents.md — Dynamic agent factory, templates
- @docs/hooks.md — Process trails, memory injection, audit logging
