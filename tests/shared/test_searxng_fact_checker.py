"""Tests for SearXNG fallback in fact-checker web verification."""
import sys
import types
import pytest


def _patch_ddgs_empty(monkeypatch):
    """Patch the ddgs module so DDGS.text() returns empty results."""
    class MockDDGS:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            pass
        def text(self, *a, **kw):
            return []

    mock_ddgs_module = types.ModuleType("ddgs")
    mock_ddgs_module.DDGS = MockDDGS
    monkeypatch.setitem(sys.modules, "ddgs", mock_ddgs_module)

    mock_ddgs_search_module = types.ModuleType("duckduckgo_search")
    mock_ddgs_search_module.DDGS = MockDDGS
    monkeypatch.setitem(sys.modules, "duckduckgo_search", mock_ddgs_search_module)


def test_quality_web_verify_uses_searxng_when_ddg_empty(monkeypatch):
    """When DuckDuckGo returns no results, quality_web_verify falls back to SearXNG."""
    _patch_ddgs_empty(monkeypatch)

    from shared.searxng_client import SearchResult
    monkeypatch.setattr(
        "shared.searxng_client.search_smart",
        lambda q, **kw: [
            SearchResult(title="Verified", url="https://example.com", content="Fact confirmed", engine="google"),
        ],
    )

    from shared.external_verifiers import quality_web_verify
    result = quality_web_verify("some claim to verify")
    assert len(result["snippets"]) > 0
    assert result["best_source_url"] == "https://example.com"


def test_quality_web_verify_returns_empty_when_both_fail(monkeypatch):
    """When both DDG and SearXNG fail, returns empty result."""
    _patch_ddgs_empty(monkeypatch)

    monkeypatch.setattr("shared.searxng_client.search_smart", lambda q, **kw: [])

    from shared.external_verifiers import quality_web_verify
    result = quality_web_verify("obscure claim")
    assert result["snippets"] == []
    assert result["best_source_url"] == ""
