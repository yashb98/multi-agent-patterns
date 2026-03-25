# Multi-Agent Orchestration + JobPulse + Knowledge MindGraph

Production autonomous agent system: 4 orchestration patterns, 7 daily automation agents, knowledge graph with 3D visualization, Enhanced Swarm with RLM.

**~10,000 LOC** | **38 Python files** | **4 databases** | **7 cron jobs** | **5 CI workflows**

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
| Gmail | Classify recruiter emails, send Telegram alerts | 1pm, 3pm, 5pm |
| Calendar | Today + tomorrow events, 2-hour reminders | 9am, 12pm, 3pm |
| GitHub | Yesterday's commits, trending repos | 8am briefing |
| Notion | Daily tasks (to_do blocks), fuzzy completion | On demand |
| Budget | Parse spending, classify, sync to Notion sheet | On demand |
| Briefing | Collect all agents → RLM synthesis → Telegram | 8:03am daily |
| Telegram | Instant command replies via long-polling daemon | Always on |

### 3. Knowledge MindGraph (mindgraph_app/)

- **Extraction**: LLM-based entity/relation extraction (14 types each)
- **Storage**: SQLite knowledge graph (entities, relations, simulation events)
- **Retrieval**: GraphRAG — local search, multi-hop traversal, temporal, RLM deep query
- **Visualization**: D3.js brain neural view + Three.js 3D galaxy view

## Enhanced Swarm + RLM

JobPulse uses Enhanced Swarm architecture (not flat dispatch):

```
Message → Task Analyzer → Priority Queue → Execute with GRPO
       → RLM Synthesis (if large context) → Store Experience
       → Persona Evolution → Reply
```

**RLM** (Recursive Language Model): when context exceeds single LLM capacity, root model writes code that processes chunks via sub-LM calls. Used for deep knowledge queries and briefing synthesis.

**Persona Evolution**: agent prompts improve over weeks. Gmail learns to skip automated rejections. Budget learns coffee = Eating out. Briefing learns to lead with interviews.

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

# Start MindGraph visualization
python -m mindgraph_app.main
# Open http://localhost:8000

# Start Three.js 3D version
cd frontend && npm install && npm run dev
# Open http://localhost:3000
```

## Environment Variables

```env
OPENAI_API_KEY=sk-...              # Required
TELEGRAM_BOT_TOKEN=...             # Telegram bot
TELEGRAM_CHAT_ID=...               # Your chat ID
NOTION_API_KEY=...                 # Notion integration
NOTION_TASKS_DB_ID=...             # Daily tasks database
GOOGLE_OAUTH_CLIENT_ID=...         # Gmail + Calendar
GOOGLE_OAUTH_CLIENT_SECRET=...
JOBPULSE_SWARM=true                # Enhanced Swarm (false = flat)
RLM_BACKEND=openai                 # RLM config
RLM_ROOT_MODEL=gpt-4o-mini
RLM_MAX_BUDGET=0.10
```

## Cost

| Architecture | Weekly | Monthly |
|---|---|---|
| Flat dispatcher | $0.07 | $0.28 |
| Dynamic Swarm | $0.09 | $0.36 |
| Enhanced Swarm (current) | $0.15 | $0.60 |

All on gpt-4o-mini. Enhanced Swarm on gpt-4o would be ~$9/month.

## Frontends

| URL | What |
|-----|------|
| http://localhost:8000 | D3.js MindGraph (brain neural + galaxy mode at 300+ nodes) |
| http://localhost:8000/processes.html | Agent process trail viewer |
| http://localhost:3000 | Three.js 3D neural visualization (requires `cd frontend && npm run dev`) |

## Documentation

| File | Content |
|------|---------|
| docs/ARCHITECTURE.md | System overview, data flows, file map |
| docs/agents.md | All agents (orchestration + JobPulse) |
| docs/rules.md | Operational rules, convergence, constraints |
| docs/skills.md | GRPO, persona evolution, RLM, prompt optimization |
| docs/subagents.md | Dynamic agent factory, templates |
| docs/hooks.md | Process trails, memory injection, audit logging |
| .claude/mistakes.md | Error log (append-only) |
