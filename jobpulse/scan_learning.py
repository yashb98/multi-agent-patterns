"""Scan Learning Engine — records scan session events with 17 signals.

Learns what triggers verification walls so the job autopilot can adapt
its scanning behaviour per platform.
"""

from __future__ import annotations

import hashlib
import sqlite3
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any

from shared.logging_config import get_logger
from jobpulse.config import DATA_DIR

logger = get_logger(__name__)

_DEFAULT_DB_PATH = str(DATA_DIR / "scan_learning.db")


# ---------------------------------------------------------------------------
# Helper bucket functions
# ---------------------------------------------------------------------------


def _time_bucket(dt: datetime) -> str:
    """Map hour to time-of-day bucket."""
    hour = dt.hour
    if 6 <= hour < 12:
        return "morning"
    elif 12 <= hour < 17:
        return "afternoon"
    elif 17 <= hour < 22:
        return "evening"
    else:
        return "night"


def _requests_bucket(n: int) -> str:
    """Map request count to bucket."""
    if n <= 3:
        return "1-3"
    elif n <= 6:
        return "4-6"
    elif n <= 10:
        return "7-10"
    else:
        return "11+"


def _delay_bucket(avg: float) -> str:
    """Map average delay (seconds) to bucket."""
    if avg < 2.0:
        return "<2s"
    elif avg < 4.0:
        return "2-4s"
    elif avg < 8.0:
        return "4-8s"
    else:
        return "8s+"


def _session_age_bucket(seconds: float) -> str:
    """Map session age (seconds) to bucket."""
    if seconds < 300:
        return "<5min"
    elif seconds < 600:
        return "5-10min"
    elif seconds < 900:
        return "10-15min"
    else:
        return "15min+"


def _pages_bucket(n: int) -> str:
    """Map page count to bucket."""
    if n <= 3:
        return "1-3"
    elif n <= 6:
        return "4-6"
    elif n <= 10:
        return "7-10"
    else:
        return "11+"


# ---------------------------------------------------------------------------
# ScanLearningEngine
# ---------------------------------------------------------------------------


class ScanLearningEngine:
    """Records scan session events and learns verification wall triggers."""

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or _DEFAULT_DB_PATH
        self._init_db()

    def _init_db(self) -> None:
        """Create the three tables if they don't exist."""
        conn = sqlite3.connect(self.db_path)
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS scan_events (
                id TEXT PRIMARY KEY,
                platform TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                time_of_day_bucket TEXT NOT NULL,
                requests_in_session INTEGER NOT NULL,
                avg_delay REAL NOT NULL,
                session_age_seconds REAL NOT NULL,
                user_agent_hash TEXT NOT NULL,
                was_fresh_session INTEGER NOT NULL,
                simulated_mouse INTEGER NOT NULL,
                used_vpn INTEGER NOT NULL,
                referrer_chain TEXT NOT NULL,
                search_query TEXT NOT NULL,
                pages_before_block INTEGER NOT NULL,
                browser_fingerprint TEXT NOT NULL,
                waited_for_page_load INTEGER NOT NULL,
                page_load_time_ms INTEGER NOT NULL,
                outcome TEXT NOT NULL,
                wall_type TEXT
            );

            CREATE TABLE IF NOT EXISTS learned_rules (
                id TEXT PRIMARY KEY,
                platform TEXT NOT NULL,
                rule_text TEXT NOT NULL,
                confidence REAL NOT NULL,
                recommendation TEXT NOT NULL,
                source TEXT NOT NULL,
                created_at TEXT NOT NULL,
                times_applied INTEGER DEFAULT 0,
                times_successful INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS cooldowns (
                platform TEXT PRIMARY KEY,
                blocked_at TEXT NOT NULL,
                cooldown_until TEXT NOT NULL,
                consecutive_blocks INTEGER DEFAULT 1,
                last_wall_type TEXT
            );
            """
        )
        conn.close()

    def record_event(
        self,
        *,
        platform: str,
        requests_in_session: int,
        avg_delay: float,
        session_age_seconds: float,
        user_agent_hash: str,
        was_fresh_session: bool,
        used_vpn: bool,
        simulated_mouse: bool,
        referrer_chain: str,
        search_query: str,
        pages_before_block: int,
        browser_fingerprint: str,
        waited_for_page_load: bool,
        page_load_time_ms: int,
        outcome: str,
        wall_type: str | None = None,
    ) -> str:
        """Record a single scan session event. Returns the event ID."""
        event_id = uuid.uuid4().hex[:16]
        now = datetime.now(timezone.utc)
        bucket = _time_bucket(now)

        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """
            INSERT INTO scan_events (
                id, platform, timestamp, time_of_day_bucket,
                requests_in_session, avg_delay, session_age_seconds,
                user_agent_hash, was_fresh_session, used_vpn,
                simulated_mouse, referrer_chain, search_query,
                pages_before_block, browser_fingerprint,
                waited_for_page_load, page_load_time_ms,
                outcome, wall_type
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                platform,
                now.isoformat(),
                bucket,
                requests_in_session,
                avg_delay,
                session_age_seconds,
                user_agent_hash,
                int(was_fresh_session),
                int(used_vpn),
                int(simulated_mouse),
                referrer_chain,
                search_query,
                pages_before_block,
                browser_fingerprint,
                int(waited_for_page_load),
                page_load_time_ms,
                outcome,
                wall_type,
            ),
        )
        conn.commit()
        conn.close()
        logger.info(
            "Recorded scan event %s: platform=%s outcome=%s",
            event_id,
            platform,
            outcome,
        )
        return event_id

    def get_total_blocks(self, platform: str | None = None) -> int:
        """Count rows where outcome='blocked', optionally filtered by platform."""
        conn = sqlite3.connect(self.db_path)
        if platform is not None:
            row = conn.execute(
                "SELECT COUNT(*) FROM scan_events WHERE outcome = 'blocked' AND platform = ?",
                (platform,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT COUNT(*) FROM scan_events WHERE outcome = 'blocked'"
            ).fetchone()
        conn.close()
        return row[0] if row else 0

    # --- Cooldown Manager ---

    _COOLDOWN_HOURS: dict[int, int] = {1: 2, 2: 4}  # consecutive_blocks → hours
    _MAX_COOLDOWN_HOURS: int = 48

    def can_scan_now(self, platform: str) -> bool:
        """Check if platform is NOT in cooldown."""
        conn = sqlite3.connect(self.db_path)
        row = conn.execute(
            "SELECT cooldown_until FROM cooldowns WHERE platform = ?",
            (platform,),
        ).fetchone()
        if row is None:
            conn.close()
            return True
        cooldown_until = datetime.fromisoformat(row[0])
        if datetime.now(timezone.utc) >= cooldown_until:
            conn.execute("DELETE FROM cooldowns WHERE platform = ?", (platform,))
            conn.commit()
            conn.close()
            return True
        conn.close()
        return False

    def start_cooldown(self, platform: str, wall_type: str) -> None:
        """Start or extend cooldown for a platform after a block."""
        now = datetime.now(timezone.utc)
        conn = sqlite3.connect(self.db_path)
        row = conn.execute(
            "SELECT consecutive_blocks FROM cooldowns WHERE platform = ?",
            (platform,),
        ).fetchone()

        consecutive = (row[0] + 1) if row else 1
        hours = self._COOLDOWN_HOURS.get(consecutive, self._MAX_COOLDOWN_HOURS)
        cooldown_until = now + timedelta(hours=hours)

        conn.execute(
            """INSERT INTO cooldowns (platform, blocked_at, cooldown_until, consecutive_blocks, last_wall_type)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(platform) DO UPDATE SET
                   blocked_at = ?, cooldown_until = ?, consecutive_blocks = ?, last_wall_type = ?""",
            (
                platform, now.isoformat(), cooldown_until.isoformat(), consecutive, wall_type,
                now.isoformat(), cooldown_until.isoformat(), consecutive, wall_type,
            ),
        )
        conn.commit()
        conn.close()

        logger.warning(
            "Cooldown started: %s blocked (%s), %dhr cooldown (block #%d), until %s",
            platform, wall_type, hours, consecutive, cooldown_until.isoformat(),
        )

    def reset_cooldown(self, platform: str) -> None:
        """Reset cooldown after a successful scan."""
        conn = sqlite3.connect(self.db_path)
        conn.execute("DELETE FROM cooldowns WHERE platform = ?", (platform,))
        conn.commit()
        conn.close()
        logger.info("Cooldown reset for %s after successful scan", platform)

    def get_cooldown_info(self, platform: str) -> dict[str, Any] | None:
        """Get current cooldown state for a platform."""
        conn = sqlite3.connect(self.db_path)
        row = conn.execute(
            "SELECT blocked_at, cooldown_until, consecutive_blocks, last_wall_type "
            "FROM cooldowns WHERE platform = ?",
            (platform,),
        ).fetchone()
        conn.close()
        if row is None:
            return None
        return {
            "blocked_at": row[0],
            "cooldown_until": row[1],
            "consecutive_blocks": row[2],
            "last_wall_type": row[3],
        }
