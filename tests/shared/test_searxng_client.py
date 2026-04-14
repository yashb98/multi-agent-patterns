import pytest
import time


class TestSearchResult:
    def test_from_dict(self):
        from shared.searxng_client import SearchResult
        r = SearchResult.from_dict({
            "title": "Test", "url": "https://example.com",
            "content": "Some content", "engine": "google",
        })
        assert r.title == "Test"
        assert r.url == "https://example.com"
        assert r.engine == "google"

    def test_from_dict_missing_fields(self):
        from shared.searxng_client import SearchResult
        r = SearchResult.from_dict({"title": "Test"})
        assert r.url == ""
        assert r.content == ""


class TestSearch:
    def test_search_returns_results(self, monkeypatch):
        from shared.searxng_client import search
        import httpx

        mock_response = httpx.Response(200, json={
            "results": [
                {"title": "Result 1", "url": "https://a.com", "content": "Text", "engine": "google"},
                {"title": "Result 2", "url": "https://b.com", "content": "More", "engine": "duckduckgo"},
            ]
        })
        monkeypatch.setattr("shared.searxng_client.httpx.get", lambda *a, **kw: mock_response)
        # Clear any cached results
        monkeypatch.setattr("shared.searxng_client._get_cached", lambda *a, **kw: None)

        results = search("test query")
        assert len(results) == 2
        assert results[0].title == "Result 1"

    def test_search_respects_max_results(self, monkeypatch):
        from shared.searxng_client import search
        import httpx

        mock_response = httpx.Response(200, json={
            "results": [{"title": f"R{i}", "url": f"https://{i}.com", "content": "", "engine": "g"} for i in range(20)]
        })
        monkeypatch.setattr("shared.searxng_client.httpx.get", lambda *a, **kw: mock_response)
        monkeypatch.setattr("shared.searxng_client._get_cached", lambda *a, **kw: None)

        results = search("test", max_results=5)
        assert len(results) == 5

    def test_search_handles_connection_error(self, monkeypatch):
        from shared.searxng_client import search
        import httpx

        def raise_connect_error(*a, **kw):
            raise httpx.ConnectError("refused")

        monkeypatch.setattr("shared.searxng_client.httpx.get", raise_connect_error)
        monkeypatch.setattr("shared.searxng_client._get_cached", lambda *a, **kw: None)

        results = search("test")
        assert results == []

    def test_search_uses_tor_url(self, monkeypatch):
        from shared.searxng_client import search
        import httpx

        captured = {}
        def mock_get(url, **kw):
            captured["url"] = url
            return httpx.Response(200, json={"results": []})

        monkeypatch.setattr("shared.searxng_client.httpx.get", mock_get)
        monkeypatch.setattr("shared.searxng_client._get_cached", lambda *a, **kw: None)
        monkeypatch.setenv("SEARXNG_TOR_URL", "http://localhost:8889")

        search("test", use_tor=True)
        assert "8889" in captured["url"]


class TestSearchSmart:
    def test_salary_uses_tor(self, monkeypatch):
        from shared.searxng_client import search_smart
        import httpx

        captured = {}
        def mock_get(url, **kw):
            captured["url"] = url
            return httpx.Response(200, json={"results": []})

        monkeypatch.setattr("shared.searxng_client.httpx.get", mock_get)
        monkeypatch.setattr("shared.searxng_client._get_cached", lambda *a, **kw: None)
        monkeypatch.setenv("SEARXNG_TOR_URL", "http://localhost:8889")

        search_smart("data engineer salary", context="salary")
        assert "8889" in captured["url"]

    def test_general_uses_fast(self, monkeypatch):
        from shared.searxng_client import search_smart
        import httpx

        captured = {}
        def mock_get(url, **kw):
            captured["url"] = url
            return httpx.Response(200, json={"results": []})

        monkeypatch.setattr("shared.searxng_client.httpx.get", mock_get)
        monkeypatch.setattr("shared.searxng_client._get_cached", lambda *a, **kw: None)

        search_smart("AI news today", context="general")
        assert "8888" in captured["url"]


class TestCache:
    def test_cache_stores_and_retrieves(self, tmp_path, monkeypatch):
        from shared.searxng_client import _cache_key, _get_cached, _set_cached

        db_path = tmp_path / "searxng_cache.db"
        monkeypatch.setattr("shared.searxng_client.CACHE_DB_PATH", db_path)

        key = _cache_key("test query", [], False)
        _set_cached(key, [{"title": "cached", "url": "https://c.com", "content": "", "engine": "g"}], db_path)

        cached = _get_cached(key, use_tor=False, db_path=db_path)
        assert cached is not None
        assert len(cached) == 1

    def test_cache_expires(self, tmp_path, monkeypatch):
        from shared.searxng_client import _cache_key, _get_cached, _set_cached
        import sqlite3

        db_path = tmp_path / "searxng_cache.db"
        monkeypatch.setattr("shared.searxng_client.CACHE_DB_PATH", db_path)

        key = _cache_key("old query", [], False)
        _set_cached(key, [{"title": "old"}], db_path)

        # Manually expire by backdating
        conn = sqlite3.connect(str(db_path))
        conn.execute("UPDATE searxng_cache SET created_at = created_at - 90000")
        conn.commit()
        conn.close()

        cached = _get_cached(key, use_tor=False, db_path=db_path)
        assert cached is None
