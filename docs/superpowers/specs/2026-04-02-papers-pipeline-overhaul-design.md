# Papers Pipeline Overhaul — Design Spec

> Layered refactor of the arXiv/papers pipeline: fix broken imports, add HuggingFace ecosystem, charts, weekly digest, clean architecture.

## Problem Statement

The papers pipeline has 3 critical issues:
1. **`notion_papers_agent.py` is broken** — imports `fast_score` and `llm_rank` which don't exist in `arxiv_agent.py` (renamed to `llm_rank_broad`, `fast_score` removed). Weekly Monday digest has been silently failing since commit `0e2aea3`.
2. **`store_papers()` reads wrong key** — `fc.get("accuracy_score")` but fact_check dict uses `"score"`. Fact-check scores always stored as 0.
3. **`blog_generator.handle_blog_command()` crashes on invalid paper index** — `get_paper_by_index()` returns `None`, not handled.

Beyond fixes, the pipeline is a single 819-line file (`arxiv_agent.py`) mixing fetching, ranking, storage, Notion publishing, and Telegram formatting. Adding HuggingFace support or new features requires touching everything.

## Architecture: Layered Pipeline

```
jobpulse/papers/
├── __init__.py              # PapersPipeline orchestrator
├── fetcher.py               # PaperFetcher (async: arXiv + HuggingFace)
├── ranker.py                # PaperRanker (fast scoring + LLM ranking)
├── store.py                 # PaperStore (SQLite + queries)
├── digest.py                # DigestBuilder (daily + weekly formatters)
├── blog_pipeline.py         # BlogPipeline (6-agent: + chart generator)
├── notion_publisher.py      # NotionPublisher (daily/weekly/blog pages)
├── chart_generator.py       # ChartGenerator (matplotlib/plotly for benchmarks)
└── models.py                # Pydantic models (Paper, RankedPaper, Digest, BlogPost)
```

### Orchestrator Wiring

```python
class PapersPipeline:
    def __init__(self):
        self.fetcher = PaperFetcher()
        self.ranker = PaperRanker()
        self.store = PaperStore()
        self.digest = DigestBuilder()
        self.blog = BlogPipeline()
        self.notion = NotionPublisher()

    async def daily_digest(self, top_n=5) -> str:
        papers = await self.fetcher.fetch_all()
        ranked = self.ranker.rank(papers, top_n=top_n)
        verified = self.ranker.summarize_and_verify(ranked)
        self.store.store(verified, digest_date=today())
        self.notion.publish_daily(verified)
        return self.digest.format_daily(verified)

    async def weekly_digest(self, top_n=7) -> str:
        stored = self.store.get_week(last_n_days=7)
        missed = await self.fetcher.fetch_missed(self.store.get_missed_dates())
        combined = stored + missed
        ranked = self.ranker.rank(combined, top_n=top_n, lens="weekly")
        themes = self.ranker.extract_themes(ranked)
        self.notion.publish_weekly(ranked, themes)
        return self.digest.format_weekly(ranked, themes)

    def generate_blog(self, paper_index: int) -> BlogPost:
        paper = self.store.get_by_index(today(), paper_index)
        if not paper:
            raise ValueError(f"No paper at index {paper_index}")
        blog = self.blog.generate(paper)
        self.notion.publish_blog(blog)
        return blog
```

## Component Specs

### 1. PaperFetcher (async)

**Sources:**

| Source | Endpoint | What It Gets |
|--------|----------|-------------|
| arXiv API | `https://export.arxiv.org/api/query` | Papers from cs.AI, cs.LG, cs.CL, cs.MA, stat.ML |
| HuggingFace Daily Papers | `https://huggingface.co/api/daily_papers` | Trending papers with community upvotes |
| HuggingFace Papers→Models | `https://huggingface.co/api/models?paper={arxiv_id}` | Papers linked to released models/datasets |

**Async flow:**
```python
async def fetch_all(self) -> list[Paper]:
    arxiv_papers, hf_papers = await asyncio.gather(
        self._fetch_arxiv(),
        self._fetch_huggingface()
    )
    return self._deduplicate_and_merge(arxiv_papers, hf_papers)

async def fetch_missed(self, missed_dates: list[str]) -> list[Paper]:
    """Fetch papers for dates with no digest (weekend gaps, API failures).
    arXiv: query with submittedDate range covering missed dates.
    HuggingFace: re-fetch daily_papers (returns recent trending, not date-specific).
    Dedup against already-stored arxiv_ids to avoid duplicates.
    """
    ...
```

**Deduplication:** Match on `arxiv_id`. When paper appears in both sources, merge: keep arXiv abstract + HuggingFace upvotes/model links.

**Error handling:** Each source fails independently. arXiv failure + HF success = HF papers only. Both fail = empty list with structured error.

### 2. PaperRanker

**Phase 1: `fast_score(paper: Paper) -> float`** (deterministic, free)
```
score = 0.0
+ 0-3 pts: category relevance (cs.AI/cs.LG weighted higher)
+ 0-2 pts: HuggingFace upvotes (>50 = 2, >20 = 1)
+ 0-2 pts: has linked models/datasets
+ 0-1 pt:  has GitHub repo in abstract
+ 0-1 pt:  author count (collaborative signal)
+ 0-1 pt:  recency (today > yesterday > 2 days ago)
```
Max 10. Pre-filters to top 30.

**Phase 2: `llm_rank(papers, top_n, lens)` → `list[RankedPaper]`**
- Daily lens: novelty 30%, significance 25%, practical 30%, breadth 15%
- Weekly lens: significance 35%, practical 25%, novelty 25%, breadth 15%
- HF signals (upvotes, model count) passed as LLM context
- Category tags: [LLM, Agents, Vision, RL, Efficiency, Safety, Reasoning]
- Model: gpt-4.1-mini. Fallback: return papers[:top_n] by fast_score.

**`extract_themes(papers) -> list[str]`** (weekly only)
- Takes top 7 papers, returns 3-5 research themes
- ~$0.005 per call

**`summarize_and_verify(papers)`** — same as current: shared/fact_checker + repo health + Ralph Loop learning.

### 3. PaperStore (SQLite)

**New columns added to papers table:**
```sql
hf_upvotes INTEGER DEFAULT NULL,
linked_models TEXT DEFAULT NULL,        -- JSON array
linked_datasets TEXT DEFAULT NULL,      -- JSON array
model_card_summary TEXT DEFAULT NULL,
source TEXT DEFAULT 'arxiv',            -- 'arxiv' | 'huggingface' | 'both'
weekly_digest_date TEXT DEFAULT NULL,
blog_generated INTEGER DEFAULT 0
```

**Public API:**
```python
class PaperStore:
    def store(papers: list[RankedPaper], digest_date: str) -> None
    def get_by_index(digest_date: str, index: int) -> Paper | None
    def get_by_arxiv_id(arxiv_id: str) -> Paper | None
    def get_week(last_n_days: int = 7) -> list[Paper]
    def get_missed_dates(last_n_days: int = 7) -> list[str]
    def mark_read(arxiv_id: str) -> None
    def get_stats() -> ReadingStats
    def search(query: str, limit: int = 20) -> list[Paper]
```

Auto-migration on init: adds new columns if missing (same pattern as current).

### 4. DigestBuilder

**Daily Telegram format:**
```
AI Research Digest — Apr 2

1. 9.2 [LLM] Paper Title
   Authors | Key technique
   Summary (4 sentences)
   Fact-check: 9.5/10 (3/3 verified)
   42 upvotes | 2 models released          <- NEW: HF signals
   arXiv | PDF

... x5 papers

"paper 3" for details | "blog 1" for full post | "read 2" to mark read
```

**Weekly Telegram format:**
```
Weekly Research Summary — Mar 28 to Apr 3

Themes This Week:
- Efficient inference continues to dominate (3 papers)
- Agent tool-use converging on ReAct variants
- Multimodal benchmarks getting harder

Top 7 Papers:
1. 9.4 [Agents] Paper Title — why it matters in 1 line
   2 models | 180 upvotes
... x7

Stats: 35 papers processed | 12 read | 8 with code
```

### 5. BlogPipeline (6-agent + charts)

**Agent pipeline:**

| # | Agent | Change from Current |
|---|-------|-------------------|
| 1 | Deep Reader | Enhanced — includes HF model card summary as extra context when available |
| 2 | GRPO Writer | No change — 3 candidates at temps 0.5/0.7/0.9, pick best |
| 3 | Fact Checker | No change — shared/fact_checker.py |
| 4 | Revision | No change — fix flags if needed |
| 5 | Chart Generator | NEW — generates matplotlib/plotly visuals |
| 6 | Diagram Generator | No change — Mermaid.js architecture flowchart |
| 7 | Editor | Enhanced — embeds chart images alongside diagram |

**Null paper fix:** `generate_blog()` raises `ValueError` if paper not found, caught in `handle_blog_command()` which returns error message to Telegram.

### 6. ChartGenerator

**Flow:**
1. LLM extracts structured data from abstract + research notes (~$0.002)
2. Pick chart type per dataset:
   - `bar_comparison`: model comparisons, ablations
   - `line_scaling`: scaling curves, training dynamics
   - `radar_multi`: multi-metric comparisons
   - `table_image`: structured results as clean image
3. Generate via matplotlib, save as PNG
4. Consistent style: dark theme, teal accent (#1a5276), max 3 charts per blog
5. Fallback: if no chartable data found, return empty list (blog still works without charts)

**Cost:** Free (matplotlib local). LLM data extraction ~$0.002.

### 7. NotionPublisher

Consolidates 3 Notion publishing paths into one class:

| Method | Replaces | Creates |
|--------|----------|---------|
| `publish_daily(papers)` | `arxiv_agent.create_notion_paper_pages()` | Daily index table + per-paper child pages |
| `publish_weekly(papers, themes)` | `notion_papers_agent.create_weekly_page()` | Weekly summary with themes + top papers |
| `publish_blog(blog)` | `blog_generator.publish_to_notion()` | Blog page with charts + diagrams embedded |

Shared internals: `_notion_api()`, block formatting, image embedding.

### 8. Pydantic Models

```python
class Paper(BaseModel):
    arxiv_id: str
    title: str
    authors: list[str]
    abstract: str
    categories: list[str]
    pdf_url: str
    arxiv_url: str
    published_at: str
    source: Literal["arxiv", "huggingface", "both"] = "arxiv"
    hf_upvotes: int | None = None
    linked_models: list[str] = []
    linked_datasets: list[str] = []
    model_card_summary: str | None = None

class RankedPaper(Paper):
    fast_score: float = 0.0
    impact_score: float = 0.0
    impact_reason: str = ""
    category_tag: str = ""
    key_technique: str = ""
    practical_takeaway: str = ""
    summary: str = ""
    fact_check: FactCheckResult | None = None

class FactCheckResult(BaseModel):
    score: float = 0.0
    total_claims: int = 0
    verified_count: int = 0
    issues: list[str] = []
    explanation: str = ""
    repo_health: str | None = None

class Chart(BaseModel):
    chart_type: Literal["bar_comparison", "line_scaling", "radar_multi", "table_image"]
    title: str
    data: dict
    png_path: str
    description: str

class BlogPost(BaseModel):
    title: str
    content: str
    charts: list[Chart] = []
    mermaid_code: str = ""
    diagram_url: str = ""
    word_count: int = 0
    grpo_score: float = 0.0
    fact_check: FactCheckResult | None = None
    paper: Paper
    generated_at: str

class ReadingStats(BaseModel):
    total: int = 0
    read: int = 0
    unread: int = 0
    this_week: int = 0
    blog_count: int = 0
    with_models: int = 0
```

## API Changes

| Endpoint | Status | Change |
|----------|--------|--------|
| `GET /api/papers/fetch` | Fix | Returns Paper model with HF data |
| `GET /api/papers/digest` | Fix | No change |
| `GET /api/papers/stats` | Fix | Adds blog_count, with_models |
| `GET /api/papers/{index}` | Fix | Returns HF metadata |
| `POST /api/papers/blog/{index}` | New | Triggers blog generation |

## Tests

```
tests/papers/
├── test_fetcher.py          # arXiv XML, HF API, dedup/merge, async, errors
├── test_ranker.py           # fast_score, LLM rank, weekly lens, themes, JSON parsing
├── test_store.py            # CRUD, migration, search, stats, missed dates
├── test_digest.py           # Daily/weekly Telegram format, empty data
├── test_blog_pipeline.py    # 6-agent, null paper, fact-check, HF model card enrichment
├── test_chart_generator.py  # Data extraction, chart types, matplotlib output, no-data fallback
├── test_notion_publisher.py # Daily/weekly/blog pages, blocks, image embeds
└── conftest.py              # Fixtures: sample papers, mock OpenAI, mock HF API, tmp_path DB
```

All tests use `tmp_path` for DB. Never touch `data/papers.db`.

## Migration Plan

1. New code in `jobpulse/papers/` package
2. Old `arxiv_agent.py` — public functions become thin wrappers delegating to `PapersPipeline` (backwards compat for dispatcher/runner/cron)
3. Old `notion_papers_agent.py` — replaced entirely (was broken anyway)
4. Old `blog_generator.py` — public functions become thin wrappers
5. Dispatcher, command_router, runner — import paths stay same (wrappers handle it)
6. Legacy shell scripts (`notion-papers.sh`, `arxiv-daily.sh`) — deleted

## Bug Fixes Included

| Bug | Fix |
|-----|-----|
| `notion_papers_agent.py` imports `fast_score`, `llm_rank` (don't exist) | New `PaperRanker` has both functions. Weekly pipeline uses `PapersPipeline.weekly_digest()` |
| `store_papers()` reads `fc.get("accuracy_score")` but dict has `"score"` | `PaperStore.store()` reads from `FactCheckResult.score` (Pydantic model, not dict) |
| `handle_blog_command()` crashes on None paper | `PapersPipeline.generate_blog()` raises ValueError, caught in handler |

## Cost Estimate

| Operation | Cost | Frequency |
|-----------|------|-----------|
| Daily digest (rank + summarize + verify) | ~$0.03 | Daily |
| Weekly digest (re-rank + themes) | ~$0.02 | Weekly |
| Blog generation (6-agent + chart extraction) | ~$0.05 | On demand |
| HuggingFace API | Free | Daily |
| Charts (matplotlib) | Free | On demand |

## Dependencies

No new pip packages required. matplotlib is already available. httpx (already installed) used for async HF fetching.
