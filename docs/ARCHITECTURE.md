# Architecture

## System Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        TELEGRAM (You)                            │
│   "briefing" / "spent 5 coffee" / "show tasks" / "calendar"     │
└─────────────────────┬───────────────────────────────────────────┘
                      │ long-poll (1-3s)
┌─────────────────────▼───────────────────────────────────────────┐
│                 ENHANCED SWARM DISPATCHER                         │
│                                                                   │
│  1. command_router.py → classify intent (rule-based + LLM)       │
│  2. swarm_dispatcher.py →                                        │
│     ├─ Task Analyzer: decompose into priority queue              │
│     ├─ Experience Memory: inject learned patterns                │
│     ├─ Execute agents (with GRPO sampling where flagged)         │
│     ├─ RLM Synthesis (if data > 5K chars)                        │
│     └─ Store experience for future learning                      │
│                                                                   │
│  Fallback: set JOBPULSE_SWARM=false → flat dispatcher            │
└──────┬──────┬──────┬──────┬──────┬──────┬──────┬────────────────┘
       │      │      │      │      │      │      │
 ┌─────▼┐ ┌──▼──┐ ┌─▼──┐ ┌▼───┐ ┌▼───┐ ┌▼───┐ ┌▼────────┐
 │Gmail │ │Cal. │ │Git │ │Not.│ │Bud.│ │arXiv│ │MindGraph│
 │Agent │ │Agent│ │Agent│ │Agent│ │Agent│ │     │ │  App    │
 └──┬───┘ └──┬──┘ └──┬──┘ └─┬──┘ └─┬──┘ └──┬──┘ └──┬─────┘
    │        │       │      │      │       │       │
    ▼        ▼       ▼      ▼      ▼       ▼       ▼
┌───────────────────────────────────────────────────────────┐
│  PROCESS TRAIL (agent_process_trails)                      │
│  Every step logged: API calls, LLM decisions, extractions  │
├───────────────────────────────────────────────────────────┤
│  EVENT LOGGER (simulation_events)                          │
│  Every agent action → feeds MindGraph timeline             │
├───────────────────────────────────────────────────────────┤
│  PERSONA EVOLUTION (persona_prompts)                       │
│  Agent prompts improve over weeks via experience learning  │
└─────────────────────────┬─────────────────────────────────┘
                          │
┌─────────────────────────▼─────────────────────────────────┐
│                   KNOWLEDGE MINDGRAPH                       │
│                                                             │
│  Storage: knowledge_entities + knowledge_relations (SQLite) │
│  Extraction: LLM-based entity/relation extraction           │
│  Retrieval: local_search + multi_hop + temporal + deep_query│
│  Deep Query: RLM recursive processing for large graphs      │
│  Frontend: D3.js (brain neural viz) + Three.js (3D galaxy)  │
└─────────────────────────────────────────────────────────────┘
```

## Databases (4 files, 16 tables)

| Database | Tables | Purpose |
|----------|--------|---------|
| mindgraph.db | knowledge_entities, knowledge_relations, processed_files, simulation_events, agent_process_trails | Knowledge graph + audit |
| jobpulse.db | processed_emails, gmail_check_state | Email dedup |
| budget.db | transactions, weekly_budgets, planned_budgets | Finance |
| swarm_experience.db | experiences, persona_prompts | Learning |

## Enhanced Swarm Flow (briefing example)

```
"briefing" → classify → BRIEFING intent
  → Task Analyzer: 6 tasks [gmail, calendar, tasks, github, budget, synthesize]
  → Load experiences: "Lead with urgent items" (score 8.5)
  → Execute priority 1 (parallel-style):
      gmail_collect → "1 interview (Barclays)"
      calendar_collect → "Interview at 3pm"
      tasks_collect → "3 open tasks"
      github_collect → "5 commits on multi-agent-patterns"
      budget_collect → "£45 spent this week"
  → Execute priority 2:
      synthesize_briefing (GRPO=true, RLM=true)
        → RLM: splits 5 sections → sub-LM summarizes each → combines
        → 3 candidates at temp 0.5/0.7/0.9 → pick best
  → Store experience: score 8.2
  → Evolve briefing persona (generation +1)
  → Send via Telegram
```

## RLM Architecture

```
deep_query("What's my history with AI companies?")
  → local_search → 20 seed entities
  → multi_hop(each, 2 hops) → 500+ entities, 40K chars
  → TOO BIG for single LLM call
  → RLM root model writes program:
      chunk_by_type(context) → companies, roles, events
      sub_lm("summarize companies", companies_chunk)
      sub_lm("summarize roles", roles_chunk)
      sub_lm("build timeline", events_chunk)
      sub_lm("synthesize answer", all_summaries)
  → Final answer
```

## Visualization (3 modes)

| Mode | Trigger | Technology |
|------|---------|-----------|
| Brain Neural | < 300 nodes | D3.js + Canvas (curved dendrites, synaptic pulses, neuron glow) |
| Multi-Galaxy | >= 300 nodes | D3.js (galaxies per entity type, orbit rings, nebula) |
| 3D Neural | frontend/ | Three.js + React (WebGL bloom, orbit camera, particle edges) |

## Scheduling

| Time | Job | System |
|------|-----|--------|
| Always | Telegram daemon | launchd |
| 7:57 AM | arXiv papers | cron |
| 8:03 AM | Morning briefing | cron |
| 9/12/3 PM | Calendar reminders | cron |
| 1/3/5 PM | Gmail check | cron |
| Every 10m | Health watchdog | cron |
| Monday 8:33 AM | Weekly papers → Notion | cron |
| Backup | All jobs | GitHub Actions |

## File Map

```
multi_agent_patterns/
├── jobpulse/               # 20 files — daily automation agents
│   ├── swarm_dispatcher.py #   Enhanced Swarm (task analyzer + GRPO + RLM)
│   ├── persona_evolution.py#   Prompt evolution over time
│   ├── process_logger.py   #   Step-by-step audit trails
│   ├── gmail_agent.py      #   Email classify + alert
│   ├── calendar_agent.py   #   Google Calendar
│   ├── github_agent.py     #   Commits + trending
│   ├── notion_agent.py     #   Tasks + research pages
│   ├── budget_agent.py     #   Income/expense tracking
│   ├── morning_briefing.py #   Collects all → Telegram
│   ├── command_router.py   #   Intent classification
│   ├── dispatcher.py       #   Flat dispatcher (fallback)
│   ├── telegram_listener.py#   Long-polling daemon
│   ├── event_logger.py     #   Simulation events
│   └── auto_extract.py     #   Knowledge extraction hooks
├── mindgraph_app/          # 5 files — knowledge graph
│   ├── api.py              #   FastAPI routes (graph + process trails)
│   ├── storage.py          #   SQLite entity/relation storage
│   ├── extractor.py        #   LLM-based extraction (14 entity types)
│   └── retriever.py        #   GraphRAG + RLM deep query
├── patterns/               # 4 orchestration patterns
├── shared/                 # 9 files — agent infrastructure
├── static/                 # D3.js frontend (index.html, processes.html)
├── frontend/               # React + Three.js 3D frontend
├── scripts/                # 11 automation scripts
├── .github/workflows/      # 5 backup CI workflows
└── data/                   # 4 SQLite databases
```
