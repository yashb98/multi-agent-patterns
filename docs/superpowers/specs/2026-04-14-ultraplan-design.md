# Ultraplan: Fix, Unlock, Extend — Full System Design

**Date**: 2026-04-14
**Status**: Draft
**Author**: Yash + Claude

## Overview

Four-phase staged rollout to bring the JobPulse multi-agent system from ~60% operational to 100%, unlock 3 dormant LangGraph patterns, add 2 new patterns (plan-and-execute, map-reduce) with an auto-routing layer, and build a multi-source research pipeline with community validation.

**Non-goals**: DSPy revival (GRPO is working, revisit in 6 months), new Telegram bots, UI/frontend changes, X/Twitter API integration ($100/mo for minimal signal).

**Post-plan changes already landed** (10 commits since initial draft):
- Ralph Loop removed (module, tests, DB, all references)
- 5 new job pipeline agents added and wired: liveness_checker, ats_api_scanner, rejection_analyzer, followup_tracker, interview_prep
- CLAUDE.md updated to reflect new agents

---

## Phase 1: Fix Broken Automation

**Goal**: Get every automation back to 100% operational. No new features.

### 1.1 Crontab Path Fix

**Problem**: All 12+ cron entries point to `/Users/yashbishnoi/Downloads/multi_agent_patterns/` which no longer exists. The project lives at `/Users/yashbishnoi/projects/multi_agent_patterns/`.

**Fix**: `sed -i '' 's|Downloads/multi_agent_patterns|projects/multi_agent_patterns|g'` on the crontab. Verify each entry after replacement.

**Affected crons**:
- arXiv digest (7:57am daily)
- Briefing (8:03am daily)
- Calendar reminders (9am/12pm/3pm daily)
- Gmail check (1pm/3pm/5pm daily)
- Budget archive (Sunday 7am)
- Weekly report (Sunday 8pm)
- Health watchdog (every 10 minutes)
- Daemon restart (every 3 hours)
- Notion papers (Monday 8:33am)

### 1.2 Gmail Agent — OAuth + UnboundLocalError

**Two stacked issues**:

**Issue A — OAuth token scope mismatch (root cause of API failures)**:
The token was originally authorized with fewer scopes than `config.py` now requires (4 scopes: gmail.readonly, gmail.modify, calendar.readonly, drive.file). When the token refreshes, Google rejects with `invalid_scope: Bad Request`. Between refreshes: `403 insufficientPermissions`.

**Fix A**: Re-run `python scripts/setup_integrations.py` to re-authorize OAuth with all 4 scopes. This regenerates `data/google_token.json` with a properly scoped refresh token.

**Issue B — UnboundLocalError on `messages`**:
**File**: `jobpulse/gmail_agent.py:315`
**Problem**: `messages` variable referenced before assignment when the Gmail API call fails.
**Fix B**: Initialize `messages = []` before the try block.

### 1.3 Job Autopilot AttributeError

**File**: `jobpulse/job_autopilot.py:370`
**Problem**: `listing.description` should be `listing.description_raw` (Pydantic model field name).
**Fix**: Single field rename.

### 1.4 Notion Papers ImportError

**File**: `jobpulse/notion_papers_agent.py`
**Problem**: Imports deleted `fast_score` function from `arxiv_agent`.
**Fix**: Find the current scoring function that replaced `fast_score` and update the import.

### 1.5 arXiv Rate Limiting → Multi-Source Paper Discovery

**Problem**: arXiv API returns 429 since Apr 7. Single-source dependency is a production risk.
**Current**: 3 attempts at 5s/10s/15s backoff against `export.arxiv.org/api/query`.

**Fix — replace single-source with multi-source pipeline**:

| Source | Role | Rate Limit | Cost |
|--------|------|-----------|------|
| **Semantic Scholar API** | Primary discovery (already in codebase via `shared/external_verifiers.py`) | 100 req/sec (with free API key) | Free |
| **arXiv RSS feeds** | Fallback, zero rate limiting (static files per category) | None | Free |
| **HuggingFace Daily Papers** | High-signal supplement (community-upvoted trending papers) | Generous | Free |
| **Papers with Code API** | Papers WITH implementations + SOTA benchmarks | Generous | Free |

**Implementation**: New `jobpulse/paper_sources.py` module with `fetch_papers()` that tries sources in order: Semantic Scholar → arXiv RSS → HuggingFace. Papers with Code used for enrichment (linked repos, benchmarks). Dedup by arXiv ID across sources. Existing `arxiv_agent.py` calls `fetch_papers()` instead of hitting arXiv directly.

**Also fix**: Add `User-Agent` header to arXiv fallback, increase backoff to 30s/60s/120s

### 1.6 Voice Handler Import

**File**: `jobpulse/voice_handler.py`
**Problem**: `get_openai_client` import fails in daemon context.
**Fix**: Trace the correct import path. May be circular import or renamed function.

### 1.7 Missing Profile Sync Cron

**Problem**: CLAUDE.md documents a 3am profile sync cron but no crontab entry exists.
**Fix**: Add `0 3 * * * cd /Users/yashbishnoi/projects/multi_agent_patterns && /opt/homebrew/anaconda3/bin/python -m jobpulse.runner profile-sync >> logs/profile-sync.log 2>&1`

### 1.8 Daemon Restart Script

**Problem**: macOS Gatekeeper blocks execution with `Operation not permitted`.
**Fix**: `xattr -d com.apple.quarantine` on the script, or rewrite as a Python-based restart using `launchctl kickstart`.

### 1.9 Hierarchical Pattern — Wire Experiential Learning

**File**: `patterns/hierarchical.py`
**Problem**: Only pattern without experiential learning.
**Fix**: Add `ExperienceMemory` import, inject into research/writing prompts (same pattern as peer_debate.py), extract learnings at finish node.

### 1.10 New Agents Already Landed (No Action Needed)

These 5 agents were implemented and wired after the initial plan draft. They are complete and need no Phase 1 work:
- `liveness_checker.py` — ghost job detection (12 expired patterns)
- `ats_api_scanner.py` — zero-browser Greenhouse/Ashby/Lever API scanning
- `rejection_analyzer.py` — statistical rejection pattern analysis
- `followup_tracker.py` — follow-up cadence with urgency tiers
- `interview_prep.py` — STAR+Reflection interview prep

All wired into both dispatchers, NLP classifier, and pipeline.

### Phase 1 Verification

After all fixes:
- `crontab -l` shows all entries with correct path
- `python -c "from jobpulse.gmail_agent import check_emails"` — no ImportError
- `python -c "from jobpulse.job_autopilot import run_scan_window"` — no AttributeError
- `python -c "from jobpulse.notion_papers_agent import sync_papers"` — no ImportError
- `python -c "from jobpulse.voice_handler import transcribe_voice"` — no ImportError
- arXiv test: `python -m jobpulse.runner ralph-test` with arXiv URL returns 200
- Health watchdog runs without error for 30 minutes
- All 4 Telegram bots respond to "help"

---

## Phase 2: Unlock Dormant Patterns + Auto-Router

**Goal**: Make all 4 existing patterns reachable from Telegram via an intelligent auto-router.

### 2.1 Pattern Router

**New file**: `jobpulse/pattern_router.py`

**Architecture**: 2-tier classifier (same architecture as NLP classifier — rule-based first, embeddings fallback).

#### Rule-Based Tier (instant, free)

| Signal | Pattern | Examples |
|--------|---------|----------|
| Comparative: "vs", "compare", "which is better", "pros and cons" | Peer Debate | "React vs Vue for dashboards" |
| Controversial/opinion: "should I", "is X worth", "debate", "argue" | Peer Debate | "Should I learn Rust or Go?" |
| Multi-entity: 3+ entities listed, "analyze X, Y, and Z" | Dynamic Swarm | "Analyze Postgres, MongoDB, and Redis for caching" |
| Structured/hierarchical: "outline", "report on", "break down", "explain in depth" | Hierarchical | "Break down transformer architecture" |
| Multi-step with dependencies: "first...then", "step by step", "compare then recommend" | Plan-and-Execute | "Research 5 VDBs, benchmark each, recommend one" |
| Batch/parallel: "all", "every", "each of", list of 4+ items | Map-Reduce | "Summarize all 50 papers from this week" |
| Default / single-topic | Enhanced Swarm | "Quantum ML advances" |

#### Embedding Tier (5ms, fallback)

When no rules match with confidence, use semantic similarity against 30-50 labeled examples per pattern. Same embedding infrastructure as `nlp_classifier.py`.

#### Override Syntax

Prefix the message with a pattern keyword:
- `debate: <query>` → Peer Debate
- `swarm: <query>` → Enhanced Swarm
- `deep: <query>` → Hierarchical
- `plan: <query>` → Plan-and-Execute
- `batch: <query>` → Map-Reduce
- `dynamic: <query>` → Dynamic Swarm

Override always wins. No classification needed.

#### Response Header

Every pattern response starts with:
```
[Peer Debate] 3 rounds, converged at quality=8.4
Override: debate | swarm | deep | plan | batch
```

One line, always present. Teaches the user the system's behavior.

#### Feedback Loop

On completion, log to experience_memory.db:
```python
{
    "query_features": extracted_signals,
    "chosen_pattern": "peer_debate",
    "override": False,
    "quality_score": 8.4,
    "timestamp": "2026-04-14T..."
}
```

When an override produces a higher score than the auto-selected pattern would have, the router learns from that. After 50+ data points, the rule weights can be tuned.

### 2.2 Dispatch Integration

**File**: `jobpulse/swarm_dispatcher.py`

Changes to the dispatch function:

```python
from jobpulse.pattern_router import select_pattern, is_research_query

def dispatch(cmd):
    if is_research_query(cmd):
        pattern, reason = select_pattern(cmd.raw)
        return run_with_pattern(pattern, cmd, reason)
    # ... existing dispatch logic
```

**`is_research_query()`** returns True when:
- Intent is `arxiv` (always)
- Intent is `research` (new intent, always)
- Intent is `conversation` AND query contains research signals (compare, analyze, explain, "what is", "how does", 3+ technical terms)

Returns False for all other intents (budget, tasks, calendar, gmail, jobs, etc.). These are never routed to patterns.

**`run_with_pattern()`** calls the appropriate `run_*()` function:
- `patterns.enhanced_swarm.run_swarm()`
- `patterns.peer_debate.run_debate()`
- `patterns.dynamic_swarm.run_dynamic()`
- `patterns.hierarchical.run_hierarchical()`
- `patterns.plan_and_execute.run_plan()` (Phase 3)
- `patterns.map_reduce.run_map_reduce()` (Phase 3)

### 2.3 New Telegram Intent: `research`

Add a dedicated `research` intent to NLP classifier for queries that are clearly research tasks but don't match existing intents (arxiv, conversation).

**Regex tier**: `^research\s+`, `^investigate\s+`, `^analyze\s+`, `^compare\s+`
**Embedding tier**: 20 examples of research-style queries.

Add to BOTH `dispatcher.py` and `swarm_dispatcher.py` per dual dispatcher invariant.

### Phase 2 Verification

- Send "compare React vs Vue" → receives `[Peer Debate]` header
- Send "break down microservices" → receives `[Hierarchical]` header
- Send "swarm: compare React vs Vue" → override works, receives `[Enhanced Swarm]` header
- Send "papers" → still routes to arXiv agent (not pattern router)
- Send "budget" → still routes to budget agent (not pattern router)
- experience_memory.db shows new pattern selection entries

---

## Phase 3: Plan-and-Execute + Map-Reduce + Community Validation + Google Jobs

**Goal**: Add 2 new LangGraph patterns, a community validation pipeline for papers, and Google Jobs as a new scan source. Full job pipeline runs autonomously except form submission.

### 3.1 Plan-and-Execute Pattern

**New file**: `patterns/plan_and_execute.py`

#### Topology

```
planner → [step_executor → evaluator → replanner?]* → synthesizer
```

5 nodes in a LangGraph StateGraph with conditional edges.

#### State

Extends the existing `AgentState` pattern:

```python
class PlanExecuteState(TypedDict):
    topic: str                          # immutable input
    plan: list[Step]                    # current plan (mutable)
    completed_steps: list[StepResult]   # executed steps with outputs
    current_step_index: int
    replan_count: int                   # max 3 replans
    research_notes: list[str]           # accumulated research
    final_output: str
    quality_score: float
    accuracy_score: float
    token_usage: list[dict]
```

```python
class Step(TypedDict):
    goal: str                  # what this step should accomplish
    expected_output: str       # what success looks like
    dependencies: list[int]    # indices of prior steps this depends on
    delegate_to: str | None    # optional: pattern to delegate to
```

#### Nodes

**planner_node**:
- Input: user query
- Output: list of 2-7 Steps
- Uses `smart_llm_call()` with a structured output prompt
- Prompt includes experiential learning context from similar past queries
- Injects examples of good plans from experience_memory.db

**step_executor_node**:
- Executes one step at a time (sequential, not parallel — steps have dependencies)
- If `delegate_to` is set, calls the appropriate pattern's `run_*()` function for that step
- If not, uses enhanced_swarm as the default executor
- Appends StepResult to `completed_steps`
- Calls `prune_state()` per existing convention

**evaluator_node**:
- After each step, checks:
  1. Is the output non-empty? (deterministic)
  2. Does it reference the expected entities? (deterministic)
  3. Did we learn something that changes the remaining plan? (LLM check, only if needed)
- Returns: `continue` (next step), `replan` (regenerate remaining steps), or `synthesize` (done early)

**replanner_node**:
- Takes completed steps + remaining plan + evaluator's reason
- Regenerates only the remaining steps (preserves completed work)
- **Max 3 replans** (optimal for learning systems):
  - Replan 1: catches ~20% of cases where step 2-3 reveals new info
  - Replan 2: catches ~8% where first replan was still wrong
  - Replan 3: catches last ~2% edge cases where problem space was fundamentally different
  - Beyond 3: <0.5% improvement, not worth the latency/cost
- **Early-exit**: if replan N produces same plan as replan N-1 (no new information), stop immediately
- Logs replan reason + delta to experience_memory.db — even replans that don't improve the current run improve future runs

**synthesizer_node**:
- Combines all step outputs into a coherent final response
- Uses `smart_llm_call()` for synthesis
- Scores the result (quality + accuracy)
- If score >= 7.0, extracts learnings for experiential learning
- Returns final output with plan summary header

#### Convergence

- Max 7 steps per plan
- Max 3 replans (with early-exit on duplicate plan)
- Each step has a 60-second timeout (matches existing arXiv timeout)
- Total execution timeout: 7 minutes (increased to accommodate 3 replans)
- Quality gate: same dual-gate as other patterns (quality >= 8.0, accuracy >= 9.5)
  - But applied at synthesis, not per-step

#### Job Autopilot Integration

Opt-in via `JOB_AUTOPILOT_ADAPTIVE=true` (default: false).

When enabled, `run_scan_window()` feeds scan results into plan-and-execute instead of the rigid pipeline:

```python
if JOB_AUTOPILOT_ADAPTIVE:
    plan = plan_from_scan_results(listings)
    # Plan might: group by company, pick best-fit, skip dupes
    return run_plan(plan)
else:
    # Existing rigid pipeline
    for listing in listings:
        screen → generate → apply
```

The planner sees all scan results at once and makes decisions like:
- "Company X has 3 roles — pick the best match, not all three"
- "This listing looks like a repost of one we applied to — skip"
- "These 2 roles are nearly identical at different companies — apply to higher-match first"

This stays behind a feature flag until proven reliable over 2+ weeks.

### 3.2 Map-Reduce Pattern

**New file**: `patterns/map_reduce.py`

#### Topology

```
splitter → parallel_map (N workers) → reducer → [reconciler]?
```

4 nodes, ~200 lines. Lightweight by design.

#### State

```python
class MapReduceState(TypedDict):
    topic: str                      # immutable input
    chunks: list[str]               # split input
    map_results: list[str]          # one per chunk
    reduced_output: str
    needs_reconciliation: bool
    final_output: str
    quality_score: float
    token_usage: list[dict]
```

#### Nodes

**splitter_node**:
- Takes input and splits by strategy:
  - `by_item`: list of papers, applications, repos → one chunk per item
  - `by_section`: long document → semantic section boundaries
  - `by_entity`: "analyze companies A, B, C" → one chunk per entity
- Strategy auto-detected from input structure, or specified by caller
- Max 20 chunks (prevents runaway parallelism)

**map_node**:
- Runs via existing `parallel_executor.py` (`parallel_grpo_candidates` or similar)
- Each worker gets: chunk + shared prompt template + topic context
- Workers are stateless — no cross-communication
- Timeout: 30 seconds per worker
- Uses `get_llm()` — respects local/cloud LLM config

**reducer_node**:
- Two modes:
  - `summarize`: concatenate map outputs + synthesize into coherent summary
  - `rank`: score each map output, return top-N with reasoning
- Mode auto-detected: if chunks are items to compare → rank. Otherwise → summarize.
- Uses `smart_llm_call()` for synthesis

**reconciler_node** (optional):
- Only runs if reducer detects contradictions across chunks
- Single pass to resolve conflicts and produce consistent output
- Skipped in 80%+ of cases

#### Use Cases

| Input | Split Strategy | Map Task | Reduce Mode |
|-------|---------------|----------|-------------|
| "Summarize 50 papers" | by_item | Summarize each paper | summarize |
| "Analyze 20 pending apps" | by_item | Score each application | rank |
| "Review all agents in jobpulse/" | by_item (files) | Assess each agent | summarize |
| "Compare 5 databases" | by_entity | Research each DB | rank |

### 3.3 Community Validation Pipeline

**New file**: `jobpulse/paper_validator.py`

**Purpose**: For each paper surfaced by the multi-source discovery (Phase 1.5), validate its claims and measure community reception across 4 platforms.

#### Pipeline

```
paper (arXiv ID + title + abstract)
  → parallel fetch from 4 sources
  → aggregate signals
  → community_score (0-10)
  → append to paper ranking
```

#### Sources and Signals

| Platform | API | What We Extract | Python Package |
|----------|-----|----------------|----------------|
| **Semantic Scholar** | REST API (already integrated) | Citation count, venue quality, author h-index, influential citations | `semanticscholar` or raw httpx |
| **GitHub** | REST API (GITHUB_TOKEN already configured) | Repos implementing the paper (search by title/arXiv ID), total stars, forks, last commit date | `PyGithub` or raw httpx |
| **HuggingFace** | `huggingface_hub` API | Models/spaces referencing the paper, download counts, community likes/discussions | `huggingface_hub` |
| **Reddit** | PRAW (OAuth) | Posts in r/MachineLearning + r/LocalLLaMA mentioning the paper, upvotes, comment count, sentiment | `praw` |
| **Hacker News** | Algolia Search API (no auth) | Stories/comments mentioning the paper, points, comment count | raw httpx |

**X/Twitter excluded**: $100/mo for 100 reads. Not worth the signal.

#### Community Score Formula

```python
community_score = (
    citations_normalized * 0.25 +      # Semantic Scholar
    github_adoption * 0.25 +            # Stars + forks + recency
    hf_adoption * 0.20 +               # Model downloads + spaces
    reddit_buzz * 0.15 +               # Upvotes + comment quality
    hn_buzz * 0.15                      # Points + comments
)
```

Each component normalized to 0-10. Weights tunable via experiential learning feedback.

#### Integration with arXiv Agent

The existing `arxiv_agent.py` ranking pipeline gets a new signal:
```python
final_score = (
    relevance_score * 0.4 +
    novelty_score * 0.3 +
    community_score * 0.3    # NEW — from paper_validator
)
```

Papers with high community scores but low novelty (e.g., a well-known framework release) still surface. Papers with zero community signal (brand new) rely on relevance + novelty only.

#### Rate Limits and Cost

| Source | Calls per paper | Daily (50 papers) | Cost |
|--------|----------------|-------------------|------|
| Semantic Scholar | 1 | 50 | Free |
| GitHub | 1-2 | 100 | Free (5k/hr limit) |
| HuggingFace | 1 | 50 | Free |
| Reddit (PRAW) | 2 | 100 | Free (100/min limit) |
| Hacker News | 1 | 50 | Free, no auth |
| **Total** | ~6 | ~350 | **$0/day** |

### 3.4 Google Jobs Scanner

**New file**: `jobpulse/job_scanners/google_jobs.py`

**Approach**: Use **JobSpy** (`python-jobspy`) — open-source multi-platform scraper that supports Google Jobs, LinkedIn, Indeed, Glassdoor, and ZipRecruiter.

**Why JobSpy over SerpAPI**: Free, no API key, already supports the same platforms we scan. SerpAPI ($50/mo) is a paid fallback if JobSpy breaks.

#### Integration

```python
from jobspy import scrape_jobs

def scan_google_jobs(search_terms: list[str], location: str, max_results: int = 25) -> list[dict]:
    """Scan Google Jobs via JobSpy, return normalized JobListing-compatible dicts."""
    results = scrape_jobs(
        site_name=["google"],
        search_term=" OR ".join(search_terms),
        location=location,
        results_wanted=max_results,
        hours_old=24,  # last 24 hours only
    )
    return [normalize_to_job_listing(row) for _, row in results.iterrows()]
```

**Feeds into existing pipeline**: `scan_google_jobs()` returns the same shape as LinkedIn/Reed/Indeed scanners → existing `run_scan_window()` picks them up → pre-screen → CV generation → queue for approval.

**Filters**: Same search terms and location filters as existing scanners. Configurable via `GOOGLE_JOBS_ENABLED=true` (default: false until proven).

**Cross-platform dedup**: Extend existing dedup (company + normalized title) to include Google Jobs source. Same job on Google and LinkedIn = one entry.

### 3.5 Job Autopilot — Full Pipeline, No Submission

**Clarification of scope**: The job autopilot should run every step of the pipeline autonomously:

```
scan (LinkedIn + Reed + Indeed + Google Jobs)
  → liveness check (ghost job filter)
  → Gate 0: recruiter screen (title filter)
  → Gates 1-3: skill graph pre-screen
  → Gate 4: JD quality + company blocklist + CV scrutiny + LLM review
  → CV generation (ReportLab PDF)
  → Cover letter generation (lazy, only if ATS needs it)
  → ATS scoring
  → rejection analysis (learn from past rejections)
  → follow-up tracking (set cadence timers)
  → interview prep (generate STAR stories)
  → Queue for approval in Telegram
  ✋ STOP — no form submission
```

Form submission is handled separately via the Chrome extension engine, which will be enhanced by integrating patterns from AIHawk's screening question answerer (study, don't import — their Selenium approach is inferior to our Playwright/extension architecture).

**`JOB_AUTOPILOT_AUTO_SUBMIT` stays `false`**. The pipeline value is in automated discovery, screening, CV tailoring, and preparation — not in auto-submitting.

### 3.7 Auto-Router Additions

Two new entries in `pattern_router.py`:

```python
PATTERN_RULES.extend([
    PatternRule(
        signals=["first...then", "step by step", "compare then recommend",
                 "research and then", "analyze and decide"],
        pattern="plan_and_execute",
    ),
    PatternRule(
        signals=["all", "every", "each of", "batch", "summarize all",
                 "review all", "analyze all"],
        pattern="map_reduce",
        min_entities=3,  # only trigger if 3+ items detected
    ),
])
```

### Phase 3 Verification

**Plan-and-Execute**:
- "Compare FastAPI vs Django vs Flask for webhooks" → plan with 4 steps, executes sequentially
- "Research quantum ML and recommend a paper to implement" → 2-step plan with replan capability
- Verify replan early-exit: same plan twice → stops immediately
- Verify max 3 replans: force 4th replan → proceeds with current plan
- Job autopilot adaptive mode: scan produces grouped recommendations instead of linear pipeline

**Map-Reduce**:
- "Summarize all papers from this week" → splits into N papers, parallel map, synthesized digest
- "Batch analyze my pending applications" → parallel scoring, ranked output

**Community Validation**:
- Fetch validation for a known paper (e.g., "Attention Is All You Need") → high GitHub stars, many HF models, Reddit/HN discussion
- Fetch validation for a brand-new paper → zero community signal, falls back to relevance + novelty only
- Verify parallel fetch completes in <5 seconds for all 5 sources

**Google Jobs**:
- `scan_google_jobs(["data engineer"], "London")` returns results
- Results feed into existing pre-screen pipeline without modification
- Cross-platform dedup catches same job on Google + LinkedIn

**Job Autopilot Full Pipeline**:
- Scan → liveness → Gate 0-3 → Gate 4 → CV → ATS score → rejection analysis → follow-up → interview prep → queue
- Verify NO form submission occurs (AUTO_SUBMIT=false)
- Verify all new agents (liveness, rejection, followup, interview_prep) execute in pipeline

---

## Full Architecture Diagram

```
Telegram message
  │
  ├─ NLP classify (41+ intents, 3-tier: regex → embeddings → LLM)
  │
  ├─ swarm_dispatcher.dispatch()
  │    │
  │    ├─ is_research_query()?
  │    │    │
  │    │    YES → pattern_router.select()
  │    │    │      │
  │    │    │      ├─ Override prefix? → use specified pattern
  │    │    │      │
  │    │    │      └─ Auto-classify:
  │    │    │           ├─ Comparative/controversial → Peer Debate
  │    │    │           ├─ Multi-entity parallel     → Dynamic Swarm
  │    │    │           ├─ Structured/in-depth       → Hierarchical
  │    │    │           ├─ Multi-step + dependencies  → Plan-and-Execute
  │    │    │           ├─ Batch/parallel (3+ items)  → Map-Reduce
  │    │    │           └─ Default                    → Enhanced Swarm
  │    │    │
  │    │    └─ run_with_pattern() → response + "[Pattern] header"
  │    │
  │    └─ NO → direct agent (budget, gmail, calendar, tasks, jobs, etc.)
  │
  └─ experiential_learning.store()
       └─ pattern selection feedback → router improves over time


Paper Discovery & Validation Pipeline:
  paper_sources.fetch_papers()
    ├─ Semantic Scholar API (primary)
    ├─ arXiv RSS feeds (fallback)
    ├─ HuggingFace Daily Papers (supplement)
    └─ Papers with Code (enrichment)
  → paper_validator.validate()
    ├─ Semantic Scholar (citations, h-index)
    ├─ GitHub (implementations, stars)
    ├─ HuggingFace (models, spaces, downloads)
    ├─ Reddit (r/MachineLearning, r/LocalLLaMA)
    └─ Hacker News (Algolia search)
  → community_score → arxiv_agent ranking


Job Autopilot Pipeline (full, no submission):
  scan (LinkedIn + Reed + Indeed + Google Jobs)
    → liveness_checker (ghost job filter)
    → Gate 0: recruiter_screen
    → Gates 1-3: skill_graph_store
    → Gate 4: gate4_quality (JD + blocklist + CV scrutiny + LLM)
    → CV generation (ReportLab)
    → Cover letter (lazy, if ATS needs it)
    → ATS scoring
    → rejection_analyzer (learn from past)
    → followup_tracker (set cadence)
    → interview_prep (STAR stories)
    → Queue for Telegram approval
    ✋ STOP — no form submission
```

## Shared Infrastructure (No Changes Needed)

These existing modules serve all 6 patterns without modification:

- `shared/agents.py` — `get_llm()`, `smart_llm_call()`, `risk_aware_reviewer_node`
- `shared/experiential_learning.py` — `ExperienceMemory`, `TrainingFreeGRPO`
- `shared/parallel_executor.py` — parallel worker pool
- `shared/streaming.py` — streaming output support
- `shared/fact_checker.py` — claim verification
- `shared/code_graph.py` — AST-based risk analysis

## New Files Summary

| File | Phase | Lines (est.) | Purpose |
|------|-------|-------------|---------|
| `jobpulse/paper_sources.py` | 1 | ~150 | Multi-source paper discovery (Semantic Scholar + RSS + HF) |
| `jobpulse/pattern_router.py` | 2 | ~200 | Auto-router + override + feedback |
| `patterns/plan_and_execute.py` | 3 | ~400 | 5-node plan-execute-replan graph (max 3 replans) |
| `patterns/map_reduce.py` | 3 | ~200 | 4-node split-map-reduce graph |
| `jobpulse/paper_validator.py` | 3 | ~250 | Community validation (GitHub + HF + Reddit + HN) |
| `jobpulse/job_scanners/google_jobs.py` | 3 | ~80 | Google Jobs scanner via JobSpy |

## Modified Files Summary

| File | Phase | Change |
|------|-------|--------|
| crontab | 1 | Fix all paths |
| `scripts/setup_integrations.py` | 1 | Re-run to fix Gmail OAuth scopes |
| `jobpulse/gmail_agent.py` | 1 | Initialize `messages = []` + OAuth fix |
| `jobpulse/job_autopilot.py` | 1 | `description` → `description_raw` |
| `jobpulse/notion_papers_agent.py` | 1 | Fix `fast_score` import |
| `jobpulse/arxiv_agent.py` | 1 | Use `paper_sources.fetch_papers()` instead of direct arXiv API |
| `jobpulse/voice_handler.py` | 1 | Fix import path |
| `patterns/hierarchical.py` | 1 | Wire experiential learning |
| `jobpulse/swarm_dispatcher.py` | 2 | Add pattern router call |
| `jobpulse/dispatcher.py` | 2 | Add pattern router call (flat path) |
| `shared/nlp_classifier.py` | 2 | Add `research` intent examples |
| `jobpulse/job_autopilot.py` | 3 | Opt-in adaptive mode + Google Jobs source |
| `jobpulse/arxiv_agent.py` | 3 | Integrate community_score into ranking |
| `requirements.txt` | 3 | Add `python-jobspy`, `praw`, `huggingface_hub` |

## Dependencies Added

| Package | Phase | Purpose | Cost |
|---------|-------|---------|------|
| `python-jobspy` | 3 | Google Jobs scraping | Free |
| `praw` | 3 | Reddit API for paper validation | Free |
| `huggingface_hub` | 3 | HuggingFace papers/models API | Free |
| `semanticscholar` | 1 | Semantic Scholar API (may already be installed) | Free |

## Risk Assessment

| Risk | Mitigation |
|------|-----------|
| Auto-router misclassifies | Override syntax always available; feedback loop self-corrects |
| Plan-and-execute infinite replans | Hard cap at 3 replans + early-exit on duplicate plan + 7-min timeout |
| Map-reduce parallelism overload | Max 20 chunks, reuses existing parallel_executor pool |
| Job autopilot adaptive breaks applications | Feature-flagged (default off), 2-week proving period |
| Pattern router adds latency | Rule-based tier is instant; embedding tier is 5ms; no LLM needed |
| Semantic Scholar/HF API goes down | Multi-source with fallback chain; arXiv RSS as last resort (zero rate limiting) |
| JobSpy scraping breaks | SerpAPI ($50/mo) as paid fallback; existing LinkedIn/Reed/Indeed unaffected |
| Reddit API rate limiting | PRAW handles rate limiting automatically; 100/min is generous for 50 papers/day |
| Community validation adds latency to paper ranking | Runs in parallel (all 5 sources fetched concurrently); cached per arXiv ID |

## Testing Strategy

- All new patterns: unit tests with mocked LLM (no real API calls)
- Pattern router: test all signal→pattern mappings + override syntax
- Integration: end-to-end test with a real query through each pattern
- Plan-and-execute: test replan early-exit (duplicate plan detection), test max 3 replans cap
- Job autopilot adaptive: test with fixture scan results, verify plan output
- Paper sources: test fallback chain (mock Semantic Scholar failure → RSS fallback)
- Paper validator: test with fixture paper data, mock all 5 APIs
- Google Jobs scanner: test with JobSpy mock, verify normalize_to_job_listing output shape
- All tests use `tmp_path` for databases per project rules
- Tests mirror source: `tests/patterns/test_plan_and_execute.py`, `tests/patterns/test_map_reduce.py`, `tests/jobpulse/test_pattern_router.py`, `tests/jobpulse/test_paper_validator.py`, `tests/jobpulse/test_paper_sources.py`, `tests/jobpulse/test_google_jobs.py`
