"""
ATS REST API scanner — zero-browser job discovery.

Hits public Greenhouse / Ashby / Lever APIs directly via httpx.
No Playwright, no browser profile needed.
"""

import re
from typing import Optional

import httpx

from shared.logging_config import get_logger

logger = get_logger(__name__)

_TIMEOUT = 10  # seconds

# ---------------------------------------------------------------------------
# ATS provider detection
# ---------------------------------------------------------------------------

_PATTERNS = {
    "greenhouse": re.compile(r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/([^/?#]+)"),
    "ashby": re.compile(r"jobs\.ashbyhq\.com/([^/?#]+)"),
    "lever": re.compile(r"jobs\.lever\.co/([^/?#]+)"),
}


def detect_ats_provider(url: str) -> tuple[Optional[str], Optional[str]]:
    """Return (provider, slug) or (None, None) if unrecognised."""
    for provider, pattern in _PATTERNS.items():
        m = pattern.search(url)
        if m:
            return provider, m.group(1)
    return None, None


# ---------------------------------------------------------------------------
# Parsers (pure functions — no I/O)
# ---------------------------------------------------------------------------

def parse_greenhouse(data: dict, company: str) -> list[dict]:
    jobs = []
    for job in data.get("jobs", []):
        location = job.get("location") or {}
        jobs.append({
            "title": job.get("title", ""),
            "url": job.get("absolute_url", ""),
            "company": company,
            "location": location.get("name", "") if isinstance(location, dict) else str(location),
            "platform": "greenhouse",
        })
    return jobs


def parse_ashby(data: dict, company: str) -> list[dict]:
    jobs = []
    for job in data.get("jobs", []):
        location = job.get("location") or ""
        jobs.append({
            "title": job.get("title", ""),
            "url": job.get("jobUrl", ""),
            "company": company,
            "location": location if isinstance(location, str) else str(location),
            "platform": "ashby",
        })
    return jobs


def parse_lever(data: list, company: str) -> list[dict]:
    jobs = []
    for job in data:
        categories = job.get("categories") or {}
        location = categories.get("location", "") if isinstance(categories, dict) else ""
        url = job.get("hostedUrl") or job.get("applyUrl", "")
        jobs.append({
            "title": job.get("text", ""),
            "url": url,
            "company": company,
            "location": location,
            "platform": "lever",
        })
    return jobs


# ---------------------------------------------------------------------------
# API callers
# ---------------------------------------------------------------------------

def scan_greenhouse(slug: str, company: str, client: Optional[httpx.Client] = None) -> list[dict]:
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
    _close = client is None
    client = client or httpx.Client(timeout=_TIMEOUT)
    try:
        resp = client.get(url)
        resp.raise_for_status()
        return parse_greenhouse(resp.json(), company)
    except Exception as exc:
        logger.warning("greenhouse scan failed for %s: %s", slug, exc)
        return []
    finally:
        if _close:
            client.close()


def scan_ashby(slug: str, company: str, client: Optional[httpx.Client] = None) -> list[dict]:
    url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"
    _close = client is None
    client = client or httpx.Client(timeout=_TIMEOUT)
    try:
        resp = client.get(url)
        resp.raise_for_status()
        return parse_ashby(resp.json(), company)
    except Exception as exc:
        logger.warning("ashby scan failed for %s: %s", slug, exc)
        return []
    finally:
        if _close:
            client.close()


def scan_lever(slug: str, company: str, client: Optional[httpx.Client] = None) -> list[dict]:
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    _close = client is None
    client = client or httpx.Client(timeout=_TIMEOUT)
    try:
        resp = client.get(url)
        resp.raise_for_status()
        return parse_lever(resp.json(), company)
    except Exception as exc:
        logger.warning("lever scan failed for %s: %s", slug, exc)
        return []
    finally:
        if _close:
            client.close()


# ---------------------------------------------------------------------------
# Unified scanner
# ---------------------------------------------------------------------------

def scan_ats_api(url: str, company: str) -> list[dict]:
    """Auto-detect provider, extract slug, call the appropriate scanner."""
    provider, slug = detect_ats_provider(url)
    if provider is None:
        logger.debug("no ATS provider detected for %s", url)
        return []
    if provider == "greenhouse":
        return scan_greenhouse(slug, company)
    if provider == "ashby":
        return scan_ashby(slug, company)
    if provider == "lever":
        return scan_lever(slug, company)
    return []
