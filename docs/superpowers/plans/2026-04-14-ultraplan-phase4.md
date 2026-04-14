# Ultraplan Phase 4: SearXNG Integration

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a self-hosted SearXNG instance as a universal web search layer with optional Tor proxy, SQLite cache, and integrations into fact-checker, briefing, and paper discovery.

**Architecture:** `shared/searxng_client.py` provides `search()` and `search_smart()` with auto Tor/no-Tor routing. SQLite cache with 24h TTL (fast) / 7d TTL (Tor). Docker Compose for SearXNG + Tor sidecar. Integrations via simple function calls into existing modules.

**Tech Stack:** httpx, SQLite, Docker, SearXNG, Tor (dperson/torproxy)

---

### Task 1: SearXNG Client — Core Module

**Files:**
- Create: `shared/searxng_client.py`
- Create: `tests/shared/test_searxng_client.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/shared/test_searxng_client.py
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

        results = search("test", max_results=5)
        assert len(results) == 5

    def test_search_handles_error(self, monkeypatch):
        from shared.searxng_client import search
        import httpx

        monkeypatch.setattr("shared.searxng_client.httpx.get", lambda *a, **kw: (_ for _ in ()).throw(httpx.ConnectError("refused")))

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

        search_smart("AI news today", context="general")
        assert "8888" in captured["url"]


class TestCache:
    def test_cache_stores_and_retrieves(self, tmp_path, monkeypatch):
        from shared.searxng_client import search, _get_cache_db, _cache_key, _get_cached, _set_cached
        import httpx

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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/shared/test_searxng_client.py -v`
Expected: FAIL with ModuleNotFoundError

- [ ] **Step 3: Implement the SearXNG client**

```python
# shared/searxng_client.py
"""SearXNG client — self-hosted metasearch with optional Tor proxy.

Provides search() for direct queries and search_smart() for auto Tor/no-Tor routing.
Results cached in SQLite with 24h TTL (fast) / 7d TTL (Tor).

Setup:
    docker run -d --name searxng -p 8888:8080 searxng/searxng
    # Optional Tor:
    docker run -d --name searxng-tor -p 8889:8080 searxng/searxng
    docker run -d --name tor-proxy -p 9050:9050 dperson/torproxy
"""

import hashlib
import json
import os
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from shared.logging_config import get_logger
from shared.paths import DATA_DIR as _DATA_DIR

logger = get_logger(__name__)

SEARXNG_URL = os.getenv("SEARXNG_URL", "http://localhost:8888")
SEARXNG_TOR_URL = os.getenv("SEARXNG_TOR_URL", "http://localhost:8889")
CACHE_DB_PATH = _DATA_DIR / "searxng_cache.db"

FAST_TTL_S = 86400      # 24 hours
TOR_TTL_S = 604800      # 7 days

TOR_CONTEXTS = {"salary", "glassdoor", "linkedin_public", "career_page"}


@dataclass
class SearchResult:
    title: str
    url: str
    content: str
    engine: str
    score: float = 0.0

    @classmethod
    def from_dict(cls, d: dict) -> "SearchResult":
        return cls(
            title=d.get("title", ""),
            url=d.get("url", ""),
            content=d.get("content", ""),
            engine=d.get("engine", ""),
            score=d.get("score", 0.0),
        )

    def to_dict(self) -> dict:
        return {"title": self.title, "url": self.url, "content": self.content, "engine": self.engine, "score": self.score}


# ── Cache ──

def _get_cache_db(db_path: Path = None) -> sqlite3.Connection:
    path = db_path or CACHE_DB_PATH
    conn = sqlite3.connect(str(path))
    conn.execute("""CREATE TABLE IF NOT EXISTS searxng_cache (
        cache_key TEXT PRIMARY KEY,
        results_json TEXT NOT NULL,
        created_at REAL NOT NULL
    )""")
    conn.commit()
    return conn


def _cache_key(query: str, engines: list[str], use_tor: bool) -> str:
    raw = f"{query}|{','.join(sorted(engines))}|{use_tor}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _get_cached(key: str, use_tor: bool, db_path: Path = None) -> list[dict] | None:
    ttl = TOR_TTL_S if use_tor else FAST_TTL_S
    try:
        conn = _get_cache_db(db_path)
        row = conn.execute("SELECT results_json, created_at FROM searxng_cache WHERE cache_key=?", (key,)).fetchone()
        conn.close()
        if row and (time.time() - row[1]) < ttl:
            return json.loads(row[0])
    except Exception as e:
        logger.debug("Cache read error: %s", e)
    return None


def _set_cached(key: str, results: list[dict], db_path: Path = None):
    try:
        conn = _get_cache_db(db_path)
        conn.execute(
            "INSERT OR REPLACE INTO searxng_cache (cache_key, results_json, created_at) VALUES (?, ?, ?)",
            (key, json.dumps(results), time.time()),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.debug("Cache write error: %s", e)


# ── Search ──

def search(
    query: str,
    categories: list[str] | None = None,
    engines: list[str] | None = None,
    max_results: int = 10,
    use_tor: bool = False,
) -> list[SearchResult]:
    """Query local SearXNG instance. Returns structured results.

    Falls back to empty list on connection error (SearXNG not running).
    """
    engines_list = engines or []
    key = _cache_key(query, engines_list, use_tor)

    # Check cache
    cached = _get_cached(key, use_tor)
    if cached is not None:
        logger.debug("SearXNG cache hit for: %s", query[:60])
        return [SearchResult.from_dict(r) for r in cached[:max_results]]

    base_url = os.getenv("SEARXNG_TOR_URL", SEARXNG_TOR_URL) if use_tor else os.getenv("SEARXNG_URL", SEARXNG_URL)
    params = {
        "q": query,
        "format": "json",
    }
    if categories:
        params["categories"] = ",".join(categories)
    if engines_list:
        params["engines"] = ",".join(engines_list)

    try:
        resp = httpx.get(f"{base_url}/search", params=params, timeout=15 if not use_tor else 30)
        if resp.status_code != 200:
            logger.warning("SearXNG returned %d for: %s", resp.status_code, query[:60])
            return []

        raw_results = resp.json().get("results", [])[:max_results]
        results = [SearchResult.from_dict(r) for r in raw_results]

        # Cache
        _set_cached(key, [r.to_dict() for r in results])

        logger.info("SearXNG: %d results for '%s' (tor=%s)", len(results), query[:40], use_tor)
        return results

    except httpx.ConnectError:
        logger.debug("SearXNG not reachable at %s", base_url)
        return []
    except Exception as e:
        logger.warning("SearXNG search failed: %s", e)
        return []


def search_smart(query: str, context: str = "general", **kwargs) -> list[SearchResult]:
    """Auto-select SearXNG mode based on context.

    Tor contexts (slow, anonymous): salary, glassdoor, linkedin_public, career_page
    Fast contexts (everything else): general, company, interview, news, fact_check
    """
    use_tor = context in TOR_CONTEXTS
    return search(query, use_tor=use_tor, **kwargs)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/shared/test_searxng_client.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add shared/searxng_client.py tests/shared/test_searxng_client.py
git commit -m "feat: add SearXNG client with smart Tor routing and SQLite cache"
git push origin main
```

---

### Task 2: Docker Compose Setup

**Files:**
- Create: `docker-compose.searxng.yml`

- [ ] **Step 1: Create the Docker Compose file**

```yaml
# docker-compose.searxng.yml
# SearXNG self-hosted metasearch + optional Tor proxy
#
# Usage:
#   docker compose -f docker-compose.searxng.yml up -d
#   curl "http://localhost:8888/search?q=test&format=json"
#
# With Tor:
#   docker compose -f docker-compose.searxng.yml --profile tor up -d
#   curl "http://localhost:8889/search?q=test&format=json"

services:
  searxng:
    image: searxng/searxng
    container_name: searxng
    ports:
      - "8888:8080"
    volumes:
      - ./searxng:/etc/searxng
    restart: unless-stopped

  searxng-tor:
    image: searxng/searxng
    container_name: searxng-tor
    ports:
      - "8889:8080"
    volumes:
      - ./searxng-tor:/etc/searxng
    depends_on:
      - tor-proxy
    restart: unless-stopped
    profiles:
      - tor

  tor-proxy:
    image: dperson/torproxy
    container_name: tor-proxy
    ports:
      - "9050:9050"
    restart: unless-stopped
    profiles:
      - tor
```

- [ ] **Step 2: Commit**

```bash
git add docker-compose.searxng.yml
git commit -m "feat: add Docker Compose for SearXNG + Tor sidecar"
git push origin main
```

---

### Task 3: Wire SearXNG into Fact-Checker

**Files:**
- Modify: `shared/external_verifiers.py` — add SearXNG fallback to `quality_web_verify()`
- Create: `tests/shared/test_searxng_fact_checker.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/shared/test_searxng_fact_checker.py
import pytest


def test_quality_web_verify_uses_searxng_fallback(monkeypatch):
    """When DuckDuckGo fails, quality_web_verify falls back to SearXNG."""
    from shared.external_verifiers import quality_web_verify

    # Make DuckDuckGo fail
    monkeypatch.setattr("shared.external_verifiers.web_verify_claim_ddg",
                        lambda q: {"snippets": [], "error": "rate limited"})

    # Mock SearXNG to return results
    from shared.searxng_client import SearchResult
    monkeypatch.setattr("shared.searxng_client.search",
                        lambda q, **kw: [SearchResult(title="Test", url="https://example.com", content="Verified fact", engine="google")])

    result = quality_web_verify("some claim to verify")
    assert len(result.get("snippets", [])) > 0 or result.get("best_source_url")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/shared/test_searxng_fact_checker.py -v`
Expected: FAIL

- [ ] **Step 3: Add SearXNG fallback to quality_web_verify**

In `shared/external_verifiers.py`, at the end of `quality_web_verify()`, before returning the empty result, add a SearXNG fallback:

```python
    # Fallback: try SearXNG if DuckDuckGo returned nothing
    if not results and not empty_result.get("snippets"):
        try:
            from shared.searxng_client import search_smart
            sxng_results = search_smart(query, context="fact_check", max_results=5)
            if sxng_results:
                results = [{"url": r.url, "snippet": r.content, "quality": 0.5} for r in sxng_results]
                # Continue with normal scoring logic below
        except Exception as e:
            logger.debug("SearXNG fallback failed: %s", e)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/shared/test_searxng_fact_checker.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add shared/external_verifiers.py tests/shared/test_searxng_fact_checker.py
git commit -m "feat: add SearXNG fallback to fact-checker web verification"
git push origin main
```

---

### Task 4: Wire SearXNG into Briefing Agent

**Files:**
- Modify: `jobpulse/briefing_agent.py` — add "Top AI News" section via SearXNG
- Create: `tests/jobpulse/test_briefing_searxng.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/jobpulse/test_briefing_searxng.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/jobpulse/test_briefing_searxng.py -v`
Expected: FAIL

- [ ] **Step 3: Add fetch_ai_news to briefing_agent**

Add to `jobpulse/briefing_agent.py`:

```python
def fetch_ai_news(max_results: int = 5) -> list[dict]:
    """Fetch top AI news via SearXNG. Returns empty list if SearXNG unavailable."""
    try:
        from shared.searxng_client import search_smart
        results = search_smart(
            "artificial intelligence machine learning news today",
            context="general",
            categories=["news"],
            max_results=max_results,
        )
        return [{"title": r.title, "url": r.url, "summary": r.content[:200]} for r in results]
    except Exception as e:
        logger.debug("AI news fetch failed: %s", e)
        return []
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/jobpulse/test_briefing_searxng.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add jobpulse/briefing_agent.py tests/jobpulse/test_briefing_searxng.py
git commit -m "feat: add AI news section to briefing via SearXNG"
git push origin main
```

---

### Task 5: Wire SearXNG into Interview Prep

**Files:**
- Modify: `jobpulse/interview_prep.py` — add SearXNG enrichment for interview questions
- Create: `tests/jobpulse/test_interview_prep_searxng.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/jobpulse/test_interview_prep_searxng.py
import pytest


def test_fetch_interview_questions_returns_results(monkeypatch):
    from jobpulse.interview_prep import fetch_interview_questions

    from shared.searxng_client import SearchResult
    monkeypatch.setattr("shared.searxng_client.search_smart",
                        lambda q, **kw: [
                            SearchResult(title="Top 10 ML questions", url="https://x.com/1", content="Q1: Explain bias-variance", engine="google"),
                        ])

    results = fetch_interview_questions("Monzo", "Data Scientist")
    assert len(results) >= 1


def test_fetch_interview_questions_handles_failure(monkeypatch):
    from jobpulse.interview_prep import fetch_interview_questions

    monkeypatch.setattr("shared.searxng_client.search_smart", lambda q, **kw: [])

    results = fetch_interview_questions("Monzo", "Data Scientist")
    assert results == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/jobpulse/test_interview_prep_searxng.py -v`
Expected: FAIL

- [ ] **Step 3: Add fetch_interview_questions to interview_prep**

Add to `jobpulse/interview_prep.py`:

```python
def fetch_interview_questions(company: str, role: str, max_results: int = 5) -> list[dict]:
    """Fetch common interview questions via SearXNG. Returns empty list if unavailable."""
    try:
        from shared.searxng_client import search_smart
        results = search_smart(
            f"{company} {role} interview questions",
            context="general",
            max_results=max_results,
        )
        return [{"title": r.title, "url": r.url, "content": r.content[:300]} for r in results]
    except Exception as e:
        logger.debug("Interview question fetch failed: %s", e)
        return []
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/jobpulse/test_interview_prep_searxng.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add jobpulse/interview_prep.py tests/jobpulse/test_interview_prep_searxng.py
git commit -m "feat: add SearXNG interview question enrichment"
git push origin main
```

---

### Task 6: Final Verification

**Files:** None (verification only)

- [ ] **Step 1: Run all new SearXNG tests**

Run: `python -m pytest tests/shared/test_searxng_client.py tests/shared/test_searxng_fact_checker.py tests/jobpulse/test_briefing_searxng.py tests/jobpulse/test_interview_prep_searxng.py -v`
Expected: All PASS

- [ ] **Step 2: Run full test suite**

Run: `python -m pytest tests/ --tb=short`
Expected: 2035+ tests passing, 0 failures

- [ ] **Step 3: Verify SearXNG client import**

```python
from shared.searxng_client import search, search_smart, SearchResult
print("SearXNG client imports clean")
```

- [ ] **Step 4: Commit plan doc**

```bash
git add docs/superpowers/plans/2026-04-14-ultraplan-phase4.md
git commit -m "docs: add ultraplan Phase 4 implementation plan"
git push origin main
```
