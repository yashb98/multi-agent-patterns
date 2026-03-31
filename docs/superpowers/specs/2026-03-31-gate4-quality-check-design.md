# Gate 4: Application Quality Check — Design Spec

**Goal:** Add a two-phase quality gate that blocks low-quality applications before wasting resources on CV generation (Phase A) and ensures generated CVs meet FAANG-level recruiter standards (Phase B).

**Architecture:** Phase A runs between Gate 3 and CV generation (deterministic, zero LLM cost). Phase B runs after CV generation (deterministic rules first, then LLM scrutiny only if rules pass). Notion Company Blocklist database for spam tracking with user approval flow.

**Tech Stack:** SQLite, Notion API, GPT-5o-mini (Phase B only, ~$0.002/call)

---

## Pipeline Position

```
Gates 0-3 (existing) → Gate 4 Phase A → CV/CL generation → Gate 4 Phase B → Drive upload → Notion sync
```

Gate 4 checks are **unique** — no overlap with existing gates:
- Gates 0-2 check skills/title/seniority — Gate 4A checks JD quality, company legitimacy, company background
- ATS scorer checks keyword coverage — Gate 4B checks CV presentation quality and recruiter-grade scrutiny

---

## Phase A: Pre-Generation Checks (before CV is created)

All deterministic. Zero LLM cost. Instant.

### A1: JD Quality Check

Blocks jobs with stub/boilerplate descriptions that can't produce a good application.

| Check | Threshold | Action |
|-------|-----------|--------|
| JD description length | < 200 chars | Block — "JD too short" |
| Extracted skills count | < 5 skills from hybrid extractor | Block — "JD too vague" |
| Boilerplate detection | 3+ boilerplate phrases with no technical requirements | Block — "Boilerplate JD" |

**Boilerplate phrases** (case-insensitive):
- "competitive salary", "dynamic team", "fast-paced environment", "great benefits", "exciting opportunity", "passionate individuals", "self-starter", "team player wanted", "immediate start", "no experience necessary"

**Logic:** If JD contains 3+ boilerplate phrases AND has < 8 extracted skills → block.

### A2: Company Legitimacy + Notion Company Blocklist

Two-layer system: automatic pattern detection + user-curated Notion blocklist.

**Layer 1 — Pattern Detection (automatic):**

| Pattern | Example | Action |
|---------|---------|--------|
| Company name contains training keywords | "IT Career Switch", "Data Academy", "Tech Bootcamp" | Flag as suspected spam |
| Company name contains recruitment keywords | "Recruitment Solutions", "Staffing Agency" | Flag as suspected spam |
| Same company 10+ listings in last 7 days | IT Career Switch with 20 "Data Science Trainee" | Flag as suspected spam |

**Training/spam keywords in company name:** "training", "bootcamp", "academy", "career switch", "career change", "recruitment agency", "staffing", "talent pipeline", "apprenticeship scheme"

**Layer 2 — Notion Company Blocklist (user-curated):**

New Notion database: **"🚫 Company Blocklist"**

| Column | Type | Purpose |
|--------|------|---------|
| Company | title | Company name |
| Status | status | "Pending" (auto-flagged) / "Blocked" (user confirmed) / "Approved" (user cleared) |
| Reason | text | Why flagged (pattern match / spam count / manual) |
| Platform | select | Where first seen |
| Times Seen | number | How many listings from this company |
| First Seen | date | When first detected |
| Last Seen | date | Most recent listing |

**Flow:**
1. Pattern detection flags a company → add to Notion as "Pending" with reason
2. User reviews in Notion → marks "Blocked" or "Approved"
3. Before every scan (cron or Telegram), fetch blocklist from Notion
4. "Blocked" companies → skip all their listings immediately
5. "Approved" companies → never flag again
6. "Pending" companies → still allow applications (don't block until user decides)

**Caching:** Fetch blocklist once at start of each scan window, cache in memory. Don't query Notion per-listing.

### A3: Company Background Check

Lightweight checks using data already available (no external API calls).

| Check | Signal | Action |
|-------|--------|--------|
| Past applications | Applied to this company in last 90 days | Flag in Telegram summary — "Already applied to {company} on {date}" |
| Company appears across platforms | Same company on Reed AND LinkedIn | Not a red flag — just note in logs (dedup handles the listing itself) |
| Generic company name | "Tech Solutions", "Digital Services", "IT Consulting" (< 3 words, all generic) | Soft flag — lower confidence, don't block |

**Generic name detection:** Company name matches pattern: 1-3 words where all words are in a generic set: {tech, digital, it, solutions, services, consulting, group, limited, ltd, uk, global, systems, software, data, cloud, cyber, enterprise}

**Action on generic name:** Don't block — just add a note to the Telegram scan summary: "⚠️ Generic company name: {name} — verify legitimacy"

---

## Phase B: Post-Generation Checks (after CV is created)

### B1: Deterministic CV Scrutiny (free, instant)

Runs on the generated CV PDF text. Checks FAANG-level presentation standards.

| Check | Rule | Severity |
|-------|------|----------|
| Metrics in project bullets | Every project bullet contains at least one number (%, count, time, xN) | Warning — count violations |
| Page limit | CV text indicates > 2 pages (heuristic: > 4000 chars after extraction) | Error — block |
| Conversational text | Contains "I worked", "I helped", "I was responsible", "My role was" | Warning — count violations |
| Professional tone | Contains "really", "very", "just", "stuff", "things", "nice" | Warning — count violations |
| URL validity | Project URLs match known GitHub repos from portfolio | Warning — flag mismatches |
| Skills overcrowding | > 40 skills listed in skills section | Warning |

**Scoring:**
- 0 warnings → "clean" (proceed to B2)
- 1-2 warnings → "acceptable" (proceed to B2 with notes)
- 3+ warnings OR any error → "needs_fix" (skip B2, flag for review, still upload)

**Note:** B1 does NOT block uploads. It flags issues and the count feeds into B2's context.

### B2: LLM FAANG Recruiter Scrutiny (~$0.002/call)

Only runs if B1 returns "clean" or "acceptable". GPT-5o-mini acts as a senior recruiter at Google/Meta reviewing the CV against the specific JD.

**Prompt:**

```
You are a senior IT recruiter at Google reviewing a CV for the role: {role} at {company}.

JD Requirements:
{required_skills}
{preferred_skills}

CV:
{cv_text}

Score this CV 0-10 on:
1. Relevance to JD (0-3): Does it address the specific requirements?
2. Evidence quality (0-3): Are claims backed by metrics and projects?
3. Presentation (0-2): Professional tone, clear structure, no fluff?
4. Standout factor (0-2): Would this make you want to interview?

Return JSON:
{
  "total_score": 0-10,
  "relevance": 0-3,
  "evidence": 0-3,
  "presentation": 0-2,
  "standout": 0-2,
  "strengths": ["...", "..."],
  "weaknesses": ["...", "..."],
  "verdict": "shortlist" | "maybe" | "reject"
}
```

**Routing based on score:**

| Score | Verdict | Action |
|-------|---------|--------|
| 7-10 | shortlist/maybe | Upload to Drive, sync to Notion, proceed normally |
| 4-6 | maybe/reject | Upload to Drive, Notion status = "Needs Review", Telegram alert with weaknesses |
| 0-3 | reject | Upload to Drive, Notion status = "Needs Review", Telegram alert: "CV scored {score}/10 — likely won't pass screening" |

**Key:** B2 never blocks the upload entirely — it flags for review. The user always gets the CV and can decide.

**Cost:** Only fires for jobs that pass Gates 0-3 AND Phase A. Expected: 5-10 calls/day max → ~$0.01-0.02/day.

---

## Files

| File | Purpose |
|------|---------|
| **Create:** `jobpulse/gate4_quality.py` | All Phase A + Phase B checks |
| **Create:** `jobpulse/company_blocklist.py` | Notion Company Blocklist CRUD + cache |
| **Create:** `tests/test_gate4_quality.py` | Unit tests for all checks |
| **Create:** `tests/test_company_blocklist.py` | Blocklist tests |
| **Modify:** `jobpulse/job_autopilot.py` | Insert Gate 4A after Gate 3, Gate 4B after CV generation |
| **Modify:** `jobpulse/config.py` | Add `NOTION_BLOCKLIST_DB_ID` env var |

---

## Testing Strategy

- **A1 tests:** Short JD, missing skills, boilerplate detection, clean JD passes
- **A2 tests:** Spam keywords, Notion blocklist fetch (mocked), pending/blocked/approved status logic
- **A3 tests:** Past application detection, generic name detection
- **B1 tests:** Metrics check, conversational text detection, page limit, URL validation
- **B2 tests:** LLM scoring (mocked), routing by score, invalid JSON handling
- All tests use `tmp_path` for DB, mock Notion API — never touch production
