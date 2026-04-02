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
        models_resp = httpx.Response(200, json=[{"id": "model-1"}])
        empty_resp = httpx.Response(200, json=[])

        async def mock_get(url, **kwargs):
            if "daily_papers" in url:
                return daily_resp
            if "models" in url:
                return models_resp if "2401.00001" in str(kwargs.get("params", {})) else empty_resp
            return empty_resp

        with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock, side_effect=mock_get):
            papers = await fetcher._fetch_huggingface()
        assert len(papers) >= 1

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
        assert merged[0].abstract == "Full arXiv abstract."
        assert merged[0].hf_upvotes == 42
        assert merged[0].linked_models == ["model-1"]


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
