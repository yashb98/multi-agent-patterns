# Multi-Agent Orchestration Patterns

A complete multi-agent automation system combining LangGraph orchestration patterns with real-world service integrations and daily automation agents.

**8,547 lines** | **55 files** | **4 orchestration patterns** | **6 cron agents** | **9 service integrations**

## What This Project Does

### 1. Multi-Agent Orchestration Engine

Four LangGraph patterns for agent coordination, each with different trade-offs:

| Pattern | File | Lines | How It Works |
|---------|------|-------|-------------|
| **Hierarchical Supervisor** | `patterns/hierarchical.py` | 468 | Supervisor routes to researcher/writer/reviewer. Hub-and-spoke. |
| **Peer Debate** | `patterns/peer_debate.py` | 455 | Agents cross-critique each other. Quality through disagreement. |
| **Dynamic Swarm** | `patterns/dynamic_swarm.py` | 499 | Task queue with runtime discovery. Adapts as it learns. |
| **Enhanced Swarm** | `patterns/enhanced_swarm.py` | 440 | Production-grade: factory + GRPO + persona evolution + experience memory. |

Run all four on the same topic and compare:
```bash
python run_all.py "How AI Agents Are Changing Software Development"
```

### 2. Shared Agent Infrastructure

Reusable modules that work across all patterns:

| Module | Lines | What It Does |
|--------|-------|-------------|
| `shared/state.py` | 76 | `AgentState` TypedDict — the shared whiteboard between agents |
| `shared/agents.py` | 288 | Core agent nodes: researcher, writer, reviewer + `get_llm()` factory |
| `shared/prompts.py` | 169 | System prompts for all agent roles |
| `shared/memory_layer.py` | 1,053 | 5-tier memory + PatternMemory + TieredRouter + MemoryManager |
| `shared/tool_integration.py` | 839 | MCP tool framework: 7 tools with permissions, risk levels, audit logging |
| `shared/dynamic_agent_factory.py` | 513 | Runtime agent spawning from 8 templates based on task complexity |
| `shared/experiential_learning.py` | 332 | Training-Free GRPO (arXiv:2510.08191) — RL in prompt space |
| `shared/persona_evolution.py` | 200+ | Search-Synthesise-Compress loop for evolving agent prompts |
| `shared/prompt_optimizer.py` | 150+ | DSPy/GEPA prompt optimization bridge |

### 3. Daily Automation Agents (Cron)

Six scripts running on system crontab:

| Time | Agent | What It Does |
|------|-------|-------------|
| 7:57am daily | `arxiv-daily.sh` | Fetches top 5 AI papers → sends to Telegram |
| 8:03am daily | `morning-digest.sh` | Aggregates emails + calendar + GitHub + Notion → morning Telegram briefing |
| 1:02pm daily | `gmail-recruiter-check.sh` | Scans Gmail for recruiter emails, classifies, instant alerts |
| 3:02pm daily | `gmail-recruiter-check.sh` | Same scan |
| 5:02pm daily | `gmail-recruiter-check.sh` | Same scan |
| Mon 8:33am | `notion-papers.sh` | Creates 500-word paper summaries in Notion |

### 4. Service Connections

| Service | Status | Method |
|---------|--------|--------|
| **Gmail** | Connected | Cloud MCP via claude.ai |
| **Google Calendar** | Connected | Cloud MCP via claude.ai |
| **Notion** | Connected | Cloud MCP via claude.ai |
| **GitHub** | Connected | `gh` CLI (authenticated as yashb98) |
| **Telegram** | Working | Bot API via curl (@IntegrationY_bot) |
| **WebSearch** | Built-in | Claude Code native |
| **arXiv** | Ready | `/arxiv-top5` skill + cron script |
| **Discord** | Token ready | MCP package needs fix |
| **Figma** | Needs re-auth | Cloud MCP via claude.ai |

### 5. Claude Code Infrastructure

| Component | Count | Purpose |
|-----------|-------|---------|
| **Skills** | 8 | `/add-pattern`, `/add-agent`, `/add-tool`, `/compare-patterns`, `/log-mistake`, `/connect-services`, `/arxiv-top5`, `/openai-agents-sdk` |
| **Subagents** | 3 | code-reviewer, pattern-explorer, memory-debugger |
| **Hooks** | 1 | Warns on hardcoded path introduction |
| **Mistakes log** | 1 | Append-only error log for self-correction |

## Project Structure

```
multi_agent_patterns/
├── CLAUDE.md                          # Project guide (59 lines, lean)
├── README.md                          # This file
├── run_all.py                         # Entry point: compare all 4 patterns
├── requirements.txt                   # Python dependencies
├── .env                               # Real API tokens (gitignored)
├── .env.example                       # Template for env vars
├── .gitignore
│
├── patterns/                          # 4 orchestration patterns
│   ├── CLAUDE.md                      #   Pattern contracts
│   ├── hierarchical.py                #   Supervisor hub-and-spoke
│   ├── peer_debate.py                 #   Multi-agent debate
│   ├── dynamic_swarm.py               #   Task-queue driven
│   └── enhanced_swarm.py              #   Production-grade adaptive
│
├── shared/                            # Reusable agent infrastructure
│   ├── CLAUDE.md                      #   Module index (pointer file)
│   ├── __init__.py                    #   Exports
│   ├── state.py                       #   AgentState TypedDict
│   ├── agents.py                      #   Core agent nodes + get_llm()
│   ├── prompts.py                     #   System prompts
│   ├── memory_layer.py                #   5-tier memory + PatternMemory + TieredRouter
│   ├── tool_integration.py            #   7 tools with permissions + audit
│   ├── dynamic_agent_factory.py       #   Runtime agent spawning
│   ├── experiential_learning.py       #   Training-Free GRPO
│   ├── persona_evolution.py           #   Persona evolution loop
│   └── prompt_optimizer.py            #   DSPy/GEPA bridge
│
├── docs/                              # Reference documentation
│   ├── CHANGELOG.md                   #   Complete session history
│   ├── agents.md                      #   Agent roles, state model
│   ├── rules.md                       #   All rules (single source of truth)
│   ├── skills.md                      #   GRPO, persona, prompt optimization
│   ├── subagents.md                   #   Dynamic agent factory
│   └── hooks.md                       #   Memory tiers, tool integration
│
├── scripts/                           # Automation
│   ├── arxiv-daily.sh                 #   Daily arXiv → Telegram
│   └── agents/                        #   Cron agent scripts
│       ├── morning-digest.sh          #     Morning Telegram briefing
│       ├── gmail-recruiter-check.sh   #     Gmail recruiter scan
│       ├── calendar-check.sh          #     Google Calendar events
│       ├── github-commits.sh          #     Yesterday's GitHub commits
│       └── notion-papers.sh           #     Weekly Notion paper summaries
│
└── .claude/                           # Claude Code configuration
    ├── settings.json                  #   Hooks + permissions
    ├── mistakes.md                    #   Self-correction log
    ├── agents/                        #   3 subagent definitions
    │   ├── code-reviewer.md
    │   ├── pattern-explorer.md
    │   └── memory-debugger.md
    └── skills/                        #   8 invokable skills
        ├── add-pattern/SKILL.md
        ├── add-agent/SKILL.md
        ├── add-tool/SKILL.md
        ├── compare-patterns/SKILL.md
        ├── log-mistake/SKILL.md
        ├── connect-services/SKILL.md
        ├── arxiv-top5/SKILL.md
        └── openai-agents-sdk/         #   Full SDK reference
            ├── SKILL.md
            └── references/            #   8 reference files
```

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Set up environment
cp .env.example .env
# Edit .env with your API keys

# 3. Run the orchestration patterns
export OPENAI_API_KEY="sk-..."
python run_all.py "Your topic here"

# 4. Check cron agents
crontab -l
```

## Key Architecture Decisions

### Agents Are Pure Functions
Every agent receives state, calls LLM, returns a partial dict. Zero knowledge of graph topology. Same agent works in any pattern.

### State-Based Communication
Agents never talk directly. All communication flows through `AgentState`. `Annotated[list, operator.add]` fields accumulate; regular fields replace.

### Memory Before Action (Operational Principle #1)
Before any task, `PatternMemory.search()` checks for reusable patterns. Score > 0.7 = reuse. Implemented in `shared/memory_layer.py`.

### 3-Tier Routing (Operational Principle #5)
Cached → Lightweight → Full Agent. `TieredRouter` in `shared/memory_layer.py` checks `[AGENT_BOOSTER_AVAILABLE]` before spawning expensive agents.

### Learn After Success (Operational Principle #4)
Every run scoring >= 7.0 stores its pattern (agents, routing, strengths) for future reuse. Implemented in `patterns/hierarchical.py`.

## Cron Schedule

```
57 7 * * *   arxiv-daily.sh              # arXiv papers → Telegram
 3 8 * * *   morning-digest.sh           # Full morning briefing → Telegram
 2 13 * * *  gmail-recruiter-check.sh    # Gmail scan (1pm)
 2 15 * * *  gmail-recruiter-check.sh    # Gmail scan (3pm)
 2 17 * * *  gmail-recruiter-check.sh    # Gmail scan (5pm)
33 8 * * 1   notion-papers.sh            # Weekly papers → Notion (Monday)
```

## Environment Variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `OPENAI_API_KEY` | Yes | LLM calls for all agents |
| `TELEGRAM_BOT_TOKEN` | Yes | Telegram notifications (@IntegrationY_bot) |
| `DISCORD_BOT_TOKEN` | No | Discord integration |
| `GMAIL_CREDENTIALS_PATH` | No | Gmail OAuth (cloud MCP handles this) |
| `NOTION_API_KEY` | No | Notion integration (cloud MCP handles this) |

## Tech Stack

- **Python 3.12** + LangGraph + LangChain + OpenAI
- **LangGraph StateGraph** for agent orchestration
- **5-tier memory**: working → short-term → episodic → semantic → procedural
- **MCP** (Model Context Protocol) for tool integration
- **System crontab** for daily automation
- **Telegram Bot API** for notifications
- **GitHub CLI** (`gh`) for commit tracking
