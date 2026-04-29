"""JobSpy-backed Indeed scanner.

Uses the existing `python-jobspy` dependency for Indeed scraping.
"""

import hashlib

from shared.logging_config import get_logger

from jobpulse.job_scanners import MAX_REQUESTS_PER_PLATFORM

logger = get_logger(__name__)

try:
    from jobspy import scrape_jobs
except ImportError:
    scrape_jobs = None


def _make_job_id(url: str, fallback: str = "") -> str:
    raw = (url or fallback or "unknown").strip().lower()
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def normalize_to_job_listing(row: dict, platform: str) -> dict:
    """Normalize a JobSpy row to a JobListing-compatible dict."""
    url = row.get("job_url", "") or row.get("url", "")
    return {
        "title": row.get("title", ""),
        "company": row.get("company", ""),
        "location": row.get("location", ""),
        "description": row.get("description", ""),
        "url": url,
        "date_posted": row.get("date_posted", ""),
        "source": platform,
        "platform": platform,
        "job_id": _make_job_id(url, fallback=f"{platform}:{row.get('title', '')}:{row.get('company', '')}"),
    }


def scan_indeed(
    search_terms: list[str],
    location: str,
    max_results: int = 50,
) -> list[dict]:
    """Scan Indeed via python-jobspy with native filters.

    Filters: last 24 hours, full-time, UK, max results capped globally.
    """
    if scrape_jobs is None:
        logger.warning("python-jobspy not installed — run: pip install python-jobspy")
        return []

    all_results: list[dict] = []
    seen_ids: set[str] = set()

    for term in search_terms:
        if len(all_results) >= MAX_REQUESTS_PER_PLATFORM:
            break

        remaining = MAX_REQUESTS_PER_PLATFORM - len(all_results)
        batch_size = min(max_results, remaining)

        try:
            kwargs = {
                "site_name": ["indeed"],
                "search_term": term,
                "location": location,
                "results_wanted": batch_size,
                "hours_old": 24,
                "country_indeed": "UK",
                "job_type": "fulltime",
            }

            try:
                results = scrape_jobs(**kwargs)
            except TypeError:
                kwargs.pop("job_type", None)
                results = scrape_jobs(**kwargs)

            for _, row in results.iterrows():
                listing = normalize_to_job_listing(row.to_dict(), "indeed")
                if listing["job_id"] in seen_ids:
                    continue
                seen_ids.add(listing["job_id"])
                all_results.append(listing)
                if len(all_results) >= MAX_REQUESTS_PER_PLATFORM:
                    break

            logger.info("Indeed: found %d listings for '%s' (total: %d)", len(results), term, len(all_results))
        except Exception as e:
            logger.error("Indeed scan failed for '%s': %s", term, e)

    logger.info("scan_indeed: returning %d total results", len(all_results))
    return all_results
