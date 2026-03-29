"""Job Scanner — scrapes/queries job platforms and returns raw job dicts.

Platform coverage:
  - Reed: fully functional via official Reed.co.uk API
  - LinkedIn: Playwright browser automation (if installed + session saved)
  - Indeed, TotalJobs, Glassdoor: stubs (log + return []) pending full scraper work

Each returned dict conforms to the shape expected by job_db.py / JobListing.
"""

from __future__ import annotations

import hashlib
import json
import random
import time
from pathlib import Path
from typing import Any

import httpx

from jobpulse.config import DATA_DIR, REED_API_KEY
from jobpulse.models.application_models import SearchConfig
from jobpulse.utils.safe_io import managed_persistent_browser
from shared.logging_config import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CONFIG_PATH = DATA_DIR / "job_search_config.json"
_LINKEDIN_SESSION_DIR = DATA_DIR / "linkedin_session"

_USER_AGENTS: list[str] = [
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) "
        "Gecko/20100101 Firefox/125.0"
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.4; rv:125.0) "
        "Gecko/20100101 Firefox/125.0"
    ),
]

MAX_REQUESTS_PER_PLATFORM = 50


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_job_id(url: str) -> str:
    """SHA-256 of the normalised URL — used as the deduplication key."""
    return hashlib.sha256(url.strip().lower().encode()).hexdigest()


def _random_ua() -> str:
    return random.choice(_USER_AGENTS)


def _anti_detection_sleep() -> None:
    """Sleep 2–8 seconds between requests to avoid rate-limiting."""
    time.sleep(random.uniform(2.0, 8.0))


# ---------------------------------------------------------------------------
# Search config persistence
# ---------------------------------------------------------------------------


def load_search_config() -> SearchConfig:
    """Load SearchConfig from data/job_search_config.json.

    Falls back to sensible defaults (London, Python/ML titles) if the file
    does not exist yet.
    """
    if _CONFIG_PATH.exists():
        raw = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
        return SearchConfig.model_validate(raw)

    # Default configuration
    default = SearchConfig(
        titles=["Software Engineer", "Python Developer", "Backend Developer"],
        location="London",
        include_remote=True,
        salary_min=27000,
    )
    save_search_config(default)
    return default


def save_search_config(config: SearchConfig) -> None:
    """Persist SearchConfig to data/job_search_config.json."""
    _CONFIG_PATH.write_text(
        config.model_dump_json(indent=2), encoding="utf-8"
    )
    logger.info("job_scanner: saved search config to %s", _CONFIG_PATH)


# ---------------------------------------------------------------------------
# Platform scanners
# ---------------------------------------------------------------------------


def scan_reed(config: SearchConfig) -> list[dict[str, Any]]:
    """Query the Reed.co.uk official REST API and return raw job dicts.

    Reed API docs: https://www.reed.co.uk/developers/jobseeker
    Basic auth: (REED_API_KEY, "")
    """
    if not REED_API_KEY:
        logger.warning("scan_reed: REED_API_KEY not set — skipping Reed scan")
        return []

    results: list[dict[str, Any]] = []
    base_url = "https://www.reed.co.uk/api/1.0/search"

    for title in config.titles:
        if len(results) >= MAX_REQUESTS_PER_PLATFORM:
            break

        params: dict[str, Any] = {
            "keywords": title,
            "locationName": config.location,
            "distanceFromLocation": 50,
            "minimumSalary": config.salary_min,
            "resultsToTake": 25,
        }

        try:
            logger.info("scan_reed: searching '%s' in '%s'", title, config.location)
            with httpx.Client(timeout=20) as client:
                data = None
                for retry in range(3):
                    resp = client.get(
                        base_url,
                        params=params,
                        auth=(REED_API_KEY, ""),
                        headers={"User-Agent": _random_ua()},
                    )

                    if resp.status_code == 429:
                        wait = 2 ** (retry + 1)  # 2s, 4s, 8s
                        logger.warning(
                            "scan_reed: rate limited (429), retrying in %ds (attempt %d/3)",
                            wait, retry + 1,
                        )
                        time.sleep(wait)
                        continue

                    resp.raise_for_status()
                    data = resp.json()
                    break
                else:
                    logger.error("scan_reed: rate limited after 3 retries for '%s'", title)
                    continue  # skip to next title

                if data is None:
                    continue

            for job in data.get("results", []):
                url = job.get("jobUrl", "")
                if not url:
                    # Fall back to constructing a canonical URL from jobId
                    reed_id = job.get("jobId", "")
                    url = f"https://www.reed.co.uk/jobs/{reed_id}" if reed_id else ""

                results.append(
                    {
                        "title": job.get("jobTitle", ""),
                        "company": job.get("employerName", ""),
                        "url": url,
                        "location": job.get("locationName", ""),
                        "salary_min": _to_float(job.get("minimumSalary")),
                        "salary_max": _to_float(job.get("maximumSalary")),
                        "description": job.get("jobDescription", ""),
                        "platform": "reed",
                        "job_id": _make_job_id(url) if url else _make_job_id(str(job.get("jobId", ""))),
                    }
                )

            logger.info("scan_reed: got %d results for '%s'", len(data.get("results", [])), title)

        except httpx.HTTPStatusError as exc:
            logger.error(
                "scan_reed: HTTP %s for title '%s': %s",
                exc.response.status_code,
                title,
                exc,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("scan_reed: unexpected error for title '%s': %s", title, exc)

        _anti_detection_sleep()

    logger.info("scan_reed: returning %d total results", len(results))
    return results


def scan_indeed(config: SearchConfig) -> list[dict[str, Any]]:
    """Indeed.co.uk public search scraper — stub (HTML parsing complex).

    Logs a fetch attempt for observability but returns an empty list.
    Full implementation requires HTML parsing (BeautifulSoup / Playwright).
    """
    for title in config.titles:
        url = (
            f"https://uk.indeed.com/jobs"
            f"?q={httpx.QueryParams({'q': title}).get('q')}"
            f"&l={config.location}&fromage=1&limit=20"
        )
        logger.info("scan_indeed: [stub] would fetch %s", url)
        _anti_detection_sleep()

    logger.warning(
        "scan_indeed: stub — returning []. "
        "Full HTML scraper not yet implemented."
    )
    return []


def scan_linkedin(config: SearchConfig) -> list[dict[str, Any]]:
    """LinkedIn job search via Playwright with saved browser session.

    Requires:
      1. `playwright` Python package installed
      2. A saved browser session at data/linkedin_session/
         (created by running: playwright codegen --save-storage=data/linkedin_session)

    Returns an empty list if either prerequisite is missing.
    """
    # Lazy import — Playwright may not be installed
    try:
        from playwright.sync_api import sync_playwright  # type: ignore[import]
    except ImportError:
        logger.warning(
            "scan_linkedin: playwright not installed. "
            "Install with: pip install playwright && playwright install chromium"
        )
        return []

    # Use the shared chrome_profile (same as browser_manager.py)
    chrome_profile = DATA_DIR / "chrome_profile"
    if not chrome_profile.exists():
        logger.warning(
            "scan_linkedin: no Chrome profile at %s. "
            "Run the login flow first to save LinkedIn cookies.",
            chrome_profile,
        )
        return []

    results: list[dict[str, Any]] = []

    try:
        with managed_persistent_browser(
            user_data_dir=str(chrome_profile),
            headless=False,
            executable_path="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
            ],
            ignore_default_args=["--enable-automation"],
            user_agent=_random_ua(),
            viewport={"width": 1280, "height": 800},
        ) as (browser, page):
            for title in config.titles:
                if len(results) >= MAX_REQUESTS_PER_PLATFORM:
                    break

                search_url = (
                    f"https://www.linkedin.com/jobs/search/"
                    f"?keywords={_url_encode(title)}"
                    f"&location={_url_encode(config.location)}"
                    f"&f_TPR=r86400"   # past 24 hours
                    f"&f_E=1,2"        # internship + entry level
                )

                try:
                    logger.info("scan_linkedin: fetching '%s'", search_url)
                    page.goto(search_url, timeout=45_000, wait_until="domcontentloaded")
                    # Wait for job cards to render (LinkedIn loads async)
                    try:
                        page.wait_for_selector(".job-card-container, .jobs-search-results-list", timeout=15_000)
                    except Exception:
                        logger.warning("scan_linkedin: job cards not found, trying scroll")
                    # Scroll to trigger lazy loading
                    page.mouse.wheel(0, 500)
                    _anti_detection_sleep()

                    cards = page.query_selector_all(".job-card-container")
                    logger.info("scan_linkedin: found %d job cards for '%s'", len(cards), title)

                    for card in cards:
                        try:
                            # LinkedIn frequently changes class names.
                            # Strategy: extract text lines and link from each card.
                            link_el = card.query_selector('a[href*="/jobs/view"]')
                            href = link_el.get_attribute("href") if link_el else ""

                            # The card's text is structured: title, company, location
                            # Split inner_text by newlines and filter empties
                            lines = [l.strip() for l in card.inner_text().split("\n") if l.strip()]

                            # First non-empty line is usually the title
                            # Company and location follow
                            job_title = lines[0] if len(lines) > 0 else ""
                            # Skip duplicate title line (LinkedIn often repeats it)
                            start = 1
                            if len(lines) > 1 and lines[1] == job_title:
                                start = 2
                            company = lines[start] if len(lines) > start else ""
                            location = lines[start + 1] if len(lines) > start + 1 else ""

                            # Normalise to absolute URL
                            if href and not href.startswith("http"):
                                href = "https://www.linkedin.com" + href

                            if not href:
                                continue

                            results.append(
                                {
                                    "title": job_title,
                                    "company": company,
                                    "url": href,
                                    "location": location,
                                    "salary_min": None,
                                    "salary_max": None,
                                    "description": "",
                                    "platform": "linkedin",
                                    "job_id": _make_job_id(href),
                                }
                            )
                        except Exception as card_err:  # noqa: BLE001
                            logger.debug("scan_linkedin: error parsing card: %s", card_err)
                            continue

                except Exception as page_err:  # noqa: BLE001
                    logger.error("scan_linkedin: error fetching '%s': %s", search_url, page_err)

    except Exception as exc:  # noqa: BLE001
        logger.error("scan_linkedin: Playwright session error: %s", exc)

    logger.info("scan_linkedin: returning %d total results", len(results))
    return results


def scan_totaljobs(config: SearchConfig) -> list[dict[str, Any]]:
    """TotalJobs public search scraper — stub (HTML parsing complex).

    Logs a fetch attempt for observability but returns an empty list.
    """
    for title in config.titles:
        url = (
            f"https://www.totaljobs.com/jobs/{_url_encode(title).replace('%20', '-')}"
            f"?postedWithin=1&radius=50&salary={int(config.salary_min)}"
        )
        logger.info("scan_totaljobs: [stub] would fetch %s", url)
        _anti_detection_sleep()

    logger.warning(
        "scan_totaljobs: stub — returning []. "
        "Full HTML scraper not yet implemented."
    )
    return []


def scan_glassdoor(config: SearchConfig) -> list[dict[str, Any]]:
    """Glassdoor job search via Playwright — stub pending session setup.

    Returns an empty list if no session or Playwright is unavailable.
    """
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401  # type: ignore[import]
    except ImportError:
        logger.warning(
            "scan_glassdoor: playwright not installed. "
            "Install with: pip install playwright && playwright install chromium"
        )
        return []

    glassdoor_session = DATA_DIR / "glassdoor_session"
    if not glassdoor_session.exists():
        logger.warning(
            "scan_glassdoor: no saved session at %s — returning []. "
            "Run: playwright codegen --save-storage=%s https://www.glassdoor.co.uk",
            glassdoor_session,
            glassdoor_session,
        )
        return []

    # Session exists — stub the actual scraping logic
    for title in config.titles:
        logger.info("scan_glassdoor: [stub] would scrape '%s' via Playwright", title)
        _anti_detection_sleep()

    logger.warning("scan_glassdoor: stub — returning []. Full scraper not yet implemented.")
    return []


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

PLATFORM_SCANNERS: dict[str, Any] = {
    "reed": scan_reed,
    "indeed": scan_indeed,
    "linkedin": scan_linkedin,
    "totaljobs": scan_totaljobs,
    "glassdoor": scan_glassdoor,
}

ALL_PLATFORMS: list[str] = list(PLATFORM_SCANNERS.keys())
QUICK_PLATFORMS: list[str] = ["linkedin", "indeed", "reed"]
SLOW_PLATFORMS: list[str] = ["glassdoor", "totaljobs"]


def scan_platforms(platforms: list[str] | None = None) -> list[dict[str, Any]]:
    """Scan the requested platforms and return a combined list of raw job dicts.

    Args:
        platforms: List of platform names to scan. Defaults to ALL_PLATFORMS.

    Returns:
        Combined list of raw job dicts from all requested platforms.
    """
    if platforms is None:
        platforms = ALL_PLATFORMS

    config = load_search_config()

    unknown = [p for p in platforms if p not in PLATFORM_SCANNERS]
    if unknown:
        logger.warning("scan_platforms: unknown platforms %s — ignoring", unknown)
        platforms = [p for p in platforms if p in PLATFORM_SCANNERS]

    all_jobs: list[dict[str, Any]] = []

    for platform in platforms:
        scanner = PLATFORM_SCANNERS[platform]
        logger.info("scan_platforms: starting %s scanner", platform)
        try:
            jobs = scanner(config)
            logger.info("scan_platforms: %s returned %d jobs", platform, len(jobs))
            all_jobs.extend(jobs)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "scan_platforms: %s scanner raised unexpectedly: %s",
                platform,
                exc,
            )

    logger.info("scan_platforms: total raw jobs collected = %d", len(all_jobs))
    return all_jobs


# ---------------------------------------------------------------------------
# Internal utilities
# ---------------------------------------------------------------------------


def _to_float(value: Any) -> float | None:
    """Coerce a JSON value to float, returning None on failure."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _url_encode(text: str) -> str:
    """Percent-encode a string for use in a URL query parameter."""
    import urllib.parse

    return urllib.parse.quote(text, safe="")
