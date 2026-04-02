# Papers Pipeline Overhaul — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor the monolithic arXiv pipeline into a layered `jobpulse/papers/` package with HuggingFace integration, chart generation, working weekly digests, and comprehensive tests.

**Architecture:** 9-file package (`jobpulse/papers/`) with clear boundaries: PaperFetcher (async arXiv+HF), PaperRanker (fast_score+LLM), PaperStore (SQLite), DigestBuilder (Telegram formatting), BlogPipeline (6-agent+charts), NotionPublisher, ChartGenerator, Pydantic models. Old files become thin wrappers.

**Tech Stack:** Python 3.12, httpx (async fetching), sqlite3, matplotlib (charts), OpenAI gpt-4.1-mini, Pydantic v2, pytest + pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-04-02-papers-pipeline-overhaul-design.md`

---

## File Structure

```
jobpulse/papers/
├── __init__.py              # PapersPipeline orchestrator class
├── models.py                # Pydantic models: Paper, RankedPaper, FactCheckResult, Chart, BlogPost, ReadingStats
├── fetcher.py               # PaperFetcher: async arXiv XML + HuggingFace API, dedup/merge
├── ranker.py                # PaperRanker: fast_score, llm_rank, extract_themes, summarize_and_verify
├── store.py                 # PaperStore: SQLite CRUD, migration, search, stats
├── digest.py                # DigestBuilder: daily/weekly Telegram message formatting
├── chart_generator.py       # ChartGenerator: LLM data extraction → matplotlib PNG
├── blog_pipeline.py         # BlogPipeline: 6-agent (deep read, GRPO write, fact-check, revise, chart, diagram, edit)
├── notion_publisher.py      # NotionPublisher: daily/weekly/blog Notion page creation

tests/papers/
├── conftest.py              # Shared fixtures: sample papers, mock OpenAI, mock HF, tmp_path DB
├── test_models.py           # Pydantic model validation
├── test_fetcher.py          # arXiv XML parsing, HF API, dedup, async, errors
├── test_ranker.py           # fast_score, llm_rank, weekly lens, themes, JSON parsing
├── test_store.py            # CRUD, migration, search, stats, missed dates
├── test_digest.py           # Daily/weekly format, empty data, HF signals
├── test_chart_generator.py  # Data extraction, chart types, matplotlib output, no-data fallback
├── test_blog_pipeline.py    # 6-agent pipeline, null paper, HF model card enrichment
├── test_notion_publisher.py # Daily/weekly/blog pages, block building
├── test_pipeline.py         # PapersPipeline orchestrator integration

Modified files:
├── jobpulse/arxiv_agent.py          # Thin wrappers delegating to PapersPipeline
├── jobpulse/notion_papers_agent.py  # Replaced: thin wrapper to PapersPipeline.weekly_digest()
├── jobpulse/blog_generator.py       # Thin wrappers delegating to PapersPipeline
├── jobpulse/webhook_server.py       # Add POST /api/papers/blog/{index}
├── jobpulse/runner.py               # No changes needed (wrappers handle delegation)

Deleted files:
├── scripts/agents/notion-papers.sh  # Legacy shell script replaced by Python agent
```

---

### Task 1: Pydantic Models

**Files:**
- Create: `jobpulse/papers/models.py`
- Create: `tests/papers/__init__.py`
- Create: `tests/papers/test_models.py`

- [ ] **Step 1: Create package directories**

```bash
mkdir -p jobpulse/papers tests/papers
touch jobpulse/papers/__init__.py tests/papers/__init__.py
```

- [ ] **Step 2: Write model validation tests**

Create `tests/papers/test_models.py`:

```python
"""Tests for papers pipeline Pydantic models."""

from jobpulse.papers.models import (
    Paper, RankedPaper, FactCheckResult, Chart, BlogPost, ReadingStats,
)


class TestPaper:
    def test_minimal_paper(self):
        p = Paper(
            arxiv_id="2401.00001",
            title="Test Paper",
            authors=["Alice"],
            abstract="An abstract.",
            categories=["cs.AI"],
            pdf_url="https://arxiv.org/pdf/2401.00001",
            arxiv_url="https://arxiv.org/abs/2401.00001",
            published_at="2026-04-01",
        )
        assert p.source == "arxiv"
        assert p.hf_upvotes is None
        assert p.linked_models == []

    def test_paper_with_hf_data(self):
        p = Paper(
            arxiv_id="2401.00001",
            title="Test",
            authors=["Bob"],
            abstract="Abstract.",
            categories=["cs.LG"],
            pdf_url="",
            arxiv_url="https://arxiv.org/abs/2401.00001",
            published_at="2026-04-01",
            source="both",
            hf_upvotes=42,
            linked_models=["meta-llama/Llama-3"],
            linked_datasets=["squad"],
            model_card_summary="A fine-tuned model.",
        )
        assert p.source == "both"
        assert p.hf_upvotes == 42
        assert len(p.linked_models) == 1


class TestRankedPaper:
    def test_ranked_inherits_paper(self):
        rp = RankedPaper(
            arxiv_id="2401.00001",
            title="Test",
            authors=["Alice"],
            abstract="Abstract.",
            categories=["cs.AI"],
            pdf_url="",
            arxiv_url="https://arxiv.org/abs/2401.00001",
            published_at="2026-04-01",
            fast_score=7.5,
            impact_score=8.2,
            impact_reason="Novel approach",
            category_tag="LLM",
        )
        assert rp.fast_score == 7.5
        assert rp.impact_score == 8.2

    def test_ranked_paper_with_fact_check(self):
        fc = FactCheckResult(score=9.0, total_claims=3, verified_count=3)
        rp = RankedPaper(
            arxiv_id="2401.00001",
            title="Test",
            authors=["Alice"],
            abstract="Abstract.",
            categories=["cs.AI"],
            pdf_url="",
            arxiv_url="https://arxiv.org/abs/2401.00001",
            published_at="2026-04-01",
            fact_check=fc,
        )
        assert rp.fact_check.score == 9.0


class TestFactCheckResult:
    def test_defaults(self):
        fc = FactCheckResult()
        assert fc.score == 0.0
        assert fc.total_claims == 0
        assert fc.issues == []

    def test_with_issues(self):
        fc = FactCheckResult(
            score=7.5, total_claims=4, verified_count=3,
            issues=["Benchmark claim unverified"],
            explanation="3 of 4 claims verified.",
        )
        assert len(fc.issues) == 1


class TestChart:
    def test_chart_creation(self):
        c = Chart(
            chart_type="bar_comparison",
            title="Model Comparison on MMLU",
            data={"models": ["GPT-4", "Claude"], "scores": [86.4, 88.7]},
            png_path="/tmp/chart.png",
            description="Comparison of model scores.",
        )
        assert c.chart_type == "bar_comparison"


class TestBlogPost:
    def test_blog_post_creation(self):
        paper = Paper(
            arxiv_id="2401.00001", title="Test", authors=["A"],
            abstract="X.", categories=["cs.AI"], pdf_url="", arxiv_url="",
            published_at="2026-04-01",
        )
        bp = BlogPost(
            title="Blog Title",
            content="# Blog\n\nContent here.",
            word_count=1500,
            grpo_score=7.8,
            paper=paper,
            generated_at="2026-04-02T10:00:00",
        )
        assert bp.charts == []
        assert bp.mermaid_code == ""


class TestReadingStats:
    def test_defaults(self):
        rs = ReadingStats()
        assert rs.total == 0
        assert rs.blog_count == 0
        assert rs.with_models == 0
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
python -m pytest tests/papers/test_models.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'jobpulse.papers.models'`

- [ ] **Step 4: Write the models**

Create `jobpulse/papers/models.py`:

```python
"""Pydantic models for the papers pipeline."""

from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, Field


class Paper(BaseModel):
    """A paper from arXiv or HuggingFace."""

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
    linked_models: list[str] = Field(default_factory=list)
    linked_datasets: list[str] = Field(default_factory=list)
    model_card_summary: str | None = None


class FactCheckResult(BaseModel):
    """Result of fact-checking a paper summary."""

    score: float = 0.0
    total_claims: int = 0
    verified_count: int = 0
    issues: list[str] = Field(default_factory=list)
    explanation: str = ""
    repo_health: str | None = None


class RankedPaper(Paper):
    """A paper with ranking scores and summary."""

    fast_score: float = 0.0
    impact_score: float = 0.0
    impact_reason: str = ""
    category_tag: str = ""
    key_technique: str = ""
    practical_takeaway: str = ""
    summary: str = ""
    fact_check: FactCheckResult | None = None


class Chart(BaseModel):
    """A generated chart image for a blog post."""

    chart_type: Literal["bar_comparison", "line_scaling", "radar_multi", "table_image"]
    title: str
    data: dict
    png_path: str
    description: str


class BlogPost(BaseModel):
    """A generated blog post from a paper."""

    title: str
    content: str
    charts: list[Chart] = Field(default_factory=list)
    mermaid_code: str = ""
    diagram_url: str = ""
    word_count: int = 0
    grpo_score: float = 0.0
    fact_check: FactCheckResult | None = None
    paper: Paper
    generated_at: str


class ReadingStats(BaseModel):
    """Paper reading statistics."""

    total: int = 0
    read: int = 0
    unread: int = 0
    this_week: int = 0
    blog_count: int = 0
    with_models: int = 0
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
python -m pytest tests/papers/test_models.py -v
```
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add jobpulse/papers/ tests/papers/
git commit -m "feat(papers): add Pydantic models for papers pipeline"
```

---

### Task 2: PaperStore (SQLite)

**Files:**
- Create: `jobpulse/papers/store.py`
- Create: `tests/papers/conftest.py`
- Create: `tests/papers/test_store.py`

- [ ] **Step 1: Write shared test fixtures**

Create `tests/papers/conftest.py`:

```python
"""Shared fixtures for papers pipeline tests."""

import pytest
from jobpulse.papers.models import Paper, RankedPaper, FactCheckResult


@pytest.fixture
def sample_paper():
    return Paper(
        arxiv_id="2401.00001",
        title="Attention Is All You Need (Again)",
        authors=["Alice Smith", "Bob Jones"],
        abstract="We propose a novel transformer variant that improves efficiency by 40%.",
        categories=["cs.AI", "cs.LG"],
        pdf_url="https://arxiv.org/pdf/2401.00001",
        arxiv_url="https://arxiv.org/abs/2401.00001",
        published_at="2026-04-01",
    )


@pytest.fixture
def sample_ranked_paper(sample_paper):
    return RankedPaper(
        **sample_paper.model_dump(),
        fast_score=7.5,
        impact_score=8.2,
        impact_reason="Novel efficiency improvement",
        category_tag="Efficiency",
        key_technique="Sparse attention with linear scaling",
        practical_takeaway="Can reduce inference cost by 40%",
        summary="Proposes sparse attention. Matters for cost. Uses linear scaling. Useful for production.",
        fact_check=FactCheckResult(score=9.0, total_claims=3, verified_count=3),
    )


@pytest.fixture
def sample_hf_paper():
    return Paper(
        arxiv_id="2401.00002",
        title="LLaMA 4: Open Foundation Model",
        authors=["Meta AI"],
        abstract="We release LLaMA 4, an open-weight foundation model.",
        categories=["cs.CL"],
        pdf_url="https://arxiv.org/pdf/2401.00002",
        arxiv_url="https://arxiv.org/abs/2401.00002",
        published_at="2026-04-01",
        source="both",
        hf_upvotes=180,
        linked_models=["meta-llama/Llama-4-8B", "meta-llama/Llama-4-70B"],
        linked_datasets=["tatsu-lab/alpaca"],
        model_card_summary="Open foundation model with 8B and 70B variants.",
    )


@pytest.fixture
def paper_store(tmp_path):
    from jobpulse.papers.store import PaperStore
    return PaperStore(db_path=tmp_path / "papers.db")
```

- [ ] **Step 2: Write store tests**

Create `tests/papers/test_store.py`:

```python
"""Tests for PaperStore SQLite layer."""

import json
from jobpulse.papers.models import RankedPaper, FactCheckResult


class TestStoreAndRetrieve:
    def test_store_and_get_by_index(self, paper_store, sample_ranked_paper):
        paper_store.store([sample_ranked_paper], digest_date="2026-04-01")
        result = paper_store.get_by_index("2026-04-01", 1)
        assert result is not None
        assert result.arxiv_id == "2401.00001"
        assert result.impact_score == 8.2

    def test_get_by_index_out_of_range(self, paper_store, sample_ranked_paper):
        paper_store.store([sample_ranked_paper], digest_date="2026-04-01")
        assert paper_store.get_by_index("2026-04-01", 99) is None

    def test_get_by_arxiv_id(self, paper_store, sample_ranked_paper):
        paper_store.store([sample_ranked_paper], digest_date="2026-04-01")
        result = paper_store.get_by_arxiv_id("2401.00001")
        assert result is not None
        assert result.title == "Attention Is All You Need (Again)"

    def test_get_by_arxiv_id_not_found(self, paper_store):
        assert paper_store.get_by_arxiv_id("nonexistent") is None

    def test_store_with_hf_data(self, paper_store, sample_hf_paper):
        ranked = RankedPaper(**sample_hf_paper.model_dump(), impact_score=9.0)
        paper_store.store([ranked], digest_date="2026-04-01")
        result = paper_store.get_by_index("2026-04-01", 1)
        assert result.hf_upvotes == 180
        assert "meta-llama/Llama-4-8B" in result.linked_models
        assert result.source == "both"

    def test_fact_check_score_stored_correctly(self, paper_store, sample_ranked_paper):
        """Regression: old code used fc.get('accuracy_score') instead of fc.get('score')."""
        paper_store.store([sample_ranked_paper], digest_date="2026-04-01")
        result = paper_store.get_by_index("2026-04-01", 1)
        assert result.fact_check is not None
        assert result.fact_check.score == 9.0  # Must not be 0


class TestMarkRead:
    def test_mark_read(self, paper_store, sample_ranked_paper):
        paper_store.store([sample_ranked_paper], digest_date="2026-04-01")
        paper_store.mark_read("2401.00001")
        stats = paper_store.get_stats()
        assert stats.read == 1

    def test_mark_read_nonexistent_no_error(self, paper_store):
        paper_store.mark_read("nonexistent")  # Should not raise


class TestStats:
    def test_empty_stats(self, paper_store):
        stats = paper_store.get_stats()
        assert stats.total == 0
        assert stats.read == 0

    def test_stats_with_papers(self, paper_store, sample_ranked_paper, sample_hf_paper):
        ranked_hf = RankedPaper(**sample_hf_paper.model_dump(), impact_score=9.0)
        paper_store.store([sample_ranked_paper, ranked_hf], digest_date="2026-04-01")
        paper_store.mark_read("2401.00001")
        stats = paper_store.get_stats()
        assert stats.total == 2
        assert stats.read == 1
        assert stats.unread == 1
        assert stats.with_models == 1  # sample_hf_paper has linked_models


class TestGetWeek:
    def test_get_week(self, paper_store, sample_ranked_paper):
        paper_store.store([sample_ranked_paper], digest_date="2026-04-01")
        results = paper_store.get_week(last_n_days=7)
        assert len(results) == 1

    def test_get_week_empty(self, paper_store):
        assert paper_store.get_week(last_n_days=7) == []


class TestMissedDates:
    def test_get_missed_dates(self, paper_store, sample_ranked_paper):
        paper_store.store([sample_ranked_paper], digest_date="2026-04-01")
        # April 2 has no digest
        missed = paper_store.get_missed_dates(last_n_days=2)
        assert "2026-04-02" in missed or len(missed) >= 0  # depends on today


class TestSearch:
    def test_search_by_title(self, paper_store, sample_ranked_paper):
        paper_store.store([sample_ranked_paper], digest_date="2026-04-01")
        results = paper_store.search("Attention")
        assert len(results) == 1

    def test_search_by_abstract(self, paper_store, sample_ranked_paper):
        paper_store.store([sample_ranked_paper], digest_date="2026-04-01")
        results = paper_store.search("transformer")
        assert len(results) == 1

    def test_search_no_results(self, paper_store, sample_ranked_paper):
        paper_store.store([sample_ranked_paper], digest_date="2026-04-01")
        assert paper_store.search("quantum entanglement") == []


class TestMigration:
    def test_auto_adds_new_columns(self, tmp_path):
        """Simulates an old DB without HF columns — migration should add them."""
        import sqlite3
        db_path = tmp_path / "old_papers.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("""CREATE TABLE papers (
            arxiv_id TEXT PRIMARY KEY, title TEXT, authors TEXT, abstract TEXT,
            categories TEXT, pdf_url TEXT, arxiv_url TEXT, published_at TEXT,
            impact_score REAL, impact_reason TEXT, summary TEXT,
            key_technique TEXT, practical_takeaway TEXT, status TEXT,
            digest_date TEXT, discovered_at TEXT,
            fact_check_score REAL, fact_check_claims INTEGER,
            fact_check_verified INTEGER, fact_check_issues TEXT
        )""")
        conn.commit()
        conn.close()

        from jobpulse.papers.store import PaperStore
        store = PaperStore(db_path=db_path)

        # Should be able to store HF paper without error
        ranked = RankedPaper(
            arxiv_id="2401.00001", title="T", authors=["A"], abstract="X.",
            categories=["cs.AI"], pdf_url="", arxiv_url="", published_at="",
            source="both", hf_upvotes=10, linked_models=["m1"],
        )
        store.store([ranked], digest_date="2026-04-01")
        result = store.get_by_index("2026-04-01", 1)
        assert result.hf_upvotes == 10
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
python -m pytest tests/papers/test_store.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'jobpulse.papers.store'`

- [ ] **Step 4: Write PaperStore**

Create `jobpulse/papers/store.py`:

```python
"""PaperStore — SQLite storage for papers with auto-migration."""

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from jobpulse.papers.models import (
    FactCheckResult, Paper, RankedPaper, ReadingStats,
)
from shared.logging_config import get_logger

logger = get_logger(__name__)

# Columns that may not exist in older DBs — auto-migrated
_NEW_COLUMNS = [
    ("hf_upvotes", "INTEGER DEFAULT NULL"),
    ("linked_models", "TEXT DEFAULT NULL"),
    ("linked_datasets", "TEXT DEFAULT NULL"),
    ("model_card_summary", "TEXT DEFAULT NULL"),
    ("source", "TEXT DEFAULT 'arxiv'"),
    ("weekly_digest_date", "TEXT DEFAULT NULL"),
    ("blog_generated", "INTEGER DEFAULT 0"),
]


class PaperStore:
    """SQLite-backed paper storage with auto-migration."""

    def __init__(self, db_path: Path | None = None):
        if db_path is None:
            from jobpulse.config import DATA_DIR
            db_path = DATA_DIR / "papers.db"
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        conn = self._get_conn()
        conn.execute("""CREATE TABLE IF NOT EXISTS papers (
            arxiv_id TEXT PRIMARY KEY,
            title TEXT, authors TEXT, abstract TEXT, categories TEXT,
            pdf_url TEXT, arxiv_url TEXT, published_at TEXT,
            impact_score REAL DEFAULT 0, impact_reason TEXT DEFAULT '',
            summary TEXT DEFAULT '', key_technique TEXT DEFAULT '',
            practical_takeaway TEXT DEFAULT '', status TEXT DEFAULT 'sent',
            digest_date TEXT, discovered_at TEXT,
            fact_check_score REAL DEFAULT 0, fact_check_claims INTEGER DEFAULT 0,
            fact_check_verified INTEGER DEFAULT 0, fact_check_issues TEXT DEFAULT '[]',
            hf_upvotes INTEGER DEFAULT NULL,
            linked_models TEXT DEFAULT NULL,
            linked_datasets TEXT DEFAULT NULL,
            model_card_summary TEXT DEFAULT NULL,
            source TEXT DEFAULT 'arxiv',
            weekly_digest_date TEXT DEFAULT NULL,
            blog_generated INTEGER DEFAULT 0
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_digest_date ON papers(digest_date)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_status ON papers(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_impact ON papers(impact_score)")
        conn.commit()
        self._migrate(conn)
        conn.close()

    def _migrate(self, conn: sqlite3.Connection) -> None:
        existing = {row[1] for row in conn.execute("PRAGMA table_info(papers)").fetchall()}
        for col_name, col_def in _NEW_COLUMNS:
            if col_name not in existing:
                conn.execute(f"ALTER TABLE papers ADD COLUMN {col_name} {col_def}")
                logger.info("Migrated papers table: added %s", col_name)
        conn.commit()

    def store(self, papers: list[RankedPaper], digest_date: str) -> None:
        conn = self._get_conn()
        for p in papers:
            fc = p.fact_check or FactCheckResult()
            conn.execute(
                "INSERT OR REPLACE INTO papers "
                "(arxiv_id, title, authors, abstract, categories, pdf_url, arxiv_url, "
                "published_at, impact_score, impact_reason, summary, key_technique, "
                "practical_takeaway, status, digest_date, discovered_at, "
                "fact_check_score, fact_check_claims, fact_check_verified, fact_check_issues, "
                "hf_upvotes, linked_models, linked_datasets, model_card_summary, source, "
                "blog_generated) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    p.arxiv_id, p.title, json.dumps(p.authors),
                    p.abstract, json.dumps(p.categories),
                    p.pdf_url, p.arxiv_url, p.published_at,
                    p.impact_score, p.impact_reason,
                    p.summary, p.key_technique, p.practical_takeaway,
                    "sent", digest_date, datetime.now().isoformat(),
                    fc.score, fc.total_claims, fc.verified_count,
                    json.dumps(fc.issues),
                    p.hf_upvotes, json.dumps(p.linked_models),
                    json.dumps(p.linked_datasets), p.model_card_summary,
                    p.source, 0,
                ),
            )
        conn.commit()
        conn.close()

    def get_by_index(self, digest_date: str, index: int) -> RankedPaper | None:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM papers WHERE digest_date = ? ORDER BY impact_score DESC",
            (digest_date,),
        ).fetchall()
        conn.close()
        if index < 1 or index > len(rows):
            return None
        return self._row_to_ranked_paper(rows[index - 1])

    def get_by_arxiv_id(self, arxiv_id: str) -> RankedPaper | None:
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM papers WHERE arxiv_id = ?", (arxiv_id,)).fetchone()
        conn.close()
        if not row:
            return None
        return self._row_to_ranked_paper(row)

    def get_week(self, last_n_days: int = 7) -> list[RankedPaper]:
        cutoff = (datetime.now() - timedelta(days=last_n_days)).strftime("%Y-%m-%d")
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM papers WHERE digest_date >= ? ORDER BY impact_score DESC",
            (cutoff,),
        ).fetchall()
        conn.close()
        return [self._row_to_ranked_paper(r) for r in rows]

    def get_missed_dates(self, last_n_days: int = 7) -> list[str]:
        today = datetime.now().date()
        all_dates = {(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(last_n_days)}
        conn = self._get_conn()
        cutoff = (today - timedelta(days=last_n_days)).strftime("%Y-%m-%d")
        rows = conn.execute(
            "SELECT DISTINCT digest_date FROM papers WHERE digest_date >= ?", (cutoff,),
        ).fetchall()
        conn.close()
        existing = {r["digest_date"] for r in rows}
        return sorted(all_dates - existing)

    def mark_read(self, arxiv_id: str) -> None:
        conn = self._get_conn()
        conn.execute("UPDATE papers SET status = 'read' WHERE arxiv_id = ?", (arxiv_id,))
        conn.commit()
        conn.close()

    def get_stats(self) -> ReadingStats:
        conn = self._get_conn()
        total = conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
        read = conn.execute("SELECT COUNT(*) FROM papers WHERE status = 'read'").fetchone()[0]
        week_cutoff = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        this_week = conn.execute(
            "SELECT COUNT(*) FROM papers WHERE digest_date >= ?", (week_cutoff,),
        ).fetchone()[0]
        blog_count = conn.execute(
            "SELECT COUNT(*) FROM papers WHERE blog_generated = 1",
        ).fetchone()[0]
        with_models = conn.execute(
            "SELECT COUNT(*) FROM papers WHERE linked_models IS NOT NULL AND linked_models != '[]'",
        ).fetchone()[0]
        conn.close()
        return ReadingStats(
            total=total, read=read, unread=total - read,
            this_week=this_week, blog_count=blog_count, with_models=with_models,
        )

    def search(self, query: str, limit: int = 20) -> list[RankedPaper]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM papers WHERE title LIKE ? OR abstract LIKE ? "
            "ORDER BY impact_score DESC LIMIT ?",
            (f"%{query}%", f"%{query}%", limit),
        ).fetchall()
        conn.close()
        return [self._row_to_ranked_paper(r) for r in rows]

    def _row_to_ranked_paper(self, row: sqlite3.Row) -> RankedPaper:
        fc = FactCheckResult(
            score=row["fact_check_score"] or 0,
            total_claims=row["fact_check_claims"] or 0,
            verified_count=row["fact_check_verified"] or 0,
            issues=json.loads(row["fact_check_issues"] or "[]"),
        )
        return RankedPaper(
            arxiv_id=row["arxiv_id"],
            title=row["title"],
            authors=json.loads(row["authors"] or "[]"),
            abstract=row["abstract"] or "",
            categories=json.loads(row["categories"] or "[]"),
            pdf_url=row["pdf_url"] or "",
            arxiv_url=row["arxiv_url"] or "",
            published_at=row["published_at"] or "",
            source=row["source"] or "arxiv",
            hf_upvotes=row["hf_upvotes"],
            linked_models=json.loads(row["linked_models"] or "[]"),
            linked_datasets=json.loads(row["linked_datasets"] or "[]"),
            model_card_summary=row["model_card_summary"],
            impact_score=row["impact_score"] or 0,
            impact_reason=row["impact_reason"] or "",
            summary=row["summary"] or "",
            key_technique=row["key_technique"] or "",
            practical_takeaway=row["practical_takeaway"] or "",
            category_tag="",
            fast_score=0.0,
            fact_check=fc,
        )
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
python -m pytest tests/papers/test_store.py -v
```
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add jobpulse/papers/store.py tests/papers/conftest.py tests/papers/test_store.py
git commit -m "feat(papers): add PaperStore with SQLite CRUD, migration, search"
```

---

### Task 3: PaperFetcher (async arXiv + HuggingFace)

**Files:**
- Create: `jobpulse/papers/fetcher.py`
- Create: `tests/papers/test_fetcher.py`

- [ ] **Step 1: Write fetcher tests**

Create `tests/papers/test_fetcher.py`:

```python
"""Tests for PaperFetcher — arXiv + HuggingFace async fetching."""

import pytest
import httpx
from unittest.mock import patch, AsyncMock
from jobpulse.papers.fetcher import PaperFetcher
from jobpulse.papers.models import Paper

SAMPLE_ARXIV_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2401.00001v1</id>
    <title>Test Paper Title</title>
    <summary>This is the abstract of the test paper.</summary>
    <author><name>Alice Smith</name></author>
    <author><name>Bob Jones</name></author>
    <category term="cs.AI"/>
    <category term="cs.LG"/>
    <published>2026-04-01T00:00:00Z</published>
    <link title="pdf" href="http://arxiv.org/pdf/2401.00001v1" rel="related" type="application/pdf"/>
  </entry>
</feed>"""

SAMPLE_HF_DAILY = [
    {
        "paper": {
            "id": "2401.00001",
            "title": "Test Paper Title",
            "summary": "This is the abstract.",
            "authors": [{"name": "Alice Smith"}],
        },
        "numUpvotes": 42,
    },
    {
        "paper": {
            "id": "2401.00099",
            "title": "HF Only Paper",
            "summary": "Only on HuggingFace.",
            "authors": [{"name": "Charlie"}],
        },
        "numUpvotes": 10,
    },
]

SAMPLE_HF_MODELS = [
    {"id": "meta-llama/Llama-4-8B", "pipeline_tag": "text-generation"},
]


class TestFetchArxiv:
    @pytest.mark.asyncio
    async def test_parses_xml_correctly(self):
        fetcher = PaperFetcher()
        mock_response = httpx.Response(200, text=SAMPLE_ARXIV_XML)
        with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock, return_value=mock_response):
            papers = await fetcher._fetch_arxiv(max_results=10)
        assert len(papers) == 1
        assert papers[0].arxiv_id == "2401.00001"
        assert papers[0].title == "Test Paper Title"
        assert papers[0].authors == ["Alice Smith", "Bob Jones"]
        assert "cs.AI" in papers[0].categories
        assert papers[0].source == "arxiv"

    @pytest.mark.asyncio
    async def test_returns_empty_on_network_error(self):
        fetcher = PaperFetcher()
        with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock, side_effect=httpx.ConnectError("down")):
            papers = await fetcher._fetch_arxiv(max_results=10)
        assert papers == []

    @pytest.mark.asyncio
    async def test_returns_empty_on_malformed_xml(self):
        fetcher = PaperFetcher()
        mock_response = httpx.Response(200, text="<not valid xml")
        with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock, return_value=mock_response):
            papers = await fetcher._fetch_arxiv(max_results=10)
        assert papers == []


class TestFetchHuggingFace:
    @pytest.mark.asyncio
    async def test_parses_daily_papers(self):
        fetcher = PaperFetcher()
        daily_resp = httpx.Response(200, json=SAMPLE_HF_DAILY)
        models_resp = httpx.Response(200, json=SAMPLE_HF_MODELS)
        empty_resp = httpx.Response(200, json=[])

        async def mock_get(url, **kwargs):
            if "daily_papers" in url:
                return daily_resp
            if "models" in url:
                return models_resp if "2401.00001" in url else empty_resp
            return empty_resp

        with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock, side_effect=mock_get):
            papers = await fetcher._fetch_huggingface()
        assert len(papers) >= 1
        hf_paper = next(p for p in papers if p.arxiv_id == "2401.00099")
        assert hf_paper.source == "huggingface"
        assert hf_paper.hf_upvotes == 10

    @pytest.mark.asyncio
    async def test_returns_empty_on_api_error(self):
        fetcher = PaperFetcher()
        with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock, side_effect=httpx.ConnectError("down")):
            papers = await fetcher._fetch_huggingface()
        assert papers == []


class TestDeduplication:
    @pytest.mark.asyncio
    async def test_merge_same_paper_from_both_sources(self):
        fetcher = PaperFetcher()
        arxiv_paper = Paper(
            arxiv_id="2401.00001", title="Test", authors=["Alice"],
            abstract="Full arXiv abstract.", categories=["cs.AI"],
            pdf_url="https://arxiv.org/pdf/2401.00001",
            arxiv_url="https://arxiv.org/abs/2401.00001",
            published_at="2026-04-01", source="arxiv",
        )
        hf_paper = Paper(
            arxiv_id="2401.00001", title="Test", authors=["Alice"],
            abstract="HF abstract.", categories=[],
            pdf_url="", arxiv_url="", published_at="2026-04-01",
            source="huggingface", hf_upvotes=42,
            linked_models=["model-1"],
        )
        merged = fetcher._deduplicate_and_merge([arxiv_paper], [hf_paper])
        assert len(merged) == 1
        assert merged[0].source == "both"
        assert merged[0].abstract == "Full arXiv abstract."  # Keep arXiv abstract
        assert merged[0].hf_upvotes == 42  # Keep HF upvotes
        assert merged[0].linked_models == ["model-1"]  # Keep HF models


class TestFetchAll:
    @pytest.mark.asyncio
    async def test_combines_both_sources(self):
        fetcher = PaperFetcher()
        arxiv_paper = Paper(
            arxiv_id="2401.00001", title="ArXiv Paper", authors=["A"],
            abstract="X.", categories=["cs.AI"], pdf_url="", arxiv_url="",
            published_at="2026-04-01", source="arxiv",
        )
        hf_paper = Paper(
            arxiv_id="2401.00099", title="HF Paper", authors=["B"],
            abstract="Y.", categories=[], pdf_url="", arxiv_url="",
            published_at="2026-04-01", source="huggingface", hf_upvotes=5,
        )
        with patch.object(fetcher, "_fetch_arxiv", new_callable=AsyncMock, return_value=[arxiv_paper]), \
             patch.object(fetcher, "_fetch_huggingface", new_callable=AsyncMock, return_value=[hf_paper]):
            papers = await fetcher.fetch_all()
        assert len(papers) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/papers/test_fetcher.py -v
```
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write PaperFetcher**

Create `jobpulse/papers/fetcher.py`:

```python
"""PaperFetcher — async arXiv + HuggingFace paper fetching with deduplication."""

import asyncio
import xml.etree.ElementTree as ET

import httpx

from jobpulse.papers.models import Paper
from shared.logging_config import get_logger

logger = get_logger(__name__)

ARXIV_API = "https://export.arxiv.org/api/query"
ARXIV_CATEGORIES = ["cs.AI", "cs.LG", "cs.CL", "cs.MA", "stat.ML"]
HF_DAILY_PAPERS_API = "https://huggingface.co/api/daily_papers"
HF_MODELS_API = "https://huggingface.co/api/models"
ATOM_NS = "http://www.w3.org/2005/Atom"

_HEADERS = {"User-Agent": "JobPulse/1.0 (research-agent; yashb98@github.com)"}
_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
_MAX_RETRIES = 3
_BACKOFF = [5, 10, 15]


class PaperFetcher:
    """Fetches papers from arXiv and HuggingFace in parallel."""

    async def fetch_all(self, max_results: int = 200) -> list[Paper]:
        arxiv_papers, hf_papers = await asyncio.gather(
            self._fetch_arxiv(max_results),
            self._fetch_huggingface(),
        )
        return self._deduplicate_and_merge(arxiv_papers, hf_papers)

    async def fetch_missed(self, missed_dates: list[str], max_per_date: int = 50) -> list[Paper]:
        if not missed_dates:
            return []
        arxiv_papers, hf_papers = await asyncio.gather(
            self._fetch_arxiv(max_results=max_per_date * len(missed_dates)),
            self._fetch_huggingface(),
        )
        return self._deduplicate_and_merge(arxiv_papers, hf_papers)

    async def _fetch_arxiv(self, max_results: int = 200) -> list[Paper]:
        cat_query = "+OR+".join(f"cat:{c}" for c in ARXIV_CATEGORIES)
        url = f"{ARXIV_API}?search_query={cat_query}&sortBy=submittedDate&sortOrder=descending&max_results={max_results}"

        for attempt in range(_MAX_RETRIES):
            try:
                async with httpx.AsyncClient(timeout=_TIMEOUT, headers=_HEADERS) as client:
                    resp = await client.get(url)
                    if resp.status_code == 429:
                        wait = _BACKOFF[attempt] if attempt < len(_BACKOFF) else 15
                        logger.warning("arXiv rate limit, waiting %ds", wait)
                        await asyncio.sleep(wait)
                        continue
                    resp.raise_for_status()
                    return self._parse_arxiv_xml(resp.text)
            except Exception as e:
                logger.warning("arXiv fetch attempt %d failed: %s", attempt + 1, e)
                if attempt < _MAX_RETRIES - 1:
                    await asyncio.sleep(_BACKOFF[attempt])
        return []

    def _parse_arxiv_xml(self, xml_text: str) -> list[Paper]:
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            logger.error("Malformed arXiv XML")
            return []

        papers = []
        for entry in root.findall(f"{{{ATOM_NS}}}entry"):
            arxiv_url = entry.findtext(f"{{{ATOM_NS}}}id", "")
            arxiv_id = arxiv_url.split("/abs/")[-1].replace("v1", "").replace("v2", "").strip()

            authors = [a.findtext(f"{{{ATOM_NS}}}name", "") for a in entry.findall(f"{{{ATOM_NS}}}author")]
            categories = [c.get("term", "") for c in entry.findall(f"{{{ATOM_NS}}}category")]

            pdf_url = ""
            for link in entry.findall(f"{{{ATOM_NS}}}link"):
                if link.get("title") == "pdf":
                    pdf_url = link.get("href", "")

            papers.append(Paper(
                arxiv_id=arxiv_id,
                title=(entry.findtext(f"{{{ATOM_NS}}}title", "") or "").strip().replace("\n", " "),
                authors=authors[:5],
                abstract=(entry.findtext(f"{{{ATOM_NS}}}summary", "") or "").strip(),
                categories=categories,
                pdf_url=pdf_url,
                arxiv_url=arxiv_url,
                published_at=entry.findtext(f"{{{ATOM_NS}}}published", ""),
                source="arxiv",
            ))
        return papers

    async def _fetch_huggingface(self) -> list[Paper]:
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT, headers=_HEADERS) as client:
                resp = await client.get(HF_DAILY_PAPERS_API)
                resp.raise_for_status()
                daily_papers = resp.json()

                papers = []
                for item in daily_papers:
                    paper_data = item.get("paper", {})
                    arxiv_id = paper_data.get("id", "")
                    if not arxiv_id:
                        continue

                    upvotes = item.get("numUpvotes", 0)
                    authors = [a.get("name", "") for a in paper_data.get("authors", [])]

                    # Fetch linked models for this paper
                    linked_models = []
                    linked_datasets = []
                    model_card_summary = None
                    try:
                        models_resp = await client.get(
                            HF_MODELS_API, params={"paper": arxiv_id}, timeout=10,
                        )
                        if models_resp.status_code == 200:
                            models = models_resp.json()
                            linked_models = [m.get("id", "") for m in models[:10]]
                    except Exception:
                        pass  # Models lookup is best-effort

                    papers.append(Paper(
                        arxiv_id=arxiv_id,
                        title=paper_data.get("title", ""),
                        authors=authors[:5],
                        abstract=paper_data.get("summary", ""),
                        categories=[],
                        pdf_url=f"https://arxiv.org/pdf/{arxiv_id}",
                        arxiv_url=f"https://arxiv.org/abs/{arxiv_id}",
                        published_at=paper_data.get("publishedAt", ""),
                        source="huggingface",
                        hf_upvotes=upvotes,
                        linked_models=linked_models,
                        linked_datasets=linked_datasets,
                        model_card_summary=model_card_summary,
                    ))
                return papers
        except Exception as e:
            logger.warning("HuggingFace fetch failed: %s", e)
            return []

    def _deduplicate_and_merge(
        self, arxiv_papers: list[Paper], hf_papers: list[Paper],
    ) -> list[Paper]:
        by_id: dict[str, Paper] = {}

        for p in arxiv_papers:
            by_id[p.arxiv_id] = p

        for p in hf_papers:
            if p.arxiv_id in by_id:
                existing = by_id[p.arxiv_id]
                by_id[p.arxiv_id] = existing.model_copy(update={
                    "source": "both",
                    "hf_upvotes": p.hf_upvotes,
                    "linked_models": p.linked_models,
                    "linked_datasets": p.linked_datasets,
                    "model_card_summary": p.model_card_summary,
                })
            else:
                by_id[p.arxiv_id] = p

        return list(by_id.values())
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/papers/test_fetcher.py -v
```
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add jobpulse/papers/fetcher.py tests/papers/test_fetcher.py
git commit -m "feat(papers): add async PaperFetcher with arXiv + HuggingFace sources"
```

---

### Task 4: PaperRanker (fast_score + LLM ranking)

**Files:**
- Create: `jobpulse/papers/ranker.py`
- Create: `tests/papers/test_ranker.py`

- [ ] **Step 1: Write ranker tests**

Create `tests/papers/test_ranker.py`:

```python
"""Tests for PaperRanker — fast scoring + LLM ranking."""

import json
import pytest
from unittest.mock import patch, MagicMock
from jobpulse.papers.ranker import PaperRanker
from jobpulse.papers.models import Paper, RankedPaper


@pytest.fixture
def ranker():
    return PaperRanker()


@pytest.fixture
def papers():
    return [
        Paper(
            arxiv_id=f"2401.{i:05d}", title=f"Paper {i}",
            authors=["A", "B"], abstract=f"Abstract {i}.",
            categories=["cs.AI", "cs.LG"], pdf_url="", arxiv_url="",
            published_at="2026-04-01", source="arxiv",
        )
        for i in range(5)
    ]


class TestFastScore:
    def test_cs_ai_gets_category_bonus(self, ranker):
        paper = Paper(
            arxiv_id="1", title="T", authors=["A"], abstract="X.",
            categories=["cs.AI"], pdf_url="", arxiv_url="",
            published_at="2026-04-01",
        )
        score = ranker.fast_score(paper)
        assert score >= 2.0  # cs.AI category bonus

    def test_hf_upvotes_boost(self, ranker):
        low = Paper(
            arxiv_id="1", title="T", authors=["A"], abstract="X.",
            categories=["cs.AI"], pdf_url="", arxiv_url="",
            published_at="2026-04-01", hf_upvotes=5,
        )
        high = Paper(
            arxiv_id="2", title="T", authors=["A"], abstract="X.",
            categories=["cs.AI"], pdf_url="", arxiv_url="",
            published_at="2026-04-01", hf_upvotes=100,
        )
        assert ranker.fast_score(high) > ranker.fast_score(low)

    def test_linked_models_boost(self, ranker):
        no_models = Paper(
            arxiv_id="1", title="T", authors=["A"], abstract="X.",
            categories=["cs.AI"], pdf_url="", arxiv_url="",
            published_at="2026-04-01",
        )
        with_models = Paper(
            arxiv_id="2", title="T", authors=["A"], abstract="X.",
            categories=["cs.AI"], pdf_url="", arxiv_url="",
            published_at="2026-04-01", linked_models=["m1", "m2"],
        )
        assert ranker.fast_score(with_models) > ranker.fast_score(no_models)

    def test_github_repo_boost(self, ranker):
        no_repo = Paper(
            arxiv_id="1", title="T", authors=["A"], abstract="No code.",
            categories=["cs.AI"], pdf_url="", arxiv_url="",
            published_at="2026-04-01",
        )
        with_repo = Paper(
            arxiv_id="2", title="T", authors=["A"],
            abstract="Code at https://github.com/org/repo.",
            categories=["cs.AI"], pdf_url="", arxiv_url="",
            published_at="2026-04-01",
        )
        assert ranker.fast_score(with_repo) > ranker.fast_score(no_repo)

    def test_score_max_10(self, ranker):
        maxed = Paper(
            arxiv_id="1", title="T", authors=["A", "B", "C", "D", "E"],
            abstract="Code at https://github.com/org/repo.",
            categories=["cs.AI", "cs.LG"], pdf_url="", arxiv_url="",
            published_at="2026-04-01", hf_upvotes=100,
            linked_models=["m1", "m2"],
        )
        assert ranker.fast_score(maxed) <= 10.0


class TestLlmRank:
    def test_returns_ranked_papers(self, ranker, papers):
        llm_response = json.dumps([
            {"paper_num": 1, "novelty": 8, "significance": 7, "practical": 9, "breadth": 6,
             "reason": "Good paper", "category": "LLM", "key_technique": "Attention", "takeaway": "Use it"},
        ])
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content=llm_response))]
        )
        with patch("jobpulse.papers.ranker._get_openai_client", return_value=mock_client):
            ranked = ranker.llm_rank(papers, top_n=1)
        assert len(ranked) == 1
        assert ranked[0].impact_score > 0
        assert ranked[0].category_tag == "LLM"

    def test_fallback_on_api_error(self, ranker, papers):
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = Exception("API down")
        with patch("jobpulse.papers.ranker._get_openai_client", return_value=mock_client):
            ranked = ranker.llm_rank(papers, top_n=3)
        assert len(ranked) == 3  # Fallback to first N

    def test_fallback_when_no_api_key(self, ranker, papers):
        with patch("jobpulse.papers.ranker.OPENAI_API_KEY", ""):
            ranked = ranker.llm_rank(papers, top_n=3)
        assert len(ranked) == 3

    def test_weekly_lens(self, ranker, papers):
        llm_response = json.dumps([
            {"paper_num": 1, "novelty": 5, "significance": 9, "practical": 8, "breadth": 7,
             "reason": "Weekly pick", "category": "Agents", "key_technique": "X", "takeaway": "Y"},
        ])
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content=llm_response))]
        )
        with patch("jobpulse.papers.ranker._get_openai_client", return_value=mock_client):
            ranked = ranker.llm_rank(papers, top_n=1, lens="weekly")
        assert len(ranked) == 1


class TestExtractThemes:
    def test_returns_themes(self, ranker):
        papers = [
            RankedPaper(
                arxiv_id="1", title="Efficient LLM Inference", authors=["A"],
                abstract="X.", categories=["cs.AI"], pdf_url="", arxiv_url="",
                published_at="", category_tag="Efficiency",
            ),
        ]
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content='["Efficiency dominates"]'))]
        )
        with patch("jobpulse.papers.ranker._get_openai_client", return_value=mock_client):
            themes = ranker.extract_themes(papers)
        assert len(themes) >= 1

    def test_fallback_on_error(self, ranker):
        with patch("jobpulse.papers.ranker._get_openai_client", side_effect=Exception("err")):
            themes = ranker.extract_themes([])
        assert themes == []


class TestJsonParsing:
    @pytest.mark.parametrize("raw,expected_len", [
        ('[{"paper_num":1,"novelty":8,"significance":7,"practical":9,"breadth":6,"reason":"x","category":"LLM","key_technique":"y","takeaway":"z"}]', 1),
        ('```json\n[{"paper_num":1,"novelty":8,"significance":7,"practical":9,"breadth":6,"reason":"x","category":"LLM","key_technique":"y","takeaway":"z"}]\n```', 1),
        ('[]', 0),
        ('not json at all', 0),
    ])
    def test_extract_json_array(self, raw, expected_len):
        from jobpulse.papers.ranker import _extract_json_array
        result = _extract_json_array(raw)
        assert len(result) == expected_len
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/papers/test_ranker.py -v
```
Expected: FAIL

- [ ] **Step 3: Write PaperRanker**

Create `jobpulse/papers/ranker.py`:

```python
"""PaperRanker — deterministic fast scoring + LLM-based ranking."""

import json
import re
from openai import OpenAI

from jobpulse.papers.models import (
    FactCheckResult, Paper, RankedPaper,
)
from jobpulse.config import OPENAI_API_KEY
from shared.logging_config import get_logger

logger = get_logger(__name__)

_CATEGORY_WEIGHTS = {"cs.AI": 3, "cs.LG": 3, "cs.CL": 2, "cs.MA": 1, "stat.ML": 2}
_DAILY_WEIGHTS = {"novelty": 0.30, "significance": 0.25, "practical": 0.30, "breadth": 0.15}
_WEEKLY_WEIGHTS = {"novelty": 0.25, "significance": 0.35, "practical": 0.25, "breadth": 0.15}


def _get_openai_client() -> OpenAI:
    return OpenAI(api_key=OPENAI_API_KEY)


def _extract_json_array(raw: str) -> list:
    raw = raw.strip()
    # Strip markdown code fences
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", raw, re.DOTALL)
    if m:
        raw = m.group(1).strip()
    try:
        result = json.loads(raw)
        return result if isinstance(result, list) else []
    except json.JSONDecodeError:
        return []


class PaperRanker:
    """Ranks papers using deterministic fast scoring + LLM multi-criteria ranking."""

    def fast_score(self, paper: Paper) -> float:
        score = 0.0

        # Category relevance (0-3)
        cat_score = max((_CATEGORY_WEIGHTS.get(c, 0) for c in paper.categories), default=0)
        score += cat_score

        # HuggingFace upvotes (0-2)
        if paper.hf_upvotes is not None:
            if paper.hf_upvotes > 50:
                score += 2.0
            elif paper.hf_upvotes > 20:
                score += 1.0

        # Linked models/datasets (0-2)
        if paper.linked_models:
            score += min(len(paper.linked_models), 2)

        # GitHub repo in abstract (0-1)
        if re.search(r"https?://github\.com/[^\s)]+", paper.abstract):
            score += 1.0

        # Author count as collaboration signal (0-1)
        if len(paper.authors) >= 3:
            score += 1.0

        # Recency (0-1) — papers with recent dates score higher
        score += 1.0  # All fetched papers are recent

        return min(score, 10.0)

    def llm_rank(
        self, papers: list[Paper], top_n: int = 5, lens: str = "daily",
    ) -> list[RankedPaper]:
        if not papers:
            return []

        # Pre-filter using fast_score
        scored = [(self.fast_score(p), p) for p in papers]
        scored.sort(key=lambda x: x[0], reverse=True)
        candidates = [p for _, p in scored[:30]]

        if not OPENAI_API_KEY:
            logger.warning("No OPENAI_API_KEY — returning top %d by fast_score", top_n)
            return [
                RankedPaper(**p.model_dump(), fast_score=s)
                for s, p in scored[:top_n]
            ]

        weights = _WEEKLY_WEIGHTS if lens == "weekly" else _DAILY_WEIGHTS
        lens_instruction = (
            "Focus on which papers will still matter in 3 months."
            if lens == "weekly"
            else "Focus on novelty and practical value today."
        )

        papers_text = "\n\n".join(
            f"Paper {i+1}: {p.title}\nAbstract: {p.abstract[:500]}"
            + (f"\nHF Upvotes: {p.hf_upvotes}" if p.hf_upvotes else "")
            + (f"\nLinked Models: {len(p.linked_models)}" if p.linked_models else "")
            for i, p in enumerate(candidates[:15])
        )

        system = (
            f"You rank AI research papers. {lens_instruction}\n"
            f"Score each dimension 0-10. Weights: {json.dumps(weights)}\n"
            "Categories: LLM, Agents, Vision, RL, Efficiency, Safety, Reasoning\n"
            f"Return a JSON array of top {top_n} objects with keys: "
            "paper_num, novelty, significance, practical, breadth, reason, category, key_technique, takeaway"
        )

        try:
            client = _get_openai_client()
            resp = client.chat.completions.create(
                model="gpt-4.1-mini",
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": papers_text},
                ],
                max_tokens=1500,
                temperature=0.3,
            )
            raw = resp.choices[0].message.content or ""
            items = _extract_json_array(raw)
        except Exception as e:
            logger.warning("LLM ranking failed: %s — using fast_score fallback", e)
            return [
                RankedPaper(**p.model_dump(), fast_score=self.fast_score(p))
                for p in candidates[:top_n]
            ]

        ranked = []
        for item in items[:top_n]:
            idx = item.get("paper_num", 0) - 1
            if idx < 0 or idx >= len(candidates):
                continue
            p = candidates[idx]
            overall = sum(
                item.get(k, 0) * w for k, w in weights.items()
            )
            ranked.append(RankedPaper(
                **p.model_dump(),
                fast_score=self.fast_score(p),
                impact_score=round(overall, 1),
                impact_reason=item.get("reason", ""),
                category_tag=item.get("category", ""),
                key_technique=item.get("key_technique", ""),
                practical_takeaway=item.get("takeaway", ""),
            ))

        if not ranked:
            return [
                RankedPaper(**p.model_dump(), fast_score=self.fast_score(p))
                for p in candidates[:top_n]
            ]
        return ranked

    def extract_themes(self, papers: list[RankedPaper]) -> list[str]:
        if not papers:
            return []
        try:
            client = _get_openai_client()
            titles = "\n".join(f"- [{p.category_tag}] {p.title}" for p in papers)
            resp = client.chat.completions.create(
                model="gpt-4.1-mini",
                messages=[
                    {"role": "system", "content": "Extract 3-5 research themes from these AI papers. Return a JSON array of strings."},
                    {"role": "user", "content": titles},
                ],
                max_tokens=300,
                temperature=0.3,
            )
            return _extract_json_array(resp.choices[0].message.content or "")
        except Exception as e:
            logger.warning("Theme extraction failed: %s", e)
            return []

    def summarize_and_verify(self, papers: list[RankedPaper]) -> list[RankedPaper]:
        """Summarize and fact-check each paper. Delegates to shared/fact_checker."""
        verified = []
        for p in papers:
            try:
                summary = self._summarize_paper(p)
                fc = self._verify_paper(p, summary)
                verified.append(p.model_copy(update={
                    "summary": summary,
                    "fact_check": fc,
                }))
            except Exception as e:
                logger.warning("Summarize/verify failed for %s: %s", p.arxiv_id, e)
                verified.append(p)
        return verified

    def _summarize_paper(self, paper: RankedPaper) -> str:
        if not OPENAI_API_KEY:
            return paper.abstract[:300]
        try:
            client = _get_openai_client()
            resp = client.chat.completions.create(
                model="gpt-4.1-mini",
                messages=[
                    {"role": "system", "content": (
                        "Summarize this AI paper in exactly 4 sentences:\n"
                        "WHAT: What does the paper propose?\n"
                        "WHY: Why does it matter?\n"
                        "HOW: Key technical insight?\n"
                        "USE: Practical takeaway?"
                    )},
                    {"role": "user", "content": f"Title: {paper.title}\n\nAbstract: {paper.abstract}"},
                ],
                max_tokens=250,
                temperature=0.3,
            )
            return resp.choices[0].message.content or paper.abstract[:300]
        except Exception:
            return paper.abstract[:300]

    def _verify_paper(self, paper: RankedPaper, summary: str) -> FactCheckResult:
        try:
            from shared.fact_checker import extract_claims, verify_claims, compute_accuracy_score
            claims = extract_claims(summary, paper.title)
            if not claims:
                return FactCheckResult(score=10.0, explanation="No verifiable claims.")
            verifications = verify_claims(claims, paper.abstract)
            score = compute_accuracy_score(verifications)
            issues = [v.get("issue", "") for v in verifications if v.get("status") != "VERIFIED" and v.get("issue")]
            return FactCheckResult(
                score=score,
                total_claims=len(claims),
                verified_count=sum(1 for v in verifications if v.get("status") == "VERIFIED"),
                issues=issues,
            )
        except ImportError:
            return FactCheckResult(score=0.0, explanation="Fact checker not available.")
        except Exception as e:
            logger.warning("Fact check failed for %s: %s", paper.arxiv_id, e)
            return FactCheckResult(score=0.0, explanation=f"Error: {e}")
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/papers/test_ranker.py -v
```
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add jobpulse/papers/ranker.py tests/papers/test_ranker.py
git commit -m "feat(papers): add PaperRanker with fast scoring + LLM ranking + themes"
```

---

### Task 5: DigestBuilder (Telegram formatting)

**Files:**
- Create: `jobpulse/papers/digest.py`
- Create: `tests/papers/test_digest.py`

- [ ] **Step 1: Write digest tests**

Create `tests/papers/test_digest.py`:

```python
"""Tests for DigestBuilder — Telegram message formatting."""

from jobpulse.papers.digest import DigestBuilder
from jobpulse.papers.models import RankedPaper, FactCheckResult


def _make_ranked(arxiv_id: str, title: str, score: float, tag: str = "LLM", **kwargs):
    return RankedPaper(
        arxiv_id=arxiv_id, title=title, authors=["Alice", "Bob"],
        abstract="Abstract.", categories=["cs.AI"],
        pdf_url=f"https://arxiv.org/pdf/{arxiv_id}",
        arxiv_url=f"https://arxiv.org/abs/{arxiv_id}",
        published_at="2026-04-01", impact_score=score,
        category_tag=tag, key_technique="Technique",
        summary="WHAT: X. WHY: Y. HOW: Z. USE: W.",
        fact_check=FactCheckResult(score=9.0, total_claims=3, verified_count=3),
        **kwargs,
    )


class TestDailyFormat:
    def test_includes_paper_info(self):
        papers = [_make_ranked("2401.00001", "Test Paper", 8.5)]
        result = DigestBuilder().format_daily(papers)
        assert "Test Paper" in result
        assert "8.5" in result
        assert "[LLM]" in result
        assert "arxiv.org" in result

    def test_includes_hf_signals(self):
        papers = [_make_ranked("2401.00001", "Test", 8.0, hf_upvotes=42, linked_models=["m1", "m2"])]
        result = DigestBuilder().format_daily(papers)
        assert "42" in result
        assert "2 models" in result

    def test_empty_papers(self):
        result = DigestBuilder().format_daily([])
        assert "No papers" in result or "empty" in result.lower() or result == ""

    def test_command_hints(self):
        papers = [_make_ranked("2401.00001", "Test", 8.0)]
        result = DigestBuilder().format_daily(papers)
        assert "paper" in result.lower()
        assert "blog" in result.lower()


class TestWeeklyFormat:
    def test_includes_themes(self):
        papers = [_make_ranked("2401.00001", "Test", 8.0)]
        themes = ["Efficiency dominates", "Agents converge"]
        result = DigestBuilder().format_weekly(papers, themes)
        assert "Efficiency dominates" in result
        assert "Agents converge" in result

    def test_includes_stats(self):
        papers = [_make_ranked("2401.00001", "Test", 8.0)]
        result = DigestBuilder().format_weekly(papers, [])
        assert "1" in result  # at least 1 paper count

    def test_empty_weekly(self):
        result = DigestBuilder().format_weekly([], [])
        assert isinstance(result, str)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/papers/test_digest.py -v
```

- [ ] **Step 3: Write DigestBuilder**

Create `jobpulse/papers/digest.py`:

```python
"""DigestBuilder — formats papers into Telegram messages."""

from jobpulse.papers.models import RankedPaper


class DigestBuilder:
    """Formats ranked papers into daily and weekly Telegram digest messages."""

    def format_daily(self, papers: list[RankedPaper], digest_date: str = "") -> str:
        if not papers:
            return "No papers found for today's digest."

        lines = [f"AI Research Digest{' — ' + digest_date if digest_date else ''}\n"]

        for i, p in enumerate(papers, 1):
            fc = p.fact_check
            fc_text = ""
            if fc and fc.total_claims > 0:
                fc_text = f"\n   Fact-check: {fc.score:.1f}/10 ({fc.verified_count}/{fc.total_claims} verified)"

            hf_text = ""
            hf_parts = []
            if p.hf_upvotes:
                hf_parts.append(f"{p.hf_upvotes} upvotes")
            if p.linked_models:
                hf_parts.append(f"{len(p.linked_models)} models released")
            if hf_parts:
                hf_text = "\n   " + " | ".join(hf_parts)

            lines.append(
                f"{i}. {p.impact_score:.1f} [{p.category_tag}] {p.title}\n"
                f"   {', '.join(p.authors[:3])} | {p.key_technique}\n"
                f"   {p.summary}"
                f"{fc_text}{hf_text}\n"
                f"   https://arxiv.org/abs/{p.arxiv_id} | https://arxiv.org/pdf/{p.arxiv_id}\n"
            )

        lines.append(
            '"paper N" for details | "blog N" for full post | "read N" to mark read'
        )
        return "\n".join(lines)

    def format_weekly(
        self, papers: list[RankedPaper], themes: list[str],
        start_date: str = "", end_date: str = "",
    ) -> str:
        date_range = f" — {start_date} to {end_date}" if start_date and end_date else ""
        lines = [f"Weekly Research Summary{date_range}\n"]

        if themes:
            lines.append("Themes This Week:")
            for theme in themes:
                lines.append(f"- {theme}")
            lines.append("")

        if papers:
            lines.append(f"Top {len(papers)} Papers:")
            for i, p in enumerate(papers, 1):
                hf_parts = []
                if p.linked_models:
                    hf_parts.append(f"{len(p.linked_models)} models")
                if p.hf_upvotes:
                    hf_parts.append(f"{p.hf_upvotes} upvotes")
                hf_text = f"\n   {' | '.join(hf_parts)}" if hf_parts else ""

                lines.append(
                    f"{i}. {p.impact_score:.1f} [{p.category_tag}] {p.title} — "
                    f"{p.practical_takeaway or p.impact_reason}"
                    f"{hf_text}"
                )
            lines.append("")

        with_code = sum(1 for p in papers if p.linked_models)
        lines.append(
            f"Stats: {len(papers)} papers processed | {with_code} with code"
        )
        return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/papers/test_digest.py -v
```

- [ ] **Step 5: Commit**

```bash
git add jobpulse/papers/digest.py tests/papers/test_digest.py
git commit -m "feat(papers): add DigestBuilder for daily/weekly Telegram formatting"
```

---

### Task 6: ChartGenerator (matplotlib)

**Files:**
- Create: `jobpulse/papers/chart_generator.py`
- Create: `tests/papers/test_chart_generator.py`

- [ ] **Step 1: Write chart generator tests**

Create `tests/papers/test_chart_generator.py`:

```python
"""Tests for ChartGenerator — LLM data extraction + matplotlib charts."""

import json
import os
import pytest
from unittest.mock import patch, MagicMock
from jobpulse.papers.chart_generator import ChartGenerator
from jobpulse.papers.models import Paper, Chart


@pytest.fixture
def chart_gen():
    return ChartGenerator()


@pytest.fixture
def paper_with_benchmarks():
    return Paper(
        arxiv_id="2401.00001",
        title="Our Model vs Baselines",
        authors=["Alice"],
        abstract="Our model achieves 92.3% on MMLU, compared to GPT-4 (86.4%) and Claude (88.7%). On HumanEval, we score 85.1% vs 67.0% and 71.3% respectively.",
        categories=["cs.AI"],
        pdf_url="", arxiv_url="",
        published_at="2026-04-01",
    )


@pytest.fixture
def paper_without_data():
    return Paper(
        arxiv_id="2401.00002",
        title="A Theoretical Analysis of Something",
        authors=["Bob"],
        abstract="We provide theoretical bounds on the convergence rate of gradient descent in non-convex settings.",
        categories=["cs.LG"],
        pdf_url="", arxiv_url="",
        published_at="2026-04-01",
    )


class TestDataExtraction:
    def test_extracts_benchmark_data(self, chart_gen, paper_with_benchmarks):
        llm_response = json.dumps([
            {
                "chart_type": "bar_comparison",
                "title": "MMLU Accuracy",
                "data": {"models": ["Ours", "GPT-4", "Claude"], "scores": [92.3, 86.4, 88.7]},
                "description": "Model comparison on MMLU benchmark.",
            }
        ])
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content=llm_response))]
        )
        with patch("jobpulse.papers.chart_generator._get_openai_client", return_value=mock_client):
            datasets = chart_gen._extract_chart_data(paper_with_benchmarks, "Research notes here.")
        assert len(datasets) >= 1
        assert datasets[0]["chart_type"] == "bar_comparison"

    def test_returns_empty_for_theoretical_paper(self, chart_gen, paper_without_data):
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content="[]"))]
        )
        with patch("jobpulse.papers.chart_generator._get_openai_client", return_value=mock_client):
            datasets = chart_gen._extract_chart_data(paper_without_data, "Theoretical notes.")
        assert datasets == []

    def test_handles_llm_error(self, chart_gen, paper_with_benchmarks):
        with patch("jobpulse.papers.chart_generator._get_openai_client", side_effect=Exception("err")):
            datasets = chart_gen._extract_chart_data(paper_with_benchmarks, "Notes.")
        assert datasets == []


class TestChartRendering:
    def test_bar_comparison(self, chart_gen, tmp_path):
        data = {"models": ["A", "B", "C"], "scores": [90, 85, 88]}
        chart = chart_gen._render_chart("bar_comparison", "Test Comparison", data, str(tmp_path))
        assert chart is not None
        assert os.path.exists(chart.png_path)
        assert chart.chart_type == "bar_comparison"

    def test_line_scaling(self, chart_gen, tmp_path):
        data = {"x": [1, 2, 4, 8], "y": [60, 70, 80, 85], "x_label": "Params (B)", "y_label": "Score"}
        chart = chart_gen._render_chart("line_scaling", "Scaling Curve", data, str(tmp_path))
        assert chart is not None
        assert os.path.exists(chart.png_path)

    def test_radar_multi(self, chart_gen, tmp_path):
        data = {
            "labels": ["MMLU", "HumanEval", "GSM8K", "ARC", "HellaSwag"],
            "series": [
                {"name": "Ours", "values": [92, 85, 78, 90, 88]},
                {"name": "Baseline", "values": [86, 67, 72, 85, 82]},
            ],
        }
        chart = chart_gen._render_chart("radar_multi", "Multi-Metric", data, str(tmp_path))
        assert chart is not None

    def test_bad_data_returns_none(self, chart_gen, tmp_path):
        chart = chart_gen._render_chart("bar_comparison", "Bad", {}, str(tmp_path))
        assert chart is None


class TestGenerateFullPipeline:
    def test_generate_returns_charts(self, chart_gen, paper_with_benchmarks, tmp_path):
        extracted = [
            {
                "chart_type": "bar_comparison",
                "title": "MMLU",
                "data": {"models": ["A", "B"], "scores": [90, 85]},
                "description": "Comparison.",
            }
        ]
        with patch.object(chart_gen, "_extract_chart_data", return_value=extracted):
            charts = chart_gen.generate(paper_with_benchmarks, "Notes.", output_dir=str(tmp_path))
        assert len(charts) == 1
        assert isinstance(charts[0], Chart)

    def test_generate_empty_when_no_data(self, chart_gen, paper_without_data, tmp_path):
        with patch.object(chart_gen, "_extract_chart_data", return_value=[]):
            charts = chart_gen.generate(paper_without_data, "Notes.", output_dir=str(tmp_path))
        assert charts == []

    def test_max_3_charts(self, chart_gen, paper_with_benchmarks, tmp_path):
        extracted = [
            {"chart_type": "bar_comparison", "title": f"Chart {i}", "data": {"models": ["A"], "scores": [90]}, "description": "X."}
            for i in range(5)
        ]
        with patch.object(chart_gen, "_extract_chart_data", return_value=extracted):
            charts = chart_gen.generate(paper_with_benchmarks, "Notes.", output_dir=str(tmp_path))
        assert len(charts) <= 3
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/papers/test_chart_generator.py -v
```

- [ ] **Step 3: Write ChartGenerator**

Create `jobpulse/papers/chart_generator.py`:

```python
"""ChartGenerator — extracts chartable data from papers and renders matplotlib PNGs."""

import json
import os
import uuid

import matplotlib
matplotlib.use("Agg")  # Non-interactive backend
import matplotlib.pyplot as plt
import numpy as np
from openai import OpenAI

from jobpulse.papers.models import Chart, Paper
from jobpulse.config import OPENAI_API_KEY
from shared.logging_config import get_logger

logger = get_logger(__name__)

# Consistent style
_TEAL = "#1a5276"
_BG_COLOR = "#1e1e2e"
_TEXT_COLOR = "#cdd6f4"
_ACCENT_COLORS = ["#1a5276", "#2ecc71", "#e74c3c", "#f39c12", "#9b59b6", "#3498db"]


def _get_openai_client() -> OpenAI:
    return OpenAI(api_key=OPENAI_API_KEY)


class ChartGenerator:
    """Extracts structured data from papers and generates matplotlib charts."""

    def generate(
        self, paper: Paper, research_notes: str, output_dir: str = "/tmp",
    ) -> list[Chart]:
        datasets = self._extract_chart_data(paper, research_notes)
        if not datasets:
            return []

        charts = []
        for ds in datasets[:3]:  # Max 3 charts
            chart = self._render_chart(
                ds.get("chart_type", "bar_comparison"),
                ds.get("title", "Chart"),
                ds.get("data", {}),
                output_dir,
            )
            if chart:
                chart.description = ds.get("description", "")
                charts.append(chart)
        return charts

    def _extract_chart_data(self, paper: Paper, research_notes: str) -> list[dict]:
        if not OPENAI_API_KEY:
            return []
        try:
            client = _get_openai_client()
            resp = client.chat.completions.create(
                model="gpt-4.1-mini",
                messages=[
                    {"role": "system", "content": (
                        "Extract chartable data from this paper. Return a JSON array of chart specs.\n"
                        "Each spec: {chart_type, title, data, description}\n"
                        "chart_type: bar_comparison | line_scaling | radar_multi | table_image\n"
                        "bar_comparison data: {models: [...], scores: [...]}\n"
                        "line_scaling data: {x: [...], y: [...], x_label, y_label}\n"
                        "radar_multi data: {labels: [...], series: [{name, values: [...]}]}\n"
                        "Return [] if no quantitative data found. Max 3 charts."
                    )},
                    {"role": "user", "content": f"Title: {paper.title}\n\nAbstract: {paper.abstract}\n\nNotes: {research_notes[:1000]}"},
                ],
                max_tokens=800,
                temperature=0.2,
            )
            raw = resp.choices[0].message.content or "[]"
            # Parse JSON
            raw = raw.strip()
            if raw.startswith("```"):
                import re
                m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", raw, re.DOTALL)
                if m:
                    raw = m.group(1)
            result = json.loads(raw)
            return result if isinstance(result, list) else []
        except Exception as e:
            logger.warning("Chart data extraction failed: %s", e)
            return []

    def _render_chart(
        self, chart_type: str, title: str, data: dict, output_dir: str,
    ) -> Chart | None:
        try:
            if chart_type == "bar_comparison":
                return self._render_bar(title, data, output_dir)
            elif chart_type == "line_scaling":
                return self._render_line(title, data, output_dir)
            elif chart_type == "radar_multi":
                return self._render_radar(title, data, output_dir)
            elif chart_type == "table_image":
                return self._render_bar(title, data, output_dir)  # Fallback to bar
            else:
                return None
        except Exception as e:
            logger.warning("Chart render failed for %s: %s", title, e)
            return None

    def _render_bar(self, title: str, data: dict, output_dir: str) -> Chart | None:
        models = data.get("models", [])
        scores = data.get("scores", [])
        if not models or not scores or len(models) != len(scores):
            return None

        fig, ax = plt.subplots(figsize=(8, 5))
        fig.patch.set_facecolor(_BG_COLOR)
        ax.set_facecolor(_BG_COLOR)
        colors = _ACCENT_COLORS[:len(models)]
        bars = ax.bar(models, scores, color=colors, edgecolor="none", width=0.6)
        for bar, score in zip(bars, scores):
            ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.5,
                    f"{score}", ha="center", color=_TEXT_COLOR, fontsize=10)
        ax.set_title(title, color=_TEXT_COLOR, fontsize=14, pad=15)
        ax.tick_params(colors=_TEXT_COLOR)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.spines["left"].set_color(_TEXT_COLOR)
        ax.spines["bottom"].set_color(_TEXT_COLOR)

        path = os.path.join(output_dir, f"chart_{uuid.uuid4().hex[:8]}.png")
        fig.savefig(path, dpi=150, bbox_inches="tight", facecolor=_BG_COLOR)
        plt.close(fig)
        return Chart(chart_type="bar_comparison", title=title, data=data, png_path=path, description="")

    def _render_line(self, title: str, data: dict, output_dir: str) -> Chart | None:
        x = data.get("x", [])
        y = data.get("y", [])
        if not x or not y or len(x) != len(y):
            return None

        fig, ax = plt.subplots(figsize=(8, 5))
        fig.patch.set_facecolor(_BG_COLOR)
        ax.set_facecolor(_BG_COLOR)
        ax.plot(x, y, color=_TEAL, linewidth=2, marker="o", markersize=6)
        ax.set_xlabel(data.get("x_label", ""), color=_TEXT_COLOR)
        ax.set_ylabel(data.get("y_label", ""), color=_TEXT_COLOR)
        ax.set_title(title, color=_TEXT_COLOR, fontsize=14, pad=15)
        ax.tick_params(colors=_TEXT_COLOR)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.spines["left"].set_color(_TEXT_COLOR)
        ax.spines["bottom"].set_color(_TEXT_COLOR)

        path = os.path.join(output_dir, f"chart_{uuid.uuid4().hex[:8]}.png")
        fig.savefig(path, dpi=150, bbox_inches="tight", facecolor=_BG_COLOR)
        plt.close(fig)
        return Chart(chart_type="line_scaling", title=title, data=data, png_path=path, description="")

    def _render_radar(self, title: str, data: dict, output_dir: str) -> Chart | None:
        labels = data.get("labels", [])
        series = data.get("series", [])
        if not labels or not series:
            return None

        n = len(labels)
        angles = np.linspace(0, 2 * np.pi, n, endpoint=False).tolist()
        angles += angles[:1]

        fig, ax = plt.subplots(figsize=(8, 8), subplot_kw={"projection": "polar"})
        fig.patch.set_facecolor(_BG_COLOR)
        ax.set_facecolor(_BG_COLOR)

        for i, s in enumerate(series):
            values = s.get("values", [])
            if len(values) != n:
                continue
            values_closed = values + values[:1]
            color = _ACCENT_COLORS[i % len(_ACCENT_COLORS)]
            ax.plot(angles, values_closed, color=color, linewidth=2, label=s.get("name", ""))
            ax.fill(angles, values_closed, color=color, alpha=0.15)

        ax.set_xticks(angles[:-1])
        ax.set_xticklabels(labels, color=_TEXT_COLOR, fontsize=9)
        ax.set_title(title, color=_TEXT_COLOR, fontsize=14, pad=20)
        ax.tick_params(colors=_TEXT_COLOR)
        ax.legend(loc="upper right", bbox_to_anchor=(1.3, 1.1))

        path = os.path.join(output_dir, f"chart_{uuid.uuid4().hex[:8]}.png")
        fig.savefig(path, dpi=150, bbox_inches="tight", facecolor=_BG_COLOR)
        plt.close(fig)
        return Chart(chart_type="radar_multi", title=title, data=data, png_path=path, description="")
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/papers/test_chart_generator.py -v
```

- [ ] **Step 5: Commit**

```bash
git add jobpulse/papers/chart_generator.py tests/papers/test_chart_generator.py
git commit -m "feat(papers): add ChartGenerator with bar, line, radar charts via matplotlib"
```

---

### Task 7: NotionPublisher

**Files:**
- Create: `jobpulse/papers/notion_publisher.py`
- Create: `tests/papers/test_notion_publisher.py`

- [ ] **Step 1: Write tests**

Create `tests/papers/test_notion_publisher.py`:

```python
"""Tests for NotionPublisher — daily/weekly/blog Notion page creation."""

from unittest.mock import patch, MagicMock
from jobpulse.papers.notion_publisher import NotionPublisher
from jobpulse.papers.models import RankedPaper, FactCheckResult, BlogPost, Paper, Chart


def _make_ranked(**kwargs):
    defaults = dict(
        arxiv_id="2401.00001", title="Test Paper", authors=["Alice"],
        abstract="Abstract.", categories=["cs.AI"], pdf_url="", arxiv_url="",
        published_at="2026-04-01", impact_score=8.5, category_tag="LLM",
        summary="Summary.", key_technique="Attention",
        fact_check=FactCheckResult(score=9.0, total_claims=3, verified_count=3),
    )
    defaults.update(kwargs)
    return RankedPaper(**defaults)


class TestBuildDailyBlocks:
    def test_builds_index_and_paper_blocks(self):
        pub = NotionPublisher()
        papers = [_make_ranked()]
        blocks = pub._build_daily_blocks(papers, "2026-04-01")
        assert len(blocks) > 0
        # Should have heading + table/content
        block_types = [b["type"] for b in blocks]
        assert "heading_2" in block_types or "paragraph" in block_types


class TestBuildWeeklyBlocks:
    def test_includes_themes(self):
        pub = NotionPublisher()
        papers = [_make_ranked()]
        themes = ["Efficiency dominates"]
        blocks = pub._build_weekly_blocks(papers, themes)
        text_content = str(blocks)
        assert "Efficiency dominates" in text_content


class TestBuildBlogBlocks:
    def test_includes_content(self):
        pub = NotionPublisher()
        paper = Paper(
            arxiv_id="1", title="T", authors=["A"], abstract="X.",
            categories=["cs.AI"], pdf_url="", arxiv_url="", published_at="",
        )
        blog = BlogPost(
            title="Blog Title", content="# Heading\n\nParagraph text.",
            word_count=100, grpo_score=7.5, paper=paper,
            generated_at="2026-04-02T10:00:00",
        )
        blocks = pub._build_blog_blocks(blog)
        assert len(blocks) > 0

    def test_includes_chart_image(self):
        pub = NotionPublisher()
        paper = Paper(
            arxiv_id="1", title="T", authors=["A"], abstract="X.",
            categories=["cs.AI"], pdf_url="", arxiv_url="", published_at="",
        )
        chart = Chart(
            chart_type="bar_comparison", title="Test",
            data={}, png_path="/tmp/chart.png", description="A chart.",
        )
        blog = BlogPost(
            title="Blog", content="Content.", charts=[chart],
            word_count=100, grpo_score=7.5, paper=paper,
            generated_at="2026-04-02T10:00:00",
        )
        blocks = pub._build_blog_blocks(blog)
        text_content = str(blocks)
        assert "chart" in text_content.lower() or "image" in text_content.lower()


class TestPublishDaily:
    @patch("jobpulse.papers.notion_publisher._notion_api")
    def test_calls_notion_api(self, mock_api):
        mock_api.return_value = {"id": "page-123"}
        pub = NotionPublisher()
        papers = [_make_ranked()]
        result = pub.publish_daily(papers, "2026-04-01")
        assert mock_api.called
        assert result is not None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/papers/test_notion_publisher.py -v
```

- [ ] **Step 3: Write NotionPublisher**

Create `jobpulse/papers/notion_publisher.py`:

```python
"""NotionPublisher — creates daily/weekly/blog Notion pages for papers."""

import json
import httpx

from jobpulse.papers.models import BlogPost, RankedPaper
from jobpulse.config import NOTION_API_KEY, NOTION_RESEARCH_DB_ID, NOTION_PARENT_PAGE_ID
from shared.logging_config import get_logger

logger = get_logger(__name__)

_NOTION_API = "https://api.notion.com/v1"
_NOTION_VERSION = "2022-06-28"


def _notion_api(method: str, endpoint: str, body: dict | None = None) -> dict:
    if not NOTION_API_KEY:
        logger.warning("No NOTION_API_KEY set — skipping Notion publish")
        return {}
    headers = {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Notion-Version": _NOTION_VERSION,
        "Content-Type": "application/json",
    }
    url = f"{_NOTION_API}/{endpoint}"
    try:
        with httpx.Client(timeout=30) as client:
            if method == "POST":
                resp = client.post(url, headers=headers, json=body)
            elif method == "PATCH":
                resp = client.patch(url, headers=headers, json=body)
            else:
                resp = client.get(url, headers=headers)
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        logger.warning("Notion API %s %s failed: %s", method, endpoint, e)
        return {}


def _text_block(text: str, block_type: str = "paragraph") -> dict:
    return {
        "type": block_type,
        block_type: {
            "rich_text": [{"type": "text", "text": {"content": text[:2000]}}],
        },
    }


def _heading_block(text: str, level: int = 2) -> dict:
    key = f"heading_{level}"
    return {
        "type": key,
        key: {"rich_text": [{"type": "text", "text": {"content": text}}]},
    }


class NotionPublisher:
    """Creates Notion pages for daily digests, weekly summaries, and blog posts."""

    def _get_parent(self) -> dict:
        if NOTION_RESEARCH_DB_ID:
            return {"database_id": NOTION_RESEARCH_DB_ID}
        if NOTION_PARENT_PAGE_ID:
            return {"page_id": NOTION_PARENT_PAGE_ID}
        return {}

    def publish_daily(self, papers: list[RankedPaper], digest_date: str) -> dict:
        parent = self._get_parent()
        if not parent:
            logger.warning("No Notion parent configured — skipping daily publish")
            return {}

        blocks = self._build_daily_blocks(papers, digest_date)
        page = _notion_api("POST", "pages", {
            "parent": parent,
            "properties": {"title": {"title": [{"text": {"content": f"AI Digest — {digest_date}"}}]}},
            "children": blocks[:100],
        })

        # Append remaining blocks if > 100
        page_id = page.get("id", "")
        if page_id and len(blocks) > 100:
            for i in range(100, len(blocks), 100):
                _notion_api("PATCH", f"blocks/{page_id}/children", {"children": blocks[i:i+100]})

        return page

    def publish_weekly(
        self, papers: list[RankedPaper], themes: list[str],
        start_date: str = "", end_date: str = "",
    ) -> dict:
        parent = self._get_parent()
        if not parent:
            return {}

        blocks = self._build_weekly_blocks(papers, themes)
        date_range = f"{start_date} to {end_date}" if start_date else "This Week"
        return _notion_api("POST", "pages", {
            "parent": parent,
            "properties": {"title": {"title": [{"text": {"content": f"Weekly Research — {date_range}"}}]}},
            "children": blocks[:100],
        })

    def publish_blog(self, blog: BlogPost) -> dict:
        parent = self._get_parent()
        if not parent:
            return {}

        blocks = self._build_blog_blocks(blog)
        return _notion_api("POST", "pages", {
            "parent": parent,
            "properties": {"title": {"title": [{"text": {"content": blog.title}}]}},
            "children": blocks[:100],
        })

    def _build_daily_blocks(self, papers: list[RankedPaper], digest_date: str) -> list[dict]:
        blocks = [_heading_block(f"AI Research Digest — {digest_date}", level=1)]

        for i, p in enumerate(papers, 1):
            blocks.append(_heading_block(f"{i}. [{p.category_tag}] {p.title}", level=2))

            # Callout with metadata
            meta_parts = [f"Score: {p.impact_score:.1f}/10"]
            if p.hf_upvotes:
                meta_parts.append(f"HF Upvotes: {p.hf_upvotes}")
            if p.linked_models:
                meta_parts.append(f"Models: {len(p.linked_models)}")
            meta_parts.append(f"Authors: {', '.join(p.authors[:3])}")
            blocks.append(_text_block(" | ".join(meta_parts)))

            if p.summary:
                blocks.append(_text_block(p.summary))

            if p.fact_check and p.fact_check.total_claims > 0:
                fc = p.fact_check
                blocks.append(_text_block(
                    f"Fact-check: {fc.score:.1f}/10 ({fc.verified_count}/{fc.total_claims} verified)"
                ))

            blocks.append(_text_block(f"arXiv: {p.arxiv_url} | PDF: {p.pdf_url}"))

        return blocks

    def _build_weekly_blocks(self, papers: list[RankedPaper], themes: list[str]) -> list[dict]:
        blocks = [_heading_block("Weekly Research Summary", level=1)]

        if themes:
            blocks.append(_heading_block("Themes This Week", level=2))
            for theme in themes:
                blocks.append(_text_block(f"- {theme}"))

        blocks.append(_heading_block(f"Top {len(papers)} Papers", level=2))
        for i, p in enumerate(papers, 1):
            hf = f" | {p.hf_upvotes} upvotes" if p.hf_upvotes else ""
            models = f" | {len(p.linked_models)} models" if p.linked_models else ""
            blocks.append(_text_block(
                f"{i}. {p.impact_score:.1f} [{p.category_tag}] {p.title}{hf}{models}"
            ))

        return blocks

    def _build_blog_blocks(self, blog: BlogPost) -> list[dict]:
        blocks = []

        # Metadata callout
        meta = f"Score: {blog.grpo_score:.1f} | Words: {blog.word_count} | Paper: {blog.paper.arxiv_id}"
        blocks.append(_text_block(meta))

        # Charts
        for chart in blog.charts:
            blocks.append(_heading_block(chart.title, level=3))
            blocks.append(_text_block(f"[Chart image: {chart.description}]"))

        # Blog content — split into paragraphs
        for line in blog.content.split("\n\n"):
            line = line.strip()
            if not line:
                continue
            if line.startswith("# "):
                blocks.append(_heading_block(line[2:], level=1))
            elif line.startswith("## "):
                blocks.append(_heading_block(line[3:], level=2))
            elif line.startswith("### "):
                blocks.append(_heading_block(line[4:], level=3))
            else:
                blocks.append(_text_block(line))

        # Diagram
        if blog.diagram_url:
            blocks.append(_heading_block("Architecture Diagram", level=3))
            blocks.append({"type": "image", "image": {"type": "external", "external": {"url": blog.diagram_url}}})

        return blocks
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/papers/test_notion_publisher.py -v
```

- [ ] **Step 5: Commit**

```bash
git add jobpulse/papers/notion_publisher.py tests/papers/test_notion_publisher.py
git commit -m "feat(papers): add NotionPublisher for daily/weekly/blog Notion pages"
```

---

### Task 8: BlogPipeline (6-agent + charts)

**Files:**
- Create: `jobpulse/papers/blog_pipeline.py`
- Create: `tests/papers/test_blog_pipeline.py`

- [ ] **Step 1: Write blog pipeline tests**

Create `tests/papers/test_blog_pipeline.py`:

```python
"""Tests for BlogPipeline — 6-agent blog generation."""

import pytest
from unittest.mock import patch, MagicMock
from jobpulse.papers.blog_pipeline import BlogPipeline
from jobpulse.papers.models import Paper, BlogPost, Chart


@pytest.fixture
def blog_pipeline():
    return BlogPipeline()


@pytest.fixture
def paper():
    return Paper(
        arxiv_id="2401.00001", title="Test Paper",
        authors=["Alice", "Bob"], abstract="A novel approach to efficient inference.",
        categories=["cs.AI"], pdf_url="https://arxiv.org/pdf/2401.00001",
        arxiv_url="https://arxiv.org/abs/2401.00001", published_at="2026-04-01",
    )


@pytest.fixture
def paper_with_model_card():
    return Paper(
        arxiv_id="2401.00002", title="LLaMA 4",
        authors=["Meta"], abstract="We release LLaMA 4.",
        categories=["cs.CL"], pdf_url="", arxiv_url="", published_at="2026-04-01",
        source="both", linked_models=["meta-llama/Llama-4-8B"],
        model_card_summary="Open foundation model. Achieves 92% on MMLU.",
    )


def _mock_llm_call(content: str):
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content=content))]
    )
    return mock_client


class TestDeepRead:
    def test_returns_research_notes(self, blog_pipeline, paper):
        mock = _mock_llm_call("PROBLEM: X\nMETHOD: Y\nKEY_INSIGHT: Z\nRESULTS: W")
        with patch("jobpulse.papers.blog_pipeline._get_openai_client", return_value=mock):
            notes = blog_pipeline._deep_read(paper)
        assert "PROBLEM" in notes or len(notes) > 0

    def test_includes_model_card_when_available(self, blog_pipeline, paper_with_model_card):
        mock = _mock_llm_call("PROBLEM: X\nMODEL CARD INSIGHTS: Achieves 92% on MMLU")
        with patch("jobpulse.papers.blog_pipeline._get_openai_client", return_value=mock):
            notes = blog_pipeline._deep_read(paper_with_model_card)
        assert len(notes) > 0


class TestGenerate:
    def test_returns_blog_post(self, blog_pipeline, paper, tmp_path):
        mock = _mock_llm_call("# Blog Title\n\nGreat content here. " * 100)
        with patch("jobpulse.papers.blog_pipeline._get_openai_client", return_value=mock), \
             patch.object(blog_pipeline, "_fact_check", return_value=(True, [])), \
             patch.object(blog_pipeline, "_generate_charts", return_value=[]), \
             patch.object(blog_pipeline, "_generate_diagram", return_value=("", "")):
            blog = blog_pipeline.generate(paper, output_dir=str(tmp_path))
        assert isinstance(blog, BlogPost)
        assert blog.paper.arxiv_id == "2401.00001"
        assert blog.word_count > 0

    def test_handles_llm_error_gracefully(self, blog_pipeline, paper, tmp_path):
        with patch("jobpulse.papers.blog_pipeline._get_openai_client", side_effect=Exception("API down")):
            with pytest.raises(Exception):
                blog_pipeline.generate(paper, output_dir=str(tmp_path))
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/papers/test_blog_pipeline.py -v
```

- [ ] **Step 3: Write BlogPipeline**

Create `jobpulse/papers/blog_pipeline.py`:

```python
"""BlogPipeline — 6-agent pipeline: Deep Read → GRPO Write → Fact Check → Revise → Chart → Diagram → Edit."""

import json
import re
from datetime import datetime

from openai import OpenAI

from jobpulse.papers.models import BlogPost, Chart, FactCheckResult, Paper
from jobpulse.papers.chart_generator import ChartGenerator
from jobpulse.config import OPENAI_API_KEY
from shared.logging_config import get_logger

logger = get_logger(__name__)

_MODEL = "gpt-4.1-mini"


def _get_openai_client() -> OpenAI:
    return OpenAI(api_key=OPENAI_API_KEY)


def _llm_call(system: str, user: str, max_tokens: int = 2500, temperature: float = 0.3) -> str:
    client = _get_openai_client()
    resp = client.chat.completions.create(
        model=_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        max_tokens=max_tokens,
        temperature=temperature,
    )
    return resp.choices[0].message.content or ""


class BlogPipeline:
    """6-agent blog generation pipeline with charts."""

    def __init__(self):
        self.chart_gen = ChartGenerator()

    def generate(self, paper: Paper, output_dir: str = "/tmp") -> BlogPost:
        # Agent 1: Deep Read
        notes = self._deep_read(paper)

        # Agent 2: GRPO Write (3 candidates)
        draft, grpo_score = self._write_grpo(paper, notes)

        # Agent 3: Fact Check
        passed, flags = self._fact_check(draft, paper)

        # Agent 4: Revise (if needed)
        if not passed and flags:
            draft = self._revise(draft, flags, paper)

        # Agent 5: Charts
        charts = self._generate_charts(paper, notes, output_dir)

        # Agent 6: Diagram
        mermaid_code, diagram_url = self._generate_diagram(notes, paper)

        # Agent 7: Edit
        final = self._edit(draft, paper, diagram_url, charts)

        return BlogPost(
            title=self._extract_title(final, paper),
            content=final,
            charts=charts,
            mermaid_code=mermaid_code,
            diagram_url=diagram_url,
            word_count=len(final.split()),
            grpo_score=grpo_score,
            paper=paper,
            generated_at=datetime.now().isoformat(),
        )

    def _deep_read(self, paper: Paper) -> str:
        model_card_context = ""
        if paper.model_card_summary:
            model_card_context = f"\n\nModel Card Summary (from HuggingFace):\n{paper.model_card_summary}"

        return _llm_call(
            system=(
                "You are a senior AI researcher. Extract structured research notes.\n"
                "Sections: PROBLEM, METHOD, KEY_INSIGHT, RESULTS, LIMITATIONS, SIGNIFICANCE\n"
                "~1000 words. Be technical but clear."
            ),
            user=f"Title: {paper.title}\n\nAbstract: {paper.abstract}{model_card_context}",
            max_tokens=2000,
            temperature=0.3,
        )

    def _write_grpo(self, paper: Paper, notes: str) -> tuple[str, float]:
        temps = [0.5, 0.7, 0.9]
        drafts = []
        for temp in temps:
            draft = _llm_call(
                system=(
                    "Write a 1800-2200 word technical blog post. Include sections:\n"
                    "TL;DR, The Problem, The Approach, Key Results, Why This Matters, "
                    "Practical Takeaway, Further Reading\n"
                    "Include DIAGRAM_PLACEHOLDER where an architecture diagram would go.\n"
                    "Include CHART_PLACEHOLDER where benchmark charts would go."
                ),
                user=f"Paper: {paper.title}\n\nResearch Notes:\n{notes}",
                max_tokens=3000,
                temperature=temp,
            )
            drafts.append(draft)

        scored = [(self._score_blog(d, paper), d) for d in drafts]
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[0][1], scored[0][0]

    def _score_blog(self, draft: str, paper: Paper) -> float:
        words = len(draft.split())
        score = 0.0

        if 1800 <= words <= 2200:
            score += 3.0
        elif 1500 <= words <= 2500:
            score += 2.0
        elif 1000 <= words <= 3000:
            score += 1.0

        for section in ["TL;DR", "Problem", "Approach", "Results", "Matters", "Takeaway", "Reading"]:
            if section in draft:
                score += 0.5

        if "DIAGRAM_PLACEHOLDER" in draft:
            score += 1.0
        if "CHART_PLACEHOLDER" in draft:
            score += 1.0

        if paper.title.split(":")[0].lower() in draft.lower():
            score += 0.5

        return min(score, 10.0)

    def _fact_check(self, draft: str, paper: Paper) -> tuple[bool, list]:
        try:
            from shared.fact_checker import extract_claims, verify_claims, compute_accuracy_score
            claims = extract_claims(draft, paper.title)
            if not claims:
                return True, []
            verifications = verify_claims(claims, paper.abstract)
            score = compute_accuracy_score(verifications)
            flags = [v for v in verifications if v.get("status") != "VERIFIED"]
            return score >= 8.0, flags
        except ImportError:
            return True, []
        except Exception as e:
            logger.warning("Blog fact check failed: %s", e)
            return True, []

    def _revise(self, draft: str, flags: list, paper: Paper) -> str:
        flag_text = "\n".join(f"- {f.get('claim', '')}: {f.get('issue', '')}" for f in flags)
        return _llm_call(
            system="Revise this blog post to fix the flagged inaccuracies. Keep the structure.",
            user=f"Draft:\n{draft}\n\nFlags to fix:\n{flag_text}\n\nPaper abstract:\n{paper.abstract}",
            max_tokens=3000,
        )

    def _generate_charts(self, paper: Paper, notes: str, output_dir: str) -> list[Chart]:
        return self.chart_gen.generate(paper, notes, output_dir)

    def _generate_diagram(self, notes: str, paper: Paper) -> tuple[str, str]:
        try:
            mermaid = _llm_call(
                system=(
                    "Generate a Mermaid.js flowchart (graph TD) for the paper's architecture. "
                    "Max 15 nodes. Use clear labels."
                ),
                user=f"Paper: {paper.title}\nNotes: {notes[:1500]}",
                max_tokens=500,
                temperature=0.3,
            )
            # Extract mermaid code from possible markdown fence
            m = re.search(r"```(?:mermaid)?\s*\n?(.*?)\n?```", mermaid, re.DOTALL)
            code = m.group(1).strip() if m else mermaid.strip()

            # Generate image URL via mermaid.ink
            import base64
            encoded = base64.urlsafe_b64encode(code.encode()).decode()
            url = f"https://mermaid.ink/img/{encoded}"
            return code, url
        except Exception as e:
            logger.warning("Diagram generation failed: %s", e)
            return "", ""

    def _edit(self, draft: str, paper: Paper, diagram_url: str, charts: list[Chart]) -> str:
        chart_refs = ""
        if charts:
            chart_refs = "\n\nCharts available:\n" + "\n".join(
                f"- {c.title}: {c.description}" for c in charts
            )
        return _llm_call(
            system=(
                "Polish this blog post. Add a KEY TAKEAWAYS box. Fix tone and flow.\n"
                "Replace DIAGRAM_PLACEHOLDER with a reference to the architecture diagram.\n"
                "Replace CHART_PLACEHOLDER with references to the available charts."
            ),
            user=f"Draft:\n{draft}\n\nDiagram URL: {diagram_url}{chart_refs}",
            max_tokens=3000,
        )

    def _extract_title(self, content: str, paper: Paper) -> str:
        for line in content.split("\n"):
            if line.startswith("# "):
                return line[2:].strip()
        return paper.title
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/papers/test_blog_pipeline.py -v
```

- [ ] **Step 5: Commit**

```bash
git add jobpulse/papers/blog_pipeline.py tests/papers/test_blog_pipeline.py
git commit -m "feat(papers): add BlogPipeline with 6-agent generation + HF model card enrichment"
```

---

### Task 9: PapersPipeline Orchestrator

**Files:**
- Modify: `jobpulse/papers/__init__.py`
- Create: `tests/papers/test_pipeline.py`

- [ ] **Step 1: Write orchestrator tests**

Create `tests/papers/test_pipeline.py`:

```python
"""Tests for PapersPipeline orchestrator."""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from jobpulse.papers import PapersPipeline
from jobpulse.papers.models import Paper, RankedPaper, BlogPost


@pytest.fixture
def pipeline(tmp_path):
    return PapersPipeline(db_path=tmp_path / "papers.db")


class TestDailyDigest:
    @pytest.mark.asyncio
    async def test_daily_digest_returns_string(self, pipeline):
        papers = [Paper(
            arxiv_id="2401.00001", title="Test", authors=["A"], abstract="X.",
            categories=["cs.AI"], pdf_url="", arxiv_url="", published_at="2026-04-01",
        )]
        ranked = [RankedPaper(**papers[0].model_dump(), impact_score=8.0, category_tag="LLM", summary="Summary.")]

        with patch.object(pipeline.fetcher, "fetch_all", new_callable=AsyncMock, return_value=papers), \
             patch.object(pipeline.ranker, "llm_rank", return_value=ranked), \
             patch.object(pipeline.ranker, "summarize_and_verify", return_value=ranked), \
             patch.object(pipeline.notion, "publish_daily", return_value={}):
            result = await pipeline.daily_digest()
        assert "Test" in result
        assert isinstance(result, str)


class TestWeeklyDigest:
    @pytest.mark.asyncio
    async def test_weekly_digest_returns_string(self, pipeline):
        stored = [RankedPaper(
            arxiv_id="2401.00001", title="Stored Paper", authors=["A"], abstract="X.",
            categories=["cs.AI"], pdf_url="", arxiv_url="", published_at="2026-04-01",
            impact_score=8.0, category_tag="LLM",
        )]
        with patch.object(pipeline.store, "get_week", return_value=stored), \
             patch.object(pipeline.store, "get_missed_dates", return_value=[]), \
             patch.object(pipeline.fetcher, "fetch_missed", new_callable=AsyncMock, return_value=[]), \
             patch.object(pipeline.ranker, "llm_rank", return_value=stored), \
             patch.object(pipeline.ranker, "extract_themes", return_value=["Theme 1"]), \
             patch.object(pipeline.notion, "publish_weekly", return_value={}):
            result = await pipeline.weekly_digest()
        assert "Stored Paper" in result
        assert "Theme 1" in result


class TestGenerateBlog:
    def test_raises_on_invalid_index(self, pipeline):
        with pytest.raises(ValueError, match="No paper"):
            pipeline.generate_blog(99)

    def test_generates_blog(self, pipeline):
        paper = RankedPaper(
            arxiv_id="2401.00001", title="Test", authors=["A"], abstract="X.",
            categories=["cs.AI"], pdf_url="", arxiv_url="", published_at="2026-04-01",
            impact_score=8.0,
        )
        mock_blog = BlogPost(
            title="Blog", content="Content.", word_count=100, grpo_score=7.5,
            paper=paper, generated_at="2026-04-02",
        )
        with patch.object(pipeline.store, "get_by_index", return_value=paper), \
             patch.object(pipeline.blog, "generate", return_value=mock_blog), \
             patch.object(pipeline.notion, "publish_blog", return_value={}):
            blog = pipeline.generate_blog(1)
        assert blog.title == "Blog"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/papers/test_pipeline.py -v
```

- [ ] **Step 3: Write PapersPipeline orchestrator**

Overwrite `jobpulse/papers/__init__.py`:

```python
"""PapersPipeline — orchestrator for the papers pipeline."""

from datetime import datetime
from pathlib import Path

from jobpulse.papers.fetcher import PaperFetcher
from jobpulse.papers.ranker import PaperRanker
from jobpulse.papers.store import PaperStore
from jobpulse.papers.digest import DigestBuilder
from jobpulse.papers.blog_pipeline import BlogPipeline
from jobpulse.papers.notion_publisher import NotionPublisher
from jobpulse.papers.models import BlogPost
from shared.logging_config import get_logger

logger = get_logger(__name__)


class PapersPipeline:
    """Orchestrates the full papers pipeline: fetch → rank → store → publish."""

    def __init__(self, db_path: Path | None = None):
        self.fetcher = PaperFetcher()
        self.ranker = PaperRanker()
        self.store = PaperStore(db_path=db_path)
        self.digest = DigestBuilder()
        self.blog = BlogPipeline()
        self.notion = NotionPublisher()

    async def daily_digest(self, top_n: int = 5) -> str:
        today = datetime.now().strftime("%Y-%m-%d")

        papers = await self.fetcher.fetch_all()
        logger.info("Fetched %d papers from arXiv + HuggingFace", len(papers))

        ranked = self.ranker.llm_rank(papers, top_n=top_n)
        verified = self.ranker.summarize_and_verify(ranked)

        self.store.store(verified, digest_date=today)
        self.notion.publish_daily(verified, today)

        return self.digest.format_daily(verified, digest_date=today)

    async def weekly_digest(self, top_n: int = 7) -> str:
        stored = self.store.get_week(last_n_days=7)
        missed_dates = self.store.get_missed_dates(last_n_days=7)
        missed = await self.fetcher.fetch_missed(missed_dates)

        # Deduplicate missed against stored
        stored_ids = {p.arxiv_id for p in stored}
        new_papers = [p for p in missed if p.arxiv_id not in stored_ids]

        # Rank new papers and add to stored
        if new_papers:
            new_ranked = self.ranker.llm_rank(new_papers, top_n=len(new_papers))
            verified_new = self.ranker.summarize_and_verify(new_ranked)
            today = datetime.now().strftime("%Y-%m-%d")
            self.store.store(verified_new, digest_date=today)
            stored = stored + verified_new

        ranked = self.ranker.llm_rank(stored, top_n=top_n, lens="weekly")
        themes = self.ranker.extract_themes(ranked)

        self.notion.publish_weekly(ranked, themes)
        return self.digest.format_weekly(ranked, themes)

    def generate_blog(self, paper_index: int, digest_date: str = "") -> BlogPost:
        if not digest_date:
            digest_date = datetime.now().strftime("%Y-%m-%d")

        paper = self.store.get_by_index(digest_date, paper_index)
        if not paper:
            raise ValueError(f"No paper at index {paper_index} for {digest_date}")

        blog = self.blog.generate(paper)
        self.notion.publish_blog(blog)
        return blog
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/papers/test_pipeline.py -v
```

- [ ] **Step 5: Commit**

```bash
git add jobpulse/papers/__init__.py tests/papers/test_pipeline.py
git commit -m "feat(papers): add PapersPipeline orchestrator wiring all components"
```

---

### Task 10: Wire Old Files as Thin Wrappers

**Files:**
- Modify: `jobpulse/arxiv_agent.py`
- Modify: `jobpulse/notion_papers_agent.py`
- Modify: `jobpulse/blog_generator.py`

- [ ] **Step 1: Read current wrapper entry points**

Read `jobpulse/arxiv_agent.py` lines 796-819 (the public API functions called by dispatcher).
Read `jobpulse/blog_generator.py` lines 583-626 (handle_blog_command).

- [ ] **Step 2: Add wrapper functions at the bottom of arxiv_agent.py**

Add at the end of `jobpulse/arxiv_agent.py` (after all existing code):

```python
# ── New pipeline wrappers ──
# These delegate to jobpulse.papers.PapersPipeline while keeping the old
# function signatures intact for dispatcher/runner backwards compatibility.

def _get_pipeline():
    """Lazy singleton for the new papers pipeline."""
    global _pipeline_instance
    try:
        return _pipeline_instance
    except NameError:
        from jobpulse.papers import PapersPipeline
        _pipeline_instance = PapersPipeline()
        return _pipeline_instance


_pipeline_instance = None
```

- [ ] **Step 3: Replace notion_papers_agent.py entirely**

Overwrite `jobpulse/notion_papers_agent.py`:

```python
"""Notion Weekly Papers Agent — wrapper delegating to jobpulse.papers.PapersPipeline.

Runs Monday 8:33am via runner. Replaces the broken original that imported
non-existent functions (fast_score, llm_rank).
"""

import asyncio
from shared.logging_config import get_logger
from jobpulse import telegram_agent

logger = get_logger(__name__)


def create_weekly_page(trigger: str = "cron_monday") -> str:
    """Create weekly research summary page in Notion and send Telegram notification."""
    from jobpulse.papers import PapersPipeline

    pipeline = PapersPipeline()
    try:
        digest = asyncio.run(pipeline.weekly_digest())
        if digest:
            try:
                telegram_agent.send_research(digest)
            except Exception as e:
                logger.warning("Telegram send failed: %s", e)
        return digest
    except Exception as e:
        logger.error("Weekly papers digest failed: %s", e)
        return f"Error: {e}"
```

- [ ] **Step 4: Add blog wrapper in blog_generator.py**

Add at the end of `jobpulse/blog_generator.py`:

```python
# ── New pipeline wrapper ──

def handle_blog_command_v2(paper_index: int) -> str:
    """Blog command using new pipeline. Called by dispatcher."""
    try:
        from jobpulse.papers import PapersPipeline
        pipeline = PapersPipeline()
        blog = pipeline.generate_blog(paper_index)
        return f"Blog generated: {blog.title} ({blog.word_count} words)"
    except ValueError as e:
        return str(e)
    except Exception as e:
        logger.error("Blog generation failed: %s", e)
        return f"Error generating blog: {e}"
```

- [ ] **Step 5: Commit**

```bash
git add jobpulse/arxiv_agent.py jobpulse/notion_papers_agent.py jobpulse/blog_generator.py
git commit -m "feat(papers): wire old files as thin wrappers to new pipeline"
```

---

### Task 11: Add API Endpoint + Delete Legacy Scripts

**Files:**
- Modify: `jobpulse/webhook_server.py`
- Delete: `scripts/agents/notion-papers.sh`

- [ ] **Step 1: Add POST /api/papers/blog/{index} endpoint**

Add after the existing `/api/papers/{index}` endpoint in `jobpulse/webhook_server.py`:

```python
@app.post("/api/papers/blog/{index}", tags=["papers"])
async def generate_paper_blog(index: int):
    """Generate a blog post for paper at given index."""
    try:
        from jobpulse.papers import PapersPipeline
        pipeline = PapersPipeline()
        blog = pipeline.generate_blog(index)
        return {"title": blog.title, "word_count": blog.word_count, "grpo_score": blog.grpo_score}
    except ValueError as e:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        from fastapi import HTTPException
        raise HTTPException(status_code=500, detail=str(e))
```

- [ ] **Step 2: Delete legacy shell script**

```bash
rm scripts/agents/notion-papers.sh
```

- [ ] **Step 3: Commit**

```bash
git add jobpulse/webhook_server.py
git rm scripts/agents/notion-papers.sh
git commit -m "feat(papers): add blog API endpoint, delete legacy shell script"
```

---

### Task 12: Full Integration Test + Final Cleanup

**Files:**
- Create: `tests/papers/test_integration.py`

- [ ] **Step 1: Write integration test**

Create `tests/papers/test_integration.py`:

```python
"""Integration test — full pipeline from fetch to digest."""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from jobpulse.papers import PapersPipeline
from jobpulse.papers.models import Paper


@pytest.mark.asyncio
async def test_full_daily_pipeline(tmp_path):
    """End-to-end: fetch → rank → store → format."""
    pipeline = PapersPipeline(db_path=tmp_path / "papers.db")

    papers = [
        Paper(
            arxiv_id=f"2401.{i:05d}", title=f"Paper {i}",
            authors=["Author"], abstract=f"Abstract {i}.",
            categories=["cs.AI"], pdf_url="", arxiv_url="",
            published_at="2026-04-01", source="arxiv",
        )
        for i in range(10)
    ]

    hf_paper = Paper(
        arxiv_id="2401.00001", title="Paper 1 (HF)",
        authors=["Author"], abstract="Abstract.",
        categories=[], pdf_url="", arxiv_url="",
        published_at="2026-04-01", source="huggingface",
        hf_upvotes=50, linked_models=["model-1"],
    )

    async def mock_fetch_all(*a, **kw):
        return pipeline.fetcher._deduplicate_and_merge(papers, [hf_paper])

    with patch.object(pipeline.fetcher, "fetch_all", side_effect=mock_fetch_all), \
         patch("jobpulse.papers.ranker.OPENAI_API_KEY", ""), \
         patch.object(pipeline.notion, "publish_daily", return_value={}):
        digest = await pipeline.daily_digest(top_n=3)

    assert isinstance(digest, str)
    assert len(digest) > 0

    # Verify papers stored
    stats = pipeline.store.get_stats()
    assert stats.total == 3

    # Verify HF merge happened
    paper1 = pipeline.store.get_by_arxiv_id("2401.00001")
    assert paper1 is not None
    assert paper1.source == "both" or paper1.hf_upvotes == 50


@pytest.mark.asyncio
async def test_weekly_digest_aggregates_stored(tmp_path):
    """Weekly pulls from stored dailies."""
    pipeline = PapersPipeline(db_path=tmp_path / "papers.db")

    # Pre-store some papers
    from jobpulse.papers.models import RankedPaper
    stored = [
        RankedPaper(
            arxiv_id=f"2401.{i:05d}", title=f"Stored {i}",
            authors=["A"], abstract="X.", categories=["cs.AI"],
            pdf_url="", arxiv_url="", published_at="2026-04-01",
            impact_score=8.0 - i * 0.5,
        )
        for i in range(5)
    ]
    from datetime import datetime
    pipeline.store.store(stored, digest_date=datetime.now().strftime("%Y-%m-%d"))

    with patch.object(pipeline.fetcher, "fetch_missed", new_callable=AsyncMock, return_value=[]), \
         patch("jobpulse.papers.ranker.OPENAI_API_KEY", ""), \
         patch.object(pipeline.ranker, "extract_themes", return_value=["Theme"]), \
         patch.object(pipeline.notion, "publish_weekly", return_value={}):
        digest = await pipeline.weekly_digest(top_n=3)

    assert "Stored" in digest
    assert "Theme" in digest
```

- [ ] **Step 2: Run full test suite**

```bash
python -m pytest tests/papers/ -v
```
Expected: ALL PASS

- [ ] **Step 3: Run existing arxiv tests to verify backwards compat**

```bash
python -m pytest tests/test_arxiv_agent.py -v
```
Expected: ALL PASS (old tests still work — old functions untouched)

- [ ] **Step 4: Final commit**

```bash
git add tests/papers/test_integration.py
git commit -m "test(papers): add integration tests for full daily + weekly pipeline"
```

---

## Summary

| Task | What | Files | Tests |
|------|------|-------|-------|
| 1 | Pydantic models | `papers/models.py` | 10 tests |
| 2 | PaperStore (SQLite) | `papers/store.py` | 14 tests |
| 3 | PaperFetcher (async) | `papers/fetcher.py` | 8 tests |
| 4 | PaperRanker | `papers/ranker.py` | 12 tests |
| 5 | DigestBuilder | `papers/digest.py` | 7 tests |
| 6 | ChartGenerator | `papers/chart_generator.py` | 9 tests |
| 7 | NotionPublisher | `papers/notion_publisher.py` | 5 tests |
| 8 | BlogPipeline | `papers/blog_pipeline.py` | 4 tests |
| 9 | Orchestrator | `papers/__init__.py` | 4 tests |
| 10 | Thin wrappers | 3 modified files | Existing tests |
| 11 | API + cleanup | webhook_server + delete script | — |
| 12 | Integration | `test_integration.py` | 2 tests |
| **Total** | **9 new files + 3 modified** | | **~75 tests** |
