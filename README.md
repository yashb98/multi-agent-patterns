# Multi-Agent Orchestration + JobPulse + Knowledge MindGraph

Production autonomous agent system: 4 orchestration patterns, 9+ daily automation agents, knowledge graph with 3D visualization, Enhanced Swarm with RLM, multi-platform remote control, Claude Code Telegram approval.

**~17,000 LOC** | **70+ Python files** | **4 databases** | **125 tests** | **3 dashboards** | **3 platforms**

## Three Integrated Systems

### 1. Orchestration Engine (patterns/)

Four LangGraph patterns for multi-agent coordination:

| Pattern | How It Works | Best For |
|---------|-------------|----------|
| **Hierarchical** | Supervisor routes to workers | Known workflows, speed |
| **Peer Debate** | Agents cross-critique each other | Quality-critical tasks |
| **Dynamic Swarm** | Task queue + runtime re-analysis | Unknown complexity |
| **Enhanced Swarm** | Swarm + GRPO + persona + RLM | Production (used by JobPulse) |

### 2. JobPulse Daily Automation (jobpulse/)

Fully autonomous agents running 24/7 via macOS daemon + cron + GitHub Actions backup:

| Agent | What It Does | Schedule |
|-------|-------------|----------|
| Gmail | Classify recruiter emails, send alerts, extract knowledge | 1pm, 3pm, 5pm |
| Calendar | Today + tomorrow events, 2-hour reminders | 9am, 12pm, 3pm |
| GitHub | Yesterday's commits (Commits API), trending repos | 8am briefing |
| Notion | Tasks: create/complete/remove, dedup, priorities, due dates, subtasks, weekly plan | On demand |
| Budget | Parse spending/income/savings, 17 categories, recurring, alerts, undo, Notion sync | On demand |
| Briefing | Collect all agents → RLM synthesis → Telegram | 8:03am daily |
| Weekly Report | 7-day aggregate across all agents | On demand |
| Voice Handler | Telegram voice → Whisper transcription → dispatch | On demand |

### 3. Knowledge MindGraph (mindgraph_app/)

- **Extraction**: LLM-based entity/relation extraction (14 types each)
- **Storage**: SQLite knowledge graph (entities, relations, simulation events)
- **Retrieval**: GraphRAG — local search, multi-hop traversal, temporal, RLM deep query
- **Visualization**: D3.js brain neural view + Three.js 3D galaxy view

## Remote Control via Telegram

Control your entire system from your phone:

| Command | What It Does |
|---------|-------------|
| **Tasks** | |
| "show tasks" | Today's checklist from Notion |
| list of items | Creates tasks (dedup check, big-task detection + subtask suggestion) |
| `!! urgent task` / `! high task` | Priority tasks (red/yellow indicators) |
| "task by Friday" | Task with NLP due date parsing |
| "done: X" / "mark X done" | Fuzzy match + complete |
| "remove: X" | Fuzzy match + delete |
| "plan" / "weekly plan" | Show undone tasks from past 7 days, carry forward |
| **Budget** | |
| "spent 15 on lunch" | Log expense → classify → SQLite → Notion sync |
| "earned 500 freelance" | Log income |
| "saved 100" | Log savings |
| "budget" | Weekly summary with alerts |
| "set budget groceries 50" | Set planned amount per category |
| "recurring: 10 on spotify monthly" | Auto-log on schedule (daily/weekly/monthly) |
| "show recurring" / "stop recurring X" | Manage recurring rules |
| "undo" | Delete last transaction, recalculate Notion |
| **Agents** | |
| "calendar" | Today + tomorrow events |
| "check emails" | Gmail scan + classify + alert |
| "commits" | Yesterday's git activity |
| "trending" | Hot GitHub repos |
| "briefing" | Enhanced Swarm 6-agent collect → RLM synthesis |
| "weekly report" | 7-day aggregate across all agents |
| "export" | Full data backup (tar.gz) |
| **Remote Control** | |
| Just type anything | Free-form conversation with project-aware LLM |
| `run: <command>` or `$ <command>` | Execute whitelisted shell command |
| `git status` / `git log` / `git diff` | Formatted git operations |
| `commit: fix bug` | Stage + commit (asks approval first) |
| `push` | Push to remote (asks approval) |
| `show: CLAUDE.md` | Read files (paginated with more/next) |
| `logs` / `errors` | View logs or recent agent errors |
| `status` | Full system dashboard |
| `clear chat` | Reset conversation history |
| Voice message | Auto-transcribed via Whisper → dispatched |

### Claude Code Remote Approval

When Claude Code runs bash commands, approvals are forwarded to Telegram:

```
🔐 CLAUDE CODE APPROVAL
Command: npm install express
Reply yes or no (1 hour timeout)
```

- **Auto-approved**: ls, cat, git status, python -c, grep, echo
- **Auto-blocked**: rm -rf, sudo, shutdown
- **Everything else**: asks you on Telegram, waits up to 1 hour

### Multi-Platform Support

| Platform | Status | Start Command |
|----------|--------|---------------|
| Telegram | Long-polling daemon | `python -m jobpulse.runner daemon` |
| Telegram Webhook | Push-based (requires public URL) | `python -m jobpulse.runner webhook <url>` |
| Slack | Channel polling | `python -m jobpulse.runner slack` |
| Discord | Channel polling | `python -m jobpulse.runner discord` |
| All platforms | Threaded multi-listener | `python -m jobpulse.runner multi` |

## Enhanced Swarm + RLM

JobPulse uses Enhanced Swarm architecture (not flat dispatch):

```
Message → Task Analyzer → Priority Queue → Execute with GRPO
       → RLM Synthesis (if large context) → Store Experience
       → Persona Evolution → Reply
```

**RLM** (Recursive Language Model): when context exceeds single LLM capacity, root model writes code that processes chunks via sub-LM calls. Used for deep knowledge queries and briefing synthesis.

**Persona Evolution**: agent prompts improve over weeks via two modes. Quick evolve (every run): single-step search-synthesize-compress. Deep meta-optimization (every 10th generation): multi-iteration reflective rewriting via `prompt_optimizer.py`. Gmail learns to skip automated rejections. Budget learns coffee = Eating out. Briefing learns to lead with interviews.

**A/B Testing**: prompt variants compared side-by-side with statistical tracking. Winners auto-promoted after 10+ trials.

## Quick Start

```bash
# Install
pip install -r requirements.txt

# Configure
cp .env.example .env  # Add your API keys

# Run integrations setup
python scripts/setup_integrations.py

# Start daemon (instant Telegram replies)
python -m jobpulse.runner daemon

# Or install as macOS service (auto-start on login)
./scripts/install_daemon.sh install

# Start MindGraph visualization + dashboards
python -m mindgraph_app.main
# Open http://localhost:8000

# Start Three.js 3D version
cd frontend && npm install && npm run dev
# Open http://localhost:3000

# Run tests
python -m pytest tests/ -v

# Export all data
python -m jobpulse.runner export

# Deploy
vercel --prod
```

## CLI Commands

```bash
python -m jobpulse.runner daemon          # Start Telegram daemon
python -m jobpulse.runner briefing        # Morning digest
python -m jobpulse.runner gmail           # Check recruiter emails
python -m jobpulse.runner calendar        # Today + tomorrow events
python -m jobpulse.runner weekly-report   # 7-day summary
python -m jobpulse.runner export          # Full data backup (tar.gz)
python -m jobpulse.runner webhook <url>   # Start webhook server
python -m jobpulse.runner slack           # Start Slack listener
python -m jobpulse.runner discord         # Start Discord listener
python -m jobpulse.runner multi           # All platform listeners
python run_all.py "topic"                 # Compare all 4 patterns
```

## Environment Variables

```env
# Required
OPENAI_API_KEY=sk-...

# Telegram
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...

# Slack (optional)
SLACK_BOT_TOKEN=...
SLACK_CHANNEL_ID=...

# Discord (optional)
DISCORD_BOT_TOKEN=...
DISCORD_CHANNEL_ID=...
DISCORD_USER_ID=...

# Notion
NOTION_API_KEY=...
NOTION_TASKS_DB_ID=...
NOTION_RESEARCH_DB_ID=...

# Google OAuth (Gmail + Calendar)
GOOGLE_OAUTH_CLIENT_ID=...
GOOGLE_OAUTH_CLIENT_SECRET=...

# System
JOBPULSE_SWARM=true                # Enhanced Swarm (false = flat)
CONVERSATION_MODEL=gpt-4o-mini     # Chat model
RLM_BACKEND=openai
RLM_ROOT_MODEL=gpt-4o-mini
RLM_MAX_BUDGET=0.10
```

## Dashboards

| URL | What |
|-----|------|
| http://localhost:8000 | D3.js MindGraph (brain neural + galaxy mode at 300+ nodes) |
| http://localhost:8000/health.html | Daemon status, agent success rates, API rate limits, errors, data export |
| http://localhost:8000/analytics.html | GRPO scores, persona drift, cost estimates, daily trends (Chart.js) |
| http://localhost:8000/processes.html | Agent process trail viewer (step-by-step audit) |
| http://localhost:3000 | Three.js 3D neural visualization |

## Test Suite

125 tests covering command routing, budget parsing (recurring, alerts, undo), task features (priority, due dates, dedup, subtasks, weekly plan), dispatcher routing, swarm logic, GRPO sampling, experience storage, and knowledge extraction.

```bash
python -m pytest tests/ -v          # Full suite
python -m pytest tests/ -v --cov    # With coverage
```

## Cost

| Architecture | Weekly | Monthly |
|---|---|---|
| Flat dispatcher | $0.07 | $0.28 |
| Dynamic Swarm | $0.09 | $0.36 |
| Enhanced Swarm (current) | $0.15 | $0.60 |

All on gpt-4o-mini. Enhanced Swarm on gpt-4o would be ~$9/month.

## Documentation

| File | Content |
|------|---------|
| CLAUDE.md | Project instructions + operational principles |
| docs/agents.md | All agents (orchestration + JobPulse + platforms) |
| docs/rules.md | Operational rules, convergence, constraints, input modes |
| docs/skills.md | GRPO, persona evolution, RLM, A/B testing, voice input |
| docs/subagents.md | Dynamic agent factory, templates |
| docs/hooks.md | Process trails, memory, logging, rate limits, export, A/B testing |
| .claude/mistakes.md | Error log (append-only, read first every session) |
