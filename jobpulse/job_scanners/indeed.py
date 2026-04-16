"""Indeed scanner via JobSpy (python-jobspy).

Returns normalized dicts compatible with the existing run_scan_window() pipeline.
"""

import os

from shared.logging_config import get_logger

logger = get_logger(__name__)

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
        "source": "indeed",
        "platform": "indeed",
    }


def scan_indeed(
    search_terms: list[str],
    location: str,
    max_results: int = 50,
) -> list[dict]:
    """Scan Indeed via JobSpy, return normalized JobListing-compatible dicts.

    Uses python-jobspy to scrape Indeed listings. No browser needed.
    Filters to last 24 hours only.
    """
    if scrape_jobs is None:
        logger.warning("python-jobspy not installed — run: pip install python-jobspy")
        return []

    all_results: list[dict] = []
    for term in search_terms:
        try:
            results = scrape_jobs(
                site_name=["indeed"],
                search_term=term,
                location=location,
                results_wanted=max_results,
                hours_old=24,
                country_indeed="UK",
            )
            listings = [normalize_to_job_listing(row.to_dict()) for _, row in results.iterrows()]
            logger.info("Indeed: found %d listings for '%s' in %s", len(listings), term, location)
            all_results.extend(listings)
        except Exception as e:
            logger.error("Indeed scan failed for '%s': %s", term, e)

    return all_results
