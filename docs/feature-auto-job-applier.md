# Feature: Auto Job Applier

Automated job discovery, matching, cover letter drafting, and application tracking — so you can focus on research while the system handles the repetitive 80% of job hunting.

## Problem

- Manually scanning job boards daily: 30-60min/day wasted
- Writing tailored cover letters for each role: 15-20min each
- Tracking what you applied to, when, and what stage: scattered across email/Notion/memory
- Missing good listings because you checked too late or the right keywords weren't used

## Solution

An agent that runs twice daily, scans multiple job APIs for roles matching your profile, ranks them by fit, drafts tailored cover letters, and queues everything for your one-tap approval on Telegram.

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                   CRON (8am + 6pm)                  │
└───────────────────────┬─────────────────────────────┘
                        ▼
┌─────────────────────────────────────────────────────┐
│              JOB DISCOVERY AGENT                     │
│                                                      │
│  1. Fetch from APIs (Adzuna, Reed, RemoteOK)        │
│  2. Deduplicate against seen_jobs DB                │
│  3. Score each job against user profile             │
│  4. Filter: score > threshold                       │
│  5. For top N: draft cover letter with LLM          │
│  6. Queue for Telegram approval                     │
│  7. Store in jobs DB + sync to Notion               │
└───────────────────────┬─────────────────────────────┘
                        ▼
┌─────────────────────────────────────────────────────┐
│              TELEGRAM APPROVAL                       │
│                                                      │
│  📋 NEW JOB MATCH (Score: 8.5/10)                   │
│                                                      │
│  Data Scientist — TUI Group                         │
│  📍 London | 💷 £45-55k | 📅 Posted 2h ago          │
│                                                      │
│  Why it matches:                                    │
│  • Python + ML + LLM experience (your core stack)   │
│  • Multi-agent systems mentioned in requirements    │
│  • Company already in your pipeline (TUI email)     │
│                                                      │
│  Draft cover letter:                                │
│  "Dear Hiring Manager, I'm excited to apply..."     │
│                                                      │
│  Reply:                                             │
│  ✅ "apply" — open application URL                   │
│  ✏️ "edit" — modify cover letter first               │
│  ❌ "skip" — mark as skipped                         │
│  🚫 "block TUI" — never show this company           │
└─────────────────────────────────────────────────────┘
```

## Data Sources

| Source | Coverage | Free Tier | Auth |
|--------|----------|-----------|------|
| **Adzuna API** (primary) | UK + global, strong salary data | 250 req/day | API key + App ID |
| **Reed.co.uk API** (UK secondary) | UK-only, detailed descriptions | Unlimited (personal) | API key (Basic Auth) |
| **RemoteOK** (remote roles) | Global remote tech jobs | Unlimited, no auth | None |
| **Remotive** (remote roles) | Global remote tech jobs | Unlimited, no auth | None |

### Why Not LinkedIn/Indeed?

LinkedIn killed RSS feeds and restricts their API to enterprise partners. Indeed shut down their public API in 2019. Both aggressively block scraping. The sources above provide equivalent coverage through legitimate, free APIs.

## User Profile (Auto-Detected)

The agent builds a profile from the codebase + knowledge graph:

```yaml
name: Yash Bishnoi
location: UK
target_roles:
  - Data Scientist
  - AI Engineer
  - ML Engineer
  - NLP Engineer
  - LLM Engineer
  - Python Developer (AI focus)

core_skills:
  - Python (production systems, 15K+ LOC)
  - LLM integration (OpenAI, GPT-4o)
  - Multi-agent orchestration (LangGraph, LangChain)
  - RAG architecture
  - Knowledge graphs
  - FastAPI, REST APIs
  - SQLite, data pipelines

bonus_skills:
  - Reinforcement learning (GRPO)
  - Prompt engineering + optimization
  - D3.js, Three.js (visualization)
  - GitHub Actions CI/CD
  - Telegram/Slack/Discord bot development

keywords_positive:
  - "multi-agent", "LLM", "RAG", "NLP", "LangChain", "LangGraph"
  - "knowledge graph", "prompt engineering", "AI agent"
  - "Python", "machine learning", "deep learning"
  - "gpt", "transformer", "fine-tuning"

keywords_negative:  # roles to skip
  - "senior manager", "director", "VP"
  - "Java only", ".NET only", "C# only"
  - "10+ years required"

salary_min: 35000  # GBP
location_preferences:
  - London
  - Remote
  - Hybrid
```

This profile lives in `data/job_profile.yaml` and is editable via Telegram: `"update profile: add skill kubernetes"`.

## Scoring Algorithm

Each job is scored 0-10 based on:

```
SCORE = (
    skill_match * 4.0        # How many of your skills appear in the listing
  + keyword_match * 2.0      # Positive keywords found
  + salary_fit * 1.5         # Within your range
  + location_fit * 1.5       # Matches your preferences
  + recency * 0.5            # Newer = better
  + company_familiarity * 0.5 # Already in knowledge graph = bonus
) / 10.0

# Penalties
if any(neg in description for neg in keywords_negative):
    SCORE *= 0.3

# Knowledge graph bonus
if company in knowledge_graph.entities:
    SCORE += 0.5  # You've interacted with this company before
```

**Threshold**: score >= 6.0 gets queued for review. Score >= 8.0 gets a "STRONG MATCH" label.

## Cover Letter Generation

For each job above threshold, the LLM drafts a cover letter:

```
System: You write concise, tailored cover letters for tech roles.
        Keep it under 250 words. No fluff. Lead with the strongest
        skill match. Reference specific projects where possible.

User: Write a cover letter for this role:

      Role: {job_title} at {company}
      Description: {job_description[:1000]}

      My profile:
      {user_profile_yaml}

      My relevant projects:
      - Multi-Agent Orchestration (LangGraph, 15K LOC, 4 patterns)
      - Velox_AI (RAG architecture)
      - Knowledge MindGraph (entity extraction, GraphRAG)

      Match this cover letter to the specific requirements mentioned
      in the job description. Be genuine, not generic.
```

Cost: ~$0.002 per cover letter (gpt-4o-mini, ~500 tokens out).

## Database Schema

New table in `data/jobs.db`:

```sql
CREATE TABLE jobs (
    id TEXT PRIMARY KEY,          -- hash of (source + external_id)
    source TEXT NOT NULL,          -- "adzuna", "reed", "remoteok", "remotive"
    external_id TEXT NOT NULL,     -- source-specific job ID
    title TEXT NOT NULL,
    company TEXT NOT NULL,
    location TEXT DEFAULT '',
    salary_min INTEGER,
    salary_max INTEGER,
    description TEXT DEFAULT '',
    url TEXT NOT NULL,             -- application/detail URL
    score REAL DEFAULT 0,
    status TEXT DEFAULT 'new',     -- new, queued, applied, skipped, blocked, rejected, interview
    cover_letter TEXT DEFAULT '',
    match_reasons TEXT DEFAULT '[]', -- JSON array of why it matched
    posted_at TEXT,
    discovered_at TEXT NOT NULL,
    applied_at TEXT,
    notes TEXT DEFAULT ''
);

CREATE TABLE blocked_companies (
    company TEXT PRIMARY KEY,
    blocked_at TEXT NOT NULL
);

CREATE INDEX idx_jobs_status ON jobs(status);
CREATE INDEX idx_jobs_score ON jobs(score DESC);
CREATE INDEX idx_jobs_company ON jobs(company);
```

## Notion Sync

Jobs sync to a Notion database (like budget syncs to budget sheet):

| Property | Type | Maps From |
|----------|------|-----------|
| Title | Title | job title |
| Company | Text | company |
| Status | Select | new/applied/skipped/interview/rejected |
| Score | Number | match score |
| Salary | Text | "£45-55k" |
| Location | Text | location |
| URL | URL | application URL |
| Applied | Date | applied_at |
| Notes | Text | user notes |

## Telegram Commands

| Command | What It Does |
|---------|-------------|
| `jobs` | Show today's new matches (top 5 by score) |
| `jobs all` | Show all pending (not yet reviewed) |
| `apply` (reply to a job message) | Mark as applied, open URL |
| `skip` (reply to a job message) | Mark as skipped |
| `block <company>` | Never show jobs from this company |
| `job stats` | This week: X discovered, Y applied, Z interviews |
| `update profile: add skill kubernetes` | Update search profile |

## Files to Create

| File | Purpose |
|------|---------|
| `jobpulse/job_agent.py` | Main agent: fetch, score, draft, queue |
| `jobpulse/job_sources.py` | API clients for Adzuna, Reed, RemoteOK, Remotive |
| `jobpulse/job_scorer.py` | Scoring algorithm + profile matching |
| `jobpulse/job_cover_letter.py` | LLM cover letter generation |
| `data/job_profile.yaml` | Editable user profile |

## Files to Modify

| File | Change |
|------|--------|
| `jobpulse/command_router.py` | Add `JOBS`, `JOB_APPLY`, `JOB_BLOCK` intents |
| `jobpulse/dispatcher.py` | Add job handlers |
| `jobpulse/swarm_dispatcher.py` | Add job routing |
| `jobpulse/config.py` | Add `ADZUNA_APP_ID`, `ADZUNA_API_KEY`, `REED_API_KEY` |
| `scripts/install_cron.py` | Add 8am + 6pm job scan cron |
| `requirements.txt` | Add `pyyaml` (for profile), `arxiv` (for arXiv feature) |

## Env Vars

```env
ADZUNA_APP_ID=...          # From developer.adzuna.com
ADZUNA_API_KEY=...         # From developer.adzuna.com
REED_API_KEY=...           # From reed.co.uk/developers
NOTION_JOBS_DB_ID=...      # Notion database for job tracking
JOB_SCAN_KEYWORDS=data scientist,AI engineer,ML engineer
JOB_SCAN_LOCATION=london
JOB_MIN_SCORE=6.0          # Minimum score to queue for review
```

## Schedule

| Time | Action |
|------|--------|
| 8:00 AM | Scan all sources, score, draft cover letters for top matches |
| 6:00 PM | Second scan (catches afternoon postings) |
| On demand | `jobs` command on Telegram |

## Knowledge Graph Integration

Every discovered job feeds into the MindGraph:
- **Company entity** (COMPANY type) — with relation to PERSON (Yash) via APPLYING_TO
- **Role entity** (PROJECT type) — "Data Scientist at TUI"
- **Skill entities** — extracted from job requirements
- Cross-referenced with existing entities (if TUI already in graph from recruiter email, the relation strengthens)

## Cost Estimate

| Component | Per Run | Daily (2 runs) | Monthly |
|-----------|---------|-----------------|---------|
| Adzuna API | Free (250/day) | Free | Free |
| Reed API | Free | Free | Free |
| RemoteOK/Remotive | Free | Free | Free |
| Scoring (local) | $0 | $0 | $0 |
| Cover letters (LLM) | ~$0.01 (5 letters) | $0.02 | $0.60 |
| Knowledge extraction | ~$0.005 | $0.01 | $0.30 |
| **Total** | | **$0.03/day** | **$0.90/month** |

## Success Metrics

- Jobs discovered per week
- Application rate (queued → applied)
- Interview conversion (applied → interview)
- Time saved vs manual scanning (target: 3+ hours/week)
- Cover letter approval rate (how often user applies without editing)

## Security Considerations

- API keys stored in .env, never committed
- Job URLs are redirect links from APIs (not direct company URLs) — safe to open
- Cover letters never sent automatically — always require human approval
- Blocked companies list prevents unwanted applications
- All job data exportable via existing export system
