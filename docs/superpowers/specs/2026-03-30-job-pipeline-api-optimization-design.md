# Job Pipeline API Call Optimization — Design Spec v2

> 4-gate recruiter-grade pre-screen + nightly skill graph + hybrid skill extraction.
> From 250 LLM calls/day ($5.63/mo) → 10-11 calls/day ($0.23/mo). 96% reduction.

**Date:** 2026-03-30
**Status:** Implemented
**Affects:** `jobpulse/job_autopilot.py`, `jobpulse/jd_analyzer.py`, `jobpulse/github_matcher.py`, `mindgraph_app/storage.py`, `scripts/install_cron.py`

---

## Problem

The current pipeline runs `extract_skills_llm()` (GPT-4o-mini) on every raw job (250/day) before any filtering. CV and cover letter now use ReportLab (free), so the only remaining LLM cost is JD skill extraction — but it runs on jobs that get killed by dedup, seniority mismatch, or low skill overlap anyway.

**Current:** 250 LLM calls/day × ~4,200 tokens each = ~1.05M tokens/day = **$0.19/day = $5.63/month**

## Solution: 3 Components

### 1. Nightly Skill/Project Graph (3am cron)
GitHub repos + Resume Prompt template + past successful apps → MindGraph entities.
New repos get 1 LLM call for deep analysis (one-time). Everything else is free.

### 2. Rule-Based Skill Extractor (hybrid with LLM fallback)
Scans JD text for 500+ skills from expanded taxonomy. If ≥ 10 skills found → no LLM.
Only ~15% of JDs are too vague → LLM fallback. Cuts 250 calls → 10-11/day.

### 3. Senior IT Recruiter 4-Gate Pre-Screen
Models the real 6-30 second recruiter screening process. Deterministic Python, zero LLM cost.

---

## Architecture

### Full Pipeline Flow

```
250 raw jobs/day (5 scan windows × 50 jobs)
    │
    ▼
GATE 0: TITLE RELEVANCE ─────────────────── FREE, instant
    │  Title matches search config roles?
    │  Any exclude_keywords in title/JD?
    │  ├── ~30% fail → DISCARD (never enters pipeline)
    │
    ▼ 175 pass
DEDUP ────────────────────────────────────── FREE, SQLite
    │  SHA-256 URL match + fuzzy company+title (Jaccard ≥ 0.8)
    │  ├── ~60% duplicates → DISCARD
    │
    ▼ 70 new jobs
SKILL EXTRACTION ─────────────────────────── HYBRID
    │  Rule-based: scan JD for 500+ skills in taxonomy
    │  ├── ≥ 10 skills found (85%) → USE THESE (0 LLM calls)
    │  └── < 10 skills found (15%) → GPT-4o-mini fallback
    │                                  10 LLM calls/day
    │
    ▼ 70 jobs with extracted skills
GATE 1: KILL SIGNALS ─────────────────────── FREE, instant
    │  K1: Seniority mismatch? (JD "5+ years" → kill)
    │  K2: Primary language missing? (JD needs Java → kill)
    │  K3: Domain disconnect? (JD embedded systems → kill)
    │  ├── ~25% killed → AUTO-REJECT (no CV, no Notion)
    │
    ▼ 53 pass
GATE 2: MUST-HAVES ───────────────────────── FREE, instant
    │  M1: ≥ 3 of top-5 required skills in profile?
    │  M2: ≥ 2 projects demonstrating core JD skills?
    │  M3: ≥ 12 absolute skill matches AND ≥ 65% required?
    │  ├── ANY fail → SKIP (save to DB, no CV generation)
    │
    ▼ 27 pass
GATE 3: COMPETITIVENESS SCORE ────────────── FREE, instant
    │  Hard Skill Match      (35 pts)
    │  Project Evidence      (25 pts)
    │  Stack Coherence       (15 pts)
    │  Domain Relevance      (15 pts)
    │  Recency Bonus         (10 pts)
    │                        ─────────
    │                        100 pts
    │
    │  ├── < 55/100 → SKIP    "Not competitive enough"
    │  ├── 55-74    → APPLY   CV + cover letter + apply
    │  └── 75+      → STRONG  Priority application
    │
    ▼ ~16 jobs (11 apply + 5 strong)
CV (ReportLab) ───────────────────────────── FREE, instant
Cover Letter (ReportLab) ─────────────────── FREE, instant
ATS Score (deterministic) ────────────────── FREE, instant
Notion create + update ───────────────────── FREE API
Apply (Playwright) ───────────────────────── FREE, rate-limited
```

### Nightly Profile Sync (3am)

```
┌─────────────────────────────────────────────┐
│  github_profile_sync.py (3am cron)          │
│                                              │
│  1. fetch_and_cache_repos() → GitHub API     │
│     For each repo:                           │
│       - Upsert PROJECT entity                │
│       - Upsert SKILL entities (langs/topics) │
│       - Create DEMONSTRATES relations        │
│       - Create BUILT_WITH relations          │
│       - NEW repos: 1 LLM call for deep       │
│         analysis (one-time, ~0-1/day)        │
│                                              │
│  2. Parse Resume Prompt template             │
│     Extract BASE_SKILLS → SKILL entities     │
│     Extract EXPERIENCE skills                │
│     Extract CERTIFICATION skills             │
│                                              │
│  3. Mine past successful apps (ATS ≥ 90%)    │
│     Boost mention_count on converting skills │
│                                              │
│  4. Compute skill recency from git commits   │
│     Store last_active_date per skill         │
└─────────────────────────────────────────────┘
```

---

## Component Details

### Component 1: Expanded Skill Taxonomy

**Modified file:** `data/skill_synonyms.json`

Expand from ~35 entries to 500+ covering:
- Programming languages (50+)
- Frameworks and libraries (100+)
- Databases and storage (40+)
- Cloud services and infra (60+)
- DevOps and tools (50+)
- Methodologies and practices (40+)
- Soft skills (30+)
- Data science and ML (60+)
- Domain concepts (70+)

### Component 2: Rule-Based Skill Extractor

**New file:** `jobpulse/skill_extractor.py`

Two-pass extraction from JD text:

**Pass 1 — Section Detection:**
```
"Requirements:" / "Essential:" / "Must have:" → required_skills
"Nice to have:" / "Preferred:" / "Bonus:"     → preferred_skills
"About:" / "We are:" / "Industry:"            → industry context
```

**Pass 2 — Taxonomy Matching:**
Scan each section for skills from expanded `skill_synonyms.json`. Normalize via existing `_normalize()` and synonym matching from `github_matcher.py`.

**LLM fallback:** If < 10 skills extracted (vague JD), call GPT-4o-mini as current `extract_skills_llm()` does.

### Component 3: 4-Gate Pre-Screen

**New file:** `jobpulse/recruiter_screen.py`

#### Gate 0: Title Relevance
```python
def gate0_title_relevance(title: str, jd_text: str, config: dict) -> bool:
    """Check title against search config. Return False = discard."""
    # Check exclude_keywords in title
    for kw in config["exclude_keywords"]:
        if kw.lower() in title.lower():
            return False
    # Check at least one search title is a substring match
    for search_title in config["titles"]:
        if _fuzzy_title_match(title, search_title):
            return True
    return False
```

#### Gate 1: Kill Signals
```python
def gate1_kill_signals(listing: JobListing, profile_skills: set[str]) -> str | None:
    """Return kill reason string, or None if passed."""
    # K1: Seniority mismatch
    if listing.seniority and _seniority_too_high(listing.seniority, jd_text):
        return f"Seniority mismatch: JD requires {listing.seniority}"

    # K2: Primary language missing
    primary_lang = listing.required_skills[0] if listing.required_skills else None
    if primary_lang and not _skill_in_profile(primary_lang, profile_skills):
        return f"Primary language missing: {primary_lang}"

    # K3: Domain disconnect
    if _domain_disconnect(listing, profile_domains):
        return f"Domain disconnect: {listing.industry}"

    return None  # passed
```

**Seniority rules:**
- JD mentions "5+ years", "7+ years", "senior", "lead", "principal" → K1 kill
- JD mentions "3+ years" with "mid-level" → borderline (don't kill, but penalize in Gate 3)
- JD mentions "junior", "graduate", "intern", "entry", "0-2 years" → pass

**Domain disconnect detection:**
- Build domain map: {"embedded": ["C", "RTOS", "firmware"], "iOS": ["Swift", "SwiftUI", "Xcode"], ...}
- If JD's top-3 required skills ALL belong to a foreign domain → kill

#### Gate 2: Must-Haves
```python
def gate2_must_haves(
    listing: JobListing,
    profile_skills: set[str],
    profile_projects: list[ProjectMatch],
    synonyms: dict,
) -> str | None:
    """Return fail reason string, or None if passed."""
    # M1: ≥ 3 of top-5 required skills
    top5 = listing.required_skills[:5]
    top5_matched = [s for s in top5 if _skill_match(s, profile_skills, synonyms)]
    if len(top5_matched) < 3:
        return f"Core skills: only {len(top5_matched)}/5 top required skills matched"

    # M2: ≥ 2 projects demonstrating core skills
    demonstrating = [p for p in profile_projects if p.skill_overlap >= 3]
    if len(demonstrating) < 2:
        return f"Project evidence: only {len(demonstrating)} projects demonstrate 3+ JD skills"

    # M3: ≥ 12 absolute matches AND ≥ 65% required
    all_skills = listing.required_skills + listing.preferred_skills
    matched = [s for s in all_skills if _skill_match(s, profile_skills, synonyms)]
    req_matched = [s for s in listing.required_skills if _skill_match(s, profile_skills, synonyms)]

    if len(matched) < 12:
        return f"Keyword density: {len(matched)} matches (need 12+)"

    req_pct = len(req_matched) / max(len(listing.required_skills), 1)
    if req_pct < 0.65:
        return f"Required coverage: {req_pct:.0%} (need 65%+)"

    return None  # passed
```

#### Gate 3: Competitiveness Score
```python
def gate3_competitiveness(
    listing: JobListing,
    profile_skills: set[str],
    profile_projects: list[ProjectMatch],
    skill_recency: dict[str, date],
    synonyms: dict,
) -> tuple[float, str]:
    """Return (score 0-100, tier 'skip'|'apply'|'strong')."""

    hard_skill = _score_hard_skills(listing, profile_skills, profile_projects, synonyms)  # 0-35
    project_ev = _score_project_evidence(listing, profile_projects, synonyms)              # 0-25
    coherence  = _score_stack_coherence(listing, profile_skills)                            # 0-15
    domain_rel = _score_domain_relevance(listing)                                           # 0-15
    recency    = _score_recency(listing, skill_recency)                                     # 0-10

    total = hard_skill + project_ev + coherence + domain_rel + recency

    if total < 55:
        return total, "skip"
    elif total < 75:
        return total, "apply"
    else:
        return total, "strong"
```

**Hard Skill Match (35 pts):**
- Skill in profile AND demonstrated in project → 3 pts per skill
- Skill in profile but not in any project → 1 pt per skill
- Score = (points earned / max possible) × 35

**Project Evidence (25 pts):**
- Project demonstrates ≥ 3 JD-required skills → 6 pts
- Project demonstrates 1-2 JD-required skills → 3 pts
- Bonus: project has metrics (LOC, users, perf) → +1 pt
- Score = min(points, 25)

**Stack Coherence (15 pts):**
- Define skill clusters: {python_ml: [python, pytorch, sklearn, pandas], web_backend: [python, fastapi, django, postgresql], ...}
- If matched skills span ≤ 2 clusters → 15 pts (focused)
- If matched skills span 3 clusters → 10 pts
- If matched skills span 4+ clusters → 5 pts (scattered)

**Domain Relevance (15 pts):**
- Direct match (ML role + ML projects) → 15 pts
- Adjacent (Data Eng role + ML projects) → 10 pts
- Transferable (Finance role + MBA Finance) → 5 pts
- No connection → 0 pts

**Recency Bonus (10 pts):**
- Skill committed in last 30 days → 2 pts per skill
- Skill committed in last 90 days → 1 pt per skill
- Score = min(total, 10)

### Component 4: SkillGraphStore Interface

**New file:** `jobpulse/skill_graph_store.py`

Abstraction layer over MindGraph. The job pipeline only talks to this interface.

```python
class ProjectMatch:
    name: str
    description: str
    skill_overlap: int          # How many JD skills this project demonstrates
    matched_skills: list[str]
    url: str

class PreScreenResult:
    gate0_passed: bool
    gate1_passed: bool
    gate1_kill_reason: str | None
    gate2_passed: bool
    gate2_fail_reason: str | None
    gate3_score: float          # 0-100
    tier: str                   # "reject" | "skip" | "apply" | "strong"
    matched_skills: list[str]
    missing_skills: list[str]
    best_projects: list[ProjectMatch]
    breakdown: dict             # Per-dimension scores for logging

class SkillGraphStore:
    def get_skill_profile(self) -> set[str]
    def get_projects_for_skills(self, jd_skills: list[str]) -> list[ProjectMatch]
    def get_skill_recency(self) -> dict[str, date]
    def pre_screen_jd(self, listing: JobListing, config: dict) -> PreScreenResult
    def upsert_project(self, repo: dict, deep_analysis: str | None = None) -> str
    def upsert_skill(self, name: str, source: str, description: str = "") -> str
    def get_profile_stats(self) -> dict
```

### Component 5: Nightly Profile Sync

**New file:** `jobpulse/github_profile_sync.py`

**Sources:**
1. GitHub repos → PROJECT + SKILL + TECH entities + DEMONSTRATES + BUILT_WITH relations
2. `cv_templates/generate_cv.py` BASE_SKILLS → SKILL entities with source=resume
3. Past apps where ATS ≥ 90% → boost mention_count on converting skills
4. Git log per-repo → skill recency (last commit date touching files with that language)

**Dedup:** MindGraph deterministic IDs. Same skill from 3 sources = 1 entity, mention_count=3.

**New repo deep analysis:** 1 GPT-4o-mini call to extract what the project demonstrates beyond just languages/topics. Stored in PROJECT entity description.

### Component 6: Pipeline Integration

**Modified file:** `jobpulse/job_autopilot.py`

```
Current:    Scan → Analyze(LLM) → Dedup → [Match → CV → CL → Score → Route]
                   ↑ ALL 250 jobs          ↑ ALL 20 new jobs

Proposed:   Scan → Gate0 → Dedup → SkillExtract(hybrid) → Gate1 → Gate2 → Gate3 → [CV → CL → Score → Route]
                   ↑ FREE    FREE    ↑ 10 LLM calls       FREE    FREE    FREE    ↑ only 16 jobs
```

Key changes to `_run_scan_window_inner()`:
1. Gate 0 runs BEFORE `analyze_jd()` — filters on title/keywords only
2. `analyze_jd()` uses new hybrid skill extractor instead of always calling LLM
3. After dedup, run Gate 1 → Gate 2 → Gate 3 before CV generation
4. `fetch_and_cache_repos()` replaced by `SkillGraphStore.get_projects_for_skills()` (pre-cached)
5. Only jobs passing Gate 3 (score ≥ 55) get CV + cover letter + Notion + apply

### Component 7: Cron + CLI

**Modified:** `scripts/install_cron.py` — add 3am profile-sync entry
**Modified:** `jobpulse/runner.py` — add `profile-sync` command

---

## Cost Analysis

### Per Day (5 windows × 50 raw jobs = 250 raw jobs)

**Current:**

| Step | Calls | Tokens/Call | Total Tokens | Cost |
|------|-------|-------------|-------------|------|
| extract_skills_llm (all jobs) | 250 | 4,200 | 1,050,000 | $0.19 |
| **Monthly** | | | | **$5.63** |

**Proposed:**

| Step | Calls | Tokens/Call | Total Tokens | Cost |
|------|-------|-------------|-------------|------|
| Skill extract LLM fallback | 10 | 4,200 | 42,000 | $0.0072 |
| Nightly new repo analysis | 0-1 | 2,500 | 2,500 | $0.0004 |
| **Daily Total** | **10-11** | | **44,500** | **$0.0076** |
| **Monthly** | | | | **$0.23** |

**Savings: 96% fewer LLM calls, 96% cost reduction**

### Other API Calls

| API | Current/Day | After/Day | Saved |
|-----|-------------|-----------|-------|
| GitHub REST | ~150 | ~1 (nightly) | 99% |
| Notion REST | ~350 | ~80 | 77% |
| Telegram | ~10 | ~10 | 0% |
| ReportLab (local) | ~100 | ~32 | 68% |

---

## Files Changed

| File | Change | Description |
|------|--------|-------------|
| `data/skill_synonyms.json` | Modified | Expand from 35 → 500+ skill entries |
| `jobpulse/skill_extractor.py` | **New** | Rule-based + LLM fallback skill extraction |
| `jobpulse/recruiter_screen.py` | **New** | 4-gate pre-screen (Gate 0-3) |
| `jobpulse/skill_graph_store.py` | **New** | SkillGraphStore abstraction over MindGraph |
| `jobpulse/github_profile_sync.py` | **New** | Nightly sync: GitHub + resume + past apps |
| `jobpulse/job_autopilot.py` | Modified | Integrate gates + hybrid extraction |
| `jobpulse/jd_analyzer.py` | Modified | Use skill_extractor instead of direct LLM |
| `jobpulse/runner.py` | Modified | Add `profile-sync` CLI command |
| `scripts/install_cron.py` | Modified | Add 3am cron entry |
| `tests/jobpulse/test_skill_extractor.py` | **New** | Tests for rule-based + fallback |
| `tests/jobpulse/test_recruiter_screen.py` | **New** | Tests for all 4 gates |
| `tests/jobpulse/test_skill_graph_store.py` | **New** | Tests for graph store + pre-screen |
| `tests/jobpulse/test_github_profile_sync.py` | **New** | Tests for nightly sync |

---

## Testing Strategy

1. **Skill extractor accuracy:** Test rule-based against 10 real JDs. Verify ≥ 10 skills extracted for 85%+ of them.
2. **Gate 0:** Test with 10 titles — 5 matching, 5 non-matching (marketing, sales, senior).
3. **Gate 1 kill signals:** Test seniority kill (5+ years), primary language kill (Swift), domain kill (embedded).
4. **Gate 2 must-haves:** Test with known profile. Verify M1 (top-5), M2 (project evidence), M3 (12+ absolute).
5. **Gate 3 scoring:** Test scoring dimensions individually. Verify total score produces correct tier.
6. **Graph store dedup:** Run sync 3 times, assert entity count unchanged.
7. **Pipeline integration:** Mock gates to return each tier, verify only passing jobs get CV/Notion/apply.
8. **All tests use `tmp_path`** — never touch production databases.

## Success Criteria

- Gate 0+1+2 adds < 100ms total per window (deterministic checks)
- Gate 3 adds < 50ms per job (SQLite query + arithmetic)
- Zero false rejects: no jobs that would score 90%+ ATS are killed by gates
- LLM calls/day ≤ 15 (target: 10-11)
- LLM cost/month ≤ $0.50 (target: $0.23)
- Nightly sync completes in < 60 seconds
- No duplicate entities after repeated syncs
