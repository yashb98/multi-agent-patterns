"""Auto-generated avoidance rules from rejection analysis and user corrections.

Rules are stored in SQLite and consumed by recruiter_screen (Gate 0) and
FormIntelligence to improve future applications.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

from shared.logging_config import get_logger

from jobpulse.config import DATA_DIR

logger = get_logger(__name__)

_DEFAULT_DB = str(DATA_DIR / "agent_rules.db")

_RULE_TTL_DAYS = 30


class AgentRulesDB:
    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = db_path or _DEFAULT_DB
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS agent_rules (
                    rule_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    rule_type TEXT NOT NULL,
                    source TEXT NOT NULL,
                    category TEXT NOT NULL,
                    pattern TEXT NOT NULL,
                    action TEXT NOT NULL,
                    value TEXT NOT NULL,
                    confidence REAL NOT NULL DEFAULT 0.0,
                    sample_count INTEGER NOT NULL DEFAULT 0,
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                )
            """)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def auto_generate_from_blocker(
        self,
        category: str,
        pattern: str,
        count: int,
        total: int,
    ) -> dict:
        """Generate an avoidance rule from a frequent blocker category.

        Args:
            category: Blocker category (geo-restriction, stack-mismatch, etc.)
            pattern: Regex or keyword pattern to match against titles/JDs.
            count: How many applications were blocked by this category.
            total: Total blocked applications considered.

        Returns:
            Dict with rule_id, category, pattern, action.
        """
        confidence = count / total if total > 0 else 0.0
        now = datetime.now(UTC).isoformat()
        expires = (datetime.now(UTC) + timedelta(days=_RULE_TTL_DAYS)).isoformat()

        with self._connect() as conn:
            # Upsert: same source+category+pattern → update counts
            existing = conn.execute(
                "SELECT rule_id FROM agent_rules WHERE source = ? AND category = ? AND pattern = ?",
                ("rejection_analyzer", category, pattern),
            ).fetchone()

            if existing:
                conn.execute(
                    """UPDATE agent_rules
                       SET confidence = ?, sample_count = ?, active = 1,
                           expires_at = ?
                       WHERE rule_id = ?""",
                    (confidence, count, expires, existing["rule_id"]),
                )
                rule_id = existing["rule_id"]
            else:
                cursor = conn.execute(
                    """INSERT INTO agent_rules
                       (rule_type, source, category, pattern, action, value,
                        confidence, sample_count, active, created_at, expires_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
                    (
                        "blocker_avoidance",
                        "rejection_analyzer",
                        category,
                        pattern,
                        "exclude_keyword",
                        pattern,
                        confidence,
                        count,
                        now,
                        expires,
                    ),
                )
                rule_id = cursor.lastrowid

        logger.info(
            "agent_rules: generated blocker rule #%d category=%s pattern=%s confidence=%.2f",
            rule_id, category, pattern, confidence,
        )
        try:
            from shared.optimization import get_optimization_engine
            get_optimization_engine().emit(
                signal_type="adaptation",
                source_loop="agent_rules",
                domain=category,
                agent_name="agent_rules",
                payload={"param": "blocker_avoidance", "old_value": "", "new_value": pattern, "reason": f"confidence={confidence:.2f}"},
                session_id=f"ar_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}",
            )
        except Exception as e:
            logger.debug("Optimization signal failed: %s", e)
        return {
            "rule_id": rule_id,
            "category": category,
            "pattern": pattern,
            "action": "exclude_keyword",
        }

    def auto_generate_from_correction(
        self,
        field_label: str,
        agent_value: str,
        user_value: str,
        domain: str,
        platform: str,
    ) -> dict:
        """Generate a correction-based override or escalation rule.

        Returns:
            Dict with rule_id, field_label, action.
        """
        now = datetime.now(UTC).isoformat()
        expires = (datetime.now(UTC) + timedelta(days=_RULE_TTL_DAYS)).isoformat()

        with self._connect() as conn:
            # Check if we already have an override for this field+domain
            existing = conn.execute(
                """SELECT rule_id, sample_count FROM agent_rules
                   WHERE source = ? AND category = ? AND pattern = ?""",
                ("correction_capture", field_label, domain),
            ).fetchone()

            if existing:
                new_count = existing["sample_count"] + 1
                # After 3+ corrections for same field, escalate instead of override
                action = "escalate" if new_count >= 3 else "override_answer"
                conn.execute(
                    """UPDATE agent_rules
                       SET action = ?, value = ?, sample_count = ?,
                           confidence = ?, active = 1, expires_at = ?
                       WHERE rule_id = ?""",
                    (action, user_value, new_count,
                     min(new_count / 5.0, 1.0), expires, existing["rule_id"]),
                )
                rule_id = existing["rule_id"]
            else:
                cursor = conn.execute(
                    """INSERT INTO agent_rules
                       (rule_type, source, category, pattern, action, value,
                        confidence, sample_count, active, created_at, expires_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
                    (
                        "correction_override",
                        "correction_capture",
                        field_label,
                        domain,
                        "override_answer",
                        user_value,
                        0.2,
                        1,
                        now,
                        expires,
                    ),
                )
                rule_id = cursor.lastrowid
                action = "override_answer"

        logger.info(
            "agent_rules: correction rule #%d field=%s domain=%s action=%s",
            rule_id, field_label, domain, action,
        )
        try:
            from shared.optimization import get_optimization_engine
            get_optimization_engine().emit(
                signal_type="adaptation",
                source_loop="agent_rules",
                domain=field_label,
                agent_name="agent_rules",
                payload={"field": field_label, "old_value": agent_value, "new_value": user_value, "platform": platform},
                session_id=f"ar_corr_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}",
            )
        except Exception as e:
            logger.debug("Optimization signal failed: %s", e)
        return {"rule_id": rule_id, "field_label": field_label, "action": action}

    def get_active_rules(self, rule_type: str | None = None) -> list[dict]:
        """Return active, non-expired rules, optionally filtered by type."""
        now = datetime.now(UTC).isoformat()
        with self._connect() as conn:
            if rule_type:
                rows = conn.execute(
                    """SELECT * FROM agent_rules
                       WHERE active = 1 AND expires_at > ? AND rule_type = ?
                       ORDER BY confidence DESC""",
                    (now, rule_type),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT * FROM agent_rules
                       WHERE active = 1 AND expires_at > ?
                       ORDER BY confidence DESC""",
                    (now,),
                ).fetchall()
        return [dict(r) for r in rows]

    def get_exclude_keywords(self) -> list[str]:
        """Return keyword values from active blocker_avoidance rules for Gate 0."""
        rules = self.get_active_rules("blocker_avoidance")
        return [r["value"] for r in rules if r["action"] == "exclude_keyword"]

    def get_escalation_fields(self) -> list[str]:
        """Return field labels that should skip auto-fill due to repeated corrections."""
        rules = self.get_active_rules("correction_override")
        return [r["category"] for r in rules if r["action"] == "escalate"]
