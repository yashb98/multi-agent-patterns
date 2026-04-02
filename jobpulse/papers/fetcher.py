"""Async PaperFetcher — arXiv + HuggingFace sources fetched in parallel."""

from __future__ import annotations

import asyncio
import xml.etree.ElementTree as ET
from typing import Any

import httpx

from jobpulse.papers.models import Paper
from shared.logging_config import get_logger

logger = get_logger(__name__)

# arXiv Atom feed namespace
_ATOM_NS = "http://www.w3.org/2005/Atom"

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
