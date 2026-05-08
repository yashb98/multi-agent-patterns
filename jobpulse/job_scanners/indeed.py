"""JobSpy-backed Indeed scanner.

Uses the existing `python-jobspy` dependency for Indeed scraping.
"""

import hashlib

from shared.logging_config import get_logger

from jobpulse.job_scanners import (
    MAX_REQUESTS_PER_PLATFORM,
    SessionSignals,
    handle_block,
    random_ua,
    record_success,
)
from jobpulse.scan_learning import ScanLearningEngine

logger = get_logger(__name__)

# JobSpy is opaque, so block detection is heuristic — match these substrings
# in raised exception messages rather than try to inspect HTTP responses
# we never see. Source: empirically observed JobSpy errors when Indeed
# blocks. See pipeline-bugs S9 M-9.B for context.
_BLOCK_PATTERNS = ("blocked", "captcha", "rate", "403", "429", "forbidden")

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
    direct = row.get("job_url_direct", "") or ""
    return {
        "title": row.get("title", ""),
        "company": row.get("company", ""),
        "location": row.get("location", ""),
        "description": row.get("description", ""),
        "url": url,
        "direct_url": direct if direct and direct != url else "",
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

    engine = ScanLearningEngine()
    if not engine.can_scan_now("indeed"):
        cooldown = engine.get_cooldown_info("indeed")
        logger.warning(
            "scan_indeed: cooldown active until %s — skipping scan",
            cooldown.get("cooldown_until") if cooldown else "unknown",
        )
        return []

    ua = random_ua()
    signals = SessionSignals("indeed", ua)

    all_results: list[dict] = []
    seen_ids: set[str] = set()

    for term in search_terms:
        if len(all_results) >= MAX_REQUESTS_PER_PLATFORM:
            break

        remaining = MAX_REQUESTS_PER_PLATFORM - len(all_results)
        batch_size = min(max_results, remaining)

        # JobSpy is a black box — every term counts as one "request" for
        # session-shape tracking. Without this, record_success short-circuits
        # on `requests_count == 0` and the success row never lands.
        signals.last_query = term
        signals.record_request()

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
            msg = str(e).lower()
            if any(pat in msg for pat in _BLOCK_PATTERNS):
                logger.warning(
                    "scan_indeed: block-shaped exception for '%s': %s — recording block",
                    term, e,
                )
                handle_block(engine, "indeed", "jobspy_exception", signals)
                return all_results
            logger.error("Indeed scan failed for '%s': %s", term, e)

    if all_results:
        record_success(engine, "indeed", signals)

    logger.info("scan_indeed: returning %d total results", len(all_results))
    return all_results
