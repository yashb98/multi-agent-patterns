"""Per-platform daily application quota tracking."""

import sqlite3
from datetime import date

from shared.logging_config import get_logger

from jobpulse.config import DATA_DIR

logger = get_logger(__name__)

DAILY_CAPS: dict[str, int] = {
    "linkedin": 15,
    "indeed": 10,
    "reed": 4,
    "totaljobs": 4,
    "greenhouse": 7,
    "lever": 7,
    "workday": 7,
    "generic": 7,
}
TOTAL_DAILY_CAP = 40
SESSION_BREAK_EVERY = 10
SESSION_BREAK_MINUTES = 5


class RateLimiter:
    """Tracks daily application counts per platform with configurable caps."""

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or str(DATA_DIR / "rate_limits.db")
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS daily_counts (
                    date TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    count INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (date, platform)
                )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS session_tracker (
                    date TEXT PRIMARY KEY,
                    total_today INTEGER NOT NULL DEFAULT 0,
                    last_break_at INTEGER NOT NULL DEFAULT 0
                )"""
            )
            conn.commit()

    def _today(self) -> str:
        return date.today().isoformat()

    def _get_platform_count(self, platform: str) -> int:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT count FROM daily_counts WHERE date = ? AND platform = ?",
                (self._today(), platform),
            ).fetchone()
            return row[0] if row else 0

    def get_total_today(self) -> int:
        """Total applications recorded today across all platforms."""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(count), 0) FROM daily_counts WHERE date = ?",
                (self._today(),),
            ).fetchone()
            return row[0] if row else 0

    def can_apply(self, platform: str) -> bool:
        """True if platform count < cap AND total < TOTAL_DAILY_CAP."""
        platform = platform.lower()
        cap = DAILY_CAPS.get(platform, DAILY_CAPS["generic"])
        platform_count = self._get_platform_count(platform)
        total = self.get_total_today()

        if platform_count >= cap:
            logger.info("Platform cap reached for %s (%d/%d)", platform, platform_count, cap)
            return False
        if total >= TOTAL_DAILY_CAP:
            logger.info("Total daily cap reached (%d/%d)", total, TOTAL_DAILY_CAP)
            return False
        return True

    def record_application(self, platform: str) -> None:
        """Increment today's count for the given platform (atomic)."""
        from jobpulse.utils.safe_io import atomic_sqlite

        platform = platform.lower()
        today = self._today()
        with atomic_sqlite(self.db_path) as conn:
            conn.execute(
                """INSERT INTO daily_counts (date, platform, count) VALUES (?, ?, 1)
                   ON CONFLICT(date, platform) DO UPDATE SET count = count + 1""",
                (today, platform),
            )
            row = conn.execute(
                "SELECT COALESCE(SUM(count), 0) FROM daily_counts WHERE date = ?",
                (today,),
            ).fetchone()
            total = row[0] if row else 0
            conn.execute(
                """INSERT INTO session_tracker (date, total_today, last_break_at) VALUES (?, ?, 0)
                   ON CONFLICT(date) DO UPDATE SET total_today = ?""",
                (today, total, total),
            )
        logger.info("Recorded application on %s (total today: %d)", platform, total)

    def get_remaining(self) -> dict[str, int]:
        """Remaining quota per platform for today."""
        remaining: dict[str, int] = {}
        for platform, cap in DAILY_CAPS.items():
            count = self._get_platform_count(platform)
            remaining[platform] = max(0, cap - count)
        # Also include total remaining
        remaining["_total"] = max(0, TOTAL_DAILY_CAP - self.get_total_today())
        return remaining

    def should_take_break(self) -> bool:
        """True if total_today is a positive multiple of SESSION_BREAK_EVERY."""
        total = self.get_total_today()
        return total > 0 and total % SESSION_BREAK_EVERY == 0

    def reset_daily(self) -> None:
        """Explicitly reset today's counts (normally unnecessary due to date-based filtering)."""
        today = self._today()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM daily_counts WHERE date = ?", (today,))
            conn.execute("DELETE FROM session_tracker WHERE date = ?", (today,))
            conn.commit()
        logger.info("Reset daily counts for %s", today)
