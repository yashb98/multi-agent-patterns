"""Integration test — full pipeline from fetch to digest."""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from jobpulse.papers import PapersPipeline
from jobpulse.papers.models import Paper, RankedPaper


@pytest.mark.asyncio
async def test_full_daily_pipeline(tmp_path):
    """End-to-end: fetch → rank → store → format."""
    pipeline = PapersPipeline(db_path=tmp_path / "papers.db")

    papers = [
        Paper(
            arxiv_id=f"2401.{i:05d}",
            title=f"Paper {i}",
            authors=["Author"],
            abstract=f"Abstract {i}.",
            categories=["cs.AI"],
            pdf_url="",
            arxiv_url="",
            published_at="2026-04-01",
            source="arxiv",
        )
        for i in range(10)
    ]

    hf_paper = Paper(
        arxiv_id="2401.00001",
        title="Paper 1 (HF)",
        authors=["Author"],
        abstract="Abstract.",
        categories=[],
        pdf_url="",
        arxiv_url="",
        published_at="2026-04-01",
        source="huggingface",
        hf_upvotes=50,
        linked_models=["model-1"],
    )

    async def mock_fetch_all(*a, **kw):
        return pipeline.fetcher._deduplicate_and_merge(papers, [hf_paper])

    with (
        patch.object(pipeline.fetcher, "fetch_all", side_effect=mock_fetch_all),
        patch.object(pipeline.fetcher, "enrich", new_callable=AsyncMock, side_effect=lambda p: p),
        patch("jobpulse.papers.ranker._get_openai_client", return_value=None),
        patch.object(pipeline.notion, "publish_daily", return_value={}),
    ):
        digest = await pipeline.daily_digest(top_n=3)

    assert isinstance(digest, str)
    assert len(digest) > 0

    # Verify papers stored
    stats = pipeline.store.get_stats()
    assert stats.total == 3

    # Verify HF merge happened — paper 1 should have upvotes from HF
    paper1 = pipeline.store.get_by_arxiv_id("2401.00001")
    assert paper1 is not None
    assert paper1.hf_upvotes == 50 or paper1.source == "both"


@pytest.mark.asyncio
async def test_full_pipeline_with_community_sources(tmp_path):
    """Smoke test: fetch → enrich → rank → summarize → store → format with new signals."""
    paper = Paper(
        arxiv_id="2401.00001",
        title="Novel Transformer",
        authors=["Alice", "Bob", "Charlie"],
        abstract="We propose a novel transformer that improves efficiency. Code at https://github.com/org/repo.",
        categories=["cs.AI", "cs.LG"],
        pdf_url="https://arxiv.org/pdf/2401.00001",
        arxiv_url="https://arxiv.org/abs/2401.00001",
        published_at="2026-04-01",
        source="both",
        hf_upvotes=60,
        community_buzz=80,
        sources=["huggingface", "hackernews"],
        s2_citation_count=15,
    )

    pipeline = PapersPipeline(db_path=tmp_path / "papers.db")

    with (
        patch.object(pipeline.fetcher, "fetch_all", new_callable=AsyncMock, return_value=[paper]),
        patch.object(pipeline.fetcher, "enrich", new_callable=AsyncMock, return_value=[paper]),
        patch("jobpulse.papers.ranker._get_openai_client", return_value=None),
        patch.object(pipeline.notion, "publish_daily"),
    ):
        result = await pipeline.daily_digest(top_n=1)

    # Verify output format
    assert "Novel Transformer" in result
    assert "arxiv.org" in result

    # Verify stored in DB
    stored = pipeline.store.get_by_arxiv_id("2401.00001")
    assert stored is not None
    assert stored.title == "Novel Transformer"
    assert stored.community_buzz == 80
    assert stored.s2_citation_count == 15
    assert "huggingface" in stored.sources


@pytest.mark.asyncio
async def test_weekly_digest_aggregates_stored(tmp_path):
    """Weekly pulls from stored dailies."""
    pipeline = PapersPipeline(db_path=tmp_path / "papers.db")

    # Pre-store some papers
    stored = [
        RankedPaper(
            arxiv_id=f"2401.{i:05d}",
            title=f"Stored {i}",
            authors=["A"],
            abstract="X.",
            categories=["cs.AI"],
            pdf_url="",
            arxiv_url="",
            published_at="2026-04-01",
            impact_score=8.0 - i * 0.5,
        )
        for i in range(5)
    ]
    from datetime import datetime

    pipeline.store.store(stored, digest_date=datetime.now().strftime("%Y-%m-%d"))

    with (
        patch.object(
            pipeline.fetcher, "fetch_missed", new_callable=AsyncMock, return_value=[]
        ),
        patch("jobpulse.papers.ranker._get_openai_client", return_value=None),
        patch.object(pipeline.ranker, "extract_themes", return_value=["Theme"]),
        patch.object(pipeline.notion, "publish_weekly", return_value={}),
    ):
        digest = await pipeline.weekly_digest(top_n=3)

    assert "Stored" in digest
    assert "Theme" in digest
