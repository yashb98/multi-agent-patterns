"""Scan Learning Engine — records scan session events with 17 signals.

Learns what triggers verification walls so the job autopilot can adapt
its scanning behaviour per platform.
"""

from __future__ import annotations

import hashlib
import json as _json
import sqlite3
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any

from shared.logging_config import get_logger
from jobpulse.config import DATA_DIR
from jobpulse.utils.safe_io import safe_openai_call

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

    def _get_conn(self) -> sqlite3.Connection:
        """Return a connection with WAL mode enabled."""
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        """Create the three tables if they don't exist."""
        with self._get_conn() as conn:
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

        with self._get_conn() as conn:
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
        logger.info(
            "Recorded scan event %s: platform=%s outcome=%s",
            event_id,
            platform,
            outcome,
        )
        if outcome == "blocked":
            try:
                from shared.optimization import get_optimization_engine
                get_optimization_engine().emit(
                    signal_type="failure",
                    source_loop="scan_learning",
                    domain=platform,
                    agent_name="scanner",
                    severity="critical",
                    payload={"action": "scan", "error": wall_type or "unknown"},
                    session_id=event_id,
                )
            except Exception as e:
                logger.debug("Optimization signal failed: %s", e)
        return event_id

    def get_total_blocks(self, platform: str | None = None) -> int:
        """Count rows where outcome='blocked', optionally filtered by platform."""
        with self._get_conn() as conn:
            if platform is not None:
                row = conn.execute(
                    "SELECT COUNT(*) FROM scan_events WHERE outcome = 'blocked' AND platform = ?",
                    (platform,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT COUNT(*) FROM scan_events WHERE outcome = 'blocked'"
                ).fetchone()
            return row[0] if row else 0

    # --- Cooldown Manager ---

    _COOLDOWN_HOURS: dict[int, int] = {1: 2, 2: 4}  # consecutive_blocks → hours
    _MAX_COOLDOWN_HOURS: int = 48

    def can_scan_now(self, platform: str) -> bool:
        """Check if platform is NOT in cooldown."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT cooldown_until FROM cooldowns WHERE platform = ?",
                (platform,),
            ).fetchone()
            if row is None:
                return True
            cooldown_until = datetime.fromisoformat(row[0])
            if datetime.now(timezone.utc) >= cooldown_until:
                conn.execute("DELETE FROM cooldowns WHERE platform = ?", (platform,))
                return True
            return False

    def start_cooldown(self, platform: str, wall_type: str) -> None:
        """Start or extend cooldown for a platform after a block."""
        now = datetime.now(timezone.utc)
        with self._get_conn() as conn:
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

        logger.warning(
            "Cooldown started: %s blocked (%s), %dhr cooldown (block #%d), until %s",
            platform, wall_type, hours, consecutive, cooldown_until.isoformat(),
        )

    def reset_cooldown(self, platform: str) -> None:
        """Reset cooldown after a successful scan."""
        with self._get_conn() as conn:
            conn.execute("DELETE FROM cooldowns WHERE platform = ?", (platform,))
        logger.info("Cooldown reset for %s after successful scan", platform)

    # --- Statistical Correlation Engine ---

    _MIN_SAMPLE_SIZE: int = 3
    _RISK_THRESHOLD: float = 0.50

    _BUCKETED_SIGNALS: list[tuple[str, Any]] = [
        ("time_of_day_bucket", None),
        ("requests_in_session", staticmethod(_requests_bucket)),
        ("avg_delay", staticmethod(_delay_bucket)),
        ("session_age_seconds", staticmethod(_session_age_bucket)),
        ("user_agent_hash", None),
        ("was_fresh_session", None),
        ("simulated_mouse", None),
        ("referrer_chain", None),
        ("pages_before_block", staticmethod(_pages_bucket)),
        ("waited_for_page_load", None),
    ]

    def compute_risk_factors(self, platform: str) -> list[dict[str, Any]]:
        """Compute block rate per signal bucket. Return factors above threshold."""
        with self._get_conn() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM scan_events WHERE platform = ? ORDER BY timestamp DESC LIMIT 200",
                (platform,),
            ).fetchall()

        if not rows:
            return []

        risk_factors: list[dict[str, Any]] = []

        for signal_col, bucket_fn in self._BUCKETED_SIGNALS:
            buckets: dict[str, list[str]] = {}
            for row in rows:
                raw_val = row[signal_col]
                if bucket_fn is not None:
                    bucket_val = bucket_fn(raw_val)
                else:
                    bucket_val = str(raw_val)
                buckets.setdefault(bucket_val, []).append(row["outcome"])

            for bucket_val, outcomes in buckets.items():
                total = len(outcomes)
                if total < self._MIN_SAMPLE_SIZE:
                    continue
                blocked = sum(1 for o in outcomes if o == "blocked")
                rate = blocked / total

                if rate >= self._RISK_THRESHOLD:
                    risk_factors.append({
                        "signal": signal_col,
                        "bucket": bucket_val,
                        "block_rate": round(rate, 2),
                        "sample_size": total,
                        "blocked_count": blocked,
                    })

        risk_factors.sort(key=lambda f: f["block_rate"], reverse=True)
        return risk_factors

    def update_learned_rules(self, platform: str) -> int:
        """Compute risk factors and store as learned rules. Returns count."""
        factors = self.compute_risk_factors(platform)
        if not factors:
            return 0

        count = 0
        with self._get_conn() as conn:
            for f in factors:
                rule_id = hashlib.sha256(
                    f"{platform}:{f['signal']}:{f['bucket']}".encode()
                ).hexdigest()[:16]
                rule_text = (
                    f"High block rate ({f['block_rate']:.0%}) when "
                    f"{f['signal']} = {f['bucket']} "
                    f"({f['blocked_count']}/{f['sample_size']} sessions blocked)"
                )
                recommendation = (
                    f"Avoid {f['signal']} = {f['bucket']} — "
                    f"use alternative values or adjust timing"
                )
                now_iso = datetime.now(timezone.utc).isoformat()
                conn.execute(
                    """INSERT INTO learned_rules (id, platform, rule_text, confidence, recommendation, source, created_at)
                       VALUES (?, ?, ?, ?, ?, 'statistical', ?)
                       ON CONFLICT(id) DO UPDATE SET
                           rule_text = ?, confidence = ?, recommendation = ?, created_at = ?""",
                    (
                        rule_id, platform, rule_text, f["block_rate"], recommendation, now_iso,
                        rule_text, f["block_rate"], recommendation, now_iso,
                    ),
                )
                count += 1
        logger.info("Updated %d learned rules for %s", count, platform)
        return count

    # --- LLM Pattern Analyzer ---

    _LLM_ANALYSIS_EVERY_N_BLOCKS: int = 5

    def should_run_llm_analysis(self) -> bool:
        """True if total blocks across all platforms is a positive multiple of 5."""
        total = self.get_total_blocks()
        return total > 0 and total % self._LLM_ANALYSIS_EVERY_N_BLOCKS == 0

    def run_llm_analysis(self, platform: str) -> None:
        """Run GPT-5o-mini analysis on recent events for a platform."""
        with self._get_conn() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM scan_events WHERE platform = ? ORDER BY timestamp DESC LIMIT 20",
                (platform,),
            ).fetchall()

        if not rows:
            return

        # Build events table for LLM
        header = "timestamp | requests | avg_delay | session_age | ua_hash | fresh | mouse | referrer | pages | waited | outcome | wall_type"
        lines = [header]
        for r in rows:
            lines.append(
                f"{r['timestamp'][:16]} | {r['requests_in_session']} | "
                f"{r['avg_delay']:.1f}s | {r['session_age_seconds']:.0f}s | "
                f"{r['user_agent_hash'][:6]} | {bool(r['was_fresh_session'])} | "
                f"{bool(r['simulated_mouse'])} | {r['referrer_chain']} | "
                f"{r['pages_before_block']} | {bool(r['waited_for_page_load'])} | "
                f"{r['outcome']} | {r['wall_type'] or 'n/a'}"
            )
        events_table = "\n".join(lines)

        prompt = (
            f"You are analyzing job scraping session data to find patterns that trigger verification walls.\n\n"
            f"Here are the last {len(rows)} scan sessions for {platform}:\n{events_table}\n\n"
            f"Identify the pattern that most likely triggers blocks. Return ONLY valid JSON:\n"
            f'{{"pattern": "human-readable description", "confidence": 0.0-1.0, '
            f'"recommendation": "specific parameter changes"}}'
        )

        from shared.agents import get_openai_client
        client = get_openai_client()
        response = safe_openai_call(
            client,
            model="gpt-5-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            caller="scan_learning_llm_analysis",
        )

        if not response:
            logger.warning("LLM analysis returned None for %s", platform)
            return

        try:
            result = _json.loads(response)
        except _json.JSONDecodeError:
            logger.warning("LLM analysis returned invalid JSON for %s: %s", platform, response[:200])
            return

        pattern = result.get("pattern", "")
        confidence = float(result.get("confidence", 0.5))
        recommendation = result.get("recommendation", "")

        if not pattern:
            return

        rule_id = uuid.uuid4().hex[:16]
        with self._get_conn() as conn:
            conn.execute(
                """INSERT INTO learned_rules (id, platform, rule_text, confidence, recommendation, source, created_at)
                   VALUES (?, ?, ?, ?, ?, 'llm', ?)""",
                (rule_id, platform, pattern, confidence, recommendation,
                 datetime.now(timezone.utc).isoformat()),
            )

        logger.info(
            "LLM analysis for %s: pattern='%s' confidence=%.2f",
            platform, pattern, confidence,
        )

    def get_cooldown_info(self, platform: str) -> dict[str, Any] | None:
        """Get current cooldown state for a platform."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT blocked_at, cooldown_until, consecutive_blocks, last_wall_type "
                "FROM cooldowns WHERE platform = ?",
                (platform,),
            ).fetchone()
        if row is None:
            return None
        return {
            "blocked_at": row[0],
            "cooldown_until": row[1],
            "consecutive_blocks": row[2],
            "last_wall_type": row[3],
        }

    # --- Adaptive Parameters ---

    _DEFAULT_PARAMS: dict[str, Any] = {
        "delay_range": (2.0, 8.0),
        "max_requests": 50,
        "simulate_human": False,
        "session_max_age_seconds": 1800,
        "referrer_strategy": "direct",
        "wait_for_load": True,
        "cooldown_active": False,
        "cooldown_until": None,
        "risk_level": "low",
    }

    def get_adaptive_params(self, platform: str) -> dict[str, Any]:
        """Build scan parameters based on learned rules + cooldown state."""
        params = dict(self._DEFAULT_PARAMS)

        # Check cooldown
        cooldown = self.get_cooldown_info(platform)
        if cooldown and not self.can_scan_now(platform):
            params["cooldown_active"] = True
            params["cooldown_until"] = cooldown["cooldown_until"]

        with self._get_conn() as conn:
            rule_count = conn.execute(
                "SELECT COUNT(*) FROM learned_rules WHERE platform = ? AND confidence >= 0.50",
                (platform,),
            ).fetchone()[0]

        if rule_count == 0:
            params["risk_level"] = "low"
        elif rule_count == 1:
            params["risk_level"] = "medium"
            params["delay_range"] = (3.0, 12.0)
            params["max_requests"] = 25
            params["simulate_human"] = True
            params["session_max_age_seconds"] = 600
        else:
            params["risk_level"] = "high"
            params["delay_range"] = (5.0, 15.0)
            params["max_requests"] = 5
            params["simulate_human"] = True
            params["session_max_age_seconds"] = 480
            params["referrer_strategy"] = "homepage_first"

        return params
