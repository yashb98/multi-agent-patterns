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
             patch.object(pipeline.fetcher, "enrich", new_callable=AsyncMock, return_value=papers), \
             patch.object(pipeline.ranker, "llm_rank", return_value=ranked), \
             patch.object(pipeline.ranker, "summarize_and_verify", return_value=ranked), \
             patch.object(pipeline.notion, "publish_daily", return_value={}):
            result = await pipeline.daily_digest()
        assert "Test" in result
        assert isinstance(result, str)


class TestDailyDigestEnrichment:
    @pytest.mark.asyncio
    async def test_calls_enrich_between_fetch_and_rank(self, pipeline):
        paper = Paper(
            arxiv_id="2401.00001", title="Test", authors=["A"],
            abstract="X.", categories=["cs.AI"], pdf_url="", arxiv_url="",
            published_at="2026-04-01",
        )
        ranked = RankedPaper(**paper.model_dump(), fast_score=5.0, summary="Summary.")

        with patch.object(pipeline.fetcher, "fetch_all", new_callable=AsyncMock, return_value=[paper]), \
             patch.object(pipeline.fetcher, "enrich", new_callable=AsyncMock, return_value=[paper]) as mock_enrich, \
             patch.object(pipeline.ranker, "llm_rank", return_value=[ranked]), \
             patch.object(pipeline.ranker, "summarize_and_verify", return_value=[ranked]), \
             patch.object(pipeline.store, "store"), \
             patch.object(pipeline.notion, "publish_daily"):
            result = await pipeline.daily_digest()

        mock_enrich.assert_called_once()
        assert "Test" in result


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


class TestBuildDigestWrapper:
    def test_build_digest_calls_pipeline(self, tmp_path):
        from unittest.mock import patch, AsyncMock

        with patch("jobpulse.papers.PapersPipeline") as MockPipeline:
            mock_instance = MockPipeline.return_value
            mock_instance.daily_digest = AsyncMock(return_value="📄 *Daily AI Papers*\n\n1. Test Paper")
            from jobpulse.arxiv_agent import build_digest
            result = build_digest(top_n=5)

        assert "Test Paper" in result
        mock_instance.daily_digest.assert_called_once_with(top_n=5)


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
