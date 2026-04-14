"""Tests for SearXNG fallback in fact-checker web verification."""
import pytest


def test_quality_web_verify_uses_searxng_when_ddg_empty(monkeypatch):
    """When DuckDuckGo returns no results, quality_web_verify falls back to SearXNG."""
    from shared.external_verifiers import quality_web_verify

    # Make DuckDuckGo return empty results
    class MockDDGS:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            pass
        def text(self, *a, **kw):
            return []  # No results

    monkeypatch.setattr("shared.external_verifiers.DDGS", MockDDGS, raising=False)

    # Mock SearXNG to return results
    from shared.searxng_client import SearchResult
    monkeypatch.setattr(
        "shared.searxng_client.search",
        lambda q, **kw: [
            SearchResult(title="Verified", url="https://example.com", content="Fact confirmed", engine="google"),
        ],
    )

    result = quality_web_verify("some claim to verify")
    assert len(result["snippets"]) > 0
    assert result["best_source_url"] == "https://example.com"


def test_quality_web_verify_returns_empty_when_both_fail(monkeypatch):
    """When both DDG and SearXNG fail, returns empty result."""
    from shared.external_verifiers import quality_web_verify

    class MockDDGS:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            pass
        def text(self, *a, **kw):
            return []

    monkeypatch.setattr("shared.external_verifiers.DDGS", MockDDGS, raising=False)

    # SearXNG also returns nothing
    monkeypatch.setattr("shared.searxng_client.search", lambda q, **kw: [])

    result = quality_web_verify("obscure claim")
    assert result["snippets"] == []
    assert result["best_source_url"] == ""
