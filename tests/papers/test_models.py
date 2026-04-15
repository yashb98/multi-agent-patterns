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


class TestPaperNewFields:
    def test_paper_has_github_fields(self):
        paper = Paper(
            arxiv_id="2401.00001", title="Test", authors=["A"],
            abstract="X.", categories=["cs.AI"], pdf_url="", arxiv_url="",
            published_at="2026-04-01",
            github_url="https://github.com/org/repo",
            github_stars=150,
        )
        assert paper.github_url == "https://github.com/org/repo"
        assert paper.github_stars == 150

    def test_paper_has_s2_fields(self):
        paper = Paper(
            arxiv_id="2401.00001", title="Test", authors=["A"],
            abstract="X.", categories=["cs.AI"], pdf_url="", arxiv_url="",
            published_at="2026-04-01",
            s2_citation_count=42,
            s2_influential_citations=5,
        )
        assert paper.s2_citation_count == 42
        assert paper.s2_influential_citations == 5

    def test_paper_has_community_fields(self):
        paper = Paper(
            arxiv_id="2401.00001", title="Test", authors=["A"],
            abstract="X.", categories=["cs.AI"], pdf_url="", arxiv_url="",
            published_at="2026-04-01",
            community_buzz=75,
            sources=["huggingface", "hackernews"],
        )
        assert paper.community_buzz == 75
        assert paper.sources == ["huggingface", "hackernews"]

    def test_new_fields_default_to_zero_or_empty(self):
        paper = Paper(
            arxiv_id="2401.00001", title="Test", authors=["A"],
            abstract="X.", categories=["cs.AI"], pdf_url="", arxiv_url="",
            published_at="2026-04-01",
        )
        assert paper.github_url == ""
        assert paper.github_stars == 0
        assert paper.s2_citation_count == 0
        assert paper.s2_influential_citations == 0
        assert paper.community_buzz == 0
        assert paper.sources == []
