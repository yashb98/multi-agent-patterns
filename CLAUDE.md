# Multi-Agent Orchestration + JobPulse Automation + Knowledge MindGraph

LangGraph + OpenAI + Enhanced Swarm + RLM. Production autonomous agent system.

## Commands

```bash
pip install -r requirements.txt
python run_all.py "topic"                 # Compare all 4 patterns
python -m jobpulse.runner daemon          # Start Telegram daemon (Enhanced Swarm)
python -m jobpulse.runner multi-bot       # Start all 3 Telegram bots (Main + Budget + Research)
python -m jobpulse.runner multi           # Start all platforms (Telegram multi-bot + Discord + Slack)
python -m jobpulse.runner stop            # Stop all running daemon processes
python -m jobpulse.runner restart         # Stop + restart (default: multi mode)
python -m jobpulse.runner briefing        # Morning digest
python -m jobpulse.runner gmail           # Check recruiter emails
python -m jobpulse.runner calendar        # Today + tomorrow events
python -m jobpulse.runner weekly-report   # 7-day summary across all agents
python -m jobpulse.runner archive-week    # Archive current week + carry over planned budgets
python -m jobpulse.runner budget-compare  # This week vs last week per category
python -m jobpulse.runner budget-export   # CSV export (12 columns) for ML
python -m jobpulse.runner export          # Full data backup (tar.gz)
python -m jobpulse.runner webhook         # Start webhook server (port 8080)
python -m jobpulse.runner slack           # Start Slack listener
python -m jobpulse.runner discord         # Start Discord listener
./scripts/install_daemon.sh install       # Auto-start daemon on login
./scripts/install_cron.py                 # Install crons (incl. Sunday 7am budget archive)
```

## Architecture (3 Systems)

**1. Orchestration Engine** — 4 LangGraph patterns: hierarchical, peer debate, dynamic swarm, enhanced swarm
**2. JobPulse Automation** — Gmail, Calendar, GitHub, Notion, Budget, arXiv, Telegram agents running 24/7
**3. Knowledge MindGraph** — Entity extraction, GraphRAG retrieval, Three.js 3D visualization

**4. NLP Intent Classification** — 3-tier pipeline: regex (instant) → semantic embeddings (5ms) → LLM fallback ($0.001). 250+ examples, 31 intents, continuous learning.

**Current dispatch mode:** Enhanced Swarm (set `JOBPULSE_SWARM=false` in .env to revert to flat)

## Operational Principles

IMPORTANT: Non-negotiable. Violating any = log to `.claude/mistakes.md`. Full details in @docs/rules.md.

## Self-Correction Protocol

1. **Before every session**: Read @.claude/mistakes.md
2. **On error**: IMMEDIATELY append to `.claude/mistakes.md`
3. **Before committing**: Re-check mistakes log
4. **On user correction**: Log it, even if minor

## Telegram Commands

### Core Agents
| You type | What happens |
|----------|-------------|
| "show tasks" | Notion → today's checklist |
| list of items | Notion → creates tasks (dedup check, big-task detection) |
| "!! urgent task" | Notion → creates urgent-priority task |
| "! high priority task" | Notion → creates high-priority task |
| "task by Friday" | Notion → creates task with NLP due date |
| "mark X done" | Notion → fuzzy match + complete |
| "remove X" | Notion → fuzzy match + delete |
| "plan" / "weekly plan" | Notion → show undone tasks from past 7 days, carry forward |
| "calendar" | Calendar → today + tomorrow |
| "check emails" | Gmail → scan + classify + alert |
| "commits" | GitHub → yesterday's activity |
| "trending" | GitHub → hot repos |
| "arxiv" | arXiv → today's top AI papers ranked by broad impact |
| "paper 3" | arXiv → full abstract for paper #3 |
| "read 1" | arXiv → mark paper #1 as read |
| "papers stats" | arXiv → read/unread counts and category breakdown |
| "briefing" | Enhanced Swarm → 7-agent collect → RLM synthesis |
| "weekly report" | All agents → 7-day summary |
| "export" | Full data backup (databases, personas, experiences) |
| voice message | Whisper transcription → intent classification → agent |
| "help" | Lists all commands |

### Stop / Undo Last Action
| You type | What happens |
|----------|-------------|
| "stop" / "cancel" / "oops" / "nope" | Undo the last command's side effects (SQLite + Notion) |
| "undo that" / "take that back" | Same as stop — reverses last action |

Works from **any bot** (Main, Budget, or Research). Undoes: expenses, income, savings, hours, task creation, task completion. Each command sends a processing indicator with time estimate before executing.

### Salary / Hours
| You type | What happens |
|----------|-------------|
| "worked 7 hours" | Salary → calculates pay at £13.99/hr, tax (20%), savings suggestion (30% after-tax) |
| "worked six hours and thirty minutes" | Salary → word numbers supported |
| "worked 8h on monday" | Salary → past date support (Sunday-based work week) |
| "saved" / "transferred" | Salary → confirms savings transfer |
| "undo hours" | Salary → shows last 5 entries, pick to remove + Notion timesheet rebuild |

### Budget
| You type | What happens |
|----------|-------------|
| "spent 15 on lunch" | Budget → classify → SQLite → Notion sync |
| "yogurt and protein shake at Tesco" | Budget → NLP item + store extraction (50+ UK stores) |
| "earned 500 freelance" | Budget → log income → Notion sync |category |
| "budget-export" | Budget → CSV export (12 columns) for ML |
| "set budget groceries 50" | Budget → set planned amount for category |
| "recurring: 10 on spotify monthly" | Budget → auto-log on schedule (daily/weekly/monthly) |
| "show recurring" | Budget → list all active recurring rules |
| "stop recurring spotify" | Budget → deactivate a recurring rule |
| "undo" | Budget → delete last transaction, recalculate Notion totals |

### Remote Control
| You type | What happens |
|----------|-------------|
| Just type anything | Free-form conversation with project-aware LLM |
| `run: <command>` or `$ <command>` | Execute whitelisted shell command |
| `git status` / `git log` / `git diff` | Formatted git operations |
| `commit: fix bug` | Stage + commit (asks approval first) |
| `push` | Push to remote (asks approval) |
| `show: CLAUDE.md` | Read file content (paginated with more/next) |
| `logs` / `errors` | View recent logs or agent errors |
| `status` | Full system dashboard (daemon, agents, APIs) |
| `clear chat` | Reset conversation history |

## Telegram Multi-Bot Setup

Four separate Telegram bots, each with its own chat/channel:

| Bot | Purpose | Env Vars |
|-----|---------|----------|
| **Main** | Tasks, calendar, briefing, remote control | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` |
| **Budget** | Expenses, income, savings, recurring, weekly summary | `TELEGRAM_BUDGET_BOT_TOKEN`, `TELEGRAM_BUDGET_CHAT_ID` |
| **Research** | Knowledge queries, MindGraph, trending repos, arXiv digest | `TELEGRAM_RESEARCH_BOT_TOKEN`, `TELEGRAM_RESEARCH_CHAT_ID` |
| **Alert** | Gmail alerts, interview notifications, urgent reminders | `TELEGRAM_ALERT_BOT_TOKEN`, `TELEGRAM_ALERT_CHAT_ID` |

Falls back to `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` for any bot whose dedicated token is not set.

## Env Vars

- `OPENAI_API_KEY` (required)
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (main bot, also fallback)
- `TELEGRAM_BUDGET_BOT_TOKEN`, `TELEGRAM_BUDGET_CHAT_ID` (optional)
- `TELEGRAM_RESEARCH_BOT_TOKEN`, `TELEGRAM_RESEARCH_CHAT_ID` (optional)
- `TELEGRAM_ALERT_BOT_TOKEN`, `TELEGRAM_ALERT_CHAT_ID` (optional)
- `SLACK_BOT_TOKEN`, `SLACK_CHANNEL_ID`
- `DISCORD_BOT_TOKEN`, `DISCORD_CHANNEL_ID`, `DISCORD_USER_ID`
- `NOTION_API_KEY`, `NOTION_TASKS_DB_ID`, `NOTION_RESEARCH_DB_ID`
- `NOTION_PARENT_PAGE_ID` — parent page for weekly budget sheets
- `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`
- `JOBPULSE_SWARM=true` — Enhanced Swarm dispatch (false = flat)
- `CONVERSATION_MODEL=gpt-4o-mini` — model for free-form chat
- `RLM_BACKEND=openai`, `RLM_ROOT_MODEL=gpt-4o-mini`, `RLM_MAX_BUDGET=0.10`

## NLP 3-Tier Intent Classification

3-tier pipeline: regex (instant) → semantic embeddings (5ms) → LLM fallback ($0.001). 250+ examples, 31 intents, continuous learning. See @docs/agents.md for full details.

## Stats

~58,000 LOC | 248 Python files | 5 databases | 429 tests | 3 dashboards | 4 Telegram bots | 3 platforms

> Auto-updated by `scripts/update_stats.py`. Git pre-commit hook runs it on every commit that touches .py files.
> Manual: `python scripts/update_stats.py` | Check-only: `python scripts/update_stats.py --check`

## Dashboards

- `/health.html` — daemon status, agent success rates, API rate limits, errors, data export
- `/analytics.html` — usage trends, intent distribution, response times
- `/processes.html` — agent run timelines, step-by-step audit trails

## Logging

All modules use `shared/logging_config.py` — structured logging with per-module loggers via `get_logger(__name__)`. Logs written to `logs/` directory.

## Documentation

- @.claude/mistakes.md — MUST READ FIRST
- @docs/rules.md — Constraints, convergence, pattern selection
- @docs/agents.md — All agents, NLP classifier, A/B testing, budget tracker
- @docs/skills.md — GRPO, persona evolution, RLM, prompt optimization
- @docs/subagents.md — Dynamic agent factory, templates
- @docs/hooks.md — Process trails, memory, logging, export
