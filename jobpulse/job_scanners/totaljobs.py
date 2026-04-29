"""TotalJobs scanner — web search-based discovery fallback."""

from __future__ import annotations

import re
from typing import Any

from shared.logging_config import get_logger

from jobpulse.job_scanners import make_job_id
from jobpulse.models.application_models import SearchConfig

logger = get_logger(__name__)


def _search_hit_to_listing(hit: Any, platform: str, location: str) -> dict[str, Any]:
    raw_title = (getattr(hit, "title", "") or "").strip()
    parts = [part.strip() for part in re.split(r"\s+[|\-–]\s+", raw_title) if part.strip()]
    title = parts[0] if parts else raw_title
    company = parts[1] if len(parts) > 1 else ""
    title = re.sub(rf"\s+(jobs?|careers?)\s*$", "", title, flags=re.IGNORECASE).strip()
    return {
        "title": title or raw_title or "Unknown role",
        "company": company,
        "url": getattr(hit, "url", ""),
        "location": location,
        "salary_min": None,
        "salary_max": None,
        "description": getattr(hit, "snippet", "") or "",
        "platform": platform,
        "source": getattr(hit, "source", platform),
        "job_id": make_job_id(getattr(hit, "url", "")),
    }


def scan_totaljobs(config: SearchConfig) -> list[dict[str, Any]]:
    """Discover TotalJobs listings via web search when direct scraping is unavailable."""
    from shared.web_search import search_web

    results: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for title in config.titles:
        query = f'site:totaljobs.com/jobs "{title}" "{config.location}"'
        try:
            hits = search_web(query, max_results=12, context="career_page")
        except Exception as exc:
            logger.warning("scan_totaljobs: search failed for '%s': %s", title, exc)
            continue
        for hit in hits:
            if "totaljobs.com" not in hit.url:
                continue
            listing = _search_hit_to_listing(hit, "totaljobs", config.location)
            if listing["job_id"] in seen_ids or not listing["url"]:
                continue
            seen_ids.add(listing["job_id"])
            results.append(listing)
    logger.info("scan_totaljobs: returning %d total results", len(results))
    return results
