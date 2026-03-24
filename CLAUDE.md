# Multi-Agent Orchestration Patterns

LangGraph + LangChain + OpenAI. Four orchestration patterns for multi-agent blog generation.

## Commands

```bash
pip install -r requirements.txt
export OPENAI_API_KEY="sk-..."
python run_all.py "topic"                 # Compare all patterns
python -m patterns.hierarchical           # Individual runs
python -m patterns.peer_debate
python -m patterns.dynamic_swarm
python -m patterns.enhanced_swarm
```

## Operational Principles

IMPORTANT: These are non-negotiable. Violating any of these is a mistake — log it.

1. **Memory before action** — ALWAYS search memory/patterns before starting any task. If a matching pattern exists with score > 0.7, reuse it. Do not reinvent.
2. **ORCHESTRATOR, not EXECUTOR** — Claude tracks state and coordinates. Your agents do the actual work. Never do agent work yourself.
3. **Hierarchical topology for production** — Always use hierarchical supervisor pattern with 6-8 max specialized agents for production work. Other patterns are for experimentation only.
4. **Learn after success** — ALWAYS store successful patterns (score >= 7.0) to memory. Namespace: `patterns`. Future runs must benefit from past wins.
5. **3-Tier routing saves 250%** — Check for `[AGENT_BOOSTER_AVAILABLE]` before spawning expensive agents. Route: cached → lightweight → full agent. Skip tiers only when lower tiers explicitly fail.
6. **Commands return instantly** — Commands create records only. Never wait. Immediately continue with execution after issuing a command.

## Self-Correction Protocol

YOU MUST follow this protocol — no exceptions:

1. **Before every session**: Read @.claude/mistakes.md to avoid repeating past errors.
2. **When you make a mistake or hit an error**: IMMEDIATELY append an entry to `.claude/mistakes.md` with what went wrong, root cause, fix applied, and the rule to prevent recurrence.
3. **Before committing code**: Re-check `.claude/mistakes.md` to verify you haven't violated any learned rules.
4. **When the user corrects you**: Log it as a mistake even if it seems minor. Small corrections compound.

The mistakes log is append-only. Never delete entries. Never skip logging.

## Code Rules

Full constraints in @docs/rules.md. The two most-violated rules:

- IMPORTANT: All LLM calls MUST go through `get_llm()` in `shared/agents.py`. Never instantiate `ChatOpenAI` directly.
- Agent functions return `dict` (partial state), NEVER full `AgentState`.

## JobPulse Commands

```bash
python -m jobpulse.runner briefing       # Send morning digest
python -m jobpulse.runner gmail          # Check recruiter emails
python -m jobpulse.runner calendar       # Today + tomorrow events
python -m jobpulse.runner tasks          # Show Notion tasks
python -m jobpulse.runner github         # Yesterday's commits
python -m jobpulse.runner daemon         # Start Telegram command daemon
./scripts/install_daemon.sh install      # Auto-start daemon on login
```

## Telegram Command Interface

The daemon (`python -m jobpulse.runner daemon`) provides instant Telegram replies:

| You type | Agent runs |
|----------|-----------|
| "show tasks" | Notion → today's checklist |
| list of items (multi-line) | Notion → creates tasks |
| "mark X done" | Notion → marks task Done |
| "calendar" | Calendar → today + tomorrow |
| "check emails" | Gmail → scan + classify |
| "commits" | GitHub → yesterday's activity |
| "trending" | GitHub → hot repos |
| "briefing" | All agents → full report |
| "spent 15 on lunch" | Budget → log + categorize + Notion |
| "£8.50 coffee" | Budget → same |
| "budget" | Budget → weekly summary |
| "help" | Lists all commands |

## Env Vars

- `OPENAI_API_KEY` (required)
- `GMAIL_CREDENTIALS_PATH`, `TELEGRAM_BOT_TOKEN`, `DISCORD_BOT_TOKEN`, `LINKEDIN_ACCESS_TOKEN` (optional, for tools)

## Documentation

- @.claude/mistakes.md — MUST READ FIRST. Learned mistakes and error prevention rules.
- @docs/rules.md — MUST READ. All rules: operational, convergence, constraints, pattern selection.
- @docs/agents.md — Agent roles, state model, topologies
- @docs/skills.md — GRPO, persona evolution, prompt optimization
- @docs/subagents.md — Dynamic agent factory, templates, spawning
- @docs/hooks.md — Memory injection, tool integration, audit logging
