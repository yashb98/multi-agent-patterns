"""API Rate Limit Monitor — tracks remaining quota for external APIs.

Captures rate limit headers from API responses and stores snapshots
in SQLite. Warns when any API drops below 20% remaining quota.
"""

import sqlite3
import json
from datetime import datetime
from pathlib import Path
from shared.logging_config import get_logger
from shared.db import get_db_conn

logger = get_logger(__name__)

from shared.paths import DATA_DIR as _DATA_DIR
DB_PATH = _DATA_DIR / "mindgraph.db"


def _get_conn() -> sqlite3.Connection:
    return get_db_conn(DB_PATH)


def _init_db():
    conn = _get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS api_rate_limits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            api_name TEXT NOT NULL,
            endpoint TEXT DEFAULT '',
            limit_total INTEGER,
            limit_remaining INTEGER,
            limit_reset TEXT,
            recorded_at TEXT NOT NULL
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_rate_api ON api_rate_limits(api_name, recorded_at)"
    )
    conn.commit()
    conn.close()


_init_db()

# Threshold below which we warn (fraction of total)
WARN_THRESHOLD = 0.20


def record_rate_limit(
    api_name: str,
    endpoint: str = "",
    limit_total: int = None,
    limit_remaining: int = None,
    limit_reset: str = None,
):
    """Record a rate limit snapshot from an API response."""
    conn = _get_conn()
    conn.execute(
        "INSERT INTO api_rate_limits (api_name, endpoint, limit_total, limit_remaining, limit_reset, recorded_at) VALUES (?,?,?,?,?,?)",
        (api_name, endpoint, limit_total, limit_remaining, limit_reset, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()

    # Warn if below threshold
    if limit_total and limit_remaining is not None and limit_total > 0:
        ratio = limit_remaining / limit_total
        if ratio < WARN_THRESHOLD:
            logger.warning(
                "%s rate limit low: %d/%d remaining (%.0f%%) — resets %s",
                api_name, limit_remaining, limit_total, ratio * 100, limit_reset or "unknown",
            )


def record_from_headers(api_name: str, headers: dict, endpoint: str = ""):
    """Extract rate limit info from HTTP response headers.

    Works with GitHub, OpenAI, Notion, and Telegram header conventions.
    """
    # Normalize header keys to lowercase
    h = {k.lower(): v for k, v in headers.items()}

    limit_total = None
    limit_remaining = None
    limit_reset = None

    # GitHub: x-ratelimit-limit, x-ratelimit-remaining, x-ratelimit-reset
    # OpenAI: x-ratelimit-limit-requests, x-ratelimit-remaining-requests
    # Notion: x-ratelimit-limit
    for key in ("x-ratelimit-limit", "x-ratelimit-limit-requests", "ratelimit-limit"):
        if key in h:
            try:
                limit_total = int(h[key])
            except (ValueError, TypeError):
                pass
            break

    for key in ("x-ratelimit-remaining", "x-ratelimit-remaining-requests", "ratelimit-remaining"):
        if key in h:
            try:
                limit_remaining = int(h[key])
            except (ValueError, TypeError):
                pass
            break

    for key in ("x-ratelimit-reset", "ratelimit-reset"):
        if key in h:
            limit_reset = h[key]
            break

    if limit_total is not None or limit_remaining is not None:
        record_rate_limit(api_name, endpoint, limit_total, limit_remaining, limit_reset)


def get_current_limits() -> list[dict]:
    """Get the most recent rate limit snapshot per API."""
    conn = _get_conn()
    rows = conn.execute("""
        SELECT api_name, endpoint, limit_total, limit_remaining, limit_reset, recorded_at
        FROM api_rate_limits
        WHERE id IN (
            SELECT MAX(id) FROM api_rate_limits GROUP BY api_name
        )
        ORDER BY api_name
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_history(api_name: str, limit: int = 50) -> list[dict]:
    """Get rate limit history for a specific API."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM api_rate_limits WHERE api_name=? ORDER BY recorded_at DESC LIMIT ?",
        (api_name, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def cleanup_old_records(retention_days: int = 7):
    """Delete rate limit records older than retention_days."""
    from datetime import timedelta
    cutoff = (datetime.now() - timedelta(days=retention_days)).isoformat()
    conn = _get_conn()
    cursor = conn.execute("DELETE FROM api_rate_limits WHERE recorded_at < ?", (cutoff,))
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    if deleted > 0:
        logger.debug("Cleaned up %d rate limit records older than %d days", deleted, retention_days)
    return deleted
