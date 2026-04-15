# Paper Pipeline Overhaul — Design Spec

> Rewire `build_digest()` to use the new `papers/` pipeline, fix broken community sources, add GitHub + Semantic Scholar enrichment, rebalance scoring.

## Problem Statement

The paper discovery pipeline has two problems:

1. **Broken sources:** PapersWithCode API redirects to HuggingFace (302). X/SearXNG/Nitter instances are all blocked. Reddit uses `praw` which isn't installed. Result: only HuggingFace + HackerNews return papers. When both fail, the arXiv API fallback gets 429 rate-limited.

2. **Dual pipeline:** `build_digest()` in `arxiv_agent.py` uses the old `paper_discovery.py` (raw dicts, sync, no Pydantic models). The new `papers/` package (`PaperFetcher` + `PaperRanker` + `PaperStore` + `DigestBuilder`) already has typed models, async fetching, HF linked models, and proper scoring — but nothing in the Telegram flow calls it.

## Approach

Upgrade the new `papers/` pipeline with all sources and enrichment, then rewire `build_digest()` to call `PapersPipeline.daily_digest()`. Single pipeline, single codepath.

## Source Architecture

7 sources across 3 tiers:

### Tier 1 — Primary (always run, high yield)

- **HuggingFace Daily Papers** — `GET https://huggingface.co/api/daily_papers`. ~50 papers/day with upvotes. Already working in `PaperFetcher._fetch_huggingface()`.
- **Semantic Scholar bulk search** (new) — `GET https://api.semanticscholar.org/graph/v1/paper/search/bulk?query="artificial+intelligence"|"machine+learning"|"large+language+model"&fields=title,citationCount,publicationDate,externalIds&year=2026-&minCitationCount=1&fieldsOfStudy=Computer+Science`. Returns recent CS/AI papers already getting cited. No auth needed (shared rate limit with other unauthenticated users). Single request, first page only (up to 1000 results, we take top 50 by citationCount).

### Tier 2 — Community buzz (best-effort, may return 0)

- **HackerNews** — `GET https://hn.algolia.com/api/v1/search_by_date?query=arxiv.org&tags=story`. ~10 papers/day. Already working.
- **Reddit JSON API** (replaces `praw`) — `GET https://www.reddit.com/r/MachineLearning/new.json?limit=50`. No dependency needed, just httpx + User-Agent header. ~2-5 papers/day.
- **Bluesky** (new, replaces X/Nitter) — `GET https://api.bsky.app/xrpc/app.bsky.feed.searchPosts?q=arxiv.org&limit=25`. No auth needed. Growing AI researcher community.

### Tier 3 — Fallback (only when Tiers 1+2 return <5 papers)

- **arXiv RSS** — 3 category feeds (cs.AI, cs.LG, cs.CL). Existing.
- **arXiv API** — Last resort, conservative (50 results max, not 200). Existing.

### Removed

- **PapersWithCode** — API now redirects to HuggingFace (302). Dead.
- **X/SearXNG/Nitter** — All Nitter instances blocked. SearXNG requires local instance + Nitter backend.

## Enrichment Pipeline

After dedup, every paper gets enriched from 3 sources. All async with conservative rate limits.

### Semantic Scholar enrichment

- Endpoint: `GET /graph/v1/paper/ARXIV:{id}?fields=title,abstract,citationCount,influentialCitationCount,authors,year`
- Gets: abstract (if missing), citation count, influential citation count, author list
- Rate: 0.2s delay between requests (unauthenticated tier). If `S2_API_KEY` set, use `x-api-key` header for 1 req/sec.
- Cap: enrich up to 60 papers per run

### GitHub repo check (new)

Two strategies in order:
1. If abstract contains `github.com` URL — extract directly (free, no API call)
2. Otherwise, search GitHub API: `GET /search/repositories?q={arxiv_id}` for top-30 papers only (after fast_score pre-filter)

Gets: `github_url`, `github_stars`

Rate: `GITHUB_TOKEN` (already set) gives 30 req/min for search. Only search top-30 candidates, not all papers.

Fallback: skip on rate limit — GitHub signals are bonus, not required.

### HuggingFace model + dataset check

- `linked_models`: already fetched by `PaperFetcher._fetch_linked_models()` for HF papers. Extended to run for all papers.
- `linked_datasets` (new): `GET https://huggingface.co/api/datasets?search={arxiv_id}&limit=5`. Same pattern as models.

## Paper Model Changes

New fields on `Paper`:
```
github_url: str = ""
github_stars: int = 0
s2_citation_count: int = 0
s2_influential_citations: int = 0
community_buzz: int = 0        # aggregated social signal across sources
sources: list[str] = []        # which discovery sources found this paper
```

## Scoring — `fast_score` Rebalance

Same 10.0 max, rebalanced to incorporate all signals:

| Signal | Points | Logic |
|--------|--------|-------|
| Category bonus | up to 2.0 | Best matching category (cs.AI/cs.LG=2.0, cs.CL/stat.ML=1.5, cs.MA=1.0) |
| Community buzz | up to 2.0 | 2.0 if >100, 1.5 if >50, 1.0 if >20, 0.5 if >5 |
| HF upvotes | up to 1.5 | 1.5 if >50, 1.0 if >20, 0.5 if >5 |
| S2 citations | up to 1.5 | 1.5 if >20, 1.0 if >10, 0.5 if >3 |
| GitHub repo | up to 1.0 | 0.5 for having a repo + 0.5 if stars>50 |
| Linked models/datasets | up to 1.0 | 0.5 per type (capped at 1.0 total) |
| Multi-source bonus | up to 0.5 | 3+ sources = 0.5, 2 sources = 0.25 |
| Recency | 0.5 | Always awarded |

LLM ranker (`llm_rank`) unchanged — pre-filters by fast_score top 30, then GPT-4.1-mini ranks by novelty/significance/practical/breadth.

## Pipeline Wiring

### `build_digest()` becomes thin wrapper

```python
# arxiv_agent.py
def build_digest(top_n: int = 5) -> str:
    import asyncio
    from jobpulse.papers import PapersPipeline
    pipeline = PapersPipeline()
    return asyncio.run(pipeline.daily_digest(top_n=top_n))
```

### `PapersPipeline.daily_digest()` updated flow

```
fetch_all()  →  enrich()  →  llm_rank()  →  summarize_and_verify()  →  store()  →  notion()  →  format()
  │                │              │                  │                     │           │            │
  7 sources      S2+GH+HF     fast_score         GPT-4.1-mini        SQLite      Notion      Telegram
  deduped       enrichment    pre-filter +                           papers.db    pages       message
                              LLM rank
```

### What stays the same

- `_handle_arxiv()` in `dispatcher.py` — still calls `build_digest()`
- Sub-commands: `paper 3`, `blog 1`, `read 1`, `papers stats` — read from same `PaperStore`
- `DigestBuilder` format — already handles HF signals, extended with GitHub/S2
- Help text — unchanged
- Intent classification — unchanged

### New in digest output

- GitHub repo link + stars alongside arXiv/PDF links
- S2 citation count displayed
- Source attribution line: "Found on: HuggingFace, HackerNews, Reddit"

## File Changes

### Modified

| File | Change |
|------|--------|
| `jobpulse/papers/models.py` | Add `github_url`, `github_stars`, `s2_citation_count`, `s2_influential_citations`, `community_buzz`, `sources` to `Paper` |
| `jobpulse/papers/fetcher.py` | Add 5 fetch methods: `_fetch_s2_trending()`, `_fetch_hackernews()`, `_fetch_reddit_json()`, `_fetch_bluesky()`, `_fetch_arxiv_rss_fallback()`. Add enrichment: `_enrich_github()`, `_fetch_linked_datasets()`. Update `fetch_all()` with tiered fallback. |
| `jobpulse/papers/ranker.py` | Rebalance `fast_score()` with new signals |
| `jobpulse/papers/digest.py` | Add GitHub link/stars + S2 citations + source attribution to format methods |
| `jobpulse/papers/store.py` | Migration for new columns |
| `jobpulse/papers/__init__.py` | Add enrichment step in `daily_digest()` |
| `jobpulse/arxiv_agent.py` | Replace `build_digest()` body with async wrapper |

### Not modified

- `jobpulse/dispatcher.py` — routing unchanged
- `jobpulse/command_router.py` — intent patterns unchanged
- `jobpulse/paper_discovery.py` — left in place, no longer called by `build_digest()`
- `jobpulse/papers/blog_pipeline.py`, `notion_publisher.py` — unchanged

## Environment Variables

New (all optional with defaults):
- `S2_API_KEY` — Semantic Scholar API key. Faster rate limits when set. Default: unauthenticated.
- `BSKY_HANDLE` / `BSKY_APP_PASSWORD` — Bluesky auth. Only needed if public search gets rate limited.

No new Python dependencies. Reddit switches from `praw` to `httpx` JSON API.

## Rate Limits & Cost

| Source | Rate | Cost |
|--------|------|------|
| HuggingFace Daily | 1 req/run | Free |
| Semantic Scholar search | 1 req/run | Free |
| Semantic Scholar enrich | ~60 req/run at 0.2s gap = 12s | Free |
| HackerNews | 1 req/run | Free |
| Reddit JSON | 2 req/run (2 subreddits) | Free |
| Bluesky search | 1 req/run | Free |
| GitHub search | up to 30 req/run | Free (with GITHUB_TOKEN) |
| HF models/datasets | up to 120 req/run (60 each) | Free |
| LLM ranking | 1 GPT-4.1-mini call | ~$0.005 |
| LLM summaries | 5 GPT-4.1-mini calls | ~$0.01 |

Total cost per digest: ~$0.015. Total time: ~30-45s (dominated by S2 enrichment delays).
