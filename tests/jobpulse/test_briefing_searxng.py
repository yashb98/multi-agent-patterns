import pytest


def test_fetch_ai_news_returns_results(monkeypatch):
    from jobpulse.briefing_agent import fetch_ai_news
    from shared.searxng_client import SearchResult

    monkeypatch.setattr("shared.searxng_client.search",
                        lambda q, **kw: [
                            SearchResult(title="GPT-5 released", url="https://news.com/1", content="Big news", engine="google"),
                            SearchResult(title="New AI regulation", url="https://news.com/2", content="EU law", engine="google"),
                        ])

    results = fetch_ai_news()
    assert len(results) >= 1
    assert "GPT-5" in results[0]["title"]


def test_fetch_ai_news_handles_searxng_down(monkeypatch):
    from jobpulse.briefing_agent import fetch_ai_news

    monkeypatch.setattr("shared.searxng_client.search", lambda q, **kw: [])

    results = fetch_ai_news()
    assert results == []
