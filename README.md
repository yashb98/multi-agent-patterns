# Multi-Agent Orchestration + JobPulse + Knowledge MindGraph

Production autonomous agent system: 6 orchestration patterns, 15+ daily automation agents, knowledge graph with 3D visualization, Enhanced Swarm with RLM, multi-platform remote control, Claude Code Telegram approval, NLP intent classification, AI research pipeline with multi-source enrichment.

**~104,000 LOC** | **495 Python files** | **33 databases** | **2716 tests** | **4 dashboards** | **5 Telegram bots** | **3 platforms**

> Stats auto-updated via `scripts/update_stats.py`. Source of truth: [CLAUDE.md](CLAUDE.md).

## Three Integrated Systems

### 1. Orchestration Engine (patterns/)

Six LangGraph patterns for multi-agent coordination, all with mandatory fact-checking:

| Pattern | How It Works | Best For |
|---------|-------------|----------|
| **Hierarchical** | Supervisor routes to workers + fact-checker | Known workflows, speed |
| **Peer Debate** | Agents cross-critique + fact-check each round | Quality-critical tasks |
| **Dynamic Swarm** | Task queue + runtime re-analysis | Unknown complexity |
| **Enhanced Swarm** | Swarm + GRPO + persona + RLM + fact-check | Production (used by JobPulse) |
| **Plan-and-Execute** | Planner decomposes → executor runs steps → replanner adapts | Multi-step reasoning |
| **Map-Reduce** | Fan-out parallel workers → reduce aggregation | Batch processing, summarization |

**Dual convergence gate:** quality score >= 8.0/10 AND factual accuracy >= 9.5/10. Claim-level verification via research notes + web search + cached facts.

### NLP 3-Tier Intent Classification

3-tier pipeline: regex (instant) → semantic embeddings (5ms) → LLM fallback ($0.001). 250+ examples, 41 intents, continuous learning. Full details in [docs/agents.md](docs/agents.md#nlp-intent-classifier-nlp_classifierpy).

### 2. JobPulse Daily Automation (jobpulse/)

Fully autonomous agents running 24/7 via macOS daemon + cron + GitHub Actions backup:

| Agent | What It Does | Schedule |
|-------|-------------|----------|
| Gmail | Classify recruiter emails (rule-based pre-classifier + LLM), send alerts, extract knowledge | 1pm, 3pm, 5pm |
| Calendar | Today + tomorrow events, 2-hour reminders | 9am, 12pm, 3pm |
| GitHub | Yesterday's commits (Commits API), trending repos | 8am briefing |
| arXiv | Daily top 5 AI papers, multi-source trending (HN/Reddit/Bluesky/S2), enrichment (GitHub/S2/HF), Notion pages | 7:57am + on demand |
| Notion | Tasks: create/complete/remove, dedup, priorities, due dates, subtasks, weekly plan | On demand |
| Budget | Parse spending/income/savings, 17 categories, recurring, alerts, undo, Notion sync, category sub-pages, item+store NLP, weekly archival, weekly comparison, historical pace alerts, CSV export | On demand |
| Budget Tracker | Weekly archival (Sunday 7am cron), category sub-page management, weekly comparison engine | Cron + on demand |
| Salary/Hours | Track work hours at £13.99/hr, tax calc, savings suggestion, Notion timesheet | On demand |
| Briefing | Collect all agents → RLM synthesis → Telegram | 8:03am daily |
| Weekly Report | 7-day aggregate across all agents | On demand |
| Voice Handler | Telegram voice → Whisper transcription → dispatch | On demand |
| **Job Autopilot** | 4-gate pre-screen → scan → hybrid skill extract → CV/CL → Gate 4 quality → apply | 7am, 10am, 1pm, 4pm, 7pm |
| **Skill Graph Sync** | GitHub repos + resume + past apps → MindGraph skill/project graph | 3am nightly |
| **Skill Gap Tracker** | Records missing skills across all JDs → CSV export for upskilling | On demand |

### Job Autopilot Pre-Screen (4-Gate Recruiter Model)

Models a senior IT recruiter's 6-30 second screening process. Zero LLM cost — pure deterministic Python.

```
250 raw jobs/day → Gate 0 (title filter) → Hybrid Skill Extraction (rule-based, LLM fallback <10 skills)
→ Gate 1 (kill: seniority ≥5yr, primary lang missing, foreign domain)
→ Gate 2 (must-haves: ≥4/5 top skills, ≥2 projects, ≥20 matches, ≥92% required)
→ Gate 3 (competitiveness 0-100: hard skill 35 + project evidence 25 + coherence 15 + domain 15 + recency 10)
→ Gate 4 (Phase A: JD quality + company blocklist | Phase B: deterministic CV scrutiny + LLM FAANG recruiter review ≥7/10)
→ ~3-5 strong matches/day → CV + Cover Letter (ReportLab, lazy CL generation) → Apply
```

**Result:** 96% fewer LLM calls ($0.23/month vs $5.63). Quality over quantity — only genuinely competitive jobs get applications.

**Skill Gap Tracker:** Every pre-screened job records which skills you're missing. `python -m jobpulse.runner skill-gaps` shows the top gaps ranked by frequency. Export CSV for Google Drive to plan your upskilling.

### Code Intelligence (shared/code_intelligence.py + MCP)

AST-based code graph powering risk-aware review and developer tooling via 20 MCP tools:

| MCP Tool | What It Does | Replaces |
|----------|-------------|----------|
| `find_symbol` | Locate any function/class definition | `grep -rn "def foo"` |
| `callers_of` | Who calls this function | Multi-file grep walks |
| `callees_of` | What does this function call | Manual code reading |
| `impact_analysis` | Blast radius of a change | Recursive grep + guess |
| `risk_report` | High-risk functions needing review | Manual triage |
| `semantic_search` | Find code by meaning, not text | Keyword grep |
| `module_summary` | Overview of a module's structure | Reading every file |
| `recent_changes` | What changed recently | `git log` + manual diff |
| `grep_search` | Ripgrep + code graph enrichment | Raw grep/glob (literal, regex, TODOs) |
| `dead_code_report` | Functions with zero callers | Manual dead code audit |
| `complexity_hotspots` | High-risk + high fan-in functions | Manual triage |
| `dependency_cycles` | Circular module dependencies | Manual dependency tracing |
| `similar_functions` | Semantic duplicate detection | Manual code review |
| `test_coverage_map` | Test coverage per function | Manual grep through tests |
| `call_path` | Shortest path between two functions | Manual call tracing |
| `batch_find` | Find multiple symbols in one call | Multiple grep invocations |
| `boundary_check` | Validate module dependency direction | Manual import auditing |
| `suggest_extract` | Suggest function extraction points | Manual refactoring analysis |
| `rename_preview` | Preview symbol rename impact | Find-and-replace guesswork |
| `diff_impact` | Blast radius of uncommitted changes | Manual diff + grep |

**19,533 nodes, 302,376 edges.** Risk scoring: security keywords, fan-in, test coverage, function size. `grep_search` wraps ripgrep as subprocess and enriches each match with enclosing function, risk score, and caller count — replacing raw Grep/Glob for all codebase searches. One MCP call replaces 5-15 Grep/Glob/Read calls — saves 10-50k tokens per exploration. Semantic search indexes all Python files and .md docs — find code by meaning, not just text.

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
| **Jobs** | Job autopilot status, scan results, application approvals | `TELEGRAM_JOBS_BOT_TOKEN`, `TELEGRAM_JOBS_CHAT_ID` |

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

**Multi-Source Fact Verification**: 3-level pipeline replaces circular LLM-checks-LLM with honest scoring. Level 1: summary vs abstract (scores 0.5 — self-referential). Level 2: external verification via Semantic Scholar (attribution, dates, citations) + SearXNG/DuckDuckGo web search with source credibility scoring (academic > docs > blogs). Level 3: GitHub repo health check (stars, tests, README, staleness). Abstract-only verification scores 5.0/10, not 10.0 — honest by design. Each paper gets a human-readable explanation: "6.2/10 — 3/4 verified externally, exaggerated: '3x faster' (benchmark shows 2.1x), repo exists but no tests". Ralph Loop stores verification experiences for persona evolution.

**Multi-Provider LLM Fallback**: automatic failover chain OpenAI → Anthropic → Ollama. Circuit breakers on OpenAI, Notion, and LinkedIn APIs with automatic recovery. CostEnforcer budget cap halts execution when LLM spend exceeds limit. Telegram alerts on cost spikes and API outages.

**SearXNG Integration**: self-hosted meta-search (Docker Compose + Tor sidecar) wired into fact-checker web verification and briefing agent interview prep. SQLite-cached results with smart Tor routing.

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
python -m jobpulse.runner multi-bot      # Start all 5 Telegram bots
python -m jobpulse.runner stop           # Stop all daemons
python -m jobpulse.runner profile-sync   # Refresh skill/project graph
python -m jobpulse.runner skill-gaps     # Show top missing skills + export CSV
python -m jobpulse.runner skill-gap-export # Export gap report CSV only
python -m jobpulse.runner ext-bridge     # Start Chrome extension WebSocket bridge
python run_all.py "topic"                 # Compare all 6 patterns
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

2266 tests covering command routing, budget parsing (recurring, alerts, undo, item+store NLP, weekly comparison, CSV export, archival), task features (priority, due dates, dedup, subtasks, weekly plan), arXiv agent (fetching, multi-criteria ranking, JSON parsing, storage, fact-check integration), external verifiers (Semantic Scholar, GitHub repo health, quality web search with source credibility), fact-checker (honest scoring, explanation generation, claim routing, multi-source verification, cache), dispatcher routing, swarm logic, GRPO sampling, experience storage, knowledge extraction, email pre-classifier (rules, confidence, evidence, audit, graduation, review flow).

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
| **Gmail Smart Filter** | Designed | `docs/feature-gmail-smart-filter.md` |

The blog pipeline uses 5 agents (Deep Reader → GRPO Writer → Fact Checker → Diagram Generator → Editor) to turn research papers into 2000-word publication-ready posts with workflow diagrams on Notion. Fact-checking now uses the unified `shared/fact_checker.py` with SearXNG/web search verification.

## Recent Features

### Papers Pipeline Overhaul (2026-04-15)
Complete rewrite of the arXiv digest pipeline. Multi-source trending aggregation (Hacker News, Reddit, Bluesky, Semantic Scholar) with tiered fetch and RSS fallback. Enrichment pipeline adds GitHub stars, S2 citation counts, HuggingFace model links, and community buzz scores. Rebalanced `fast_score` weights community, S2, and GitHub signals alongside novelty/significance. Source attribution tracks where each paper was discovered.

### Security Hardening (2026-04-01 → 2026-04-12)
ATS account passwords encrypted at rest using Fernet (was plaintext SQLite). Prompt injection boundary markers for all LLM prompts. Fixed command injection in terminal tool, path traversal in browser screenshots, and Telegram API parameter injection (CRITICAL — chat_id/text). ToolExecutor sandboxed with deny-by-default approval and sliding-window rate limit. Default `max_tokens=4096` on `get_llm()` prevents unbounded output (OWASP LLM10). Sensitive file permissions script (600).

### Reliability & Observability (2026-04-01 → 2026-04-14)
Multi-provider LLM fallback chain (OpenAI → Anthropic → Ollama). Circuit breakers on OpenAI, Notion, LinkedIn APIs. CostEnforcer budget cap halts on spend limit. WAL mode on all SQLite connections (prevents SQLITE_BUSY with concurrent bots). Run ID propagation across all agent logs. Retrieval quality metrics (MRR, NDCG@k, recall@k). Telegram alerts for cost spikes and API outages.

### Gate 4 Application Quality Check (2026-04-05)
Two-phase quality gate after CV generation. Phase A (free, deterministic): JD quality filter (<200 chars, <5 skills, boilerplate), Company Blocklist with Notion curation + spam keyword auto-detect, company background check. Phase B (post-generation): deterministic CV scrutiny (metrics, tone, page limit) + LLM FAANG recruiter review scoring 0-10. Score ≥7 proceeds, <7 goes to Notion "Needs Review" with weaknesses.

### Verification Wall Learning (2026-04-04)
Universal detector for Cloudflare Turnstile, reCAPTCHA, hCaptcha, text challenges, HTTP 403/429. 17 signals tracked per scan session. Statistical correlation engine identifies risk factors (>50% block rate, ≥3 samples). LLM pattern analyzer (GPT-5o-mini every 5th block, ~$0.002/call). Exponential cooldown: 2hr → 4hr → 48hr. Adaptive params adjust delays and human simulation based on risk level.

### 4-Gate Pre-Screen + Skill Gap Tracker (2026-03-30)
Job autopilot now uses a 4-gate recruiter-grade pre-screen modeled after senior IT recruiter behavior. Gate 0: title relevance (pre-LLM). Gate 1: kill signals (seniority, primary language, foreign domain). Gate 2: must-haves (≥4/5 top skills, ≥2 projects with 3+ overlap, ≥20 matches, ≥92% required). Gate 3: competitiveness score (0-100 across 5 dimensions). Hybrid skill extraction uses a 582-entry taxonomy first (free), LLM fallback only when < 10 skills found (15% of JDs). **Result:** 250 → 10-11 LLM calls/day (96% reduction), $5.63 → $0.23/month. Skill gap tracker records every missing skill across all scanned JDs and exports ranked CSV for Google Drive upskilling. 7-day experiment running 2026-03-31 → 2026-04-06.

### Gmail Pre-Classifier (2026-03-27)
Rule-based email triage that eliminates 70-85% of unnecessary LLM calls. Static rules match sender patterns, domain patterns, subject keywords, and dual subject+body patterns. Evidence-based attribution on every decision. Adaptive audit decay (50% → 10%). Telegram review flow with ✅/❌/🔄 for user feedback. Auto-graduates when rule accuracy exceeds 95%.

### Fact-Check Accuracy 9.5+/10 (2026-03-27)
Mandatory fact-checking across all 4 orchestration patterns. Extracts verifiable claims from drafts, verifies against research notes + paper abstracts + DuckDuckGo web search. Deterministic scoring with hard accuracy gate (9.5/10). Targeted revision notes give the writer specific claim-level fix instructions. Verified facts cached in SQLite for reuse.

### Hybrid Fact Verification + Multi-Criteria Ranking (2026-03-28)
Major upgrade to arXiv ranking and fact-checking. **Multi-criteria scoring**: papers ranked on 4 weighted dimensions (novelty 30%, significance 25%, practical 30%, breadth 15%) instead of a single flat score. **3-level fact verification**: Semantic Scholar for attribution/date claims, quality web search with source credibility scoring for benchmark/comparison claims, GitHub repo health check (stars, tests, staleness). **Honest scoring**: abstract-only verification scores 0.5 (not 1.0) — a paper verified only against itself gets 5.0/10. **Human-readable explanations**: "6.2/10 — 3/4 verified externally, exaggerated: '3x faster' (shows 2.1x), repo exists but no tests". **82 tests** across 3 test files. Benchmark improved from 5.0/10 to 10.0/10.

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
| Gmail Smart Filter | Designed | [docs/feature-gmail-smart-filter.md](docs/feature-gmail-smart-filter.md) |
| Notion Budget v2 | Implemented | [docs/feature-notion-budget-v2.md](docs/feature-notion-budget-v2.md) |
| NLP Intent Classification | Implemented | [docs/feature-nlp-intent.md](docs/feature-nlp-intent.md) |
| arXiv Digest | Implemented | [docs/feature-arxiv-digest.md](docs/feature-arxiv-digest.md) |
| Gmail Pre-Classifier | Implemented | [docs/feature-gmail-preclassifier.md](docs/feature-gmail-preclassifier.md) |
| Fact-Check Accuracy 9.5+ | Implemented | [docs/feature-fact-check-accuracy.md](docs/feature-fact-check-accuracy.md) |
| Hybrid Fact Verification | Implemented | [docs/superpowers/specs/2026-03-28-hybrid-fact-verification-design.md](docs/superpowers/specs/2026-03-28-hybrid-fact-verification-design.md) |
| Multi-Criteria arXiv Ranking | Implemented | [docs/superpowers/specs/2026-03-28-arxiv-ranking-fact-checking-design.md](docs/superpowers/specs/2026-03-28-arxiv-ranking-fact-checking-design.md) |
| 4-Gate Pre-Screen Pipeline | Implemented | [docs/superpowers/specs/2026-03-30-job-pipeline-api-optimization-design.md](docs/superpowers/specs/2026-03-30-job-pipeline-api-optimization-design.md) |
| Papers Pipeline Overhaul | Implemented | [docs/superpowers/specs/2026-04-15-paper-pipeline-overhaul-design.md](docs/superpowers/specs/2026-04-15-paper-pipeline-overhaul-design.md) |
| Code Intelligence | Implemented | [docs/superpowers/specs/2026-04-04-code-intelligence-design.md](docs/superpowers/specs/2026-04-04-code-intelligence-design.md) |
| Chrome Extension Job Engine | Implemented | [docs/superpowers/specs/2026-04-03-chrome-extension-job-engine-design.md](docs/superpowers/specs/2026-04-03-chrome-extension-job-engine-design.md) |
| LinkedIn Live Apply | Implemented | [docs/superpowers/specs/2026-04-01-linkedin-live-apply-design.md](docs/superpowers/specs/2026-04-01-linkedin-live-apply-design.md) |
| Semantic Search Upgrade | Implemented | [docs/superpowers/specs/2026-04-05-semantic-search-upgrade-design.md](docs/superpowers/specs/2026-04-05-semantic-search-upgrade-design.md) |
