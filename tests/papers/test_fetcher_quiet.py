"""Tests for fetch_all default source exclusion and include_community flag."""

import pytest
from jobpulse.papers.fetcher import PaperFetcher


@pytest.mark.asyncio
async def test_default_fetch_excludes_reddit_hn_bluesky(monkeypatch):
    called: list[str] = []

    def make_stub(name):
        async def fn(*a, **k):
            called.append(name)
            return []
        return fn

    fetcher = PaperFetcher()
    monkeypatch.setattr(fetcher, "_fetch_arxiv", make_stub("arxiv"))
    monkeypatch.setattr(fetcher, "_fetch_huggingface", make_stub("hf"))
    monkeypatch.setattr(fetcher, "_fetch_s2_trending", make_stub("s2"))
    monkeypatch.setattr(fetcher, "_fetch_openreview", make_stub("openreview"))
    monkeypatch.setattr(fetcher, "_fetch_hackernews", make_stub("hn"))
    monkeypatch.setattr(fetcher, "_fetch_reddit", make_stub("reddit"))
    monkeypatch.setattr(fetcher, "_fetch_bluesky", make_stub("bsky"))

    await fetcher.fetch_all()
    assert "arxiv" in called and "hf" in called and "openreview" in called
    assert "hn" not in called and "reddit" not in called and "bsky" not in called


@pytest.mark.asyncio
async def test_include_community_runs_all_sources(monkeypatch):
    called: list[str] = []

    def make_stub(name):
        async def fn(*a, **k):
            called.append(name)
            return []
        return fn

    fetcher = PaperFetcher()
    for src in ("arxiv", "huggingface", "s2_trending", "openreview", "hackernews", "reddit", "bluesky"):
        monkeypatch.setattr(fetcher, f"_fetch_{src}", make_stub(src))

    await fetcher.fetch_all(include_community=True)
    for src in ("arxiv", "huggingface", "s2_trending", "openreview", "hackernews", "reddit", "bluesky"):
        assert src in called
