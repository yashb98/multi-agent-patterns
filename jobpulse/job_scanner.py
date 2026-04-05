"""Job Scanner — scrapes/queries job platforms and returns raw job dicts.

Platform coverage:
  - Reed: fully functional via official Reed.co.uk API (search + detail endpoint for full JD)
  - LinkedIn: public guest API (httpx + BeautifulSoup, no login/browser needed)
  - Indeed: Playwright browser automation (public search, no login required)
  - TotalJobs, Glassdoor: stubs (log + return []) pending full scraper work

Each returned dict conforms to the shape expected by job_db.py / JobListing.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import time
from typing import Any

import httpx
from bs4 import BeautifulSoup
from shared.logging_config import get_logger

from jobpulse.config import DATA_DIR, REED_API_KEY
from jobpulse.models.application_models import SearchConfig
from jobpulse.verification_detector import detect_verification_wall, simulate_human_interaction
from jobpulse.scan_learning import ScanLearningEngine

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CONFIG_PATH = DATA_DIR / "job_search_config.json"

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
    if not url:
        import uuid
        logger.warning("_make_job_id: received empty URL, generating random ID")
        return f"unknown-{uuid.uuid4().hex[:8]}"
    return hashlib.sha256(url.strip().lower().encode()).hexdigest()[:16]


def _random_ua() -> str:
    return random.choice(_USER_AGENTS)


def _anti_detection_sleep() -> None:
    """Sleep 2–8 seconds between requests to avoid rate-limiting."""
    time.sleep(random.uniform(2.0, 8.0))


class _SessionSignals:
    """Track signals for the current scan session."""

    def __init__(self, platform: str, user_agent: str) -> None:
        self.platform = platform
        self.start_time = time.monotonic()
        self.request_times: list[float] = []
        self.user_agent_hash = hashlib.sha256(user_agent.encode()).hexdigest()[:8]
        self.browser_fingerprint = hashlib.sha256(
            f"{platform}:1280x800:{user_agent}".encode()
        ).hexdigest()[:8]
        self.was_fresh_session = True
        self.simulated_mouse = False
        self.referrer_chain = "direct"
        self.last_query = ""
        self.waited_for_load = True
        self.last_load_time_ms = 0

    def record_request(self) -> None:
        self.request_times.append(time.monotonic())

    @property
    def requests_count(self) -> int:
        return len(self.request_times)

    @property
    def avg_delay(self) -> float:
        if len(self.request_times) < 2:
            return 0.0
        deltas = [
            self.request_times[i] - self.request_times[i - 1]
            for i in range(1, len(self.request_times))
        ]
        return sum(deltas) / len(deltas)

    @property
    def session_age(self) -> float:
        return time.monotonic() - self.start_time


def _handle_block(engine: ScanLearningEngine, platform: str, wall: Any, signals: _SessionSignals) -> None:
    """Record block event, start cooldown, update rules, optionally run LLM analysis."""
    engine.record_event(
        platform=platform,
        requests_in_session=signals.requests_count,
        avg_delay=signals.avg_delay,
        session_age_seconds=signals.session_age,
        user_agent_hash=signals.user_agent_hash,
        was_fresh_session=signals.was_fresh_session,
        used_vpn=False,
        simulated_mouse=signals.simulated_mouse,
        referrer_chain=signals.referrer_chain,
        search_query=signals.last_query,
        pages_before_block=signals.requests_count,
        browser_fingerprint=signals.browser_fingerprint,
        waited_for_page_load=signals.waited_for_load,
        page_load_time_ms=signals.last_load_time_ms,
        outcome="blocked",
        wall_type=wall.wall_type,
    )
    engine.start_cooldown(platform, wall.wall_type)
    engine.update_learned_rules(platform)
    if engine.should_run_llm_analysis():
        engine.run_llm_analysis(platform)


def _record_success(engine: ScanLearningEngine, platform: str, signals: _SessionSignals) -> None:
    """Record a successful scan session."""
    if signals.requests_count > 0:
        engine.record_event(
            platform=platform,
            requests_in_session=signals.requests_count,
            avg_delay=signals.avg_delay,
            session_age_seconds=signals.session_age,
            user_agent_hash=signals.user_agent_hash,
            was_fresh_session=signals.was_fresh_session,
            used_vpn=False,
            simulated_mouse=signals.simulated_mouse,
            referrer_chain=signals.referrer_chain,
            search_query=signals.last_query,
            pages_before_block=signals.requests_count,
            browser_fingerprint=signals.browser_fingerprint,
            waited_for_page_load=signals.waited_for_load,
            page_load_time_ms=signals.last_load_time_ms,
            outcome="success",
            wall_type=None,
        )
        engine.reset_cooldown(platform)


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
        config = SearchConfig.model_validate(raw)
        if not config.titles:
            logger.warning("load_search_config: no job titles configured — scan will find nothing")
        return config

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

    # --- Cooldown gate ---
    engine = ScanLearningEngine()
    if not engine.can_scan_now("reed"):
        cooldown = engine.get_cooldown_info("reed")
        logger.warning(
            "scan_reed: cooldown active until %s — skipping scan",
            cooldown.get("cooldown_until") if cooldown else "unknown",
        )
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
                    try:
                        from shared.rate_monitor import record_from_headers
                        record_from_headers("reed", dict(resp.headers))
                    except Exception:
                        pass  # Non-blocking monitoring
                    data = resp.json()
                    break
                else:
                    logger.error("scan_reed: rate limited after 3 retries for '%s'", title)
                    continue  # skip to next title

                if data is None:
                    continue

            for job in data.get("results", []):
                url = job.get("jobUrl", "")
                reed_id = str(job.get("jobId", ""))
                if not url:
                    # Fall back to constructing a canonical URL from jobId
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
                        "job_id": _make_job_id(url) if url else _make_job_id(reed_id),
                        "reed_id": reed_id,
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
        except Exception as exc:
            logger.error("scan_reed: unexpected error for title '%s': %s", title, exc)

        _anti_detection_sleep()

    # Second pass: fetch full JD text via detail API
    # The search API returns truncated jobDescription (1-2 sentences).
    # The detail endpoint returns the complete description.
    detail_url = "https://www.reed.co.uk/api/1.0/jobs"
    for job in results:
        reed_id = job.get("reed_id", "")
        if not reed_id:
            continue

        try:
            with httpx.Client(timeout=15) as client:
                resp = client.get(
                    f"{detail_url}/{reed_id}",
                    auth=(REED_API_KEY, ""),
                    headers={"User-Agent": _random_ua()},
                )
                if resp.status_code == 200:
                    detail = resp.json()
                    full_desc = detail.get("jobDescription", "")
                    if full_desc and len(full_desc) > len(job.get("description", "")):
                        job["description"] = full_desc
                        logger.debug(
                            "scan_reed: enriched JD for %s (%d chars)",
                            reed_id,
                            len(full_desc),
                        )
                elif resp.status_code == 429:
                    logger.warning(
                        "scan_reed: rate limited on detail fetch, stopping enrichment"
                    )
                    break
        except Exception as exc:
            logger.debug("scan_reed: detail fetch failed for %s: %s", reed_id, exc)

        # Brief pause between detail fetches
        time.sleep(random.uniform(0.5, 1.5))

    logger.info("scan_reed: returning %d total results", len(results))
    return results



# Indeed scanning is handled entirely by the Chrome extension (scanner.js).
# The extension uses fetch() with session cookies. No Python scanner needed.


def scan_linkedin(config: SearchConfig) -> list[dict[str, Any]]:
    """LinkedIn job search via public guest API (no login required).

    Uses LinkedIn's guest jobs API which returns public HTML listings.
    No browser automation needed — uses httpx + BeautifulSoup.
    No reCAPTCHA risk since no Playwright/browser fingerprinting.
    """
    # --- Adaptive pre-scan gate ---
    engine = ScanLearningEngine()
    params = engine.get_adaptive_params("linkedin")
    if params.get("cooldown_active"):
        logger.warning(
            "scan_linkedin: cooldown active until %s — skipping scan",
            params.get("cooldown_until"),
        )
        return []

    max_requests = params.get("max_requests", MAX_REQUESTS_PER_PLATFORM)

    results: list[dict[str, Any]] = []
    ua = _random_ua()
    signals = _SessionSignals("linkedin", ua)

    try:
        with httpx.Client(
            timeout=20,
            headers={"User-Agent": ua, "Accept-Language": "en-GB,en;q=0.9"},
            follow_redirects=True,
        ) as client:
            for title in config.titles:
                if len(results) >= max_requests:
                    break

                signals.last_query = title
                start = 0

                while len(results) < max_requests:
                    search_url = (
                        "https://www.linkedin.com/jobs-guest/jobs/api/"
                        "seeMoreJobPostings/search"
                        f"?keywords={_url_encode(title)}"
                        f"&location={_url_encode(config.location)}"
                        f"&f_TPR=r86400"
                        f"&start={start}"
                    )

                    logger.info(
                        "scan_linkedin: fetching page start=%d for '%s'",
                        start, title,
                    )

                    # Fetch with retry on 429
                    resp = None
                    for retry in range(3):
                        try:
                            load_start = time.monotonic()
                            resp = client.get(search_url)
                            signals.last_load_time_ms = int(
                                (time.monotonic() - load_start) * 1000
                            )
                            signals.record_request()
                        except httpx.HTTPError as exc:
                            logger.error(
                                "scan_linkedin: HTTP error on page fetch: %s", exc,
                            )
                            resp = None
                            break

                        if resp.status_code == 429:
                            wait = (retry + 1) * 5  # 5s, 10s, 15s
                            logger.warning(
                                "scan_linkedin: rate limited (429), waiting %ds "
                                "(attempt %d/3)",
                                wait, retry + 1,
                            )
                            time.sleep(wait)
                            continue
                        break

                    if resp is None or resp.status_code != 200:
                        if resp is not None:
                            logger.warning(
                                "scan_linkedin: got status %d for '%s', stopping "
                                "pagination",
                                resp.status_code, title,
                            )
                        break

                    try:
                        from shared.rate_monitor import record_from_headers
                        record_from_headers("linkedin", dict(resp.headers))
                    except Exception:
                        pass  # Non-blocking monitoring

                    soup = BeautifulSoup(resp.text, "html.parser")
                    cards = soup.select(
                        "div.base-search-card, div.job-search-card"
                    )

                    if not cards:
                        logger.info(
                            "scan_linkedin: no more cards at start=%d for '%s'",
                            start, title,
                        )
                        break  # No more results

                    logger.info(
                        "scan_linkedin: found %d cards at start=%d for '%s'",
                        len(cards), start, title,
                    )

                    for card in cards:
                        if len(results) >= max_requests:
                            break

                        try:
                            title_el = card.select_one(
                                "h3.base-search-card__title"
                            )
                            company_el = card.select_one(
                                "h4.base-search-card__subtitle"
                            )
                            location_el = card.select_one(
                                "span.job-search-card__location"
                            )
                            link_el = card.select_one(
                                "a.base-card__full-link"
                            )

                            job_title = (
                                title_el.get_text(strip=True)
                                if title_el else ""
                            )
                            company = (
                                company_el.get_text(strip=True)
                                if company_el else ""
                            )
                            location = (
                                location_el.get_text(strip=True)
                                if location_el else ""
                            )
                            href = (
                                link_el["href"]
                                if link_el and link_el.has_attr("href")
                                else ""
                            )

                            if not href or not job_title:
                                continue

                            # Normalise to absolute URL
                            if href and not href.startswith("http"):
                                href = "https://www.linkedin.com" + href

                            # Fetch full JD from detail page
                            description = ""
                            try:
                                time.sleep(random.uniform(1.5, 3.0))
                                detail_resp = client.get(href)
                                signals.record_request()
                                if detail_resp.status_code == 200:
                                    detail_soup = BeautifulSoup(
                                        detail_resp.text, "html.parser"
                                    )
                                    desc_el = detail_soup.select_one(
                                        ".show-more-less-html__markup, "
                                        ".description__text, "
                                        "#job-details"
                                    )
                                    if desc_el:
                                        description = desc_el.get_text(
                                            separator="\n", strip=True
                                        )[:5000]
                                elif detail_resp.status_code == 429:
                                    logger.warning(
                                        "scan_linkedin: rate limited on detail "
                                        "fetch, skipping remaining details"
                                    )
                                    # Still add the job without description
                            except Exception as detail_err:
                                logger.debug(
                                    "scan_linkedin: detail fetch failed for "
                                    "'%s': %s",
                                    job_title, detail_err,
                                )

                            # Extract salary from card text if present
                            salary_min_val = None
                            salary_max_val = None
                            card_text = card.get_text()
                            sal_match = re.search(
                                r"£([\d,]+)\s*[-–]\s*£([\d,]+)", card_text
                            )
                            if sal_match:
                                try:
                                    salary_min_val = float(
                                        sal_match.group(1).replace(",", "")
                                    )
                                    salary_max_val = float(
                                        sal_match.group(2).replace(",", "")
                                    )
                                except ValueError:
                                    pass

                            results.append(
                                {
                                    "title": job_title,
                                    "company": company,
                                    "url": href,
                                    "location": location,
                                    "salary_min": salary_min_val,
                                    "salary_max": salary_max_val,
                                    "description": description,
                                    "platform": "linkedin",
                                    "job_id": _make_job_id(href),
                                }
                            )
                        except Exception as card_err:
                            logger.debug(
                                "scan_linkedin: error parsing card: %s",
                                card_err,
                            )
                            continue

                    # Paginate — next 25 results
                    start += 25
                    time.sleep(random.uniform(2.0, 5.0))

            # Session completed without blocks — record success
            _record_success(engine, "linkedin", signals)

    except Exception as exc:
        logger.error("scan_linkedin: guest API error: %s", exc)

    logger.info("scan_linkedin: returning %d total results", len(results))
    return results



# TotalJobs and Glassdoor scanners removed — never implemented beyond stubs.
# Can be added to the extension scanner if needed in the future.


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

PLATFORM_SCANNERS: dict[str, Any] = {
    "reed": scan_reed,
    "linkedin": scan_linkedin,
    # Indeed scanning handled by Chrome extension (scanner.js)
    # TotalJobs/Glassdoor not implemented — stubs removed
}

ALL_PLATFORMS: list[str] = list(PLATFORM_SCANNERS.keys())
QUICK_PLATFORMS: list[str] = ["linkedin", "reed"]


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
        except Exception as exc:
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
