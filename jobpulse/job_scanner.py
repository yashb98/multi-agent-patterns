"""Job Scanner — scrapes/queries job platforms and returns raw job dicts.

Platform coverage:
  - Reed: fully functional via official Reed.co.uk API (search + detail endpoint for full JD)
  - LinkedIn: Playwright browser automation (if installed + session saved), click-to-detail for full JD
  - Indeed: Playwright browser automation (public search, no login required)
  - TotalJobs, Glassdoor: stubs (log + return []) pending full scraper work

Each returned dict conforms to the shape expected by job_db.py / JobListing.
"""

from __future__ import annotations

import hashlib
import json
import random
import time
from typing import Any

import httpx
from shared.logging_config import get_logger

from jobpulse.config import DATA_DIR, REED_API_KEY
from jobpulse.models.application_models import SearchConfig
from jobpulse.utils.safe_io import managed_persistent_browser
from jobpulse.verification_detector import detect_verification_wall, simulate_human_interaction
from jobpulse.scan_learning import ScanLearningEngine

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


def scan_indeed(config: SearchConfig) -> list[dict[str, Any]]:
    """Indeed.co.uk job search via Playwright (public search, no login required).

    Scrapes job cards from uk.indeed.com and clicks into each to extract the
    full job description text.  Integrates verification wall detection and
    adaptive scan parameters via ScanLearningEngine.
    """
    try:
        from playwright.sync_api import sync_playwright as _  # noqa: F401
    except ImportError:
        logger.warning(
            "scan_indeed: playwright not installed. "
            "Install with: pip install playwright && playwright install chromium"
        )
        return []

    # --- Adaptive pre-scan gate ---
    engine = ScanLearningEngine()
    params = engine.get_adaptive_params("indeed")
    if params.get("cooldown_active"):
        logger.warning(
            "scan_indeed: cooldown active until %s — skipping scan",
            params.get("cooldown_until"),
        )
        return []

    delay_min, delay_max = params.get("delay_range", (2.0, 8.0))
    max_requests = params.get("max_requests", MAX_REQUESTS_PER_PLATFORM)
    risk_level = params.get("risk_level", "medium")

    results: list[dict[str, Any]] = []
    ua = _random_ua()
    signals = _SessionSignals("indeed", ua)

    try:
        # Indeed doesn't need a saved profile — public search
        with managed_persistent_browser(
            user_data_dir=str(DATA_DIR / "indeed_profile"),
            headless=False,
            executable_path="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            args=["--disable-blink-features=AutomationControlled", "--disable-infobars"],
            ignore_default_args=["--enable-automation"],
            user_agent=ua,
            viewport={"width": 1280, "height": 800},
        ) as (_browser, page):
            for title in config.titles:
                if len(results) >= max_requests:
                    break

                search_url = (
                    f"https://uk.indeed.com/jobs"
                    f"?q={_url_encode(title)}"
                    f"&l={_url_encode(config.location)}"
                    f"&fromage=1"  # past 24 hours
                )
                signals.last_query = title

                try:
                    logger.info("scan_indeed: fetching '%s'", search_url)
                    load_start = time.monotonic()
                    page.goto(search_url, timeout=30_000, wait_until="networkidle")
                    signals.last_load_time_ms = int((time.monotonic() - load_start) * 1000)
                    signals.record_request()

                    # Simulate human interaction for medium/high risk
                    if risk_level != "low":
                        simulate_human_interaction(page)
                        signals.simulated_mouse = True

                    # Check for verification wall after page load
                    wall = detect_verification_wall(page, expected_results=True)
                    if wall and wall.confidence >= 0.7:
                        logger.warning(
                            "scan_indeed: verification wall detected (%s, %.0f%%) — aborting",
                            wall.wall_type, wall.confidence * 100,
                        )
                        _handle_block(engine, "indeed", wall, signals)
                        return results

                    time.sleep(random.uniform(delay_min, delay_max))

                    # Find job cards
                    cards = page.query_selector_all(
                        ".job_seen_beacon, .resultContent, [data-jk]"
                    )
                    logger.info(
                        "scan_indeed: found %d cards for '%s'", len(cards), title
                    )

                    for card in cards:
                        try:
                            title_el = card.query_selector(
                                "h2.jobTitle a, h2 a, .jobTitle a"
                            )
                            company_el = card.query_selector(
                                "[data-testid='company-name'], .companyName, .company"
                            )
                            location_el = card.query_selector(
                                "[data-testid='text-location'], .companyLocation, .location"
                            )

                            job_title = (
                                title_el.inner_text().strip() if title_el else ""
                            )
                            company = (
                                company_el.inner_text().strip() if company_el else ""
                            )
                            location = (
                                location_el.inner_text().strip() if location_el else ""
                            )
                            href = (
                                title_el.get_attribute("href") if title_el else ""
                            )

                            if href and not href.startswith("http"):
                                href = "https://uk.indeed.com" + href

                            if not href or not job_title:
                                continue

                            # Click to get full description
                            description = ""
                            try:
                                if title_el:
                                    title_el.click()
                                    signals.record_request()
                                    time.sleep(random.uniform(delay_min, delay_max))

                                    # Check for verification wall after click
                                    wall = detect_verification_wall(page)
                                    if wall and wall.confidence >= 0.7:
                                        logger.warning(
                                            "scan_indeed: wall after card click (%s) — returning partial results",
                                            wall.wall_type,
                                        )
                                        _handle_block(engine, "indeed", wall, signals)
                                        return results

                                    desc_el = page.query_selector(
                                        ".jobsearch-jobDescriptionText, "
                                        "#jobDescriptionText, "
                                        "[class*='jobDescription']"
                                    )
                                    if desc_el:
                                        description = desc_el.inner_text()[:5000]
                            except Exception:
                                pass

                            results.append(
                                {
                                    "title": job_title,
                                    "company": company,
                                    "url": href,
                                    "location": location,
                                    "salary_min": None,
                                    "salary_max": None,
                                    "description": description,
                                    "platform": "indeed",
                                    "job_id": _make_job_id(href),
                                }
                            )
                        except Exception as card_err:
                            logger.debug(
                                "scan_indeed: card parse error: %s", card_err
                            )

                except Exception as page_err:
                    logger.error(
                        "scan_indeed: error fetching '%s': %s", search_url, page_err
                    )

            # Session completed without blocks — record success
            _record_success(engine, "indeed", signals)

    except Exception as exc:
        logger.error("scan_indeed: Playwright error: %s", exc)

    logger.info("scan_indeed: returning %d total results", len(results))
    return results


def scan_linkedin(config: SearchConfig) -> list[dict[str, Any]]:
    """LinkedIn job search via Playwright with saved browser session.

    Requires:
      1. `playwright` Python package installed
      2. A saved browser session at data/linkedin_session/
         (created by running: playwright codegen --save-storage=data/linkedin_session)

    Returns an empty list if either prerequisite is missing.
    Integrates verification wall detection and adaptive scan parameters
    via ScanLearningEngine.
    """
    # Lazy import — Playwright may not be installed
    try:
        from playwright.sync_api import sync_playwright as _  # noqa: F401
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

    # --- Adaptive pre-scan gate ---
    engine = ScanLearningEngine()
    params = engine.get_adaptive_params("linkedin")
    if params.get("cooldown_active"):
        logger.warning(
            "scan_linkedin: cooldown active until %s — skipping scan",
            params.get("cooldown_until"),
        )
        return []

    delay_min, delay_max = params.get("delay_range", (2.0, 8.0))
    max_requests = params.get("max_requests", MAX_REQUESTS_PER_PLATFORM)

    results: list[dict[str, Any]] = []
    ua = _random_ua()
    signals = _SessionSignals("linkedin", ua)

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
            user_agent=ua,
            viewport={"width": 1280, "height": 800},
        ) as (_browser, page):
            for title in config.titles:
                if len(results) >= max_requests:
                    break

                search_url = (
                    f"https://www.linkedin.com/jobs/search/"
                    f"?keywords={_url_encode(title)}"
                    f"&location={_url_encode(config.location)}"
                    f"&f_TPR=r86400"   # past 24 hours
                    f"&f_E=1,2"        # internship + entry level
                )
                signals.last_query = title

                try:
                    logger.info("scan_linkedin: fetching '%s'", search_url)
                    load_start = time.monotonic()
                    page.goto(search_url, timeout=45_000, wait_until="networkidle")
                    signals.last_load_time_ms = int((time.monotonic() - load_start) * 1000)
                    signals.record_request()

                    # LinkedIn has ML detection — always simulate human interaction
                    simulate_human_interaction(page)
                    signals.simulated_mouse = True

                    # Check for verification wall after page load
                    wall = detect_verification_wall(page, expected_results=True)
                    if wall and wall.confidence >= 0.7:
                        logger.warning(
                            "scan_linkedin: verification wall detected (%s, %.0f%%) — aborting",
                            wall.wall_type, wall.confidence * 100,
                        )
                        _handle_block(engine, "linkedin", wall, signals)
                        return results

                    # Wait for job cards to render (LinkedIn loads async)
                    try:
                        page.wait_for_selector(".job-card-container, .jobs-search-results-list", timeout=15_000)
                    except Exception:
                        logger.warning("scan_linkedin: job cards not found, trying scroll")
                    # Scroll to trigger lazy loading
                    page.mouse.wheel(0, 500)
                    time.sleep(random.uniform(delay_min, delay_max))

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

                            # Click into job to get full description
                            description = ""
                            try:
                                card.click()
                                signals.record_request()
                                time.sleep(random.uniform(1.5, 3.0))

                                # Check for verification wall after card click
                                wall = detect_verification_wall(page)
                                if wall and wall.confidence >= 0.7:
                                    logger.warning(
                                        "scan_linkedin: wall after card click (%s) — returning partial results",
                                        wall.wall_type,
                                    )
                                    _handle_block(engine, "linkedin", wall, signals)
                                    return results

                                # Wait for JD side panel to render (LinkedIn loads async via AJAX)
                                _JD_PANEL_SELECTORS = (
                                    "#job-details, "
                                    ".jobs-description-content__text, "
                                    ".jobs-description__content, "
                                    ".jobs-box__html-content, "
                                    ".jobs-unified-top-card__job-insight, "
                                    "[class*='jobs-description']"
                                )
                                try:
                                    page.wait_for_selector(_JD_PANEL_SELECTORS, timeout=8_000)
                                except Exception:
                                    logger.debug("scan_linkedin: JD panel selector timeout for %s", job_title)

                                # Try extracting from side panel
                                desc_el = page.query_selector(_JD_PANEL_SELECTORS)
                                if desc_el:
                                    description = desc_el.inner_text()[:5000]

                                # Fallback: if description is empty/too short, navigate to full job page
                                if len(description.strip()) < 50 and href:
                                    logger.info(
                                        "scan_linkedin: side panel empty for '%s', navigating to full page",
                                        job_title,
                                    )
                                    try:
                                        page.goto(href, timeout=20_000, wait_until="networkidle")
                                        time.sleep(random.uniform(1.5, 3.0))
                                        simulate_human_interaction(page)

                                        # Full page has different selectors
                                        full_desc_el = page.query_selector(
                                            "#job-details, "
                                            ".description__text, "
                                            ".show-more-less-html__markup, "
                                            "[class*='description']"
                                        )
                                        if full_desc_el:
                                            description = full_desc_el.inner_text()[:5000]
                                            logger.info(
                                                "scan_linkedin: got %d chars from full page for '%s'",
                                                len(description), job_title,
                                            )

                                        # Navigate back to search results
                                        page.go_back(timeout=15_000)
                                        time.sleep(random.uniform(1.0, 2.0))
                                    except Exception as nav_err:
                                        logger.debug(
                                            "scan_linkedin: full page fallback failed: %s", nav_err
                                        )
                                        # Try to get back to search
                                        try:
                                            page.goto(search_url, timeout=30_000, wait_until="networkidle")
                                            time.sleep(random.uniform(2.0, 4.0))
                                        except Exception:
                                            pass
                            except Exception as desc_err:
                                logger.debug(
                                    "scan_linkedin: could not fetch JD detail: %s",
                                    desc_err,
                                )

                            # Try extracting salary from card text
                            salary_min_val = None
                            salary_max_val = None
                            card_text = " ".join(lines)
                            import re as _re
                            sal_match = _re.search(
                                r"£([\d,]+)\s*[-–]\s*£([\d,]+)", card_text
                            )
                            if sal_match:
                                try:
                                    salary_min_val = float(sal_match.group(1).replace(",", ""))
                                    salary_max_val = float(sal_match.group(2).replace(",", ""))
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
                            logger.debug("scan_linkedin: error parsing card: %s", card_err)
                            continue

                except Exception as page_err:
                    logger.error("scan_linkedin: error fetching '%s': %s", search_url, page_err)

            # Session completed without blocks — record success
            _record_success(engine, "linkedin", signals)

    except Exception as exc:
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

    stub_platforms = {"totaljobs", "glassdoor"}

    all_jobs: list[dict[str, Any]] = []

    for platform in platforms:
        if platform in stub_platforms:
            logger.warning(
                "scan_platforms: '%s' is not yet implemented — skipping. "
                "Only reed, linkedin, and indeed are functional.",
                platform,
            )
            continue
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
