"""Job deduplicator for the Job Autopilot pipeline.

Filters incoming job listings against already-tracked applications stored in
JobDB using two checks (applied in order):

  1. Exact URL match  — job_id (SHA-256 of URL) already in the db.
  2. Fuzzy company+title match — same company (case-insensitive) with a title
     word-overlap Jaccard score >= 0.8 within the last 30 days.

Only genuinely new listings are returned.
"""

from __future__ import annotations

from jobpulse.job_db import JobDB
from jobpulse.models.application_models import JobListing
from shared.logging_config import get_logger

logger = get_logger(__name__)


def deduplicate(listings: list[JobListing], db: JobDB) -> list[JobListing]:
    """Filter out listings matching existing applications.

    Checks (in order):
      1. Exact URL match — job_id (SHA-256 of URL) already in db.
      2. Fuzzy company + title — same company + similar title
         (word overlap >= 0.8) within 30 days.

    Returns only genuinely new listings.
    """
    if not listings:
        return []

    new_listings: list[JobListing] = []
    # Track company+title within this batch to catch same-batch duplicates
    seen_in_batch: set[str] = set()

    for listing in listings:
        # --- Check 0: within-batch dedup (same company+title) ---
        batch_key = f"{listing.company.lower().strip()}|{listing.title.lower().strip()}"
        if batch_key in seen_in_batch:
            logger.debug(
                "Dedup: batch duplicate — title=%r company=%r",
                listing.title,
                listing.company,
            )
            continue
        seen_in_batch.add(batch_key)

        # --- Check 1: exact job_id match ---
        if db.listing_exists(listing.job_id):
            logger.debug(
                "Dedup: exact match — job_id=%s title=%r company=%r",
                listing.job_id,
                listing.title,
                listing.company,
            )
            continue

        # --- Check 2: fuzzy company + title match ---
        if db.fuzzy_match_exists(listing.company, listing.title):
            logger.debug(
                "Dedup: fuzzy match — job_id=%s title=%r company=%r",
                listing.job_id,
                listing.title,
                listing.company,
            )
            continue

        new_listings.append(listing)

    logger.info(
        "Dedup: %d in, %d new, %d filtered",
        len(listings),
        len(new_listings),
        len(listings) - len(new_listings),
    )
    return new_listings
