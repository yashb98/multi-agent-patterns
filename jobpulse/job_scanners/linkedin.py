"""LinkedIn scanner — public guest API (httpx + BeautifulSoup, no login)."""

from __future__ import annotations

import random
import re
import time
from typing import Any

import httpx
from bs4 import BeautifulSoup

from shared.logging_config import get_logger

from jobpulse.job_scanners import (
    MAX_REQUESTS_PER_PLATFORM,
    SessionSignals,
    make_job_id,
    random_ua,
    record_success,
    url_encode,
)
from jobpulse.models.application_models import SearchConfig
from jobpulse.scan_learning import ScanLearningEngine

logger = get_logger(__name__)


def scan_linkedin(config: SearchConfig) -> list[dict[str, Any]]:
    """LinkedIn job search via public guest API (no login required).

    Uses LinkedIn's guest jobs API which returns public HTML listings.
    No browser automation needed — uses httpx + BeautifulSoup.
    No reCAPTCHA risk since no Playwright/browser fingerprinting.
    """
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
    ua = random_ua()
    signals = SessionSignals("linkedin", ua)

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
                        f"?keywords={url_encode(title)}"
                        f"&location={url_encode(config.location)}"
                        f"&f_TPR=r86400"
                        f"&f_E=1%2C2"
                        f"&f_JT=F"
                        f"&start={start}"
                    )

                    logger.info(
                        "scan_linkedin: fetching page start=%d for '%s'",
                        start, title,
                    )

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
                            wait = (retry + 1) * 5
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
                        pass

                    soup = BeautifulSoup(resp.text, "html.parser")
                    cards = soup.select(
                        "div.base-search-card, div.job-search-card"
                    )

                    if not cards:
                        logger.info(
                            "scan_linkedin: no more cards at start=%d for '%s'",
                            start, title,
                        )
                        break

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

                            if href and not href.startswith("http"):
                                href = "https://www.linkedin.com" + href

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
                            except Exception as detail_err:
                                logger.debug(
                                    "scan_linkedin: detail fetch failed for "
                                    "'%s': %s",
                                    job_title, detail_err,
                                )

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
                                    "job_id": make_job_id(href),
                                }
                            )
                        except Exception as card_err:
                            logger.debug(
                                "scan_linkedin: error parsing card: %s",
                                card_err,
                            )
                            continue

                    start += 25
                    time.sleep(random.uniform(2.0, 5.0))

            record_success(engine, "linkedin", signals)

    except Exception as exc:
        logger.error("scan_linkedin: guest API error: %s", exc)

    logger.info("scan_linkedin: returning %d total results", len(results))
    return results
