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
| Job Autopilot | 4-gate pre-screen → scan → hybrid skill extract → tailor CV → ATS score → apply/queue (25 apps/day) |
| Skill Graph | Nightly 3am GitHub sync → MindGraph skill/project graph → recruiter-grade pre-screen |
| Form Engine | Detect + fill any HTML input (select, radio, checkbox, text, date, file, multi-select) |
| MindGraph | Entity extraction, GraphRAG retrieval, Three.js 3D visualization |
| NLP Classifier | 3-tier: regex → embeddings (5ms) → LLM fallback. 250+ examples, 41 intents |

**Dispatch mode:** Enhanced Swarm (`JOBPULSE_SWARM=false` to revert to flat)

## Rules

- Read @.claude/mistakes.md before every session
- Log errors immediately to `.claude/mistakes.md`
- Full constraints in @docs/rules.md

## Do NOT (extracted from production incidents)
- NEVER update only one dispatcher — always update BOTH dispatcher.py AND swarm_dispatcher.py for new intents
- NEVER use http:// for external APIs — always HTTPS (arXiv HTTP→HTTPS redirect burned rate limit)
- NEVER let tests touch production DBs in data/*.db — always patch DB_PATH to tmp_path
- NEVER wait for Telegram replies in Claude Code sessions — poll the API directly
- NEVER use GitHub Events API for commit counting — use Commits API per-repo
- NEVER rewrite a file without first grepping for all function names used by other modules
- NEVER use == for date filtering on pushed_at — use >= or < comparisons
- NEVER assume Whisper output is lowercase — strip trailing punctuation before regex matching

## Telegram Commands

### Tasks & Productivity
`show tasks` `!! urgent task` `mark X done` `remove X` `plan` `calendar` `check emails` `commits` `trending` `arxiv` `paper 3` `read 1` `briefing` `weekly report` `export`

### Jobs (Jobs Bot)
`scan jobs` `jobs` `apply 1,3,5` `apply all` `reject 2` `job 3` `job stats` `pause jobs` `resume jobs` `search: add title X`

### Budget (Budget Bot)
`spent 15 on lunch` `earned 500 freelance` `saved 100` `set budget groceries 50` `budget` `undo` `recurring: 10 on spotify monthly` `show recurring` `budget-export`

### Hours
`worked 7 hours` `worked six and a half hours` `worked 8h on monday` `saved` `undo hours`

### Remote Control
`run: <cmd>` `git status` `commit: msg` `push` `show: file.py` `logs` `errors` `status` `clear chat`

### Undo
`stop` `cancel` `oops` `undo that` — reverses last command from any bot.

## 5 Telegram Bots

| Bot | Intents | Fallback |
|-----|---------|----------|
| Main | Tasks, calendar, briefing, remote control | Default |
| Budget | Expenses, income, savings, recurring | `TELEGRAM_BUDGET_BOT_TOKEN` |
| Research | arXiv, trending, MindGraph | `TELEGRAM_RESEARCH_BOT_TOKEN` |
| Jobs | Scan, apply, reject, stats | `TELEGRAM_JOBS_BOT_TOKEN` |
| Alert | Send-only (gmail alerts, interviews) | `TELEGRAM_ALERT_BOT_TOKEN` |

All fall back to `TELEGRAM_BOT_TOKEN` if dedicated token not set.

## Job Autopilot

**Daily caps** (March 2026, research-backed):

| Platform | Cap | Notes |
|----------|-----|-------|
| LinkedIn | 15 | Guest API for scanning (httpx, no browser), Playwright only for applying |
| Greenhouse/Lever | 7 | Anti-automation flags, headed mode |
| Indeed/Workday/Generic | 8 | Conservative — aggressive detection |
| Reed | 7 | Official API with 429 retry |
| **Total** | **30** | 20-45s delay between apps, 10min break every 5 |

**Safety:** mutex on `apply_job()`, record-before-submit, pipeline lock, UTC daily caps.

**Verification Wall Learning:** Universal detection (Cloudflare, reCAPTCHA, hCaptcha, text challenges, HTTP blocks). Event-sourced learning with 17 signals per scan session. Statistical correlation engine (zero LLM cost) + periodic GPT-5o-mini analysis (every 5th block). 2hr cooldown with exponential backoff (max 48hr). Human-like page interaction (scroll, mouse, networkidle wait). DB: `data/scan_learning.db`.

**CV/Cover Letter:** ReportLab PDFs (no xelatex). CV always generated upfront. Cover letter generated **lazily** — only when ATS form has a CL upload field (Greenhouse/Lever). Dynamic points from matched projects + LLM polish (~$0.002/call). Auto-uploaded to Google Drive with shareable links synced to Notion Job Tracker. Recruiter emails extracted from JDs and stored in Notion.

**Application Analytics:** `job stats` command shows conversion funnel (Found→Applied→Interview with rates), per-platform breakdown, gate block stats. Weekly report includes same + budget comparison.

**Pre-Screen (5-Gate Recruiter Model):**
Gate 0: Title relevance (instant, pre-LLM) → Gate 1: Kill signals (seniority, primary lang, domain) →
Gate 2: Must-haves (top-5 skills, project evidence, 12+ matches, 65%+ required) →
Gate 3: Competitiveness score (0-100: hard skill 35 + project evidence 25 + coherence 15 + domain 15 + recency 10).
Tiers: reject | skip (<55) | apply (55-74) | strong (75+). LLM calls: ~10/day ($0.23/month vs $5.63 before).
Gate 4A (pre-gen): JD quality (≥200 chars, ≥5 skills, no boilerplate) + Company Blocklist (Notion-curated spam detection) + company background.
Gate 4B (post-gen): Deterministic CV scrutiny (metrics, tone, length) + LLM FAANG recruiter review (≥7/10 → proceed, <7 → Notion "Needs Review").
**Skill Gap Tracker:** Every pre-screened job records missing skills → `skill-gaps` command shows top gaps → CSV export.
**Notion Skill Tracker:** Unverified skills → Notion as "Pending" → user marks "I Know" / "Don't Know" → verified skills auto-sync to profile. Telegram link after every scan.

## API (18 endpoints)

`python -m jobpulse.runner webhook` — Swagger at `localhost:8080/docs`

Papers: `/api/papers/fetch` `/digest` `/stats` `/{index}`
GitHub: `/api/github/commits` `/trending`
Health: `/api/health/status` `/errors` `/agents` `/rate-limits` `POST /export`
Analytics: `/api/analytics/grpo` `/personas` `/costs` `/ab-tests` `/nlp` `/trends`

## Env Vars

**Required:** `OPENAI_API_KEY`

**Telegram:** `TELEGRAM_BOT_TOKEN` `TELEGRAM_CHAT_ID` + optional per-bot tokens (BUDGET, RESEARCH, JOBS, ALERT)

**Platforms:** `SLACK_BOT_TOKEN` `DISCORD_BOT_TOKEN` `DISCORD_USER_ID`

**Notion:** `NOTION_API_KEY` `NOTION_TASKS_DB_ID` `NOTION_RESEARCH_DB_ID` `NOTION_PARENT_PAGE_ID` `NOTION_APPLICATIONS_DB_ID`

**Jobs:** `REED_API_KEY` `GITHUB_TOKEN` `JOB_AUTOPILOT_AUTO_SUBMIT=false` `JOB_AUTOPILOT_MAX_DAILY=10`

**AI:** `JOBPULSE_SWARM=true` `CONVERSATION_MODEL=gpt-5o-mini` `RLM_BACKEND=openai` `RLM_ROOT_MODEL=gpt-5o-mini` `RLM_MAX_BUDGET=0.10`

## Stats

~66,000 LOC | 274 Python files | 5 databases | 614 tests | 3 dashboards | 4 Telegram bots | 3 platforms

> Auto-updated by pre-commit hook. Manual: `python scripts/update_stats.py`

## Docs

- @.claude/mistakes.md — **READ FIRST**
- @docs/rules.md — Constraints, rate limits, anti-detection
- @docs/agents.md — All agents, NLP, budget, salary, A/B testing
- @docs/skills.md — GRPO, persona evolution, RLM, prompt optimization
- @docs/subagents.md — Dynamic agent factory
- @docs/hooks.md — Process trails, memory, logging, export

## Module Context (auto-loaded per directory)
- @jobpulse/CLAUDE.md — JobPulse agents, dispatch, Telegram
- @patterns/CLAUDE.md — 4 LangGraph orchestration patterns
- @mindgraph_app/CLAUDE.md — Knowledge graph, GraphRAG, 3D viz
- @shared/CLAUDE.md — Cross-cutting utilities, NLP, fact-checker
