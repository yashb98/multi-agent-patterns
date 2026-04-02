"""PatternStore — SQLite-backed storage for learned fix patterns.

Stores fix patterns keyed by (platform, step_name, error_signature).
Tracks apply attempts for audit trail. Consolidates redundant patterns.
DB: data/ralph_patterns.db
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from shared.logging_config import get_logger
from jobpulse.config import DATA_DIR

logger = get_logger(__name__)

_DEFAULT_DB_PATH = str(DATA_DIR / "ralph_patterns.db")

# Fix types — structured data, not code patches
FIX_TYPES = frozenset({
    "selector_override",
    "strategy_switch",
    "interaction_change",
    "wait_adjustment",
    "field_remap",
})


@dataclass
class FixPattern:
    id: str
    platform: str
    step_name: str
    error_signature: str
    fix_type: str
    fix_payload: str  # JSON string
    confidence: float
    times_applied: int
    times_succeeded: int
    success_rate: float
    created_at: str
    last_used_at: str | None
    superseded_by: str | None
    source: str = "production"   # "test" | "production" | "manual"
    confirmed: bool = True
    occurrence_count: int = 1

    @property
    def payload(self) -> dict:
        """Parse fix_payload JSON."""
        return json.loads(self.fix_payload)


# ---------------------------------------------------------------------------
# Error signature computation
# ---------------------------------------------------------------------------

# Patterns to strip from error messages before hashing
_DYNAMIC_PATTERNS = [
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}[.\d]*Z?",  # timestamps
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",  # UUIDs
    r"[0-9a-f]{16,}",  # long hex IDs
    r"/[^\s]+/[^\s]+\.[a-z]+",  # file paths
    r"\b\d{5,}\b",  # long numeric IDs
    r"https?://[^\s]+",  # URLs
]
_DYNAMIC_RE = re.compile("|".join(_DYNAMIC_PATTERNS), re.IGNORECASE)


def compute_error_signature(platform: str, step_name: str, error_message: str) -> str:
    """Compute a stable hash for an error class.

    Strips dynamic content (timestamps, UUIDs, paths, numeric IDs) so that
    the same class of error always produces the same signature.
    """
    normalized = _DYNAMIC_RE.sub("", error_message)
    normalized = normalized.lower().strip()
    normalized = re.sub(r"\s+", " ", normalized)
    normalized = normalized[:200]
    raw = f"{platform}:{step_name}:{normalized}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# PatternStore
# ---------------------------------------------------------------------------


class PatternStore:
    """SQLite store for learned fix patterns and apply attempt history."""

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or _DEFAULT_DB_PATH
        self._init_db()

    def _init_db(self) -> None:
        conn = sqlite3.connect(self.db_path)
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS fix_patterns (
                id TEXT PRIMARY KEY,
                platform TEXT NOT NULL,
                step_name TEXT NOT NULL,
                error_signature TEXT NOT NULL,
                fix_type TEXT NOT NULL,
                fix_payload TEXT NOT NULL,
                confidence REAL NOT NULL DEFAULT 0.5,
                times_applied INTEGER DEFAULT 0,
                times_succeeded INTEGER DEFAULT 0,
                success_rate REAL DEFAULT 0.0,
                created_at TEXT NOT NULL,
                last_used_at TEXT,
                superseded_by TEXT,
                source TEXT NOT NULL DEFAULT 'production',
                confirmed BOOLEAN NOT NULL DEFAULT 1,
                occurrence_count INTEGER NOT NULL DEFAULT 1,
                UNIQUE(platform, step_name, error_signature)
            );

            CREATE TABLE IF NOT EXISTS apply_attempts (
                id TEXT PRIMARY KEY,
                job_url TEXT NOT NULL,
                platform TEXT NOT NULL,
                iteration INTEGER NOT NULL,
                step_name TEXT NOT NULL,
                error_message TEXT,
                error_signature TEXT,
                screenshot_path TEXT,
                dom_snapshot_path TEXT,
                diagnosis TEXT,
                fix_applied TEXT,
                outcome TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS consolidation_log (
                id TEXT PRIMARY KEY,
                platform TEXT NOT NULL,
                patterns_merged INTEGER NOT NULL,
                new_pattern_id TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        conn.close()

    # --- Fix Pattern CRUD ---

    def save_fix(
        self,
        platform: str,
        step_name: str,
        error_signature: str,
        fix_type: str,
        fix_payload: dict,
        confidence: float = 0.5,
        source: str = "production",
    ) -> FixPattern:
        """Save or update a fix pattern. Upserts on (platform, step_name, error_signature).

        Confirmation logic:
        - production: always confirmed=True
        - manual: always confirmed=True
        - test (1st occurrence): confirmed=False
        - test (2nd+ occurrence): auto-promoted to confirmed=True
        - production overwriting an existing test: promoted to confirmed=True
        """
        if fix_type not in FIX_TYPES:
            raise ValueError(f"Unknown fix_type: {fix_type}. Must be one of {FIX_TYPES}")

        fix_id = hashlib.sha256(
            f"{platform}:{step_name}:{error_signature}".encode()
        ).hexdigest()[:16]
        now_iso = datetime.now(timezone.utc).isoformat()
        payload_json = json.dumps(fix_payload)

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row

        # Check for existing row to handle occurrence counting + promotion
        existing = conn.execute(
            "SELECT source, occurrence_count FROM fix_patterns WHERE id = ?",
            (fix_id,),
        ).fetchone()

        if existing is None:
            # First insert — determine confirmed based on source alone
            confirmed = source != "test"
            occurrence_count = 1
            conn.execute(
                """INSERT INTO fix_patterns
                   (id, platform, step_name, error_signature, fix_type, fix_payload,
                    confidence, created_at, source, confirmed, occurrence_count)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fix_id, platform, step_name, error_signature, fix_type,
                    payload_json, confidence, now_iso,
                    source, confirmed, occurrence_count,
                ),
            )
        else:
            prev_source: str = existing["source"]
            occurrence_count = existing["occurrence_count"] + 1

            # Promotion rules:
            # - source is not "test" → always confirmed
            # - test on 2nd+ occurrence → promote
            # - production overwriting existing test → promote
            if source != "test":
                confirmed = True
            elif occurrence_count >= 2:
                confirmed = True
            else:
                confirmed = False

            # production overwriting existing test also forces source update
            if prev_source == "test" and source == "production":
                effective_source = "production"
            else:
                effective_source = source

            conn.execute(
                """UPDATE fix_patterns SET
                    fix_type = ?, fix_payload = ?, confidence = ?, created_at = ?,
                    source = ?, confirmed = ?, occurrence_count = ?
                   WHERE id = ?
                """,
                (
                    fix_type, payload_json, confidence, now_iso,
                    effective_source, confirmed, occurrence_count,
                    fix_id,
                ),
            )
            source = effective_source

        conn.commit()
        conn.close()

        logger.info(
            "Saved fix pattern %s: platform=%s step=%s type=%s confidence=%.2f source=%s confirmed=%s",
            fix_id, platform, step_name, fix_type, confidence, source, confirmed,
        )

        return FixPattern(
            id=fix_id,
            platform=platform,
            step_name=step_name,
            error_signature=error_signature,
            fix_type=fix_type,
            fix_payload=payload_json,
            confidence=confidence,
            times_applied=0,
            times_succeeded=0,
            success_rate=0.0,
            created_at=now_iso,
            last_used_at=None,
            superseded_by=None,
            source=source,
            confirmed=confirmed,
            occurrence_count=occurrence_count,
        )

    def get_fix(
        self, platform: str, step_name: str, error_signature: str,
    ) -> FixPattern | None:
        """Look up a specific fix by (platform, step, signature)."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """SELECT * FROM fix_patterns
               WHERE platform = ? AND step_name = ? AND error_signature = ?
               AND superseded_by IS NULL""",
            (platform, step_name, error_signature),
        ).fetchone()
        conn.close()
        if row is None:
            return None
        return self._row_to_fix(row)

    def get_fixes_for_platform(
        self, platform: str, min_success_rate: float = 0.0,
    ) -> list[FixPattern]:
        """Get all active (non-superseded) fixes for a platform."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT * FROM fix_patterns
               WHERE platform = ? AND superseded_by IS NULL
               AND success_rate >= ?
               ORDER BY success_rate DESC, confidence DESC""",
            (platform, min_success_rate),
        ).fetchall()
        conn.close()
        return [self._row_to_fix(r) for r in rows]

    def mark_fixes_applied(self, fix_ids: list[str]) -> None:
        """Increment times_applied and update last_used_at for given fix IDs."""
        if not fix_ids:
            return
        now_iso = datetime.now(timezone.utc).isoformat()
        conn = sqlite3.connect(self.db_path)
        for fid in fix_ids:
            conn.execute(
                """UPDATE fix_patterns
                   SET times_applied = times_applied + 1, last_used_at = ?
                   WHERE id = ?""",
                (now_iso, fid),
            )
        conn.commit()
        conn.close()

    def mark_fixes_successful(self, fix_ids: list[str]) -> None:
        """Increment times_succeeded and recalculate success_rate."""
        if not fix_ids:
            return
        conn = sqlite3.connect(self.db_path)
        for fid in fix_ids:
            conn.execute(
                """UPDATE fix_patterns
                   SET times_succeeded = times_succeeded + 1,
                       success_rate = CAST(times_succeeded + 1 AS REAL) / MAX(times_applied, 1)
                   WHERE id = ?""",
                (fid,),
            )
        conn.commit()
        conn.close()

    # --- Apply Attempts ---

    def record_attempt(
        self,
        *,
        job_url: str,
        platform: str,
        iteration: int,
        step_name: str,
        outcome: str,
        error_message: str | None = None,
        error_signature: str | None = None,
        screenshot_path: str | None = None,
        dom_snapshot_path: str | None = None,
        diagnosis: dict | None = None,
        fix_applied: dict | None = None,
    ) -> str:
        """Record a single apply attempt. Returns the attempt ID."""
        attempt_id = uuid.uuid4().hex[:16]
        now_iso = datetime.now(timezone.utc).isoformat()

        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """INSERT INTO apply_attempts
               (id, job_url, platform, iteration, step_name, error_message,
                error_signature, screenshot_path, dom_snapshot_path,
                diagnosis, fix_applied, outcome, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                attempt_id, job_url, platform, iteration, step_name,
                error_message, error_signature, screenshot_path, dom_snapshot_path,
                json.dumps(diagnosis) if diagnosis else None,
                json.dumps(fix_applied) if fix_applied else None,
                outcome, now_iso,
            ),
        )
        conn.commit()
        conn.close()

        logger.info(
            "Recorded attempt %s: url=%s iter=%d step=%s outcome=%s",
            attempt_id, job_url[:60], iteration, step_name, outcome,
        )
        return attempt_id

    def get_attempt_history(self, job_url: str) -> list[dict[str, Any]]:
        """Get all attempts for a job URL, ordered by iteration."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM apply_attempts WHERE job_url = ? ORDER BY iteration",
            (job_url,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def flag_for_human_review(self, job_url: str, platform: str, attempt_ids: list[str]) -> None:
        """Log that a job exhausted all retries and needs human review."""
        logger.warning(
            "Ralph Loop EXHAUSTED for %s on %s — %d attempts, flagging for human review",
            job_url[:60], platform, len(attempt_ids),
        )

    # --- Consolidation ---

    def consolidate_patterns(self, platform: str, min_fixes: int = 10) -> int:
        """Merge redundant patterns for a platform. Returns count merged."""
        fixes = self.get_fixes_for_platform(platform)
        if len(fixes) < min_fixes:
            return 0

        # Group by step_name
        by_step: dict[str, list[FixPattern]] = {}
        for f in fixes:
            by_step.setdefault(f.step_name, []).append(f)

        merged = 0
        conn = sqlite3.connect(self.db_path)

        for step_name, step_fixes in by_step.items():
            if len(step_fixes) <= 1:
                continue

            # Keep the fix with highest success_rate
            step_fixes.sort(key=lambda f: (f.success_rate, f.confidence), reverse=True)
            winner = step_fixes[0]
            losers = step_fixes[1:]

            for loser in losers:
                if loser.success_rate < 0.7:
                    conn.execute(
                        "UPDATE fix_patterns SET superseded_by = ? WHERE id = ?",
                        (winner.id, loser.id),
                    )
                    merged += 1

        if merged > 0:
            log_id = uuid.uuid4().hex[:16]
            now_iso = datetime.now(timezone.utc).isoformat()
            conn.execute(
                """INSERT INTO consolidation_log (id, platform, patterns_merged, new_pattern_id, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (log_id, platform, merged, "multiple", now_iso),
            )
            conn.commit()
            logger.info("Consolidated %d patterns for %s", merged, platform)

        conn.close()
        return merged

    # --- Pruning ---

    def prune_stale_test_fixes(self, max_age_days: int = 14) -> int:
        """Delete unconfirmed, single-occurrence test fixes older than max_age_days.

        Prune criteria (ALL must match):
          - source = 'test'
          - confirmed = 0 (False)
          - occurrence_count = 1
          - created_at < (now - max_age_days)

        Returns the number of rows deleted.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
        cutoff_iso = cutoff.isoformat()

        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute(
            """DELETE FROM fix_patterns
               WHERE source = 'test'
                 AND confirmed = 0
                 AND occurrence_count = 1
                 AND created_at < ?""",
            (cutoff_iso,),
        )
        deleted = cursor.rowcount
        conn.commit()
        conn.close()

        if deleted:
            logger.info("Pruned %d stale unconfirmed test fix(es) older than %d days", deleted, max_age_days)

        return deleted

    # --- Helpers ---

    @staticmethod
    def _row_to_fix(row: sqlite3.Row) -> FixPattern:
        keys = row.keys()
        return FixPattern(
            id=row["id"],
            platform=row["platform"],
            step_name=row["step_name"],
            error_signature=row["error_signature"],
            fix_type=row["fix_type"],
            fix_payload=row["fix_payload"],
            confidence=row["confidence"],
            times_applied=row["times_applied"],
            times_succeeded=row["times_succeeded"],
            success_rate=row["success_rate"],
            created_at=row["created_at"],
            last_used_at=row["last_used_at"],
            superseded_by=row["superseded_by"],
            source=row["source"] if "source" in keys else "production",
            confirmed=bool(row["confirmed"]) if "confirmed" in keys else True,
            occurrence_count=row["occurrence_count"] if "occurrence_count" in keys else 1,
        )
