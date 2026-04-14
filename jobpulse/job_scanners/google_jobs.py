"""Google Jobs scanner via JobSpy (python-jobspy).

Feature-gated: GOOGLE_JOBS_ENABLED=true (default: false).
Returns normalized dicts compatible with the existing run_scan_window() pipeline.
"""

import os

from shared.logging_config import get_logger

logger = get_logger(__name__)

# Lazy import — overridden in tests
try:
    from jobspy import scrape_jobs
except ImportError:
    scrape_jobs = None


def normalize_to_job_listing(row: dict) -> dict:
    """Normalize a JobSpy row to a JobListing-compatible dict."""
    return {
        "title": row.get("title", ""),
        "company": row.get("company", ""),
        "location": row.get("location", ""),
        "description": row.get("description", ""),
        "url": row.get("job_url", ""),
        "date_posted": row.get("date_posted", ""),
        "source": "google_jobs",
    }


def scan_google_jobs(
    search_terms: list[str],
    location: str,
    max_results: int = 25,
) -> list[dict]:
    """Scan Google Jobs via JobSpy, return normalized JobListing-compatible dicts.

    Disabled by default — set GOOGLE_JOBS_ENABLED=true to activate.
    """
    if os.environ.get("GOOGLE_JOBS_ENABLED", "false").lower() != "true":
        logger.debug("Google Jobs scanner disabled (GOOGLE_JOBS_ENABLED != true)")
        return []

    if scrape_jobs is None:
        logger.warning("python-jobspy not installed — run: pip install python-jobspy")
        return []

    try:
        results = scrape_jobs(
            site_name=["google"],
            search_term=" OR ".join(search_terms),
            location=location,
            results_wanted=max_results,
            hours_old=24,
        )
        listings = [normalize_to_job_listing(row.to_dict()) for _, row in results.iterrows()]
        logger.info("Google Jobs: found %d listings for %s in %s", len(listings), search_terms, location)
        return listings
    except Exception as e:
        logger.error("Google Jobs scan failed: %s", e)
        return []
