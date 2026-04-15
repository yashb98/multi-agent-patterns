"""Async PaperFetcher — arXiv + HuggingFace sources fetched in parallel."""

from __future__ import annotations

import asyncio
import os
import re
import time as _time
import xml.etree.ElementTree as ET
from typing import Any

import httpx

from jobpulse.papers.models import Paper
from shared.logging_config import get_logger

logger = get_logger(__name__)

# arXiv Atom feed namespace
_ATOM_NS = "http://www.w3.org/2005/Atom"

# Regex to extract bare arXiv IDs from arbitrary text
_ARXIV_ID_RE = re.compile(r"(\d{4}\.\d{4,5})")

# Target AI/ML categories
ARXIV_CATEGORIES = ["cs.AI", "cs.LG", "cs.CL", "cs.MA", "stat.ML"]

# arXiv requires a descriptive User-Agent — raw requests get rate-limited
_USER_AGENT = "JobPulse-PaperFetcher/1.0 (https://github.com/yashb98/multi-agent-patterns)"


class PaperFetcher:
    """Fetches papers from arXiv and HuggingFace Daily Papers in parallel."""

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    async def fetch_all(self, max_results: int = 50) -> list[Paper]:
        """Fetch from both sources concurrently and return deduplicated list."""
        arxiv_papers, hf_papers = await asyncio.gather(
            self._fetch_arxiv(max_results=max_results),
            self._fetch_huggingface(),
        )
        return self._deduplicate_and_merge(arxiv_papers, hf_papers)

    async def fetch_missed(self, dates: list[str]) -> list[Paper]:
        """Fetch papers for a list of missed dates by re-running fetch_all.

        Dates are provided as YYYY-MM-DD strings. Since arXiv does not offer
        reliable per-date filtering without the export API, this performs a
        fresh fetch_all and returns the results.  An empty date list returns
        an empty list immediately.
        """
        if not dates:
            return []
        return await self.fetch_all()

    # ------------------------------------------------------------------ #
    # arXiv                                                                #
    # ------------------------------------------------------------------ #

    async def _fetch_arxiv(self, max_results: int = 50) -> list[Paper]:
        """Fetch papers from the arXiv Atom API.

        Always HTTPS — HTTP causes a 301 redirect that burns rate-limit quota
        (see mistakes.md 2026-03-30).
        """
        categories_query = " OR ".join(f"cat:{c}" for c in ARXIV_CATEGORIES)
        url = "https://export.arxiv.org/api/query"
        params = {
            "search_query": categories_query,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
            "max_results": max_results,
        }
        headers = {"User-Agent": _USER_AGENT}

        for attempt in range(1, 4):  # 3 attempts with backoff
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    response = await client.get(url, params=params, headers=headers)
                if response.status_code == 429:
                    wait = attempt * 5
                    logger.warning(
                        "arXiv 429 rate-limit, attempt %d/3, waiting %ds", attempt, wait
                    )
                    await asyncio.sleep(wait)
                    continue
                if response.status_code >= 400:
                    logger.error("arXiv HTTP error %d", response.status_code)
                    return []
                return self._parse_arxiv_xml(response.text)
            except (httpx.ConnectError, httpx.TimeoutException) as exc:
                logger.error("arXiv network error (attempt %d): %s", attempt, exc)
                if attempt == 3:
                    return []
                await asyncio.sleep(attempt * 5)
            except ET.ParseError as exc:
                logger.error("arXiv XML parse error: %s", exc)
                return []

        return []

    def _parse_arxiv_xml(self, xml_text: str) -> list[Paper]:
        """Parse arXiv Atom XML into Paper objects."""
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as exc:
            logger.error("Failed to parse arXiv XML: %s", exc)
            return []

        papers: list[Paper] = []
        for entry in root.findall(f"{{{_ATOM_NS}}}entry"):
            try:
                paper = self._entry_to_paper(entry)
                if paper:
                    papers.append(paper)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Skipping malformed arXiv entry: %s", exc)

        logger.info("Parsed %d papers from arXiv XML", len(papers))
        return papers

    def _entry_to_paper(self, entry: ET.Element) -> Paper | None:
        """Convert a single <entry> element to a Paper."""
        # ID — strip version suffix (v1, v2, …) and extract bare ID
        raw_id = (entry.findtext(f"{{{_ATOM_NS}}}id") or "").strip()
        arxiv_id = self._clean_arxiv_id(raw_id)
        if not arxiv_id:
            return None

        title = (entry.findtext(f"{{{_ATOM_NS}}}title") or "").strip().replace("\n", " ")
        abstract = (entry.findtext(f"{{{_ATOM_NS}}}summary") or "").strip().replace("\n", " ")
        published_at = (entry.findtext(f"{{{_ATOM_NS}}}published") or "")[:10]

        # Authors — cap at 5
        authors = [
            (a.findtext(f"{{{_ATOM_NS}}}name") or "").strip()
            for a in entry.findall(f"{{{_ATOM_NS}}}author")
        ][:5]

        # Categories
        categories = [
            cat.get("term", "")
            for cat in entry.findall(f"{{{_ATOM_NS}}}category")
            if cat.get("term")
        ]

        # PDF URL
        pdf_url = ""
        for link in entry.findall(f"{{{_ATOM_NS}}}link"):
            if link.get("title") == "pdf" or link.get("type") == "application/pdf":
                pdf_url = link.get("href", "")
                break

        arxiv_url = f"https://arxiv.org/abs/{arxiv_id}"

        return Paper(
            arxiv_id=arxiv_id,
            title=title,
            authors=authors,
            abstract=abstract,
            categories=categories,
            pdf_url=pdf_url,
            arxiv_url=arxiv_url,
            published_at=published_at,
            source="arxiv",
        )

    # ------------------------------------------------------------------ #
    # HuggingFace                                                          #
    # ------------------------------------------------------------------ #

    async def _fetch_huggingface(self) -> list[Paper]:
        """Fetch from HuggingFace Daily Papers API and enrich with linked models."""
        url = "https://huggingface.co/api/daily_papers"
        headers = {"User-Agent": _USER_AGENT}

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(url, headers=headers)
            if response.status_code >= 400:
                logger.error("HuggingFace HTTP error %d", response.status_code)
                return []
            data: list[dict[str, Any]] = response.json()
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            logger.error("HuggingFace network error: %s", exc)
            return []
        except Exception as exc:  # noqa: BLE001
            logger.error("HuggingFace unexpected error: %s", exc)
            return []

        papers: list[Paper] = []
        for item in data:
            try:
                paper = await self._hf_item_to_paper(item)
                if paper:
                    papers.append(paper)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Skipping malformed HF entry: %s", exc)

        logger.info("Fetched %d papers from HuggingFace", len(papers))
        return papers

    async def _hf_item_to_paper(self, item: dict[str, Any]) -> Paper | None:
        """Convert a HuggingFace Daily Papers item into a Paper."""
        raw = item.get("paper", {})
        arxiv_id = self._clean_arxiv_id(raw.get("id", ""))
        if not arxiv_id:
            return None

        title = (raw.get("title") or "").strip()
        abstract = (raw.get("summary") or raw.get("abstract") or "").strip()
        authors = [
            (a.get("name") or "").strip()
            for a in (raw.get("authors") or [])
        ][:5]
        upvotes: int = item.get("numUpvotes", 0)

        # Look up linked models on HuggingFace Hub
        linked_models = await self._fetch_linked_models(arxiv_id)

        return Paper(
            arxiv_id=arxiv_id,
            title=title,
            authors=authors,
            abstract=abstract,
            categories=[],
            pdf_url=f"https://arxiv.org/pdf/{arxiv_id}",
            arxiv_url=f"https://arxiv.org/abs/{arxiv_id}",
            published_at="",
            source="huggingface",
            hf_upvotes=upvotes,
            linked_models=linked_models,
        )

    async def _fetch_linked_models(self, arxiv_id: str) -> list[str]:
        """Return model IDs on HuggingFace Hub that cite this paper."""
        url = "https://huggingface.co/api/models"
        params = {"search": arxiv_id, "limit": 5}
        headers = {"User-Agent": _USER_AGENT}

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(url, params=params, headers=headers)
            if response.status_code >= 400:
                return []
            models: list[dict[str, Any]] = response.json()
            return [m["id"] for m in models if m.get("id")]
        except Exception as exc:  # noqa: BLE001
            logger.debug("Could not fetch linked models for %s: %s", arxiv_id, exc)
            return []

    # ------------------------------------------------------------------ #
    # Community sources                                                    #
    # ------------------------------------------------------------------ #

    async def _fetch_hackernews(self) -> list[Paper]:
        """Fetch papers from HackerNews Algolia API."""
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
                for aid in ids[:1]:
                    papers.append(Paper(
                        arxiv_id=self._clean_arxiv_id(aid),
                        title=title, authors=[], abstract="", categories=[],
                        pdf_url=f"https://arxiv.org/pdf/{aid}",
                        arxiv_url=f"https://arxiv.org/abs/{aid}",
                        published_at="", source="huggingface",
                        community_buzz=hit.get("points", 0),
                        sources=["hackernews"],
                    ))
            logger.info("HackerNews: %d papers", len(papers))
            return papers
        except Exception as exc:
            logger.warning("HackerNews fetch failed: %s", exc)
            return []

    async def _fetch_reddit(self) -> list[Paper]:
        """Fetch papers from Reddit JSON API."""
        papers: list[Paper] = []
        seen_ids: set[str] = set()
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
                    if _time.time() - d.get("created_utc", 0) > 172800:
                        continue
                    text = d.get("url", "") + " " + d.get("selftext", "")
                    ids = _ARXIV_ID_RE.findall(text)
                    for aid in ids[:1]:
                        clean_id = self._clean_arxiv_id(aid)
                        if clean_id in seen_ids:
                            continue
                        seen_ids.add(clean_id)
                        papers.append(Paper(
                            arxiv_id=clean_id,
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
                        community_buzz=10,
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

    # ------------------------------------------------------------------ #
    # Deduplication                                                        #
    # ------------------------------------------------------------------ #

    def _deduplicate_and_merge(
        self,
        arxiv_papers: list[Paper],
        hf_papers: list[Paper],
    ) -> list[Paper]:
        """Merge both lists; papers present in both get source='both'.

        Strategy:
        - Keep arXiv abstract (richer, full text)
        - Keep HuggingFace upvotes and linked_models
        - source becomes 'both' when matched on arxiv_id
        """
        arxiv_by_id: dict[str, Paper] = {p.arxiv_id: p for p in arxiv_papers}
        hf_by_id: dict[str, Paper] = {p.arxiv_id: p for p in hf_papers}

        merged: list[Paper] = []

        # Papers in arXiv (may be enriched by HF)
        for arxiv_id, arxiv_paper in arxiv_by_id.items():
            hf_paper = hf_by_id.get(arxiv_id)
            if hf_paper:
                merged.append(
                    arxiv_paper.model_copy(
                        update={
                            "source": "both",
                            "hf_upvotes": hf_paper.hf_upvotes,
                            "linked_models": hf_paper.linked_models,
                            "linked_datasets": hf_paper.linked_datasets,
                        }
                    )
                )
            else:
                merged.append(arxiv_paper)

        # HF-only papers (not in arXiv results)
        for arxiv_id, hf_paper in hf_by_id.items():
            if arxiv_id not in arxiv_by_id:
                merged.append(hf_paper)

        logger.info(
            "Dedup: %d arXiv + %d HF → %d unique papers",
            len(arxiv_papers),
            len(hf_papers),
            len(merged),
        )
        return merged

    # ------------------------------------------------------------------ #
    # Helpers                                                              #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _clean_arxiv_id(raw: str) -> str:
        """Extract bare arXiv ID, stripping URL prefix and version suffix.

        Examples:
          http://arxiv.org/abs/2401.00001v1  → 2401.00001
          2401.00001v2                       → 2401.00001
          2401.00001                         → 2401.00001
        """
        # Strip URL prefix
        for prefix in ("http://arxiv.org/abs/", "https://arxiv.org/abs/"):
            if raw.startswith(prefix):
                raw = raw[len(prefix):]
                break

        # Strip version suffix
        if "v" in raw:
            raw = raw.split("v")[0]

        return raw.strip()
