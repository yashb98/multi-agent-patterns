"""Reed.co.uk scanner — official REST API with full JD enrichment."""

from __future__ import annotations

import random
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from shared.logging_config import get_logger

from jobpulse.config import REED_API_KEY
from jobpulse.job_scanners import (
    MAX_REQUESTS_PER_PLATFORM,
    SessionSignals,
    anti_detection_sleep,
    handle_block,
    make_job_id,
    random_ua,
    record_success,
    to_float,
)
from jobpulse.models.application_models import SearchConfig
from jobpulse.scan_learning import ScanLearningEngine

logger = get_logger(__name__)


def scan_reed(config: SearchConfig) -> list[dict[str, Any]]:
    """Query the Reed.co.uk official REST API and return raw job dicts.

    Reed API docs: https://www.reed.co.uk/developers/jobseeker
    Basic auth: (REED_API_KEY, "")
    """
    if not REED_API_KEY:
        logger.warning("scan_reed: REED_API_KEY not set — skipping Reed scan")
        return []

    engine = ScanLearningEngine()
    if not engine.can_scan_now("reed"):
        cooldown = engine.get_cooldown_info("reed")
        logger.warning(
            "scan_reed: cooldown active until %s — skipping scan",
            cooldown.get("cooldown_until") if cooldown else "unknown",
        )
        return []

    ua = random_ua()
    signals = SessionSignals("reed", ua)

    results: list[dict[str, Any]] = []
    base_url = "https://www.reed.co.uk/api/1.0/search"
    blocked_terminally = False

    for title in config.titles:
        if blocked_terminally or len(results) >= MAX_REQUESTS_PER_PLATFORM:
            break

        signals.last_query = title

        try:
            logger.info("scan_reed: searching '%s' in '%s'", title, config.location)
            page_size = 25
            max_pages = 5

            with httpx.Client(timeout=20) as client:
                for page in range(max_pages):
                    if len(results) >= MAX_REQUESTS_PER_PLATFORM:
                        break

                    params: dict[str, Any] = {
                        "keywords": title,
                        "locationName": config.location,
                        "distanceFromLocation": 50,
                        "minimumSalary": config.salary_min,
                        "resultsToTake": page_size,
                        "resultsToSkip": page * page_size,
                        "fullTime": True,
                        "graduate": True,
                        "postedByDirectEmployer": True,
                    }

                    data = None
                    for retry in range(3):
                        load_start = time.monotonic()
                        resp = client.get(
                            base_url,
                            params=params,
                            auth=(REED_API_KEY, ""),
                            headers={"User-Agent": ua},
                        )
                        signals.last_load_time_ms = int(
                            (time.monotonic() - load_start) * 1000
                        )
                        signals.record_request()

                        if resp.status_code == 429:
                            wait = 2 ** (retry + 1)
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
                            pass
                        data = resp.json()
                        break
                    else:
                        # for/else: retries exhausted → terminal 429 block
                        logger.error("scan_reed: rate limited after 3 retries for '%s' page %d", title, page + 1)
                        handle_block(engine, "reed", "http_429", signals)
                        blocked_terminally = True
                        break

                    if data is None:
                        break

                    page_results = data.get("results", [])
                    if not page_results:
                        break

                    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
                    for job in page_results:
                        posted = job.get("date")
                        if posted:
                            try:
                                posted_dt = datetime.fromisoformat(posted.replace("Z", "+00:00"))
                                if posted_dt < cutoff:
                                    continue
                            except (ValueError, TypeError):
                                pass

                        url = job.get("jobUrl", "")
                        reed_id = str(job.get("jobId", ""))
                        if not url:
                            url = f"https://www.reed.co.uk/jobs/{reed_id}" if reed_id else ""

                        results.append(
                            {
                                "title": job.get("jobTitle", ""),
                                "company": job.get("employerName", ""),
                                "url": url,
                                "location": job.get("locationName", ""),
                                "salary_min": to_float(job.get("minimumSalary")),
                                "salary_max": to_float(job.get("maximumSalary")),
                                "description": job.get("jobDescription", ""),
                                "platform": "reed",
                                "job_id": make_job_id(url) if url else make_job_id(reed_id),
                                "reed_id": reed_id,
                            }
                        )

                    logger.info(
                        "scan_reed: page %d got %d results for '%s' (total so far: %d)",
                        page + 1, len(page_results), title, len(results),
                    )

                    if len(page_results) < page_size:
                        break

                    time.sleep(random.uniform(0.5, 1.5))

        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            logger.error(
                "scan_reed: HTTP %s for title '%s': %s",
                status, title, exc,
            )
            # 429 / 503 are scraper-block signals; 401 / 403 mean wrong API
            # key — that's a config problem, not a block. Don't trip a
            # cooldown for auth issues (Reed would silently throttle real
            # scans afterwards). 5xx server errors are transient.
            if status in (429, 503):
                handle_block(engine, "reed", f"http_{status}", signals)
                blocked_terminally = True
        except Exception as exc:
            logger.error("scan_reed: unexpected error for title '%s': %s", title, exc)

        anti_detection_sleep()

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
                    headers={"User-Agent": random_ua()},
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

        time.sleep(random.uniform(0.5, 1.5))

    if results and not blocked_terminally:
        record_success(engine, "reed", signals)

    logger.info("scan_reed: returning %d total results", len(results))
    return results
