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

## Stats

~58,500 LOC | 249 Python files | 12 databases | 1098 tests | 4 dashboards | 5 Telegram bots | 3 platforms

> Auto-updated by pre-commit hook. Manual: `python scripts/update_stats.py`

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
