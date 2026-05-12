"""Auto-generated avoidance rules from rejection analysis and user corrections.

Rules are stored in SQLite and consumed by recruiter_screen (Gate 0) and
FormIntelligence to improve future applications.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

from shared.db_observability import observe_lookup
from shared.logging_config import get_logger

from jobpulse.config import DATA_DIR

logger = get_logger(__name__)

_DEFAULT_DB = str(DATA_DIR / "agent_rules.db")

_RULE_TTL_DAYS = 30


def _normalize_domain(value: str | None) -> str:
    """Canonicalize a domain string for AgentRulesDB pattern matching.

    Accepts: bare host, host+path, full URL, with or without scheme,
    with or without `www.`, mixed case. Returns lowercase host without
    leading `www.`. Empty input returns empty string.
    """
    if not value:
        return ""
    from urllib.parse import urlparse
    s = value.strip().lower()
    if "://" in s:
        s = urlparse(s).netloc
    else:
        # Drop any path portion for bare host[+path] inputs
        s = s.split("/", 1)[0]
    if s.startswith("www."):
        s = s[4:]
    return s


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
                    times_applied INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                )
            """)
            # Detect legacy schema (trigger_pattern column) and migrate
            try:
                cols = {row[1] for row in conn.execute("PRAGMA table_info(agent_rules)").fetchall()}
                if "trigger_pattern" in cols and "rule_type" not in cols:
                    logger.info("agent_rules: migrating legacy schema to current schema")
                    old_rows = conn.execute("SELECT * FROM agent_rules").fetchall()
                    conn.execute("DROP TABLE agent_rules")
                    conn.execute("""
                        CREATE TABLE agent_rules (
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
                            times_applied INTEGER NOT NULL DEFAULT 0,
                            created_at TEXT NOT NULL,
                            expires_at TEXT NOT NULL
                        )
                    """)
                    for row in old_rows:
                        # conn has no row_factory — use positional indexing
                        # Legacy columns: 0=rule_id, 1=category, 2=trigger_pattern,
                        # 3=action_type, 4=action_value, 5=platform, 6=domain,
                        # 7=source, 8=confidence, 9=times_applied, 10=created_at, 11=updated_at
                        category = row[1]
                        rule_type = "screening_override" if category == "screening" else "field_mapping_override"
                        source = row[7] or "legacy_migration"
                        conn.execute(
                            """INSERT INTO agent_rules
                               (rule_type, source, category, pattern, action, value,
                                confidence, sample_count, active, times_applied, created_at, expires_at)
                               VALUES (?, ?, ?, ?, ?, ?, ?, 1, 1, ?, ?, ?)""",
                            (
                                rule_type,
                                source,
                                category,
                                row[2],   # trigger_pattern
                                row[3],   # action_type
                                row[4],   # action_value
                                row[8] if row[8] is not None else 1.0,  # confidence
                                row[9] if row[9] is not None else 0,    # times_applied
                                row[10] or datetime.now(UTC).isoformat(),  # created_at
                                (datetime.now(UTC) + timedelta(days=_RULE_TTL_DAYS)).isoformat(),
                            ),
                        )
                    logger.info("agent_rules: migrated %d legacy rules", len(old_rows))
            except Exception as exc:
                logger.warning("agent_rules: schema migration check failed: %s", exc)
            # Migration: add times_applied for older new-schema DBs missing it
            try:
                conn.execute(
                    "ALTER TABLE agent_rules ADD COLUMN times_applied INTEGER NOT NULL DEFAULT 0"
                )
            except sqlite3.OperationalError:
                pass  # Column already exists
            # 2026-05 migration — normalize correction-style patterns to lowercase host without www/scheme/path
            try:
                rows = conn.execute(
                    "SELECT rule_id, pattern FROM agent_rules "
                    "WHERE rule_type='correction_override' OR source LIKE 'correction%' "
                    "   OR source IN ('user_correction', 'user_feedback', 'correction_capture')"
                ).fetchall()
                normalized_count = 0
                for row in rows:
                    rule_id, raw = row[0], row[1]
                    if not raw:
                        continue
                    normalized = _normalize_domain(raw)
                    if normalized and normalized != raw:
                        conn.execute(
                            "UPDATE agent_rules SET pattern = ? WHERE rule_id = ?",
                            (normalized, rule_id),
                        )
                        normalized_count += 1
                        logger.info(
                            "agent_rules: normalized pattern rule_id=%d %r → %r",
                            rule_id, raw, normalized,
                        )
                if normalized_count > 0:
                    logger.info("agent_rules: migration normalized %d patterns", normalized_count)
            except Exception as exc:
                logger.warning("agent_rules: pattern normalization migration failed: %s", exc)

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
        domain = _normalize_domain(domain)
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
                # Audit S5 B-3: the aggregator's adaptation_worked
                # detector reads `payload["param"]` (see
                # `_aggregator._detect_adaptation_effectiveness` L341).
                # The previous payload only carried `field`, so every
                # correction-driven adaptation insight reported
                # "Adaptation 'unknown' on …" and lost the field-label
                # provenance. Match the schema used by
                # `auto_generate_from_blocker` (L231) and
                # `auto_rule_generator.deploy_rule` (L411).
                payload={
                    "param": "correction_override",
                    "field": field_label,
                    "old_value": agent_value,
                    "new_value": user_value,
                    "platform": platform,
                },
                session_id=f"ar_corr_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}",
            )
        except Exception as e:
            logger.debug("Optimization signal failed: %s", e)
        return {"rule_id": rule_id, "field_label": field_label, "action": action}

    @observe_lookup("agent_rules", "agent_rules", key_arg=1)
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

    @observe_lookup("agent_rules", "agent_rules.exclude_keywords", key_arg=None)
    def get_exclude_keywords(self) -> list[str]:
        """Return keyword values from active blocker_avoidance rules for Gate 0."""
        rules = self.get_active_rules("blocker_avoidance")
        return [r["value"] for r in rules if r["action"] == "exclude_keyword"]

    @observe_lookup("agent_rules", "agent_rules.field_overrides", key_arg=1)
    def get_field_overrides(self, domain: str = "", platform: str = "") -> dict[str, dict]:
        """Return {field_label: {value, action, confidence, rule_id}} for form-fill consumption.

        Queries correction_override rules matching domain or platform.
        Increments times_applied for each returned rule.
        """
        domain = _normalize_domain(domain)
        rules = self.get_active_rules("correction_override")
        overrides: dict[str, dict] = {}
        rule_ids_used: list[int] = []

        for r in rules:
            if domain and r.get("pattern") and r["pattern"] != domain:
                continue
            field = r["category"]
            if field in overrides:
                if r["confidence"] <= overrides[field]["confidence"]:
                    continue
            overrides[field] = {
                "value": r["value"],
                "action": r["action"],
                "confidence": r["confidence"],
                "rule_id": r["rule_id"],
            }
            rule_ids_used.append(r["rule_id"])

        if rule_ids_used:
            with self._connect() as conn:
                for rid in rule_ids_used:
                    conn.execute(
                        "UPDATE agent_rules SET times_applied = times_applied + 1 WHERE rule_id = ?",
                        (rid,),
                    )

        return overrides
