# Hybrid Fact Verification — Design Spec

## Goal

Replace the current circular fact-checker (LLM checking LLM against the same abstract) with a multi-source verification system that cross-references claims against external databases and checks repository health. Output a single honest score with a human-readable explanation of why.

## Problem Statement

Current fact-checking is dishonest:
- Verifies paper summaries against abstracts only (the paper checking itself)
- A paper claiming "92% on MMLU" gets VERIFIED if the summary matches the abstract, even if the actual leaderboard shows 89.1%
- No check on whether code exists, runs, or is reproducible
- DuckDuckGo search is low-quality (blog posts count as "evidence")
- Result: papers with exaggerated/unverifiable claims get 10/10 scores

## Architecture

### 3-Level Verification Pipeline

```
Paper + Summary + Claims
    │
    ├── Level 1: Summary vs Abstract (EXISTING)
    │   Checks: did the LLM summarizer distort the paper's claims?
    │   Source: paper abstract
    │   Verdict weight: 0.5 (self-referential, less trustworthy)
    │
    ├── Level 2: External Source Verification (NEW)
    │   ├── Papers With Code API — benchmark claims
    │   ├── Semantic Scholar API — citations, authors, dates, venue
    │   └── DuckDuckGo — fallback with source quality scoring
    │   Verdict weight: 1.0 (independent sources)
    │
    └── Level 3 Free: Repo Health Check (NEW)
        ├── Repo existence (from paper URL or Papers With Code)
        ├── README, tests/, requirements presence
        ├── Stars, forks, last commit recency
        ├── Open issues mentioning reproducibility problems
        └── License present
        Verdict: REPO_HEALTHY / REPO_UNHEALTHY / REPO_MISSING / REPO_NA
```

### Verification Flow

```
extract_claims(summary, title)
    │
    ▼
For each claim:
    1. Check cache (verified_facts.db) → if cached + fresh (<30 days), use it
    2. Classify claim type → route to appropriate verifier:
       - "benchmark" → Papers With Code leaderboard lookup
       - "date", "attribution" → Semantic Scholar metadata
       - "comparison" → Papers With Code + Semantic Scholar
       - "technical" → Abstract check + DuckDuckGo
       - "opinion", "definition" → SKIP
    3. Verify against source → verdict + evidence + source_url
    4. Cache result with source attribution
    │
    ▼
Check repo health (once per paper, not per claim):
    1. Extract repo URL from paper (abstract, Papers With Code, arxiv page)
    2. GitHub API: exists? stars? forks? last commit? has tests?
    3. Check open issues for reproducibility complaints
    │
    ▼
Compute honest score + generate explanation
```

### Scoring Model

**Per-claim scoring (weighted by verification source):**

| Verdict | Source | Points | Rationale |
|---------|--------|--------|-----------|
| VERIFIED | External (PwC/S2/web) | +1.0 | Confirmed by independent source |
| VERIFIED | Abstract only | +0.5 | Self-referential, less trustworthy |
| UNVERIFIED | No evidence found | -1.0 | Absence of evidence is concerning for specific claims |
| EXAGGERATED | External contradicts | -1.5 | Leaderboard/database shows different numbers |
| INACCURATE | External contradicts | -2.0 | Directly wrong |
| SKIPPED | Opinion/definition | 0.0 | Not counted |

**Repo health adjustment (applied once per paper):**

| Repo Status | Adjustment | Rationale |
|-------------|-----------|-----------|
| REPO_HEALTHY | +0.0 | Expected — no bonus for doing the minimum |
| REPO_UNHEALTHY | -0.3 | Exists but no tests/docs/stale — yellow flag |
| REPO_MISSING | -0.5 | Claims open-source but no working repo |
| REPO_NA | +0.0 | Paper doesn't claim code release — no penalty |

**Final score:** `10.0 * (total_points / max_points) + repo_adjustment`, clamped to [0.0, 10.0]

### Explanation Generation

Every score includes a human-readable explanation built from the verification results:

```
Fact-check: 6.2/10 — 3/4 claims verified externally,
1 benchmark exaggerated (claims 92% MMLU, leaderboard shows 89.1%),
repo exists but no tests
```

Template: `{score}/10 — {verified_count}/{total_claims} claims verified{external_note}, {issues_summary}, {repo_summary}`

Components:
- `external_note`: " externally" if any claim verified via PwC/S2, omitted if abstract-only
- `issues_summary`: list of non-VERIFIED claims with specifics (what was claimed vs what evidence shows)
- `repo_summary`: one of "repo healthy (N stars, tests pass)", "repo exists but {problems}", "no repo found", or omitted if N/A

### External APIs

**Papers With Code (free, no auth):**
- `GET https://paperswithcode.com/api/v1/papers/?arxiv_id={id}` — paper exists?
- `GET https://paperswithcode.com/api/v1/papers/{id}/results/` — benchmark results
- `GET https://paperswithcode.com/api/v1/papers/{id}/repositories/` — linked repos
- Rate limit: reasonable (no published limit, be polite with 1s delays)

**Semantic Scholar (free, no auth for <100 req/s):**
- `GET https://api.semanticscholar.org/graph/v1/paper/ArXiv:{id}?fields=title,authors,citationCount,referenceCount,venue,publicationDate,openAccessPdf,externalIds`
- Fields: citation count, venue (peer-reviewed?), publication date, author list
- Rate limit: 100 requests/second without API key

**GitHub API (via `gh` CLI, uses stored auth):**
- Repo metadata: stars, forks, open issues, last push date
- Directory listing: check for tests/, README, requirements.txt, LICENSE
- Issue search: `is:issue reproducibility OR "cannot reproduce" OR bug`

### Caching Strategy

- **Papers With Code results:** Cache for 7 days (leaderboards update slowly)
- **Semantic Scholar metadata:** Cache for 30 days (citations grow but slowly)
- **Repo health:** Cache for 3 days (repos change frequently)
- **Claim verifications:** Cache for 30 days (existing behavior) but tag with source level

Add `source_level` column to `verified_facts` table: "abstract", "external", "web"

### Ralph Loop Learning Integration

After each daily digest:
1. Store verification results as experiences in `swarm_experience.db`
2. Track per-lab/author verification success rates
3. Evolve arXiv agent persona to:
   - Flag papers from labs with history of exaggerated benchmarks
   - Boost papers with confirmed external benchmarks + healthy repos
   - Learn which claim types are most often unverifiable
4. Experience key: `"arxiv_verification_{arxiv_id}"`

### Files

| File | Action |
|------|--------|
| `shared/fact_checker.py` | Modify — add multi-source verification, honest scoring, explanation generation |
| `shared/external_verifiers.py` | Create — Papers With Code, Semantic Scholar, Repo Health checkers |
| `jobpulse/arxiv_agent.py` | Modify — pass verification results through, update digest format |
| `tests/test_external_verifiers.py` | Create — mock API tests for all 3 external sources |
| `tests/test_fact_checker.py` | Modify — update scoring tests for new weights |
| `tests/test_arxiv_agent.py` | Modify — update for new explanation format |
| `scripts/arxiv_benchmark.py` | Modify — add external verification benchmarks |

### What This Does NOT Include

- Paid API access (everything is free tier)
- Code execution or Docker sandboxing (Level 3 free only)
- Full benchmark reproduction (would need GPUs)
- Changing the ranking algorithm (separate concern)
- Verifying papers older than the current daily digest

### Cost

- Papers With Code: $0 (free API)
- Semantic Scholar: $0 (free under 100 req/s)
- GitHub: $0 (uses existing gh auth)
- DuckDuckGo: $0 (existing)
- OpenAI (claim extraction): ~$0.005/paper (existing cost, unchanged)
- **Total per daily digest (5 papers): ~$0.025** (same as before)
