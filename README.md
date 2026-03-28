# Multi-Agent Orchestration + JobPulse + Knowledge MindGraph

Production autonomous agent system: 4 orchestration patterns, 10+ daily automation agents, knowledge graph with 3D visualization, Enhanced Swarm with RLM, multi-platform remote control, Claude Code Telegram approval, NLP intent classification, AI research blog pipeline.

**~48,500 LOC** | **190 Python files** | **5 databases** | **258 tests** | **3 dashboards** | **4 Telegram bots** | **3 platforms**

> Stats auto-updated via `scripts/update_stats.py`. Source of truth: [CLAUDE.md](CLAUDE.md).

## Three Integrated Systems

### 1. Orchestration Engine (patterns/)

Four LangGraph patterns for multi-agent coordination, all with mandatory fact-checking:

| Pattern | How It Works | Best For |
|---------|-------------|----------|
| **Hierarchical** | Supervisor routes to workers + fact-checker | Known workflows, speed |
| **Peer Debate** | Agents cross-critique + fact-check each round | Quality-critical tasks |
| **Dynamic Swarm** | Task queue + runtime re-analysis | Unknown complexity |
| **Enhanced Swarm** | Swarm + GRPO + persona + RLM + fact-check | Production (used by JobPulse) |

**Dual convergence gate:** quality score >= 8.0/10 AND factual accuracy >= 9.5/10. Claim-level verification via research notes + web search + cached facts.

### NLP 3-Tier Intent Classification

3-tier pipeline: regex (instant) → semantic embeddings (5ms) → LLM fallback ($0.001). 250+ examples, 31 intents, continuous learning. Full details in [docs/agents.md](docs/agents.md#nlp-intent-classifier-nlp_classifierpy).

### 2. JobPulse Daily Automation (jobpulse/)

Fully autonomous agents running 24/7 via macOS daemon + cron + GitHub Actions backup:

| Agent | What It Does | Schedule |
|-------|-------------|----------|
| Gmail | Classify recruiter emails (rule-based pre-classifier + LLM), send alerts, extract knowledge | 1pm, 3pm, 5pm |
| Calendar | Today + tomorrow events, 2-hour reminders | 9am, 12pm, 3pm |
| GitHub | Yesterday's commits (Commits API), trending repos | 8am briefing |
| arXiv | Daily top 5 AI papers ranked by broad impact, paper DB, read tracking, blog post pipeline | 7:57am + on demand |
| Notion | Tasks: create/complete/remove, dedup, priorities, due dates, subtasks, weekly plan | On demand |
| Budget | Parse spending/income/savings, 17 categories, recurring, alerts, undo, Notion sync, category sub-pages, item+store NLP, weekly archival, weekly comparison, historical pace alerts, CSV export | On demand |
| Budget Tracker | Weekly archival (Sunday 7am cron), category sub-page management, weekly comparison engine | Cron + on demand |
| Salary/Hours | Track work hours at £13.99/hr, tax calc, savings suggestion, Notion timesheet | On demand |
| Briefing | Collect all agents → RLM synthesis → Telegram | 8:03am daily |
| Weekly Report | 7-day aggregate across all agents | On demand |
| Voice Handler | Telegram voice → Whisper transcription → dispatch | On demand |

### 3. Knowledge MindGraph (mindgraph_app/)

- **Extraction**: LLM-based entity/relation extraction (14 types each)
- **Storage**: SQLite knowledge graph (entities, relations, simulation events)
- **Retrieval**: GraphRAG — local search, multi-hop traversal, temporal, RLM deep query
- **Visualization**: Three.js 3D neural/galaxy visualization (React frontend)

## Remote Control via Telegram

Control your entire system from your phone. Full command reference in [CLAUDE.md](CLAUDE.md#telegram-commands).

Highlights: tasks (create/complete/remove with dedup + priorities + due dates), budget (spend/earn/save with NLP parsing, 17 categories, recurring, alerts, undo), salary tracking (£13.99/hr with tax calc), calendar, Gmail, GitHub, arXiv papers, briefing, remote shell, git ops, file viewer, voice messages via Whisper.

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

### Multi-Bot Telegram Setup

Four separate bots route messages to dedicated chats:

| Bot | Purpose | Env Vars |
|-----|---------|----------|
| **Main** | Tasks, calendar, briefing, remote control | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` |
| **Budget** | Expenses, income, savings, recurring | `TELEGRAM_BUDGET_BOT_TOKEN`, `TELEGRAM_BUDGET_CHAT_ID` |
| **Research** | Knowledge queries, MindGraph, trending, arXiv digest | `TELEGRAM_RESEARCH_BOT_TOKEN`, `TELEGRAM_RESEARCH_CHAT_ID` |
| **Alert** | Gmail alerts, interview notifications | `TELEGRAM_ALERT_BOT_TOKEN`, `TELEGRAM_ALERT_CHAT_ID` |

Each bot is optional -- falls back to the main bot token/chat if not configured.

### Multi-Platform Support

| Platform | Status | Start Command |
|----------|--------|---------------|
| Telegram | Long-polling daemon | `python -m jobpulse.runner daemon` |
| Telegram Webhook | Push-based (requires public URL) | `python -m jobpulse.runner webhook <url>` |
| Slack | Channel polling | `python -m jobpulse.runner slack` |
| Discord | Channel polling | `python -m jobpulse.runner discord` |
| All platforms | Threaded multi-listener | `python -m jobpulse.runner multi` |

## Enhanced Swarm + RLM + Fact-Check

JobPulse uses Enhanced Swarm architecture (not flat dispatch):

```
Message → NLP 3-Tier Classifier (regex → embeddings → LLM)
       → Task Analyzer → Priority Queue → Execute with GRPO
       → Fact Checker (claim extraction → web search → scoring)
       → RLM Synthesis (if large context) → Store Experience
       → Persona Evolution → Reply
```

**RLM** (Recursive Language Model): when context exceeds single LLM capacity, root model writes code that processes chunks via sub-LM calls. Used for deep knowledge queries and briefing synthesis.

**Fact-Check Accuracy (9.5+/10)**: every blog article goes through claim-level verification. Claims are extracted from the draft, verified against research notes + paper abstracts + live DuckDuckGo web search + cached facts. Deterministic scoring (VERIFIED +1.0, INACCURATE -2.0, EXAGGERATED -1.0). Failed claims get targeted revision notes with specific fix instructions. Verified facts cached in SQLite for instant reuse.

**Gmail Pre-Classifier**: rule-based triage eliminates 70-85% of unnecessary LLM calls. 4-tier system: Learning → Static Rules → LLM Fallback → User Feedback. Adaptive audit decay (50% → 10%). Telegram review flow (✅/❌/🔄). Auto-graduates when accuracy exceeds 95%.

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
python -m jobpulse.runner archive-week    # Archive week + carry over planned budgets
python -m jobpulse.runner budget-compare  # This week vs last week per category
python -m jobpulse.runner budget-export   # CSV export (12 columns) for ML
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

# Telegram (main bot + fallback)
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...

# Telegram dedicated bots (optional, falls back to main)
TELEGRAM_BUDGET_BOT_TOKEN=...
TELEGRAM_BUDGET_CHAT_ID=...
TELEGRAM_RESEARCH_BOT_TOKEN=...
TELEGRAM_RESEARCH_CHAT_ID=...
TELEGRAM_ALERT_BOT_TOKEN=...
TELEGRAM_ALERT_CHAT_ID=...

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
| http://localhost:8000/health.html | Daemon status, agent success rates, API rate limits, errors, data export |
| http://localhost:8000/analytics.html | GRPO scores, persona drift, cost estimates, daily trends (Chart.js) |
| http://localhost:8000/processes.html | Agent process trail viewer (step-by-step audit) |
| http://localhost:3000 | Three.js 3D neural/galaxy visualization (primary frontend) |

## Test Suite

204 tests covering command routing, budget parsing (recurring, alerts, undo, item+store NLP, weekly comparison, CSV export, archival), task features (priority, due dates, dedup, subtasks, weekly plan), arXiv agent (ranking, read tracking, category tags, stats), dispatcher routing, swarm logic, GRPO sampling, experience storage, knowledge extraction, email pre-classifier (rules, confidence, evidence, audit, graduation, review flow), and fact-checker (claim extraction, scoring, web search, cache).

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

## Planned Features (Design Docs Ready)

| Feature | Status | Doc |
|---------|--------|-----|
| **arXiv Blog Pipeline** | Designed | `docs/feature-arxiv-blogpost.md` |
| **Auto Job Applier** | Designed | `docs/feature-auto-job-applier.md` |
| **Gmail Smart Filter** | Designed | `docs/feature-gmail-smart-filter.md` |

The blog pipeline uses 5 agents (Deep Reader → GRPO Writer → Fact Checker → Diagram Generator → Editor) to turn research papers into 2000-word publication-ready posts with workflow diagrams on Notion. Fact-checking now uses the unified `shared/fact_checker.py` with web search verification.

## Recent Features

### Gmail Pre-Classifier (2026-03-27)
Rule-based email triage that eliminates 70-85% of unnecessary LLM calls. Static rules match sender patterns, domain patterns, subject keywords, and dual subject+body patterns. Evidence-based attribution on every decision. Adaptive audit decay (50% → 10%). Telegram review flow with ✅/❌/🔄 for user feedback. Auto-graduates when rule accuracy exceeds 95%.

### Fact-Check Accuracy 9.5+/10 (2026-03-27)
Mandatory fact-checking across all 4 orchestration patterns. Extracts verifiable claims from drafts, verifies against research notes + paper abstracts + DuckDuckGo web search. Deterministic scoring with hard accuracy gate (9.5/10). Targeted revision notes give the writer specific claim-level fix instructions. Verified facts cached in SQLite for reuse.

## Documentation Map

> **CLAUDE.md** is the source of truth for project stats, commands, and Telegram interface.
> Stats are auto-updated via `scripts/update_stats.py`.

| Question | Read This |
|----------|-----------|
| How do I use the system? (commands, Telegram) | [CLAUDE.md](CLAUDE.md) |
| What does each agent do? | [docs/agents.md](docs/agents.md) |
| What are the rules and constraints? | [docs/rules.md](docs/rules.md) |
| How do GRPO, personas, RLM work? | [docs/skills.md](docs/skills.md) |
| How does the dynamic agent factory work? | [docs/subagents.md](docs/subagents.md) |
| Process trails, memory, logging, export? | [docs/hooks.md](docs/hooks.md) |
| What mistakes have been made? (read first!) | [.claude/mistakes.md](.claude/mistakes.md) |

### Feature Design Docs

| Feature | Status | Doc |
|---------|--------|-----|
| arXiv Blog Pipeline | Designed | [docs/feature-arxiv-blogpost.md](docs/feature-arxiv-blogpost.md) |
| Auto Job Applier | Designed | [docs/feature-auto-job-applier.md](docs/feature-auto-job-applier.md) |
| Gmail Smart Filter | Designed | [docs/feature-gmail-smart-filter.md](docs/feature-gmail-smart-filter.md) |
| Notion Budget v2 | Implemented | [docs/feature-notion-budget-v2.md](docs/feature-notion-budget-v2.md) |
| NLP Intent Classification | Implemented | [docs/feature-nlp-intent.md](docs/feature-nlp-intent.md) |
| arXiv Digest | Implemented | [docs/feature-arxiv-digest.md](docs/feature-arxiv-digest.md) |
| Gmail Pre-Classifier | Implemented | [docs/feature-gmail-preclassifier.md](docs/feature-gmail-preclassifier.md) |
| Fact-Check Accuracy 9.5+ | Implemented | [docs/feature-fact-check-accuracy.md](docs/feature-fact-check-accuracy.md) |
