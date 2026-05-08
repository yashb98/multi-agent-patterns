"""Job Scanner — orchestrates platform scanners and returns raw job dicts.

Platform scanners live in jobpulse/job_scanners/ (one file per platform).
This module handles config persistence, platform dispatch, and liveness checks.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from shared.logging_config import get_logger

from jobpulse.config import DATA_DIR
from jobpulse.models.application_models import SearchConfig

# Re-export platform scanners for backward compatibility
from jobpulse.job_scanners.reed import scan_reed  # noqa: F401
from jobpulse.job_scanners.linkedin import scan_linkedin  # noqa: F401

logger = get_logger(__name__)

_CONFIG_PATH = DATA_DIR / "job_search_config.json"


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
# Platform dispatch
# ---------------------------------------------------------------------------


def _scan_indeed_wrapper(config: SearchConfig) -> list[dict[str, Any]]:
    """Wrap the JobSpy-based Indeed scanner to match the SearchConfig signature."""
    from jobpulse.job_scanners.indeed import scan_indeed

    return scan_indeed(config.titles, config.location, max_results=50)


PLATFORM_SCANNERS: dict[str, Any] = {
    "reed": scan_reed,
    "linkedin": scan_linkedin,
    "indeed": _scan_indeed_wrapper,
}

ALL_PLATFORMS: list[str] = list(PLATFORM_SCANNERS.keys())


def scan_platforms(platforms: list[str] | None = None) -> list[dict[str, Any]]:
    """Scan the requested platforms and return a combined list of raw job dicts."""
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

    try:
        from jobpulse.ats_api_scanner import scan_ats_api

        raw_config = json.loads(_CONFIG_PATH.read_text(encoding="utf-8")) if _CONFIG_PATH.exists() else {}
        for entry in raw_config.get("ats_companies", []):
            ats_results = scan_ats_api(entry["url"], entry["name"])
            all_jobs.extend(ats_results)
            logger.info("scan_platforms: ATS %s returned %d jobs", entry["name"], len(ats_results))
    except Exception as exc:
        logger.warning("scan_platforms: ATS API scanning failed: %s", exc)

    logger.info("scan_platforms: total raw jobs collected = %d", len(all_jobs))
    return all_jobs


# ---------------------------------------------------------------------------
# Liveness check
# ---------------------------------------------------------------------------


def check_liveness_batch(listings: list[dict], timeout: float = 15.0) -> tuple[list[dict], list[dict]]:
    """Check liveness of job URLs via HTTP. Returns (alive, expired)."""
    from jobpulse.liveness_checker import classify_liveness

    alive, expired = [], []
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        for listing in listings:
            url = listing.get("url", "")
            if not url:
                alive.append(listing)
                continue
            try:
                resp = client.get(url)
                result = classify_liveness(
                    status_code=resp.status_code,
                    url=str(resp.url),
                    body=resp.text[:5000],
                )
                if result.status == "expired":
                    expired.append({**listing, "liveness": result.reason})
                else:
                    alive.append(listing)
            except httpx.HTTPError:
                alive.append(listing)
    return alive, expired
