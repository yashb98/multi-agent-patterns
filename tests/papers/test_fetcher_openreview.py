"""Tests for PaperFetcher._fetch_openreview."""

import pytest
import httpx
from jobpulse.papers.fetcher import PaperFetcher


@pytest.mark.asyncio
async def test_openreview_returns_papers(monkeypatch):
    fake = {
        "notes": [
            {
                "id": "abc", "content": {
                    "title": {"value": "ICLR Paper Foo"},
                    "abstract": {"value": "We propose..."},
                    "authors": {"value": ["A", "B"]},
                    "pdf": {"value": "/pdf?id=abc"},
                },
                "cdate": 1714000000000,
            }
        ]
    }
    async def fake_get(self, url, **kwargs):
        class _R:
            status_code = 200
            def raise_for_status(self): pass
            def json(self): return fake
        return _R()
    monkeypatch.setattr("httpx.AsyncClient.get", fake_get)
    fetcher = PaperFetcher()
    papers = await fetcher._fetch_openreview()
    assert len(papers) >= 1
    assert papers[0].title == "ICLR Paper Foo"


@pytest.mark.asyncio
async def test_openreview_failure_returns_empty(monkeypatch):
    async def fake_get(self, url, **kwargs):
        raise httpx.HTTPError("rate limit")
    monkeypatch.setattr("httpx.AsyncClient.get", fake_get)
    fetcher = PaperFetcher()
    papers = await fetcher._fetch_openreview()
    assert papers == []  # graceful degrade
