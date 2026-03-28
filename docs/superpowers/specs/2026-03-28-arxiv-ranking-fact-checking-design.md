# arXiv Ranking & Fact-Checking Quality Improvement — Design Spec

## Goal

Improve arXiv paper ranking quality and add fact-checking verification for paper claims, with measurable before/after benchmarking.

## Current State

### arXiv Agent (`jobpulse/arxiv_agent.py`)
- Fetches 200 papers from arXiv, takes top 30 by recency, LLM ranks top 5
- Ranking uses single flat 0-10 score on 4 criteria (novelty, significance, practical, breadth) — no per-criterion breakdown
- JSON parsing is brittle (string-split, breaks on markdown wrappers or text prefixes)
- **0 tests** — no test file exists
- No fact-checking on paper summaries — claims like "3x faster than BERT" go to Telegram unverified

### Fact Checker (`shared/fact_checker.py`)
- Extracts claims from text, verifies against sources + web search + SQLite cache
- Deterministic scoring: VERIFIED +1.0, INACCURATE -2.0, EXAGGERATED -1.0
- **Known bugs:** confidence not clamped to [0,1], verdict not normalized to uppercase, opinion/definition claims not filtered before sending to LLM
- 23 tests exist but gaps in: cache hit path, mixed batch, confidence bounds

## Architecture

### 5-Phase Execution

```
Phase 1: BASELINE → Write benchmark script, capture current metrics
Phase 2: SUBAGENT-DRIVEN → Tasks 1-6 (tests + fixes, deterministic)
Phase 3: SUBAGENT-DRIVEN → Tasks 7-11 (integration, well-defined)
Phase 4: RALPH LOOP → Refine ranking prompt + fact-check accuracy iteratively
Phase 5: BENCHMARK → Compare before vs after
```

### Benchmark Metrics (captured in Phase 1 and Phase 5)

| Metric | How Measured | Target |
|--------|-------------|--------|
| Ranking quality | LLM judges top 5 papers against ground truth (today's github.com/trending-style consensus) | >= 8.0/10 |
| Fact-check accuracy | Run against 10 known claims with known verdicts | >= 9.5/10 |
| JSON parse reliability | Feed 20 messy LLM response formats, count successful parses | 20/20 |
| Per-criteria scoring | Check that scores dict has all 4 dimensions | Present in all papers |
| Test coverage | pytest --cov for arxiv_agent + fact_checker | >= 80% |

### Changes to arXiv Agent

1. **Multi-criteria scoring** — Replace flat `score` with `scores: {novelty, significance, practical, breadth}` and weighted `overall` (0.3/0.25/0.3/0.15)
2. **Robust JSON parsing** — Regex-based extraction handles markdown wrappers, text prefixes, raw JSON
3. **`summarize_and_verify_paper()`** — New function: summarize paper, then fact-check claims in summary against abstract
4. **Fact-check badges in digest** — Telegram digest shows "3/3 verified" or "1/2 verified — exaggerated: '3x faster'"
5. **DB schema migration** — Add `fact_check_score`, `fact_check_claims`, `fact_check_verified`, `fact_check_issues` columns to papers table
6. **Full test suite** — ~20 new tests covering fetch, rank, parse, verify, format, store

### Changes to Fact Checker

1. **Confidence clamping** — Post-process LLM output: `max(0.0, min(1.0, confidence))`
2. **Verdict normalization** — `.upper()` after parsing
3. **Skip-type enforcement** — Filter opinion/definition claims before LLM call, add back as SKIPPED

### Ralph Loop Refinement (Phase 4)

After mechanical fixes are done, Ralph Loop iteratively improves:
- Ranking prompt wording and scoring weights
- Fact-check extraction sensitivity
- Until benchmark scores exceed targets

## Data Flow

```
arXiv API → fetch_papers(200) → top 30 by recency
  → llm_rank_broad(top_n=5) → multi-criteria scores
  → summarize_and_verify_paper() per paper
    → summarize_paper() → summary text
    → extract_claims(summary) → claims list
    → verify_claims(claims, abstract) → verdicts
    → compute_accuracy_score() → 0-10
  → store in papers.db (with fact_check columns)
  → format_digest() → Telegram with fact-check badges
```

## Files Touched

| File | Action |
|------|--------|
| `scripts/arxiv_benchmark.py` | Create — benchmark script |
| `tests/test_arxiv_agent.py` | Create — ~20 tests |
| `tests/test_fact_checker.py` | Modify — +5 tests |
| `jobpulse/arxiv_agent.py` | Modify — multi-criteria, JSON fix, fact-check integration |
| `shared/fact_checker.py` | Modify — confidence, verdict, skip-type fixes |

## What This Does NOT Include

- Changing the arXiv API query categories or result count
- Adding new fact-check data sources beyond DuckDuckGo
- Changing the Notion integration
- Modifying other agents (budget, email, etc.)
