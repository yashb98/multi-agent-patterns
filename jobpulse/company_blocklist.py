"""Company blocklist — spam detection + Notion-backed blocklist cache.

Detects spam companies (training providers, recruitment agencies, etc.)
and maintains a Notion database of blocked/approved/pending companies.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from datetime import date

from shared.logging_config import get_logger

from jobpulse.config import NOTION_API_KEY

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Spam detection
# ---------------------------------------------------------------------------

SPAM_KEYWORDS: list[str] = [
    "training",
    "bootcamp",
    "academy",
    "career switch",
    "career change",
    "recruitment agency",
    "staffing",
    "talent pipeline",
    "apprenticeship scheme",
]

LISTING_COUNT_THRESHOLD = 10


@dataclass
class SpamDetectionResult:
    """Result of spam detection for a company."""

    is_spam: bool
    reason: str
    company: str


def detect_spam_company(
    company: str, listing_count_7d: int = 0
) -> SpamDetectionResult:
    """Check whether a company name looks like a spam/training provider.

    Args:
        company: Company name to check.
        listing_count_7d: Number of listings this company posted in the last 7 days.

    Returns:
        SpamDetectionResult with is_spam flag and reason.
    """
    name_lower = company.lower()

    for keyword in SPAM_KEYWORDS:
        if keyword in name_lower:
            return SpamDetectionResult(
                is_spam=True,
                reason=f"Spam keyword detected: '{keyword}'",
                company=company,
            )

    if listing_count_7d >= LISTING_COUNT_THRESHOLD:
        return SpamDetectionResult(
            is_spam=True,
            reason=f"Excessive listings ({listing_count_7d} in 7 days)",
            company=company,
        )

    return SpamDetectionResult(is_spam=False, reason="", company=company)


# ---------------------------------------------------------------------------
# Notion blocklist cache
# ---------------------------------------------------------------------------


def _get_blocklist_db_id() -> str:
    """Lazily load the Notion blocklist DB ID from env."""
    db_id = os.getenv("NOTION_BLOCKLIST_DB_ID", "")
    if not db_id:
        logger.warning("NOTION_BLOCKLIST_DB_ID not set — blocklist features disabled")
    return db_id


def _notion_api(method: str, endpoint: str, data: dict | None = None) -> dict:
    """Call Notion API via curl (same pattern as job_notion_sync.py)."""
    cmd = [
        "curl", "-s", "-X", method,
        f"https://api.notion.com/v1{endpoint}",
        "-H", f"Authorization: Bearer {NOTION_API_KEY}",
        "-H", "Content-Type: application/json",
        "-H", "Notion-Version: 2022-06-28",
    ]
    if data:
        cmd.extend(["-d", json.dumps(data)])

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        return json.loads(result.stdout) if result.stdout else {}
    except Exception as e:
        logger.error("Notion API error %s %s: %s", method, endpoint, e)
        return {}


class BlocklistCache:
    """In-memory cache of company blocklist entries from Notion."""

    def __init__(self) -> None:
        self._entries: dict[str, str] = {}  # company_lower → status

    def refresh(self) -> None:
        """Refresh cache from Notion."""
        self._entries = fetch_blocklist_from_notion()
        logger.info("Blocklist cache refreshed: %d entries", len(self._entries))

    def is_blocked(self, company: str) -> bool:
        """Return True only if company status is 'Blocked'."""
        return self._entries.get(company.lower()) == "Blocked"

    def is_approved(self, company: str) -> bool:
        """Return True only if company status is 'Approved'."""
        return self._entries.get(company.lower()) == "Approved"

    def is_known(self, company: str) -> bool:
        """Return True if company exists in the blocklist (any status)."""
        return company.lower() in self._entries


# ---------------------------------------------------------------------------
# Notion CRUD
# ---------------------------------------------------------------------------


def fetch_blocklist_from_notion() -> dict[str, str]:
    """Query Notion blocklist DB and return {company_lower: status}."""
    db_id = _get_blocklist_db_id()
    if not db_id:
        return {}

    entries: dict[str, str] = {}
    has_more = True
    start_cursor: str | None = None

    while has_more:
        payload: dict = {}
        if start_cursor:
            payload["start_cursor"] = start_cursor

        resp = _notion_api("POST", f"/databases/{db_id}/query", payload)
        results = resp.get("results", [])

        for page in results:
            props = page.get("properties", {})
            # Company is a title property
            company_parts = props.get("Company", {}).get("title", [])
            company = company_parts[0]["plain_text"] if company_parts else ""
            # Status is a select property
            status_obj = props.get("Status", {}).get("select")
            status = status_obj["name"] if status_obj else "Pending"

            if company:
                entries[company.lower()] = status

        has_more = resp.get("has_more", False)
        start_cursor = resp.get("next_cursor")

    return entries


def flag_company_in_notion(
    company: str,
    reason: str,
    platform: str = "",
    times_seen: int = 1,
) -> dict:
    """Create a page in the Notion blocklist DB with Status='Pending'.

    Args:
        company: Company name.
        reason: Why it was flagged (spam keyword, excessive listings, etc.).
        platform: Source platform (reed, linkedin, etc.).
        times_seen: How many times this company has been seen.

    Returns:
        Notion API response dict.
    """
    db_id = _get_blocklist_db_id()
    if not db_id:
        return {}

    today = date.today().isoformat()
    payload = {
        "parent": {"database_id": db_id},
        "properties": {
            "Company": {
                "title": [{"text": {"content": company}}],
            },
            "Status": {
                "select": {"name": "Pending"},
            },
            "Reason": {
                "rich_text": [{"text": {"content": reason}}],
            },
            "Platform": {
                "select": {"name": platform or "Unknown"},
            },
            "Times Seen": {
                "number": times_seen,
            },
            "First Seen": {
                "date": {"start": today},
            },
            "Last Seen": {
                "date": {"start": today},
            },
        },
    }

    resp = _notion_api("POST", "/pages", payload)
    if resp.get("id"):
        logger.info("Flagged company '%s' in Notion blocklist (reason: %s)", company, reason)
    else:
        logger.error("Failed to flag company '%s': %s", company, resp)
    return resp


