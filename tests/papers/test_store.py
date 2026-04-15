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
        paper_store.store([sample_ranked_paper], digest_date="2026-04-01")
        result = paper_store.get_by_index("2026-04-01", 1)
        assert result.fact_check is not None
        assert result.fact_check.score == 9.0


class TestMarkRead:
    def test_mark_read(self, paper_store, sample_ranked_paper):
        paper_store.store([sample_ranked_paper], digest_date="2026-04-01")
        paper_store.mark_read("2401.00001")
        stats = paper_store.get_stats()
        assert stats.read == 1

    def test_mark_read_nonexistent_no_error(self, paper_store):
        paper_store.mark_read("nonexistent")


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
        assert stats.with_models == 1


class TestGetWeek:
    def test_get_week(self, paper_store, sample_ranked_paper):
        paper_store.store([sample_ranked_paper], digest_date="2026-04-01")
        results = paper_store.get_week(last_n_days=7)
        assert len(results) == 1

    def test_get_week_empty(self, paper_store):
        assert paper_store.get_week(last_n_days=7) == []


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

        ranked = RankedPaper(
            arxiv_id="2401.00001", title="T", authors=["A"], abstract="X.",
            categories=["cs.AI"], pdf_url="", arxiv_url="", published_at="",
            source="both", hf_upvotes=10, linked_models=["m1"],
        )
        store.store([ranked], digest_date="2026-04-01")
        result = store.get_by_index("2026-04-01", 1)
        assert result.hf_upvotes == 10


class TestNewColumnMigration:
    def test_store_and_retrieve_github_fields(self, paper_store, sample_ranked_paper):
        paper = sample_ranked_paper.model_copy(update={
            "github_url": "https://github.com/org/repo",
            "github_stars": 150,
            "s2_citation_count": 42,
            "s2_influential_citations": 5,
            "community_buzz": 75,
            "sources": ["huggingface", "hackernews"],
        })
        paper_store.store([paper], digest_date="2026-04-15")
        retrieved = paper_store.get_by_arxiv_id("2401.00001")
        assert retrieved is not None
        assert retrieved.github_url == "https://github.com/org/repo"
        assert retrieved.github_stars == 150
        assert retrieved.s2_citation_count == 42
        assert retrieved.s2_influential_citations == 5
        assert retrieved.community_buzz == 75
        assert retrieved.sources == ["huggingface", "hackernews"]
