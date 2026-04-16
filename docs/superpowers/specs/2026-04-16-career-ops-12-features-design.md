# Design Spec: 12 Career-Ops Inspired Features for JobPulse

**Date:** 2026-04-16
**Branch:** `feature/auto-ai-application`
**Architecture:** Pipeline-stage organization (Discovery → Evaluation → Generation → Post-Apply → Infrastructure)
**Framework:** Each feature designed through 7 engineering dimensions: System Design, Tool & Contract Design, Retrieval Engineering, Reliability Engineering, Security & Safety, Evaluation & Observability, Product Thinking

---

## Table of Contents

1. [Stage 1: Discovery](#stage-1-discovery)
   - [F1: Zero-Cost ATS API Scanning](#f1-zero-cost-ats-api-scanning)
   - [F2: Ghost Job Detection](#f2-ghost-job-detection)
2. [Stage 2: Evaluation](#stage-2-evaluation)
   - [F3: Archetype-Adaptive Engine](#f3-archetype-adaptive-engine)
   - [F4: Multi-Language Market Awareness](#f4-multi-language-market-awareness)
3. [Stage 3: Generation](#stage-3-generation)
   - [F5: Archetype-Adaptive CV Generation](#f5-archetype-adaptive-cv-generation)
   - [F6: ATS Unicode Normalization](#f6-ats-unicode-normalization)
   - [F7: "I'm Choosing You" Tone Framework](#f7-im-choosing-you-tone-framework)
4. [Stage 4: Post-Apply](#stage-4-post-apply)
   - [F8: Follow-Up Cadence System](#f8-follow-up-cadence-system)
   - [F9: Interview Story Bank](#f9-interview-story-bank)
5. [Stage 5: Infrastructure](#stage-5-infrastructure)
   - [F10: Batch Processing with Parallel Workers](#f10-batch-processing-with-parallel-workers)
   - [F11: User/System Layer Separation with Auto-Updates](#f11-usersystem-layer-separation-with-auto-updates)
   - [F12: Go TUI Dashboard](#f12-go-tui-dashboard)
6. [Cross-Cutting Concerns](#cross-cutting-concerns)
7. [Data Model Changes](#data-model-changes)
8. [New Files Summary](#new-files-summary)

---

## Stage 1: Discovery

### F1: Zero-Cost ATS API Scanning

#### 1. System Design

Extend existing `jobpulse/ats_api_scanner.py` with direct HTTP API parsers for 6 ATS platforms. Runs as a **first pass** before Playwright — if the API returns results, skip the browser entirely for that company.

```
scan_platforms()
  +-- Phase 1: ats_api_scan() -> Greenhouse, Ashby, Lever, BambooHR, Teamtailor, Workday
  +-- Phase 2: browser_scan() -> LinkedIn, Indeed, Reed (only these need browsers)
  +-- Merge + dedup -> JobDB
```

Each ATS gets a parser class inheriting from `BaseATSParser`:

| Parser | Endpoint | Method |
|--------|----------|--------|
| `GreenhouseParser` | `boards-api.greenhouse.io/v1/boards/{slug}/jobs` | GET |
| `AshbyParser` | `jobs.ashbyhq.com/api/non-user-graphql?op=ApiJobBoardWithTeams` | GraphQL POST |
| `LeverParser` | `api.lever.co/v0/postings/{slug}` | GET |
| `BambooHRParser` | `{company}.bamboohr.com/careers/list` + `/careers/{id}/detail` | GET (two-step) |
| `TeamtailorParser` | `{company}.teamtailor.com/jobs.rss` | RSS GET |
| `WorkdayParser` | `{company}.{shard}.myworkdayjobs.com/wday/cxs/{company}/{site}/jobs` | POST (paginated) |

Company-to-ATS mapping stored in `data/ats_company_registry.json`:
```json
{
  "anthropic": {"ats": "ashby", "slug": "anthropic"},
  "stripe": {"ats": "greenhouse", "slug": "stripe"}
}
```

Auto-detection from careers_url patterns when registry entry is missing.

#### 2. Tool and Contract Design

```python
class BaseATSParser(ABC):
    @abstractmethod
    async def fetch_jobs(self, company_slug: str) -> list[ATSJob]

@dataclass
class ATSJob:
    title: str
    url: str
    company: str
    location: str | None
    posted_at: str | None  # ISO 8601
    source: str            # "greenhouse-api", "ashby-api", etc.
```

Output contract: `ATSJob` maps 1:1 to existing `JobDB.save_listing()` input schema. No adapter needed.

Config contract: `data/ats_company_registry.json` is user-editable. Schema validated on load. New companies auto-discovered and appended when their careers URL matches a known ATS pattern.

#### 3. Retrieval Engineering

- HTTP-only, no browser. `httpx.AsyncClient` with connection pooling.
- Bounded concurrency: `asyncio.Semaphore(10)`.
- Response caching: ETag/Last-Modified headers where supported (Greenhouse supports both). Skip re-fetch if unchanged.
- Workday pagination: offset-based, 20 per page, max 5 pages (100 jobs per company).
- Ashby GraphQL: single query returns all postings — no pagination needed.
- BambooHR two-step: list endpoint returns IDs, detail endpoint returns full JD. Parallelize detail fetches.

#### 4. Reliability Engineering

- Per-company `try/except` — one API failure doesn't kill the batch.
- 10s timeout per request via `httpx` timeout config.
- Retry: 3 attempts with exponential backoff (1s, 2s, 4s) on 429/5xx.
- Fallback: if API returns error/empty, fall back to browser scan for that company and log it.
- Circuit breaker: if a platform fails 3 consecutive times across scan runs, auto-disable for 24h and alert via Telegram.

#### 5. Security and Safety

- All requests over HTTPS (enforced in `BaseATSParser`).
- No authentication tokens stored — these are all public APIs.
- User-Agent header: `JobPulse/1.0 (contact: {email})` — transparent, not deceptive.
- Rate limiting: respect `Retry-After` headers. Never exceed 1 req/sec per platform.
- No PII sent in requests — only company slugs.

#### 6. Evaluation and Observability

- Metrics per scan run: `jobs_found`, `api_calls`, `api_errors`, `cache_hits`, `fallback_to_browser`, `duration_ms`.
- Logged to `data/scan_analytics.db` — new table `ats_scan_runs`.
- Telegram scan summary includes API vs browser breakdown: "Found 47 jobs (32 via API, 15 via browser)".
- Alert on: API returning 0 jobs for a company that had jobs last scan (possible API change).

#### 7. Product Thinking

- Scans complete 5-10x faster (API ~2s vs browser ~30s per company), zero detection risk.
- Auto-enabled for companies with known ATS URLs. User adds new companies via `job config add-company <name> <careers_url>` Telegram command.
- Edge case: company switches ATS platform -> auto-detection picks it up on next scan, updates registry.
- Progressive: API scanning doesn't replace browser scanning — it augments it. LinkedIn/Indeed/Reed still need browsers.

---

### F2: Ghost Job Detection

#### 1. System Design

New module `jobpulse/ghost_detector.py`. Runs as **Gate 0.5** — after title relevance (Gate 0), before skill matching (Gates 1-3). Kills ghost jobs before spending LLM tokens on skill extraction.

```
Gate 0 (title) -> Gate 0.5 (ghost detection) -> Gates 1-3 (skills) -> Gate 4 (quality)
```

5 signal analyzers, each returning a score and confidence:

| Signal | Method | Cost |
|--------|--------|------|
| Posting freshness | Parse date from JD/ATS metadata | Free |
| Apply button state | Check URL liveness (HTTP or Playwright) | Free/cheap |
| JD quality signals | Heuristic: specificity ratio, contradiction detection | Free |
| Reposting detection | Query JobDB for same company+similar title in 90 days | Free |
| Company hiring signals | WebSearch "{company} layoffs {year}" | ~$0.001 (conditional) |

Weighted aggregation -> 3 tiers: **High Confidence / Proceed with Caution / Suspicious**.

#### 2. Tool and Contract Design

```python
@dataclass
class GhostSignal:
    name: str
    score: float       # 0.0 (suspicious) to 1.0 (legitimate)
    confidence: str    # "high", "medium", "low"
    reason: str

@dataclass
class GhostDetectionResult:
    tier: str          # "high_confidence", "proceed_with_caution", "suspicious"
    signals: list[GhostSignal]
    recommendation: str
    should_block: bool  # True only for "suspicious"

def detect_ghost_job(listing: JobListing, jd_text: str, job_db: JobDB) -> GhostDetectionResult
```

`GhostDetectionResult` stored on `JobListing` model as `ghost_tier` field. Notion tracker gets a "Legitimacy" column.

#### 3. Retrieval Engineering

- Posting date: extract from ATS metadata first (Greenhouse/Lever APIs include `created_at`), fall back to regex on JD text ("Posted X days ago"), fall back to "unknown".
- Repost detection: SQLite query `WHERE company = ? AND title LIKE ? AND found_at > date('now', '-90 days')`. Fuzzy title match via normalized word overlap.
- Company hiring signals: only triggered when other signals are mixed (saves WebSearch cost). Cached per company for 7 days in `data/ghost_cache.db`.

#### 4. Reliability Engineering

- Each signal analyzer is independent — if one fails, others still contribute.
- Default to "Proceed with Caution" when data is insufficient (never "Suspicious" without evidence).
- Edge cases handled explicitly:
  - Government/academic: 60-90 day timelines normal -> boost freshness score
  - Evergreen roles ("continuous hiring"): not ghost -> override to High Confidence
  - Recruiter-sourced (no public posting): active recruiter contact = positive signal
  - No date available: neutral signal, doesn't drag score down

#### 5. Security and Safety

- WebSearch queries don't include PII — only company name + "layoffs/hiring freeze" + year.
- Ghost detection is advisory, not blocking by default. `GHOST_DETECTION_BLOCK=true` env var to auto-reject "Suspicious" tier.
- Ethical framing in Telegram output: "Posting shows mixed signals" not "This is a ghost job."

#### 6. Evaluation and Observability

- Track ghost detection accuracy: when a "Suspicious" job later gets an interview callback -> log false positive. When a "High Confidence" job gets no response after 30 days -> potential false negative.
- Metrics: `ghost_blocked_count`, `ghost_cautioned_count`, `false_positive_rate`.
- Weekly Telegram report: "Ghost filter saved you from X suspicious postings this week."

#### 7. Product Thinking

- Stops wasting applications (and emotional energy) on dead postings.
- Telegram scan results show legitimacy inline: `[ok] Anthropic - AI Engineer` vs `[?] Acme Corp - Data Scientist` vs `[x] Generic LLC - ML Lead`.
- Override: user can force-apply on suspicious jobs via `apply --force`.
- Learning: false positives/negatives feed back into signal weights over time.

---

## Stage 2: Evaluation

### F3: Archetype-Adaptive Engine

#### 1. System Design

New module `jobpulse/archetype_engine.py`. Central intelligence that feeds into CV generation, screening answers, and interview prep. Runs during JD analysis, after skill extraction.

```
jd_analyzer.py extracts skills
  -> archetype_engine.detect_archetype(jd_text, required_skills)
  -> returns ArchetypeResult (stored on JobListing model)
  -> consumed by: generate_cv.py, screening_answers.py, interview_prep.py
```

6 archetypes adapted to Yash's profile:

| Archetype | JD Signals | Framing |
|-----------|------------|---------|
| Data/ML Platform | pipelines, evals, observability, MLOps | Production ML builder, monitoring, cost optimization |
| Agentic/Automation | agents, HITL, orchestration, multi-agent, LangGraph | Multi-agent systems architect, JobPulse as proof |
| Data Analyst/BI | dashboards, SQL, stakeholders, insights, reporting | Insight-to-action, BI tooling, data storytelling |
| Data Scientist | modeling, experiments, A/B tests, statistics, research | Research-to-production, experimentation rigor |
| AI/ML Engineer | training, fine-tuning, deployment, inference, GPU | Full-stack ML, model serving, infrastructure |
| Data Engineer | ETL, warehousing, Spark, Airflow, dbt, streaming | Pipeline architect, data quality, scale |

Detection: hybrid approach — rule-based keyword scoring first (free), LLM fallback for ambiguous JDs (~15% of cases, ~$0.001 each).

#### 2. Tool and Contract Design

```python
@dataclass
class ArchetypeResult:
    primary: str            # "agentic", "data_platform", etc.
    secondary: str | None   # for hybrid roles
    confidence: float       # 0.0-1.0
    proof_points: list[str] # matched CV lines per archetype
    emphasis: dict          # {"tagline": "...", "summary_angle": "...", "top_projects": [...]}

ARCHETYPE_PROFILES: dict[str, ArchetypeProfile]  # loaded from data/archetype_profiles.json

@dataclass
class ArchetypeProfile:
    name: str
    keywords: dict[str, float]    # keyword -> weight
    tagline: str
    summary_angle: str
    project_priority: list[str]   # project names in priority order
    skills_to_highlight: list[str]
    yoe_framing: str              # "3+ years" vs "2+ years"
```

User-editable config `data/archetype_profiles.json`:
```json
{
  "agentic": {
    "keywords": {"agent": 3.0, "orchestration": 2.5, "HITL": 2.0, "multi-agent": 3.0, "LangGraph": 2.5},
    "tagline": "Multi-agent systems engineer with production orchestration experience",
    "summary_angle": "Building reliable agent systems from prototype to production",
    "project_priority": ["JobPulse", "Multi-Agent Patterns", "MindGraph"],
    "skills_to_highlight": ["LangGraph", "OpenAI Agents SDK", "Swarm", "Python"],
    "yoe_framing": "2+ years"
  }
}
```

`ArchetypeResult` added as a field on `JobListing` model. Pure data — no behavior coupling.

#### 3. Retrieval Engineering

- Keyword scoring: in-memory dict lookup. O(keywords x archetypes) ~ 120 comparisons. Sub-millisecond.
- Proof point matching: `ArchetypeProfile.project_priority` maps to projects in CV data (loaded once at startup from `data/cv_profile.json`). No DB query.
- LLM fallback: only when top two archetype scores are within 1.2x of each other. Uses `smart_llm_call()` with a 50-token response. Cost ~$0.001.

#### 4. Reliability Engineering

- Keyword scoring always succeeds (pure computation). LLM is fallback only.
- If LLM fails: use top keyword-scored archetype with `confidence=0.6` and note "archetype inferred from keywords only."
- Unknown archetypes: if no archetype scores above threshold (2.0) -> tag as "general" with generic profile. Never block pipeline.
- Profile JSON validation on load: if malformed, fall back to hardcoded defaults and log warning.

#### 5. Security and Safety

- No external API calls for keyword scoring. LLM fallback uses `get_llm()` (existing secure path).
- `archetype_profiles.json` validated against schema — prevents injection via malformed JSON.
- Archetype detection never modifies the JD or listing — read-only analysis.

#### 6. Evaluation and Observability

- Log every detection: `{job_id, primary, secondary, confidence, method, top_3_scores}`.
- Track archetype distribution: "Last 30 days: 40% Agentic, 25% Data Platform, 20% Data Scientist, 15% other."
- Track confidence distribution: if >30% low-confidence, keyword weights need tuning.
- A/B metric: compare application success rate per archetype.

#### 7. Product Thinking

- Telegram scan summary shows archetype: "Found 12 new jobs: 5 Agentic, 3 Data Platform, 2 ML Engineer, 2 Data Analyst".
- User override: `apply --archetype=data_scientist` forces specific framing.
- Profile editing via Telegram: `archetype edit agentic tagline "..."`.
- Highest-impact feature: same CV, 6 different framings. A Data Analyst role emphasizes SQL/dashboards. An Agentic role emphasizes LangGraph/orchestration.

---

### F4: Multi-Language Market Awareness

#### 1. System Design

New module `jobpulse/market_locale.py`. Detects JD language and applies market-specific knowledge during evaluation and generation.

```
jd_analyzer.py
  -> market_locale.detect_locale(jd_text, company_location)
  -> returns LocaleContext
  -> consumed by: skill_graph_store.py, generate_cv.py, screening_answers.py
```

#### 2. Tool and Contract Design

```python
@dataclass
class LocaleContext:
    language: str       # "en", "de", "fr", "ja"
    market: str         # "uk", "dach", "france", "nordics", "us"
    paper_format: str   # "a4" or "letter"
    currency: str       # "GBP", "EUR", "USD"
    salary_norm: str    # "annual" or "daily"
    market_terms: dict[str, str]  # {"probation": "Probezeit", "notice_period": "Kundigungsfrist"}
    visa_context: str   # "Graduate Visa (UK)", "Blue Card (EU)", etc.

MARKET_KNOWLEDGE: dict[str, MarketProfile]  # loaded from data/market_profiles.json
```

Market profiles:
- **DACH**: 13th month salary, Probezeit (6 months), Kundigungsfrist, Tarifvertrag, AGG
- **France**: CDI/CDD distinction, SYNTEC convention, RTT days, mutuelle, prevoyance
- **UK** (default): Graduate Visa, notice periods, pension auto-enrolment
- **Nordics**: flat hierarchies, personnummer requirements

#### 3. Retrieval Engineering

- Language detection: `langdetect` library on first 500 chars. Fast, no API call.
- Market inference: company location from JD/ATS metadata -> country -> market profile. Fallback: infer from language.
- Market profiles: static JSON loaded once at startup. ~2KB per market.

#### 4. Reliability Engineering

- Language detection fallback: if confidence < 0.7, default to English.
- Unknown market: falls back to UK profile — no degradation.
- Market terms are additive only — enrich screening answers, never replace core logic.

#### 5. Security and Safety

- No external calls. Pure local computation.
- Market profiles don't contain PII.
- Salary norms advisory — never auto-filled without user confirmation.

#### 6. Evaluation and Observability

- Track locale distribution: "Last 30 days: 70% UK, 15% DACH, 10% France, 5% Nordics."
- Alert if JD misclassified (German job but English CV generated).

#### 7. Product Thinking

- Screening answers correctly state "Graduate Visa" for UK, "Blue Card eligible" for EU.
- CV paper format auto-switches (A4 for Europe, Letter for US/Canada).
- Scan results show locale tag: `[UK] Anthropic` vs `[DACH] Aleph Alpha`.
- v1: locale-aware English only. Future: full JD-language CV generation.

---

## Stage 3: Generation

### F5: Archetype-Adaptive CV Generation

#### 1. System Design

Extends `jobpulse/cv_templates/generate_cv.py`. Replaces current `get_role_profile()` with archetype-driven generation that reorders bullets, selects projects, and rewrites the professional summary.

```
generate_cv_pdf(company, location, listing)
  -> listing.archetype.emphasis
  -> reorder_bullets(experience, archetype)
  -> select_projects(all_projects, archetype)
  -> build_competency_grid(jd_keywords, archetype)
  -> inject_keywords(cv_sections, jd_keywords)
  -> generate PDF
```

`get_role_profile()` replaced by `get_archetype_framing(archetype_result) -> CVFraming`.

#### 2. Tool and Contract Design

```python
@dataclass
class CVFraming:
    tagline: str
    professional_summary: str    # 3-4 lines, keyword-injected
    competency_grid: list[str]   # 6-8 JD keyword phrases
    project_order: list[str]     # project names in priority order
    bullet_weights: dict[str, list[float]]  # per-job bullet relevance scores
    extra_skills: dict[str, list[str]]      # "Also proficient in" per category

def get_archetype_framing(archetype: ArchetypeResult, jd_keywords: list[str], cv_data: dict) -> CVFraming
```

Keyword injection (ethical, truth-based):
```python
def inject_keywords(text: str, jd_keywords: list[str], cv_facts: list[str]) -> str:
    """Reformulate existing experience using JD vocabulary.
    NEVER add skills the candidate doesn't have."""
```

Substitution rules in `data/keyword_synonyms.json`:
```json
{
  "RAG pipelines": ["LLM workflows with retrieval", "retrieval-augmented generation"],
  "MLOps": ["observability", "evals", "model monitoring"]
}
```

#### 3. Retrieval Engineering

- CV data loaded once from `data/cv_profile.json`. No DB query during generation.
- JD keywords from `jd_analyzer.py` output (already extracted).
- Bullet relevance scoring: TF-IDF similarity between each bullet and JD text. Computed at generation time (different per JD).
- Keyword synonyms: static JSON, ~200 entries.

#### 4. Reliability Engineering

- If archetype detection failed (confidence < 0.3): fall back to current `get_role_profile()` behavior.
- If keyword injection changes length > 30%: revert to original bullet text and log warning.
- Competency grid: if fewer than 6 JD keywords, pad with CV base categories.
- 2-page limit enforced: if reordered content exceeds 2 pages, drop lowest-relevance bullets.

#### 5. Security and Safety

- **NEVER invent experience or metrics** — `inject_keywords()` only substitutes from synonym map.
- All project URLs verified against real GitHub repos.
- No em-dashes, en-dashes, or double dashes in output.
- Generation is deterministic given same inputs — no LLM in the generation loop.

#### 6. Evaluation and Observability

- Log per-generation: `{company, archetype, keywords_injected, bullets_reordered, projects_selected, page_count}`.
- Before/after diff of professional summary.
- Track which archetype framings lead to interview callbacks.

#### 7. Product Thinking

- User sees the difference: "Your CV for Anthropic (Agentic) leads with multi-agent orchestration. Your CV for OakNorth (Data Platform) leads with pipeline observability."
- Telegram preview before apply: professional summary + top 3 projects.
- Override: `apply --projects="JobPulse,MindGraph"` forces project selection.

---

### F6: ATS Unicode Normalization

#### 1. System Design

New function in `jobpulse/cv_templates/generate_cv.py` — `normalize_text_for_ats()`. Runs as the **last step** before ReportLab renders the PDF.

#### 2. Tool and Contract Design

```python
UNICODE_REPLACEMENTS = {
    '\u2014': '-',    # em-dash
    '\u2013': '-',    # en-dash
    '\u2018': "'",    # left single quote
    '\u2019': "'",    # right single quote
    '\u201C': '"',    # left double quote
    '\u201D': '"',    # right double quote
    '\u2026': '...',  # ellipsis
    '\u00A0': ' ',    # non-breaking space
    '\u200B': '',     # zero-width space
    '\u200C': '',     # zero-width non-joiner
    '\u200D': '',     # zero-width joiner
    '\u2060': '',     # word joiner
    '\uFEFF': '',     # BOM
}

def normalize_text_for_ats(text: str) -> tuple[str, dict[str, int]]:
    """Returns (normalized_text, replacement_counts)"""
```

Pure function. No side effects. Returns replacement counts for observability.

#### 3. Retrieval Engineering

No retrieval — pure string transformation. O(n) single pass.

#### 4. Reliability Engineering

- Idempotent: running twice produces same output.
- Only replaces known problematic characters — never mutates alphanumeric content.
- If replacement count > 50, log warning (source data might be corrupted).

#### 5. Security and Safety

No security surface — pure text transformation on local data.

#### 6. Evaluation and Observability

- Log replacement counts per PDF: "Normalized 3 em-dashes, 2 smart quotes for Anthropic CV."
- Track over time: if certain sources consistently produce bad Unicode, fix upstream.

#### 7. Product Thinking

- Invisible to user but critical: ATS parsers silently drop content around zero-width characters.
- Zero user action — auto-enabled.

---

### F7: "I'm Choosing You" Tone Framework

#### 1. System Design

New module `jobpulse/tone_framework.py`. Adds a tone layer on top of existing pattern-match + LLM fallback in `screening_answers.py`. Shapes all generated text: screening answers, cover letter bullets, follow-up emails.

```
screening_answers.py
  -> pattern match -> raw answer
  -> tone_framework.apply_tone(raw_answer, question_type, archetype)
  -> polished answer with "choosing you" positioning
```

#### 2. Tool and Contract Design

```python
BANNED_PHRASES = [
    "passionate about", "results-oriented", "proven track record",
    "leveraged", "spearheaded", "facilitated", "synergies",
    "robust", "seamless", "cutting-edge", "innovative",
    "just checking in", "just following up", "touching base",
    "circling back", "I would love the opportunity",
    "in today's fast-paced world", "demonstrated ability to",
]

QUESTION_FRAMEWORKS = {
    "why_this_role": "Your {jd_specific} maps directly to {cv_specific}.",
    "why_this_company": "I've been {concrete_usage}. {company_specific} is where I want to apply that.",
    "relevant_experience": "{quantified_proof_point}. {metric}.",
    "good_fit": "I sit at the intersection of {skill_a} and {skill_b}, which is exactly where this role lives.",
    "how_heard": "Found through {source}, evaluated against my criteria.",
    "additional_info": "{archetype_proof_point}. {portfolio_link}.",
}

def apply_tone(answer: str, question_type: str, archetype: ArchetypeResult, listing: JobListing) -> str
def classify_question_type(question: str) -> str
```

#### 3. Retrieval Engineering

- Proof points from `archetype.proof_points` (already computed upstream). No DB query.
- Company-specific facts from JD text (already parsed).
- Banned phrase detection: regex match against list. Sub-millisecond.

#### 4. Reliability Engineering

- Tone application is post-processing — if it fails, raw answer still works.
- Banned phrase replacement: replace with concrete alternative, don't just delete. "Passionate about ML" -> "Built 3 production ML systems."
- If no proof points available: fall back to generic strong answer without "choosing you" hook.
- Length guard: 2-4 sentences max. If tone application expands beyond 4, truncate to strongest 3.

#### 5. Security and Safety

- Never invents facts — reshapes how existing facts are presented.
- Proof points sourced from `cv_profile.json` (user-controlled).
- User reviews all answers before apply.

#### 6. Evaluation and Observability

- Log per-answer: `{question_type, banned_phrases_removed, proof_points_injected, archetype_used}`.
- Track which toned answers correlate with interview callbacks.
- Weekly Telegram report: "Tone framework improved 23 answers this week. 4 banned phrases caught."

#### 7. Product Thinking

- The difference between "I'm passionate about data science" and "I built a multi-agent system that processes 50+ job applications daily" is the difference between screened out and callback.
- Telegram preview shows toned answer with proof points highlighted.
- Override: `apply --tone=formal` or `apply --tone=casual`.
- Banned phrase list catches corporate-speak that LLMs default to.

---

## Stage 4: Post-Apply

### F8: Follow-Up Cadence System

#### 1. System Design

New module `jobpulse/followup_cadence.py`. Tracks when to follow up, generates email/LinkedIn drafts, integrates with Telegram Jobs Bot.

```
Cron (daily 9am)
  -> followup_cadence.check_due()
  -> queries applications with follow_up_date <= today
  -> generates drafts per application
  -> sends Telegram notification with urgency ranking
```

New columns on `applications` table in `applications.db`:
- `followup_count` (int, default 0)
- `followup_last_at` (datetime, nullable)
- `followup_status` (enum: active/cold/completed)

#### 2. Tool and Contract Design

```python
@dataclass
class FollowUpItem:
    job_id: str
    company: str
    role: str
    status: str        # Applied, Responded, Interview
    urgency: str       # URGENT, OVERDUE, WAITING, COLD
    days_since_last: int
    followup_count: int
    draft: str | None
    channel: str       # "email" or "linkedin"

CADENCE_RULES = {
    "Applied":    {"first_after_days": 7, "subsequent_days": 7, "max_attempts": 2},
    "Responded":  {"first_after_days": 1, "subsequent_days": 3, "max_attempts": None},
    "Interview":  {"first_after_days": 1, "subsequent_days": 3, "max_attempts": None},
}

def check_due(db_path: str = None) -> list[FollowUpItem]
def generate_draft(item: FollowUpItem, archetype: ArchetypeResult, tone: str = "professional") -> str
def record_sent(job_id: str, channel: str) -> None  # only after user confirms
```

#### 3. Retrieval Engineering

- Due check: single SQL query `WHERE follow_up_date <= date('now') AND followup_status = 'active'`.
- Draft generation: loads evaluation context from `applications.db`. One query per due item.
- Recruiter email from `JobListing.recruiter_email` field.

#### 4. Reliability Engineering

- **Confirmation-gated recording**: `record_sent()` only called after user confirms in Telegram.
- Cold threshold: after `max_attempts`, status -> `cold`. No more drafts. User can manually reactivate.
- If no recruiter email: suggest LinkedIn outreach. If no LinkedIn contact: flag for manual research.
- Second follow-up requires **different angle** — checks previous draft, generates with `avoid_previous=True`.

#### 5. Security and Safety

- Emails generated but NEVER auto-sent. User must manually send.
- Draft content never includes sensitive personal data beyond name and role experience.
- Rate limit: max 5 follow-ups generated per day.

#### 6. Evaluation and Observability

- Track: `{followups_generated, followups_sent, responses_received, response_rate}`.
- Per-cadence metrics: "Applied follow-ups: 12 sent, 3 responses (25% rate)."
- Weekly digest: "Follow-up due for 4 applications. 2 turning cold."
- Alert when response rate drops below 10%.

#### 7. Product Thinking

- Daily 9am Telegram notification sorted by urgency. URGENT items at top.
- Draft includes subject line for email, 300-char version for LinkedIn.
- "Don't be desperate" philosophy: max 2 attempts for Applied, banned phrases, cold after 2 non-responses.
- Most candidates never follow up or follow up too aggressively. This finds the middle ground.

---

### F9: Interview Story Bank

#### 1. System Design

New module `jobpulse/interview_prep.py`. Accumulates STAR+R stories across job evaluations. When interview triggered, maps stories to company-specific questions.

```
post_evaluation (score >= 4.0)
  -> interview_prep.extract_stories(jd_text, archetype, matched_projects)
  -> saves to data/story_bank.db

status -> Interview
  -> interview_prep.prepare(company, role, archetype)
  -> loads story bank + company-specific prep
  -> sends to Telegram
```

#### 2. Tool and Contract Design

```python
@dataclass
class STARStory:
    id: str
    situation: str
    task: str
    action: str
    result: str
    reflection: str       # the R+ that signals seniority
    tags: list[str]       # ["orchestration", "production", "debugging"]
    source_project: str
    archetype_fit: list[str]
    times_used: int
    last_used_for: str | None

@dataclass
class InterviewPrepKit:
    company: str
    role: str
    archetype: str
    likely_questions: list[dict]  # {question, category, mapped_story_id, fit_rating}
    gaps: list[str]              # topics with no matching story
    red_flag_questions: list[dict]
    company_vocab: list[str]

def extract_stories(jd_text: str, archetype: ArchetypeResult, projects: list) -> list[STARStory]
def prepare(company: str, role: str, archetype: ArchetypeResult) -> InterviewPrepKit
def get_story_bank_stats() -> dict
```

#### 3. Retrieval Engineering

- Story bank: SQLite `data/story_bank.db` with FTS5 index on `situation + action + result + tags`.
- Story-to-question mapping: semantic similarity via `shared/nlp_classifier.py` embedding tier (5ms per comparison).
- Company research: WebSearch for interview questions — cached per company for 30 days.

#### 4. Reliability Engineering

- Story extraction is LLM-assisted but **user-reviewed** before persisting. Telegram approval: "[Keep/Edit/Discard]".
- Empty story bank (first run): generate 5 seed stories from `cv_profile.json`.
- If WebSearch fails: fall back to archetype-generic questions (~10 per archetype).
- Gap detection: if likely question has no story with fit > 0.5, flag explicitly.

#### 5. Security and Safety

- Stories contain professional experience only.
- Interview questions sourced from public sites — attributed with source.
- LLM-generated questions labeled `[inferred from JD]`.
- Story bank is local-only SQLite. Never uploaded.

#### 6. Evaluation and Observability

- Story coverage: "You have stories covering 5/6 archetypes. Gap: Data Engineer."
- Usage tracking: "Your 'multi-agent orchestrator' story mapped to 8 interviews."
- Post-interview feedback: user marks "landed well" or "fell flat" -> adjusts fit ratings.

#### 7. Product Thinking

- Auto-generates prep kit when status changes to "Interview".
- **Reflection** column differentiates: "Built the orchestrator" (junior) vs "Built the orchestrator, which taught me that agent reliability requires explicit state machines" (senior).
- Story bank grows naturally — every high-scoring evaluation adds 2-3 stories. After 20 evaluations: 15-20 master stories.
- Gap detector: "You have no story about production incidents. Consider framing your scan-learning debugging as STAR+R."

---

## Stage 5: Infrastructure

### F10: Batch Processing with Parallel Workers

#### 1. System Design

New directory `jobpulse/batch/`:
```
batch/
  +-- orchestrator.py    # main batch coordinator
  +-- worker.py          # single-job evaluation worker
  +-- state.py           # TSV-based state tracking for resumability
```

```
Telegram: "batch evaluate" or Cron trigger
  -> orchestrator.run_batch(job_ids, parallel=3)
  -> spawns N worker processes
  -> each worker: evaluate -> pre-screen -> generate CV -> update DB
  -> orchestrator merges results, sends summary
```

#### 2. Tool and Contract Design

```python
@dataclass
class BatchConfig:
    parallel: int = 3
    max_retries: int = 2
    min_score: float = 3.5
    dry_run: bool = False

@dataclass
class BatchResult:
    job_id: str
    status: str       # "completed", "failed", "skipped"
    score: float | None
    archetype: str | None
    error: str | None
    duration_ms: int

def run_batch(job_ids: list[str], config: BatchConfig) -> list[BatchResult]
def resume_batch(state_file: str) -> list[BatchResult]
```

State file: `data/batch_state.tsv`:
```
job_id	status	started_at	completed_at	score	error	retries
```

#### 3. Retrieval Engineering

- Each worker loads `JobListing` from `applications.db` independently.
- Workers share no state — fully independent processes.
- State file read/write uses `fcntl.flock` for concurrent safety.
- Post-batch: single merge pass updates `applications.db`.

#### 4. Reliability Engineering

- Per-worker `try/except` — one failure doesn't kill the batch.
- State file enables resumability: `resume_batch()` skips completed, retries failed.
- Worker timeout: 120s per job. Killed via `Process.terminate()`.
- File-based locking prevents concurrent batch runs.

#### 5. Security and Safety

- Workers inherit parent environment (API keys, DB paths). No credential passing over IPC.
- Each worker writes to own temp directory.
- Batch never auto-applies — only evaluates and generates CVs.

#### 6. Evaluation and Observability

- Telegram summary: "Batch: 15 evaluated, 8 scored 4.0+, 3 failed, avg 12s/job."
- Per-worker logging: timing, memory, retry count.
- Throughput metric: jobs/minute.

#### 7. Product Thinking

- Telegram: `batch evaluate` processes all pending. `batch evaluate --top 10` processes top 10.
- Resumability: phone dies mid-batch -> `batch resume` picks up exactly.
- `parallel=3` default is conservative. User can increase.

---

### F11: User/System Layer Separation with Auto-Updates

#### 1. System Design

New module `jobpulse/update_manager.py`. Defines user-owned vs system-owned file boundaries.

```
User Layer (never touched):
  data/*.db, data/*.json, reports/, output/, .env

System Layer (replaceable):
  jobpulse/**/*.py, shared/**/*.py, patterns/**/*.py, templates/, scripts/
```

#### 2. Tool and Contract Design

```python
USER_PATHS = [
    "data/", "reports/", "output/", "config/", ".env",
    "data/archetype_profiles.json", "data/market_profiles.json",
    "data/cv_profile.json", "data/ats_company_registry.json"
]
SYSTEM_PATHS = ["jobpulse/", "shared/", "patterns/", "scripts/", "templates/"]

def check_update() -> dict   # {"status": "available|up-to-date", "version", "changelog"}
def apply_update(backup: bool = True) -> dict
def rollback() -> dict
```

#### 3. Retrieval Engineering

- Version check: `git fetch origin main --dry-run` + compare VERSION files.
- Changelog: GitHub Releases API.
- Backup: `git stash` user changes + `git checkout origin/main -- <system_paths>`.

#### 4. Reliability Engineering

- Safety validation: post-checkout `git diff --stat` checked against `USER_PATHS`. Any user file modified -> abort + revert.
- Backup branch created before every update.
- Rollback always available.

#### 5. Security and Safety

- Updates only from `origin/main`.
- User data directories never in `git checkout` path list.
- Requires explicit user confirmation before applying.

#### 6. Evaluation and Observability

- Log every update: version, files changed, backup branch.
- Telegram: "Update available: v2.1.0 -> v2.2.0. [Apply/Dismiss]"

#### 7. Product Thinking

- System improves without manual git operations.
- Check runs on daemon startup (daily). Non-intrusive.
- Backup + rollback + safety validation = safe updates.

---

### F12: Go TUI Dashboard

#### 1. System Design

New directory `dashboard/`:
```
dashboard/
  +-- main.go
  +-- go.mod
  +-- internal/
      +-- data/career.go      # SQLite reader
      +-- model/career.go     # data models
      +-- theme/theme.go      # Catppuccin dark/light
      +-- ui/
          +-- pipeline.go     # main list view
          +-- progress.go     # analytics view
          +-- viewer.go       # report detail view
```

Reads `applications.db` directly via `mattn/go-sqlite3`. No API server needed.

#### 2. Tool and Contract Design

- Input: reads `applications.db` directly.
- Read-only (v1). Status changes go through Telegram.
- 3 screens: Pipeline (filterable list), Progress (funnel analytics), Viewer (report detail).
- Vim keybindings: j/k, g/G, `/` search, `f` filter, `s` sort.

#### 3. Retrieval Engineering

- SQLite queries match existing `job_analytics.py` — same data, different presentation.
- Lazy loading: report details fetched only when cursor reaches entry.
- Refresh: `r` key triggers full reload, preserves cursor position.

#### 4. Reliability Engineering

- Read-only — can't corrupt data.
- If DB locked (daemon writing): retry with 100ms backoff.
- If DB doesn't exist: empty state with instructions.
- Cross-platform URL opening via `runtime.GOOS`.

#### 5. Security and Safety

- Read-only SQLite access. No write operations.
- No network access. Fully local.

#### 6. Evaluation and Observability

- The dashboard IS the observability tool: funnel, conversion rates, score distributions, archetype breakdown.
- Color-coded scores: green (>=4.2), yellow (>=3.8), red (<3.0).

#### 7. Product Thinking

- See entire pipeline at a glance without Telegram.
- Launch: `jobpulse dashboard` or `go run dashboard/main.go --path data/`.
- Optional power-user tool. Telegram remains primary.
- Future: write capability (status changes) once read-only stable.

---

## Cross-Cutting Concerns

### Data Flow Through Pipeline
```
DISCOVERY                    EVALUATION                 GENERATION
ats_api_scanner.py ----+     archetype_engine.py        generate_cv.py
job_scanner.py --------+--> jd_analyzer.py ----------> (archetype framing)
ghost_detector.py -----+     market_locale.py           tone_framework.py
                             skill_graph_store.py        normalize_text_for_ats()
                                    |
                                    v
POST-APPLY                   INFRASTRUCTURE
followup_cadence.py          batch/orchestrator.py
interview_prep.py            update_manager.py
                             dashboard/
```

### Shared Dependencies
- `ArchetypeResult` flows from F3 into F5 (CV gen), F7 (tone), F8 (follow-up), F9 (interview prep)
- `LocaleContext` flows from F4 into F5 (paper format), screening answers (visa context)
- `GhostDetectionResult` flows from F2 into Notion tracker, Telegram display
- All features use `get_llm()` and `smart_llm_call()` for any LLM interactions

### Dual Dispatcher Rule
New Telegram intents required:
- `JOB_BATCH` — batch evaluate command
- `JOB_FOLLOWUP` — follow-up cadence check
- `JOB_INTERVIEW_PREP` — interview prep trigger
- `JOB_GHOST_CHECK` — manual ghost check on a URL
- `ARCHETYPE_EDIT` — edit archetype profiles

All must be added to BOTH `dispatcher.py` AND `swarm_dispatcher.py`.

---

## Data Model Changes

### `JobListing` model — new fields:
- `archetype: str | None` — primary archetype tag
- `archetype_secondary: str | None` — secondary (hybrid roles)
- `archetype_confidence: float` — detection confidence
- `ghost_tier: str | None` — "high_confidence" / "proceed_with_caution" / "suspicious"
- `locale_market: str | None` — "uk", "dach", "france", etc.
- `locale_language: str | None` — "en", "de", "fr"
- `posted_at: str | None` — ISO 8601 from ATS metadata

### `applications` table — new columns:
- `followup_count: int` (default 0)
- `followup_last_at: datetime | None`
- `followup_status: str` (default "active")

### New SQLite databases:
- `data/story_bank.db` — STAR+R stories with FTS5
- `data/ghost_cache.db` — company hiring signal cache (7-day TTL)
- `data/scan_analytics.db` — ATS scan run metrics

### New JSON configs (user-editable):
- `data/archetype_profiles.json` — 6 archetype definitions
- `data/market_profiles.json` — DACH/France/UK/Nordics market knowledge
- `data/cv_profile.json` — candidate projects, experience, skills (source of truth). Schema:
  ```json
  {
    "name": "Yash Bishnoi",
    "projects": [
      {"name": "JobPulse", "description": "...", "metrics": ["50+ daily applications"], "tags": ["agents", "LangGraph"], "github_url": "..."},
    ],
    "experience": [
      {"company": "...", "role": "...", "bullets": ["...", "..."], "period": "2024-2026"}
    ],
    "base_skills": {"Languages": ["Python", "SQL"], "AI/ML": ["LangGraph", "OpenAI"]},
    "education": [...],
    "certifications": [...]
  }
  ```
- `data/keyword_synonyms.json` — ~200 JD-to-CV keyword mappings
- `data/ats_company_registry.json` — company-to-ATS platform mapping

---

## New Files Summary

| File | Stage | Purpose |
|------|-------|---------|
| `jobpulse/ats_api_scanner.py` (extend) | Discovery | 6 ATS API parsers |
| `jobpulse/ghost_detector.py` | Discovery | Ghost job detection (Gate 0.5) |
| `jobpulse/archetype_engine.py` | Evaluation | 6-archetype detection engine |
| `jobpulse/market_locale.py` | Evaluation | Language + market detection |
| `jobpulse/cv_templates/generate_cv.py` (extend) | Generation | Archetype framing + ATS normalization |
| `jobpulse/tone_framework.py` | Generation | "I'm choosing you" positioning |
| `jobpulse/followup_cadence.py` | Post-Apply | Follow-up tracking + draft gen |
| `jobpulse/interview_prep.py` | Post-Apply | STAR+R story bank + prep kits |
| `jobpulse/batch/orchestrator.py` | Infrastructure | Parallel batch coordinator |
| `jobpulse/batch/worker.py` | Infrastructure | Single-job evaluation worker |
| `jobpulse/batch/state.py` | Infrastructure | TSV state tracking |
| `jobpulse/update_manager.py` | Infrastructure | Auto-update with safety validation |
| `dashboard/` (new directory) | Infrastructure | Go TUI dashboard |
| `data/archetype_profiles.json` | Config | Archetype definitions |
| `data/market_profiles.json` | Config | Market knowledge profiles |
| `data/cv_profile.json` | Config | Candidate source of truth |
| `data/keyword_synonyms.json` | Config | JD-to-CV keyword mappings |
| `data/ats_company_registry.json` | Config | Company-to-ATS mapping |
