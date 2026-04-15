# Paper Pipeline Overhaul Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewire `build_digest()` to use the new `papers/` pipeline, fix broken sources, add community + enrichment signals, rebalance scoring.

**Architecture:** Add 5 new fetch methods + enrichment to `papers/fetcher.py`, extend `Paper` model with GitHub/S2/community fields, rebalance `fast_score` in `ranker.py`, update digest formatting, and rewire `arxiv_agent.build_digest()` as a thin async wrapper around `PapersPipeline.daily_digest()`.

**Tech Stack:** Python, httpx (async), Pydantic, SQLite, pytest, asyncio

**Spec:** `docs/superpowers/specs/2026-04-15-paper-pipeline-overhaul-design.md`

---

### Task 1: Extend `Paper` model with new fields

**Files:**
- Modify: `jobpulse/papers/models.py:9-25`
- Modify: `tests/papers/conftest.py`
- Test: `tests/papers/test_models.py`

- [ ] **Step 1: Write failing test for new Paper fields**

Add to `tests/papers/test_models.py`:

```python
class TestPaperNewFields:
    def test_paper_has_github_fields(self):
        paper = Paper(
            arxiv_id="2401.00001", title="Test", authors=["A"],
            abstract="X.", categories=["cs.AI"], pdf_url="", arxiv_url="",
            published_at="2026-04-01",
            github_url="https://github.com/org/repo",
            github_stars=150,
        )
        assert paper.github_url == "https://github.com/org/repo"
        assert paper.github_stars == 150

    def test_paper_has_s2_fields(self):
        paper = Paper(
            arxiv_id="2401.00001", title="Test", authors=["A"],
            abstract="X.", categories=["cs.AI"], pdf_url="", arxiv_url="",
            published_at="2026-04-01",
            s2_citation_count=42,
            s2_influential_citations=5,
        )
        assert paper.s2_citation_count == 42
        assert paper.s2_influential_citations == 5

    def test_paper_has_community_fields(self):
        paper = Paper(
            arxiv_id="2401.00001", title="Test", authors=["A"],
            abstract="X.", categories=["cs.AI"], pdf_url="", arxiv_url="",
            published_at="2026-04-01",
            community_buzz=75,
            sources=["huggingface", "hackernews"],
        )
        assert paper.community_buzz == 75
        assert paper.sources == ["huggingface", "hackernews"]

    def test_new_fields_default_to_zero_or_empty(self):
        paper = Paper(
            arxiv_id="2401.00001", title="Test", authors=["A"],
            abstract="X.", categories=["cs.AI"], pdf_url="", arxiv_url="",
            published_at="2026-04-01",
        )
        assert paper.github_url == ""
        assert paper.github_stars == 0
        assert paper.s2_citation_count == 0
        assert paper.s2_influential_citations == 0
        assert paper.community_buzz == 0
        assert paper.sources == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/papers/test_models.py::TestPaperNewFields -v`
Expected: FAIL with validation errors (fields not defined on Paper)

- [ ] **Step 3: Add new fields to Paper model**

In `jobpulse/papers/models.py`, add these fields to the `Paper` class after `model_card_summary`:

```python
    github_url: str = ""
    github_stars: int = 0
    s2_citation_count: int = 0
    s2_influential_citations: int = 0
    community_buzz: int = 0
    sources: list[str] = Field(default_factory=list)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/papers/test_models.py::TestPaperNewFields -v`
Expected: PASS

- [ ] **Step 5: Run all existing model tests to verify no regressions**

Run: `pytest tests/papers/test_models.py -v`
Expected: All PASS (new defaults don't break existing Paper construction)

- [ ] **Step 6: Commit**

```bash
git add jobpulse/papers/models.py tests/papers/test_models.py
git commit -m "feat(papers): add github, s2, community fields to Paper model"
```

---

### Task 2: Add SQLite migration for new columns

**Files:**
- Modify: `jobpulse/papers/store.py:16-24`
- Test: `tests/papers/test_store.py`

- [ ] **Step 1: Write failing test for new columns**

Add to `tests/papers/test_store.py`:

```python
class TestNewColumnMigration:
    def test_store_and_retrieve_github_fields(self, paper_store, sample_ranked_paper):
        paper = sample_ranked_paper.model_copy(update={
            "github_url": "https://github.com/org/repo",
            "github_stars": 150,
            "s2_citation_count": 42,
            "s2_influential_citations": 5,
            "community_buzz": 75,
            "sources": ["huggingface", "hackernews"],
        })
        paper_store.store([paper], digest_date="2026-04-15")
        retrieved = paper_store.get_by_arxiv_id("2401.00001")
        assert retrieved is not None
        assert retrieved.github_url == "https://github.com/org/repo"
        assert retrieved.github_stars == 150
        assert retrieved.s2_citation_count == 42
        assert retrieved.s2_influential_citations == 5
        assert retrieved.community_buzz == 75
        assert retrieved.sources == ["huggingface", "hackernews"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/papers/test_store.py::TestNewColumnMigration -v`
Expected: FAIL (columns don't exist in schema)

- [ ] **Step 3: Add columns to store schema and migration**

In `jobpulse/papers/store.py`, add to `_HF_COLUMNS`:

```python
    ("github_url", "TEXT DEFAULT ''"),
    ("github_stars", "INTEGER DEFAULT 0"),
    ("s2_citation_count", "INTEGER DEFAULT 0"),
    ("s2_influential_citations", "INTEGER DEFAULT 0"),
    ("community_buzz", "INTEGER DEFAULT 0"),
    ("sources", "TEXT"),
```

Add the same columns to `_CREATE_TABLE` after `model_card_summary`:

```sql
    github_url          TEXT DEFAULT '',
    github_stars        INTEGER DEFAULT 0,
    s2_citation_count   INTEGER DEFAULT 0,
    s2_influential_citations INTEGER DEFAULT 0,
    community_buzz      INTEGER DEFAULT 0,
    sources             TEXT,
```

Update `store()` method's INSERT statement to include the new columns and their values.

Update `_row_to_ranked_paper()` to read the new columns:

```python
    github_url=row["github_url"] or "",
    github_stars=row["github_stars"] or 0,
    s2_citation_count=row["s2_citation_count"] or 0,
    s2_influential_citations=row["s2_influential_citations"] or 0,
    community_buzz=row["community_buzz"] or 0,
    sources=json.loads(row["sources"]) if row["sources"] else [],
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/papers/test_store.py::TestNewColumnMigration -v`
Expected: PASS

- [ ] **Step 5: Run all store tests for regressions**

Run: `pytest tests/papers/test_store.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add jobpulse/papers/store.py tests/papers/test_store.py
git commit -m "feat(papers): add github/s2/community columns to paper store"
```

---

### Task 3: Add community source fetchers to `PaperFetcher`

**Files:**
- Modify: `jobpulse/papers/fetcher.py`
- Test: `tests/papers/test_fetcher.py`

- [ ] **Step 1: Write failing tests for new fetchers**

Add to `tests/papers/test_fetcher.py`:

```python
SAMPLE_HN_RESPONSE = {
    "hits": [
        {"url": "https://arxiv.org/abs/2401.00050", "title": "HN Paper on LLMs", "points": 42},
        {"url": "https://example.com/no-arxiv", "title": "Not a paper", "points": 10},
    ]
}

SAMPLE_REDDIT_RESPONSE = {
    "data": {
        "children": [
            {"data": {"url": "https://arxiv.org/abs/2401.00060", "selftext": "", "title": "Reddit ML Paper", "score": 15, "created_utc": __import__("time").time() - 3600}},
            {"data": {"url": "https://example.com", "selftext": "Check 2401.00070 out", "title": "Text with ID", "score": 8, "created_utc": __import__("time").time() - 3600}},
        ]
    }
}

SAMPLE_BLUESKY_RESPONSE = {
    "posts": [
        {"record": {"text": "Great paper https://arxiv.org/abs/2401.00080 on agents"}},
        {"record": {"text": "No arxiv link here"}},
    ]
}

SAMPLE_S2_BULK_RESPONSE = {
    "total": 100,
    "data": [
        {"paperId": "abc123", "title": "S2 Paper", "citationCount": 25, "publicationDate": "2026-04-01", "externalIds": {"ArXiv": "2401.00090"}},
        {"paperId": "def456", "title": "S2 Paper No ArXiv", "citationCount": 10, "publicationDate": "2026-04-01", "externalIds": {}},
    ]
}


class TestFetchHackerNews:
    @pytest.mark.asyncio
    async def test_extracts_arxiv_ids_from_hn(self):
        fetcher = PaperFetcher()
        mock_resp = httpx.Response(200, json=SAMPLE_HN_RESPONSE)
        with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock, return_value=mock_resp):
            papers = await fetcher._fetch_hackernews()
        assert len(papers) == 1
        assert papers[0].arxiv_id == "2401.00050"
        assert papers[0].community_buzz == 42
        assert "hackernews" in papers[0].sources

    @pytest.mark.asyncio
    async def test_returns_empty_on_error(self):
        fetcher = PaperFetcher()
        with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock, side_effect=httpx.ConnectError("down")):
            papers = await fetcher._fetch_hackernews()
        assert papers == []


class TestFetchReddit:
    @pytest.mark.asyncio
    async def test_extracts_arxiv_ids_from_reddit(self):
        fetcher = PaperFetcher()
        mock_resp = httpx.Response(200, json=SAMPLE_REDDIT_RESPONSE)
        with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock, return_value=mock_resp):
            papers = await fetcher._fetch_reddit()
        assert len(papers) == 2
        ids = {p.arxiv_id for p in papers}
        assert "2401.00060" in ids
        assert "2401.00070" in ids

    @pytest.mark.asyncio
    async def test_returns_empty_on_error(self):
        fetcher = PaperFetcher()
        with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock, side_effect=httpx.ConnectError("down")):
            papers = await fetcher._fetch_reddit()
        assert papers == []


class TestFetchBluesky:
    @pytest.mark.asyncio
    async def test_extracts_arxiv_ids_from_bluesky(self):
        fetcher = PaperFetcher()
        mock_resp = httpx.Response(200, json=SAMPLE_BLUESKY_RESPONSE)
        with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock, return_value=mock_resp):
            papers = await fetcher._fetch_bluesky()
        assert len(papers) == 1
        assert papers[0].arxiv_id == "2401.00080"
        assert "bluesky" in papers[0].sources

    @pytest.mark.asyncio
    async def test_returns_empty_on_error(self):
        fetcher = PaperFetcher()
        with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock, side_effect=httpx.ConnectError("down")):
            papers = await fetcher._fetch_bluesky()
        assert papers == []


class TestFetchS2Trending:
    @pytest.mark.asyncio
    async def test_extracts_papers_with_arxiv_ids(self):
        fetcher = PaperFetcher()
        mock_resp = httpx.Response(200, json=SAMPLE_S2_BULK_RESPONSE)
        with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock, return_value=mock_resp):
            papers = await fetcher._fetch_s2_trending()
        assert len(papers) == 1  # only the one with ArXiv externalId
        assert papers[0].arxiv_id == "2401.00090"
        assert papers[0].s2_citation_count == 25
        assert "semantic_scholar" in papers[0].sources

    @pytest.mark.asyncio
    async def test_returns_empty_on_error(self):
        fetcher = PaperFetcher()
        with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock, side_effect=httpx.ConnectError("down")):
            papers = await fetcher._fetch_s2_trending()
        assert papers == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/papers/test_fetcher.py::TestFetchHackerNews tests/papers/test_fetcher.py::TestFetchReddit tests/papers/test_fetcher.py::TestFetchBluesky tests/papers/test_fetcher.py::TestFetchS2Trending -v`
Expected: FAIL (methods don't exist)

- [ ] **Step 3: Implement the 4 new fetch methods**

Add to `jobpulse/papers/fetcher.py`:

```python
import re
import os
import time as _time

_ARXIV_ID_RE = re.compile(r"(\d{4}\.\d{4,5})")

async def _fetch_hackernews(self) -> list[Paper]:
    """Fetch papers from HackerNews Algolia API — stories linking to arxiv.org."""
    try:
        cutoff = int(_time.time()) - 86400
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                "https://hn.algolia.com/api/v1/search_by_date",
                params={"query": "arxiv.org", "tags": "story", "numericFilters": f"created_at_i>{cutoff}"},
            )
        if resp.status_code >= 400:
            return []
        papers: list[Paper] = []
        for hit in resp.json().get("hits", []):
            url = hit.get("url", "")
            title = hit.get("title", "")
            ids = _ARXIV_ID_RE.findall(url + " " + title)
            for aid in ids[:1]:  # one paper per HN post
                papers.append(Paper(
                    arxiv_id=self._clean_arxiv_id(aid),
                    title=title, authors=[], abstract="", categories=[],
                    pdf_url=f"https://arxiv.org/pdf/{aid}",
                    arxiv_url=f"https://arxiv.org/abs/{aid}",
                    published_at="", source="huggingface",  # overwritten in dedup
                    community_buzz=hit.get("points", 0),
                    sources=["hackernews"],
                ))
        logger.info("HackerNews: %d papers", len(papers))
        return papers
    except Exception as exc:
        logger.warning("HackerNews fetch failed: %s", exc)
        return []

async def _fetch_reddit(self) -> list[Paper]:
    """Fetch papers from Reddit JSON API — r/MachineLearning + r/LocalLLaMA."""
    papers: list[Paper] = []
    for sub in ["MachineLearning", "LocalLLaMA"]:
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    f"https://www.reddit.com/r/{sub}/new.json",
                    params={"limit": 50},
                    headers={"User-Agent": _USER_AGENT},
                )
            if resp.status_code >= 400:
                continue
            for child in resp.json().get("data", {}).get("children", []):
                d = child.get("data", {})
                # Skip posts older than 48h
                if _time.time() - d.get("created_utc", 0) > 172800:
                    continue
                text = d.get("url", "") + " " + d.get("selftext", "")
                ids = _ARXIV_ID_RE.findall(text)
                for aid in ids[:1]:
                    papers.append(Paper(
                        arxiv_id=self._clean_arxiv_id(aid),
                        title=d.get("title", ""), authors=[], abstract="",
                        categories=[], pdf_url=f"https://arxiv.org/pdf/{aid}",
                        arxiv_url=f"https://arxiv.org/abs/{aid}",
                        published_at="", source="huggingface",
                        community_buzz=d.get("score", 0),
                        sources=["reddit"],
                    ))
        except Exception as exc:
            logger.warning("Reddit r/%s fetch failed: %s", sub, exc)
    logger.info("Reddit: %d papers", len(papers))
    return papers

async def _fetch_bluesky(self) -> list[Paper]:
    """Fetch papers from Bluesky public search API."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                "https://api.bsky.app/xrpc/app.bsky.feed.searchPosts",
                params={"q": "arxiv.org", "limit": 25},
            )
        if resp.status_code >= 400:
            return []
        papers: list[Paper] = []
        for post in resp.json().get("posts", []):
            text = post.get("record", {}).get("text", "")
            ids = _ARXIV_ID_RE.findall(text)
            for aid in ids[:1]:
                papers.append(Paper(
                    arxiv_id=self._clean_arxiv_id(aid),
                    title="", authors=[], abstract="", categories=[],
                    pdf_url=f"https://arxiv.org/pdf/{aid}",
                    arxiv_url=f"https://arxiv.org/abs/{aid}",
                    published_at="", source="huggingface",
                    community_buzz=10,  # flat buzz — Bluesky has no public like count
                    sources=["bluesky"],
                ))
        logger.info("Bluesky: %d papers", len(papers))
        return papers
    except Exception as exc:
        logger.warning("Bluesky fetch failed: %s", exc)
        return []

async def _fetch_s2_trending(self) -> list[Paper]:
    """Fetch recently cited AI papers from Semantic Scholar bulk search."""
    s2_key = os.environ.get("S2_API_KEY", "")
    headers: dict[str, str] = {"User-Agent": _USER_AGENT}
    if s2_key:
        headers["x-api-key"] = s2_key
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                "https://api.semanticscholar.org/graph/v1/paper/search/bulk",
                params={
                    "query": "artificial intelligence|machine learning|large language model",
                    "fields": "title,citationCount,publicationDate,externalIds",
                    "year": "2026-",
                    "minCitationCount": "1",
                    "fieldsOfStudy": "Computer Science",
                },
                headers=headers,
            )
        if resp.status_code >= 400:
            logger.warning("S2 bulk search HTTP %d", resp.status_code)
            return []
        papers: list[Paper] = []
        for item in resp.json().get("data", [])[:50]:
            ext_ids = item.get("externalIds") or {}
            arxiv_id = ext_ids.get("ArXiv", "")
            if not arxiv_id:
                continue
            arxiv_id = self._clean_arxiv_id(arxiv_id)
            papers.append(Paper(
                arxiv_id=arxiv_id,
                title=item.get("title", ""),
                authors=[], abstract="", categories=[],
                pdf_url=f"https://arxiv.org/pdf/{arxiv_id}",
                arxiv_url=f"https://arxiv.org/abs/{arxiv_id}",
                published_at=item.get("publicationDate", "") or "",
                source="huggingface",
                s2_citation_count=item.get("citationCount", 0),
                community_buzz=item.get("citationCount", 0),
                sources=["semantic_scholar"],
            ))
        logger.info("Semantic Scholar: %d papers", len(papers))
        return papers
    except Exception as exc:
        logger.warning("S2 trending fetch failed: %s", exc)
        return []
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/papers/test_fetcher.py::TestFetchHackerNews tests/papers/test_fetcher.py::TestFetchReddit tests/papers/test_fetcher.py::TestFetchBluesky tests/papers/test_fetcher.py::TestFetchS2Trending -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add jobpulse/papers/fetcher.py tests/papers/test_fetcher.py
git commit -m "feat(papers): add HN, Reddit, Bluesky, S2 trending fetchers"
```

---

### Task 4: Add arXiv RSS fallback to `PaperFetcher`

**Files:**
- Modify: `jobpulse/papers/fetcher.py`
- Test: `tests/papers/test_fetcher.py`

- [ ] **Step 1: Write failing test**

Add to `tests/papers/test_fetcher.py`:

```python
SAMPLE_ARXIV_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
         xmlns="http://purl.org/rss/1.0/">
  <item>
    <title>RSS Paper Title</title>
    <link>https://arxiv.org/abs/2401.00100</link>
  </item>
</rdf:RDF>"""


class TestFetchArxivRss:
    @pytest.mark.asyncio
    async def test_parses_rss_feed(self):
        fetcher = PaperFetcher()
        mock_resp = httpx.Response(200, text=SAMPLE_ARXIV_RSS)
        with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock, return_value=mock_resp):
            papers = await fetcher._fetch_arxiv_rss()
        assert len(papers) >= 1
        assert papers[0].arxiv_id == "2401.00100"
        assert "arxiv_rss" in papers[0].sources

    @pytest.mark.asyncio
    async def test_returns_empty_on_error(self):
        fetcher = PaperFetcher()
        with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock, side_effect=httpx.ConnectError("down")):
            papers = await fetcher._fetch_arxiv_rss()
        assert papers == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/papers/test_fetcher.py::TestFetchArxivRss -v`
Expected: FAIL

- [ ] **Step 3: Implement arXiv RSS fetcher**

Add to `jobpulse/papers/fetcher.py`:

```python
import xml.etree.ElementTree as ET

async def _fetch_arxiv_rss(self) -> list[Paper]:
    """Fallback: fetch from arXiv RSS feeds for cs.AI, cs.LG, cs.CL."""
    papers: list[Paper] = []
    for category in ["cs.AI", "cs.LG", "cs.CL"]:
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    f"https://rss.arxiv.org/rss/{category}",
                    headers={"User-Agent": _USER_AGENT},
                )
            if resp.status_code >= 400:
                continue
            root = ET.fromstring(resp.text)
            for item in root.findall(".//{http://purl.org/rss/1.0/}item"):
                link = item.findtext("{http://purl.org/rss/1.0/}link", "")
                title = item.findtext("{http://purl.org/rss/1.0/}title", "")
                ids = _ARXIV_ID_RE.findall(link)
                if ids:
                    aid = self._clean_arxiv_id(ids[0])
                    papers.append(Paper(
                        arxiv_id=aid, title=title, authors=[], abstract="",
                        categories=[category],
                        pdf_url=f"https://arxiv.org/pdf/{aid}",
                        arxiv_url=f"https://arxiv.org/abs/{aid}",
                        published_at="", source="arxiv",
                        sources=["arxiv_rss"],
                    ))
        except Exception as exc:
            logger.warning("arXiv RSS %s failed: %s", category, exc)
    logger.info("arXiv RSS fallback: %d papers", len(papers))
    return papers
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/papers/test_fetcher.py::TestFetchArxivRss -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add jobpulse/papers/fetcher.py tests/papers/test_fetcher.py
git commit -m "feat(papers): add arXiv RSS fallback fetcher"
```

---

### Task 5: Update `fetch_all()` with tiered source orchestration + dedup

**Files:**
- Modify: `jobpulse/papers/fetcher.py`
- Test: `tests/papers/test_fetcher.py`

- [ ] **Step 1: Write failing test for tiered fetch_all**

Add to `tests/papers/test_fetcher.py`:

```python
class TestFetchAllTiered:
    @pytest.mark.asyncio
    async def test_combines_all_sources(self):
        fetcher = PaperFetcher()
        hf_paper = Paper(
            arxiv_id="2401.00001", title="HF Paper", authors=["A"],
            abstract="X.", categories=[], pdf_url="", arxiv_url="",
            published_at="2026-04-01", source="huggingface", hf_upvotes=10,
            sources=["huggingface"],
        )
        hn_paper = Paper(
            arxiv_id="2401.00002", title="HN Paper", authors=[],
            abstract="", categories=[], pdf_url="", arxiv_url="",
            published_at="", sources=["hackernews"], community_buzz=30,
        )
        with patch.object(fetcher, "_fetch_arxiv", new_callable=AsyncMock, return_value=[]), \
             patch.object(fetcher, "_fetch_huggingface", new_callable=AsyncMock, return_value=[hf_paper]), \
             patch.object(fetcher, "_fetch_s2_trending", new_callable=AsyncMock, return_value=[]), \
             patch.object(fetcher, "_fetch_hackernews", new_callable=AsyncMock, return_value=[hn_paper]), \
             patch.object(fetcher, "_fetch_reddit", new_callable=AsyncMock, return_value=[]), \
             patch.object(fetcher, "_fetch_bluesky", new_callable=AsyncMock, return_value=[]):
            papers = await fetcher.fetch_all()
        assert len(papers) == 2

    @pytest.mark.asyncio
    async def test_dedup_merges_sources(self):
        fetcher = PaperFetcher()
        hf_paper = Paper(
            arxiv_id="2401.00001", title="Same Paper", authors=["A"],
            abstract="Full abstract.", categories=["cs.AI"], pdf_url="", arxiv_url="",
            published_at="2026-04-01", source="huggingface", hf_upvotes=50,
            community_buzz=50, sources=["huggingface"],
        )
        hn_paper = Paper(
            arxiv_id="2401.00001", title="Same Paper", authors=[],
            abstract="", categories=[], pdf_url="", arxiv_url="",
            published_at="", community_buzz=30, sources=["hackernews"],
        )
        with patch.object(fetcher, "_fetch_arxiv", new_callable=AsyncMock, return_value=[]), \
             patch.object(fetcher, "_fetch_huggingface", new_callable=AsyncMock, return_value=[hf_paper]), \
             patch.object(fetcher, "_fetch_s2_trending", new_callable=AsyncMock, return_value=[]), \
             patch.object(fetcher, "_fetch_hackernews", new_callable=AsyncMock, return_value=[hn_paper]), \
             patch.object(fetcher, "_fetch_reddit", new_callable=AsyncMock, return_value=[]), \
             patch.object(fetcher, "_fetch_bluesky", new_callable=AsyncMock, return_value=[]):
            papers = await fetcher.fetch_all()
        assert len(papers) == 1
        assert papers[0].community_buzz == 80  # aggregated
        assert set(papers[0].sources) == {"huggingface", "hackernews"}

    @pytest.mark.asyncio
    async def test_falls_back_to_rss_when_few_papers(self):
        fetcher = PaperFetcher()
        rss_paper = Paper(
            arxiv_id="2401.00999", title="RSS Paper", authors=[],
            abstract="", categories=["cs.AI"], pdf_url="", arxiv_url="",
            published_at="", sources=["arxiv_rss"],
        )
        with patch.object(fetcher, "_fetch_arxiv", new_callable=AsyncMock, return_value=[]), \
             patch.object(fetcher, "_fetch_huggingface", new_callable=AsyncMock, return_value=[]), \
             patch.object(fetcher, "_fetch_s2_trending", new_callable=AsyncMock, return_value=[]), \
             patch.object(fetcher, "_fetch_hackernews", new_callable=AsyncMock, return_value=[]), \
             patch.object(fetcher, "_fetch_reddit", new_callable=AsyncMock, return_value=[]), \
             patch.object(fetcher, "_fetch_bluesky", new_callable=AsyncMock, return_value=[]), \
             patch.object(fetcher, "_fetch_arxiv_rss", new_callable=AsyncMock, return_value=[rss_paper]):
            papers = await fetcher.fetch_all()
        assert len(papers) == 1
        assert papers[0].arxiv_id == "2401.00999"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/papers/test_fetcher.py::TestFetchAllTiered -v`
Expected: FAIL

- [ ] **Step 3: Rewrite `fetch_all()` and `_deduplicate_and_merge()`**

Replace `fetch_all()` in `jobpulse/papers/fetcher.py`:

```python
async def fetch_all(self, max_results: int = 50) -> list[Paper]:
    """Fetch from all sources with tiered fallback, deduplicate, and return."""
    # Tier 1 + Tier 2: all run concurrently
    arxiv_papers, hf_papers, s2_papers, hn_papers, reddit_papers, bsky_papers = (
        await asyncio.gather(
            self._fetch_arxiv(max_results=max_results),
            self._fetch_huggingface(),
            self._fetch_s2_trending(),
            self._fetch_hackernews(),
            self._fetch_reddit(),
            self._fetch_bluesky(),
        )
    )

    all_papers = arxiv_papers + hf_papers + s2_papers + hn_papers + reddit_papers + bsky_papers
    merged = self._deduplicate_and_merge_all(all_papers)

    # Tier 3: fallback if < 5 unique papers
    if len(merged) < 5:
        logger.warning("Only %d papers from Tiers 1+2, falling back to arXiv RSS", len(merged))
        rss_papers = await self._fetch_arxiv_rss()
        existing_ids = {p.arxiv_id for p in merged}
        for p in rss_papers:
            if p.arxiv_id not in existing_ids:
                merged.append(p)
                existing_ids.add(p.arxiv_id)

    logger.info("fetch_all: %d unique papers from all sources", len(merged))
    return merged
```

Add new dedup method that handles the `sources` and `community_buzz` aggregation:

```python
def _deduplicate_and_merge_all(self, papers: list[Paper]) -> list[Paper]:
    """Deduplicate by arxiv_id, aggregating community_buzz and sources."""
    seen: dict[str, Paper] = {}
    for paper in papers:
        aid = paper.arxiv_id
        if not aid:
            continue
        if aid in seen:
            existing = seen[aid]
            # Aggregate community buzz
            new_buzz = existing.community_buzz + paper.community_buzz
            # Merge sources
            new_sources = list(set(existing.sources + paper.sources))
            # Keep the richer version (longer abstract, more authors)
            base = existing if len(existing.abstract) >= len(paper.abstract) else paper
            seen[aid] = base.model_copy(update={
                "community_buzz": new_buzz,
                "sources": new_sources,
                "hf_upvotes": max(existing.hf_upvotes or 0, paper.hf_upvotes or 0) or None,
                "linked_models": existing.linked_models or paper.linked_models,
                "linked_datasets": existing.linked_datasets or paper.linked_datasets,
                "s2_citation_count": max(existing.s2_citation_count, paper.s2_citation_count),
                "s2_influential_citations": max(existing.s2_influential_citations, paper.s2_influential_citations),
                "source": "both" if len(new_sources) > 1 else base.source,
            })
        else:
            seen[aid] = paper
    return list(seen.values())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/papers/test_fetcher.py::TestFetchAllTiered -v`
Expected: All PASS

- [ ] **Step 5: Run ALL fetcher tests**

Run: `pytest tests/papers/test_fetcher.py -v`
Expected: All PASS. Note: `TestFetchAll` and `TestDeduplication` tests may need updating since `fetch_all` signature changed. Update them to mock the new source methods too (return `[]` for each).

- [ ] **Step 6: Commit**

```bash
git add jobpulse/papers/fetcher.py tests/papers/test_fetcher.py
git commit -m "feat(papers): tiered fetch_all with source aggregation and RSS fallback"
```

---

### Task 6: Add enrichment methods (GitHub + HF datasets + S2 details)

**Files:**
- Modify: `jobpulse/papers/fetcher.py`
- Test: `tests/papers/test_fetcher.py`

- [ ] **Step 1: Write failing tests for enrichment**

Add to `tests/papers/test_fetcher.py`:

```python
class TestEnrichGithub:
    @pytest.mark.asyncio
    async def test_extracts_github_url_from_abstract(self):
        fetcher = PaperFetcher()
        paper = Paper(
            arxiv_id="2401.00001", title="Test", authors=["A"],
            abstract="Code at https://github.com/org/repo available.",
            categories=["cs.AI"], pdf_url="", arxiv_url="", published_at="2026-04-01",
        )
        result = await fetcher._enrich_github([paper])
        assert result[0].github_url == "https://github.com/org/repo"

    @pytest.mark.asyncio
    async def test_searches_github_api_when_no_url_in_abstract(self):
        fetcher = PaperFetcher()
        paper = Paper(
            arxiv_id="2401.00001", title="Test", authors=["A"],
            abstract="No github link here.",
            categories=["cs.AI"], pdf_url="", arxiv_url="", published_at="2026-04-01",
        )
        gh_resp = httpx.Response(200, json={"items": [{"html_url": "https://github.com/found/repo", "stargazers_count": 42}]})
        with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock, return_value=gh_resp):
            result = await fetcher._enrich_github([paper])
        assert result[0].github_url == "https://github.com/found/repo"
        assert result[0].github_stars == 42

    @pytest.mark.asyncio
    async def test_skips_on_api_error(self):
        fetcher = PaperFetcher()
        paper = Paper(
            arxiv_id="2401.00001", title="Test", authors=["A"],
            abstract="No link.", categories=["cs.AI"], pdf_url="", arxiv_url="", published_at="2026-04-01",
        )
        with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock, side_effect=httpx.ConnectError("down")):
            result = await fetcher._enrich_github([paper])
        assert result[0].github_url == ""
        assert result[0].github_stars == 0


class TestEnrichS2:
    @pytest.mark.asyncio
    async def test_enriches_abstract_and_citations(self):
        fetcher = PaperFetcher()
        paper = Paper(
            arxiv_id="2401.00001", title="Test", authors=[],
            abstract="", categories=[], pdf_url="", arxiv_url="", published_at="2026-04-01",
        )
        s2_resp = httpx.Response(200, json={
            "abstract": "Enriched abstract from S2.",
            "citationCount": 25,
            "influentialCitationCount": 3,
            "authors": [{"name": "Alice"}, {"name": "Bob"}],
            "year": 2026,
        })
        with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock, return_value=s2_resp):
            result = await fetcher._enrich_s2([paper])
        assert result[0].abstract == "Enriched abstract from S2."
        assert result[0].s2_citation_count == 25
        assert result[0].s2_influential_citations == 3
        assert result[0].authors == ["Alice", "Bob"]


class TestFetchLinkedDatasets:
    @pytest.mark.asyncio
    async def test_fetches_datasets(self):
        fetcher = PaperFetcher()
        mock_resp = httpx.Response(200, json=[{"id": "dataset-1"}, {"id": "dataset-2"}])
        with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock, return_value=mock_resp):
            datasets = await fetcher._fetch_linked_datasets("2401.00001")
        assert datasets == ["dataset-1", "dataset-2"]

    @pytest.mark.asyncio
    async def test_returns_empty_on_error(self):
        fetcher = PaperFetcher()
        with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock, side_effect=httpx.ConnectError("down")):
            datasets = await fetcher._fetch_linked_datasets("2401.00001")
        assert datasets == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/papers/test_fetcher.py::TestEnrichGithub tests/papers/test_fetcher.py::TestEnrichS2 tests/papers/test_fetcher.py::TestFetchLinkedDatasets -v`
Expected: FAIL

- [ ] **Step 3: Implement enrichment methods**

Add to `jobpulse/papers/fetcher.py`:

```python
_GITHUB_URL_RE = re.compile(r"https?://github\.com/[\w\-]+/[\w\-]+")

async def enrich(self, papers: list[Paper]) -> list[Paper]:
    """Run all enrichment: S2 details, GitHub repos, HF datasets."""
    papers = await self._enrich_s2(papers)
    papers = await self._enrich_github(papers[:30])  # top 30 only for GitHub
    papers = await self._enrich_hf_extras(papers)
    return papers

async def _enrich_s2(self, papers: list[Paper]) -> list[Paper]:
    """Enrich papers with Semantic Scholar citation data and abstracts."""
    s2_key = os.environ.get("S2_API_KEY", "")
    headers: dict[str, str] = {"User-Agent": _USER_AGENT}
    if s2_key:
        headers["x-api-key"] = s2_key
    delay = 0.15 if s2_key else 0.2

    enriched: list[Paper] = []
    async with httpx.AsyncClient(timeout=10) as client:
        for paper in papers[:60]:
            try:
                resp = await client.get(
                    f"https://api.semanticscholar.org/graph/v1/paper/ARXIV:{paper.arxiv_id}",
                    params={"fields": "title,abstract,citationCount,influentialCitationCount,authors,year"},
                    headers=headers,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    updates: dict = {}
                    if not paper.abstract and data.get("abstract"):
                        updates["abstract"] = data["abstract"]
                    if not paper.authors and data.get("authors"):
                        updates["authors"] = [a.get("name", "") for a in data["authors"]][:5]
                    updates["s2_citation_count"] = max(paper.s2_citation_count, data.get("citationCount", 0))
                    updates["s2_influential_citations"] = max(
                        paper.s2_influential_citations, data.get("influentialCitationCount", 0)
                    )
                    enriched.append(paper.model_copy(update=updates))
                else:
                    enriched.append(paper)
                await asyncio.sleep(delay)
            except Exception:
                enriched.append(paper)
    # Add un-enriched papers beyond the cap
    enriched.extend(papers[60:])
    return enriched

async def _enrich_github(self, papers: list[Paper]) -> list[Paper]:
    """Enrich papers with GitHub repo URL and star count."""
    gh_token = os.environ.get("GITHUB_TOKEN", "")
    headers: dict[str, str] = {"User-Agent": _USER_AGENT, "Accept": "application/vnd.github.v3+json"}
    if gh_token:
        headers["Authorization"] = f"token {gh_token}"

    enriched: list[Paper] = []
    async with httpx.AsyncClient(timeout=10) as client:
        for paper in papers:
            # Strategy 1: extract from abstract
            match = _GITHUB_URL_RE.search(paper.abstract)
            if match:
                enriched.append(paper.model_copy(update={"github_url": match.group(0)}))
                continue
            # Strategy 2: GitHub search API
            try:
                resp = await client.get(
                    "https://api.github.com/search/repositories",
                    params={"q": paper.arxiv_id, "per_page": 1},
                    headers=headers,
                )
                if resp.status_code == 200:
                    items = resp.json().get("items", [])
                    if items:
                        enriched.append(paper.model_copy(update={
                            "github_url": items[0].get("html_url", ""),
                            "github_stars": items[0].get("stargazers_count", 0),
                        }))
                        continue
            except Exception:
                pass
            enriched.append(paper)
    return enriched

async def _enrich_hf_extras(self, papers: list[Paper]) -> list[Paper]:
    """Fetch linked models and datasets for papers missing them."""
    enriched: list[Paper] = []
    for paper in papers:
        updates: dict = {}
        if not paper.linked_models:
            updates["linked_models"] = await self._fetch_linked_models(paper.arxiv_id)
        if not paper.linked_datasets:
            updates["linked_datasets"] = await self._fetch_linked_datasets(paper.arxiv_id)
        enriched.append(paper.model_copy(update=updates) if updates else paper)
    return enriched

async def _fetch_linked_datasets(self, arxiv_id: str) -> list[str]:
    """Return dataset IDs on HuggingFace Hub that cite this paper."""
    url = "https://huggingface.co/api/datasets"
    params = {"search": arxiv_id, "limit": 5}
    headers = {"User-Agent": _USER_AGENT}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, params=params, headers=headers)
        if resp.status_code >= 400:
            return []
        datasets = resp.json()
        return [d["id"] for d in datasets if d.get("id")]
    except Exception:
        return []
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/papers/test_fetcher.py::TestEnrichGithub tests/papers/test_fetcher.py::TestEnrichS2 tests/papers/test_fetcher.py::TestFetchLinkedDatasets -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add jobpulse/papers/fetcher.py tests/papers/test_fetcher.py
git commit -m "feat(papers): add S2, GitHub, HF enrichment pipeline"
```

---

### Task 7: Rebalance `fast_score` in ranker

**Files:**
- Modify: `jobpulse/papers/ranker.py:13-91`
- Test: `tests/papers/test_ranker.py`

- [ ] **Step 1: Write failing tests for new scoring**

Add to `tests/papers/test_ranker.py`, updating `make_paper` first to accept new fields:

```python
# Update make_paper to accept new fields:
def make_paper(
    arxiv_id: str = "2401.00001",
    title: str = "Test Paper",
    authors: list[str] | None = None,
    abstract: str = "An abstract.",
    categories: list[str] | None = None,
    hf_upvotes: int | None = None,
    linked_models: list[str] | None = None,
    linked_datasets: list[str] | None = None,
    github_url: str = "",
    github_stars: int = 0,
    s2_citation_count: int = 0,
    community_buzz: int = 0,
    sources: list[str] | None = None,
) -> Paper:
    return Paper(
        arxiv_id=arxiv_id,
        title=title,
        authors=authors or ["Alice", "Bob", "Charlie"],
        abstract=abstract,
        categories=categories or ["cs.AI"],
        pdf_url=f"https://arxiv.org/pdf/{arxiv_id}",
        arxiv_url=f"https://arxiv.org/abs/{arxiv_id}",
        published_at="2026-04-01",
        hf_upvotes=hf_upvotes,
        linked_models=linked_models or [],
        linked_datasets=linked_datasets or [],
        github_url=github_url,
        github_stars=github_stars,
        s2_citation_count=s2_citation_count,
        community_buzz=community_buzz,
        sources=sources or [],
    )


class TestFastScoreV2:
    def test_community_buzz_high(self):
        base = make_paper(community_buzz=0)
        high = make_paper(community_buzz=150)
        assert fast_score(high) > fast_score(base)

    def test_s2_citations_boost(self):
        base = make_paper(s2_citation_count=0)
        cited = make_paper(s2_citation_count=25)
        assert fast_score(cited) > fast_score(base)

    def test_github_repo_boost(self):
        base = make_paper(github_url="")
        with_repo = make_paper(github_url="https://github.com/org/repo", github_stars=100)
        assert fast_score(with_repo) > fast_score(base)

    def test_multi_source_bonus(self):
        one = make_paper(sources=["huggingface"])
        three = make_paper(sources=["huggingface", "hackernews", "reddit"])
        assert fast_score(three) > fast_score(one)

    def test_linked_datasets_boost(self):
        base = make_paper(linked_datasets=[])
        with_ds = make_paper(linked_datasets=["ds-1"])
        assert fast_score(with_ds) > fast_score(base)

    def test_max_still_capped_at_10(self):
        paper = make_paper(
            categories=["cs.AI"], hf_upvotes=100, linked_models=["m1", "m2"],
            linked_datasets=["d1"], github_url="https://github.com/x/y",
            github_stars=100, s2_citation_count=30, community_buzz=200,
            sources=["huggingface", "hackernews", "reddit"],
        )
        assert fast_score(paper) <= 10.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/papers/test_ranker.py::TestFastScoreV2 -v`
Expected: FAIL (new fields not used in scoring yet)

- [ ] **Step 3: Rewrite `fast_score` and `_CATEGORY_WEIGHTS`**

Replace in `jobpulse/papers/ranker.py`:

```python
_CATEGORY_WEIGHTS: dict[str, float] = {
    "cs.AI": 2.0,
    "cs.LG": 2.0,
    "cs.CL": 1.5,
    "stat.ML": 1.5,
    "cs.MA": 1.0,
}


def fast_score(paper: Paper) -> float:
    """Deterministic score for a paper. Maximum possible value is 10.0.

    Scoring breakdown:
    - Category bonus       : up to 2.0
    - Community buzz       : up to 2.0
    - HF upvotes           : up to 1.5
    - S2 citations         : up to 1.5
    - GitHub repo          : up to 1.0
    - Linked models/datasets: up to 1.0
    - Multi-source bonus   : up to 0.5
    - Recency              : 0.5
    """
    score = 0.0

    # Category bonus — best matching weight
    cat_bonus = max((_CATEGORY_WEIGHTS.get(c, 0.0) for c in paper.categories), default=0.0)
    score += cat_bonus

    # Community buzz (aggregated across sources)
    buzz = paper.community_buzz
    if buzz > 100:
        score += 2.0
    elif buzz > 50:
        score += 1.5
    elif buzz > 20:
        score += 1.0
    elif buzz > 5:
        score += 0.5

    # HF upvotes
    if paper.hf_upvotes is not None:
        if paper.hf_upvotes > 50:
            score += 1.5
        elif paper.hf_upvotes > 20:
            score += 1.0
        elif paper.hf_upvotes > 5:
            score += 0.5

    # S2 citations
    cites = paper.s2_citation_count
    if cites > 20:
        score += 1.5
    elif cites > 10:
        score += 1.0
    elif cites > 3:
        score += 0.5

    # GitHub repo
    if paper.github_url:
        score += 0.5
        if paper.github_stars > 50:
            score += 0.5

    # Linked models/datasets (0.5 each, capped at 1.0)
    model_ds_score = 0.0
    if paper.linked_models:
        model_ds_score += 0.5
    if paper.linked_datasets:
        model_ds_score += 0.5
    score += min(model_ds_score, 1.0)

    # Multi-source bonus
    n_sources = len(paper.sources)
    if n_sources >= 3:
        score += 0.5
    elif n_sources >= 2:
        score += 0.25

    # Recency bonus
    score += 0.5

    return min(score, 10.0)
```

- [ ] **Step 4: Run new tests**

Run: `pytest tests/papers/test_ranker.py::TestFastScoreV2 -v`
Expected: All PASS

- [ ] **Step 5: Update old `TestFastScore` tests for new weights**

The old tests assert specific point values based on old weights (cs.AI=3.0, etc.). Update the assertions to match the new weights. Key changes:
- `test_cs_ai_gets_category_bonus`: cs.AI=2.0 now, not 3.0. `score >= 3.0` (2.0 cat + 0.5 recency + something)
- `test_hf_upvotes_over_50_boost`: now gives 1.5, not 2.0
- `test_hf_upvotes_21_to_50_boost`: now gives 1.0 (same)
- `test_linked_models_boost`: now gives 0.5 for any models (not 1.0 per model)

Update each assertion to match the new scoring formula. Run:

Run: `pytest tests/papers/test_ranker.py::TestFastScore -v`
Expected: All PASS after updates

- [ ] **Step 6: Run all ranker tests**

Run: `pytest tests/papers/test_ranker.py -v`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add jobpulse/papers/ranker.py tests/papers/test_ranker.py
git commit -m "feat(papers): rebalance fast_score with community, S2, GitHub signals"
```

---

### Task 8: Update digest formatting with new signals

**Files:**
- Modify: `jobpulse/papers/digest.py`
- Test: `tests/papers/test_digest.py`

- [ ] **Step 1: Write failing tests for new digest lines**

Add to `tests/papers/test_digest.py`:

```python
class TestDailyFormatNewSignals:
    def test_includes_github_link(self):
        papers = [_make_ranked("2401.00001", "Test", 8.0, github_url="https://github.com/org/repo", github_stars=42)]
        result = DigestBuilder().format_daily(papers)
        assert "github.com" in result
        assert "42" in result

    def test_includes_s2_citations(self):
        papers = [_make_ranked("2401.00001", "Test", 8.0, s2_citation_count=25)]
        result = DigestBuilder().format_daily(papers)
        assert "25" in result
        assert "cit" in result.lower()

    def test_includes_source_attribution(self):
        papers = [_make_ranked("2401.00001", "Test", 8.0, sources=["huggingface", "hackernews", "reddit"])]
        result = DigestBuilder().format_daily(papers)
        assert "HuggingFace" in result or "huggingface" in result
```

Note: `_make_ranked` needs to accept the new kwargs. Update it at the top of the test file to pass `**kwargs` through to `RankedPaper`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/papers/test_digest.py::TestDailyFormatNewSignals -v`
Expected: FAIL

- [ ] **Step 3: Update `_format_daily_entry` in digest.py**

Add these sections to `_format_daily_entry` after the HuggingFace signals block:

```python
        # S2 citations
        if paper.s2_citation_count > 0:
            parts.append(f"   📊 {paper.s2_citation_count} citations")

        # Source attribution
        if paper.sources:
            source_names = {"huggingface": "HuggingFace", "hackernews": "HackerNews",
                           "reddit": "Reddit", "bluesky": "Bluesky",
                           "semantic_scholar": "Semantic Scholar", "arxiv_rss": "arXiv",
                           "arxiv": "arXiv"}
            names = [source_names.get(s, s) for s in paper.sources]
            parts.append(f"   📡 Found on: {', '.join(names)}")
```

Update the links section to include GitHub:

```python
        # Links
        links: list[str] = []
        if paper.arxiv_url:
            links.append(f"[arXiv]({paper.arxiv_url})")
        if paper.pdf_url:
            links.append(f"[PDF]({paper.pdf_url})")
        if paper.github_url:
            gh_label = f"GitHub ⭐{paper.github_stars}" if paper.github_stars else "GitHub"
            links.append(f"[{gh_label}]({paper.github_url})")
        if links:
            parts.append(f"   {' · '.join(links)}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/papers/test_digest.py::TestDailyFormatNewSignals -v`
Expected: All PASS

- [ ] **Step 5: Run all digest tests**

Run: `pytest tests/papers/test_digest.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add jobpulse/papers/digest.py tests/papers/test_digest.py
git commit -m "feat(papers): add GitHub, S2, source attribution to digest format"
```

---

### Task 9: Wire enrichment into `PapersPipeline.daily_digest()`

**Files:**
- Modify: `jobpulse/papers/__init__.py:47-55`
- Test: `tests/papers/test_pipeline.py`

- [ ] **Step 1: Write failing test**

Add to `tests/papers/test_pipeline.py`:

```python
class TestDailyDigestEnrichment:
    @pytest.mark.asyncio
    async def test_calls_enrich_between_fetch_and_rank(self):
        from unittest.mock import patch, MagicMock, AsyncMock
        from jobpulse.papers import PapersPipeline
        from jobpulse.papers.models import Paper, RankedPaper

        paper = Paper(
            arxiv_id="2401.00001", title="Test", authors=["A"],
            abstract="X.", categories=["cs.AI"], pdf_url="", arxiv_url="",
            published_at="2026-04-01",
        )
        ranked = RankedPaper(**paper.model_dump(), fast_score=5.0, summary="Summary.")

        pipeline = PapersPipeline(db_path=tmp_path / "papers.db")

        with patch.object(pipeline.fetcher, "fetch_all", new_callable=AsyncMock, return_value=[paper]), \
             patch.object(pipeline.fetcher, "enrich", new_callable=AsyncMock, return_value=[paper]) as mock_enrich, \
             patch.object(pipeline.ranker, "llm_rank", return_value=[ranked]), \
             patch.object(pipeline.ranker, "summarize_and_verify", return_value=[ranked]), \
             patch.object(pipeline.store, "store"), \
             patch.object(pipeline.notion, "publish_daily"):
            result = await pipeline.daily_digest()

        mock_enrich.assert_called_once()
        assert "Test" in result
```

Note: This test needs `tmp_path` — add it as a parameter to the test function.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/papers/test_pipeline.py::TestDailyDigestEnrichment -v`
Expected: FAIL (enrich not called in daily_digest)

- [ ] **Step 3: Add enrichment step to `daily_digest()`**

Update `daily_digest` in `jobpulse/papers/__init__.py`:

```python
async def daily_digest(self, top_n: int = 5) -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    papers = await self.fetcher.fetch_all()
    logger.info("Fetched %d papers from all sources", len(papers))
    papers = await self.fetcher.enrich(papers)
    logger.info("Enriched %d papers with S2/GitHub/HF data", len(papers))
    ranked = self.ranker.llm_rank(papers, top_n=top_n)
    verified = self.ranker.summarize_and_verify(ranked)
    self.store.store(verified, digest_date=today)
    self.notion.publish_daily(verified, today)
    return self.digest.format_daily(verified, digest_date=today)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/papers/test_pipeline.py::TestDailyDigestEnrichment -v`
Expected: PASS

- [ ] **Step 5: Run all pipeline tests**

Run: `pytest tests/papers/test_pipeline.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add jobpulse/papers/__init__.py tests/papers/test_pipeline.py
git commit -m "feat(papers): wire enrichment into daily_digest pipeline"
```

---

### Task 10: Rewire `build_digest()` in `arxiv_agent.py`

**Files:**
- Modify: `jobpulse/arxiv_agent.py:601-720`
- Test: `tests/papers/test_pipeline.py` (integration)

- [ ] **Step 1: Write test for the new build_digest wrapper**

Add to `tests/papers/test_pipeline.py`:

```python
class TestBuildDigestWrapper:
    def test_build_digest_calls_pipeline(self):
        from unittest.mock import patch, AsyncMock

        with patch("jobpulse.papers.PapersPipeline") as MockPipeline:
            mock_instance = MockPipeline.return_value
            mock_instance.daily_digest = AsyncMock(return_value="📄 *Daily AI Papers*\n\n1. Test Paper")
            from jobpulse.arxiv_agent import build_digest
            result = build_digest(top_n=5)

        assert "Daily AI Papers" in result or "Test Paper" in result
        mock_instance.daily_digest.assert_called_once_with(top_n=5)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/papers/test_pipeline.py::TestBuildDigestWrapper -v`
Expected: FAIL (build_digest still uses old pipeline)

- [ ] **Step 3: Replace `build_digest()` body**

In `jobpulse/arxiv_agent.py`, replace the `build_digest` function body (lines ~601-720) with:

```python
def build_digest(top_n: int = 5) -> str:
    """Full pipeline: fetch from all sources -> enrich -> rank -> summarize -> format."""
    import asyncio
    from jobpulse.papers import PapersPipeline

    pipeline = PapersPipeline()
    try:
        return asyncio.run(pipeline.daily_digest(top_n=top_n))
    except RuntimeError:
        # Event loop already running (e.g., inside async context)
        loop = asyncio.get_event_loop()
        return loop.run_until_complete(pipeline.daily_digest(top_n=top_n))
```

Keep the old `build_digest` body commented out or in a `_build_digest_legacy` function for reference, in case you need to roll back.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/papers/test_pipeline.py::TestBuildDigestWrapper -v`
Expected: PASS

- [ ] **Step 5: Run the full paper test suite**

Run: `pytest tests/papers/ -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add jobpulse/arxiv_agent.py tests/papers/test_pipeline.py
git commit -m "feat(papers): rewire build_digest to use PapersPipeline"
```

---

### Task 11: End-to-end smoke test

**Files:**
- Test: `tests/papers/test_integration.py`

- [ ] **Step 1: Write integration test**

Add to `tests/papers/test_integration.py`:

```python
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from jobpulse.papers import PapersPipeline
from jobpulse.papers.models import Paper


class TestEndToEndDigest:
    @pytest.mark.asyncio
    async def test_full_pipeline_with_mocked_sources(self, tmp_path):
        """Smoke test: fetch → enrich → rank → summarize → store → format."""
        paper = Paper(
            arxiv_id="2401.00001", title="Novel Transformer",
            authors=["Alice", "Bob", "Charlie"],
            abstract="We propose a novel transformer that improves efficiency. Code at https://github.com/org/repo.",
            categories=["cs.AI", "cs.LG"],
            pdf_url="https://arxiv.org/pdf/2401.00001",
            arxiv_url="https://arxiv.org/abs/2401.00001",
            published_at="2026-04-01",
            source="both", hf_upvotes=60, community_buzz=80,
            sources=["huggingface", "hackernews"],
            s2_citation_count=15,
        )

        pipeline = PapersPipeline(db_path=tmp_path / "papers.db")

        # Mock external calls
        with patch.object(pipeline.fetcher, "fetch_all", new_callable=AsyncMock, return_value=[paper]), \
             patch.object(pipeline.fetcher, "enrich", new_callable=AsyncMock, return_value=[paper]), \
             patch("jobpulse.papers.ranker._get_openai_client", return_value=None), \
             patch.object(pipeline.notion, "publish_daily"):
            result = await pipeline.daily_digest(top_n=1)

        # Verify output format
        assert "Novel Transformer" in result
        assert "arxiv.org" in result
        assert "Daily" in result or "paper" in result.lower()

        # Verify stored in DB
        stored = pipeline.store.get_by_arxiv_id("2401.00001")
        assert stored is not None
        assert stored.title == "Novel Transformer"
        assert stored.community_buzz == 80
```

- [ ] **Step 2: Run integration test**

Run: `pytest tests/papers/test_integration.py::TestEndToEndDigest -v`
Expected: PASS

- [ ] **Step 3: Run the FULL test suite**

Run: `pytest tests/papers/ -v`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add tests/papers/test_integration.py
git commit -m "test(papers): add end-to-end smoke test for pipeline overhaul"
```

---

### Task 12: Clean up and final verification

**Files:**
- No new files

- [ ] **Step 1: Run full paper test suite with coverage**

Run: `pytest tests/papers/ -v --tb=short`
Expected: All PASS

- [ ] **Step 2: Quick manual test — run build_digest**

Run: `python3 -c "from jobpulse.arxiv_agent import build_digest; print(build_digest(top_n=2)[:500])"`
Expected: Prints the first 500 chars of a formatted digest with papers from community sources. Should see source attribution, GitHub links (if any), and S2 citation counts.

- [ ] **Step 3: Verify no import errors across the codebase**

Run: `python3 -c "from jobpulse.papers import PapersPipeline; from jobpulse.papers.fetcher import PaperFetcher; from jobpulse.papers.ranker import fast_score; print('All imports OK')"`
Expected: "All imports OK"

- [ ] **Step 4: Final commit — update old TestFetchAll if needed**

If `TestFetchAll` or `TestDeduplication` in `test_fetcher.py` are still failing because `fetch_all` now calls more methods, update them to mock the new source methods returning `[]`. Then:

```bash
git add -u
git commit -m "chore(papers): fix test compatibility after pipeline overhaul"
```
