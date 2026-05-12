"""Per-domain form experience store.

Records what the form looked like (adapter, pages, field types, screening questions,
time) after each successful application. Cron jobs query this to skip LLM page
detection and pre-load the right expectations for known domains.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from urllib.parse import urlparse

from shared.db_observability import observe_lookup
from shared.logging_config import get_logger

from jobpulse.config import DATA_DIR

logger = get_logger(__name__)

_DEFAULT_DB = str(DATA_DIR / "form_experience.db")


class FormExperienceDB:
    def __init__(self, db_path: str | None = None):
        self._db_path = db_path or _DEFAULT_DB
        self._init_db_heal()

    def _schema_sql(self) -> str:
        return """
            CREATE TABLE IF NOT EXISTS form_experience (
                domain TEXT PRIMARY KEY,
                platform TEXT NOT NULL,
                adapter TEXT NOT NULL,
                pages_filled INTEGER NOT NULL,
                field_types TEXT NOT NULL,
                screening_questions TEXT NOT NULL,
                time_seconds REAL NOT NULL,
                success INTEGER NOT NULL,
                apply_count INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_form_experience_platform
            ON form_experience (platform);
            CREATE TABLE IF NOT EXISTS field_label_mappings (
                domain TEXT,
                field_label TEXT,
                profile_key TEXT,
                confidence REAL DEFAULT 1.0,
                PRIMARY KEY (domain, field_label)
            );
            CREATE TABLE IF NOT EXISTS fill_techniques (
                domain TEXT NOT NULL,
                field_label TEXT NOT NULL,
                field_type TEXT NOT NULL,
                technique TEXT NOT NULL,
                value_used TEXT,
                success INTEGER NOT NULL DEFAULT 1,
                apply_count INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (domain, field_label)
            );
            CREATE TABLE IF NOT EXISTS form_failure_reasons (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                domain TEXT NOT NULL,
                platform TEXT NOT NULL,
                failure_type TEXT NOT NULL,
                field_label TEXT,
                selector TEXT,
                details TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_failures_domain
            ON form_failure_reasons (domain);
            CREATE INDEX IF NOT EXISTS idx_failures_platform
            ON form_failure_reasons (platform);
            CREATE TABLE IF NOT EXISTS container_selectors (
                domain TEXT PRIMARY KEY,
                selector TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS page_timings (
                domain TEXT PRIMARY KEY,
                avg_hydration_ms INTEGER NOT NULL,
                avg_fill_ms INTEGER NOT NULL,
                avg_transition_ms INTEGER NOT NULL,
                sample_count INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS scan_strategy_preferences (
                domain TEXT PRIMARY KEY,
                preferred_strategy TEXT NOT NULL,
                field_count INTEGER NOT NULL,
                sample_count INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS field_confidence_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                domain TEXT NOT NULL,
                field_label TEXT NOT NULL,
                predicted_confidence REAL NOT NULL,
                actual_correct INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_confidence_domain
            ON field_confidence_log (domain);
            CREATE TABLE IF NOT EXISTS negative_exemplars (
                domain TEXT NOT NULL,
                field_label TEXT NOT NULL,
                value_tried TEXT NOT NULL,
                failure_reason TEXT NOT NULL,
                platform TEXT NOT NULL DEFAULT '',
                content_hash TEXT NOT NULL DEFAULT '',
                attempt_count INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (domain, field_label, value_tried)
            );
            CREATE INDEX IF NOT EXISTS idx_neg_content_hash
            ON negative_exemplars (content_hash);
            CREATE TABLE IF NOT EXISTS signal_corrections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                domain TEXT NOT NULL,
                field_label TEXT NOT NULL,
                signal_type TEXT NOT NULL,
                error_message TEXT NOT NULL,
                original_value TEXT NOT NULL,
                corrected_value TEXT NOT NULL,
                transform TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_sc_domain_field
            ON signal_corrections (domain, field_label);
        """

    def _init_db_heal(self):
        """Initialise DB with self-healing fallback on corruption."""
        try:
            self._init_db()
        except sqlite3.DatabaseError:
            from shared.self_healing import heal_db_if_needed
            report = heal_db_if_needed(self._db_path, fallback_schema=self._schema_sql())
            if report.healthy:
                logger.info("FormExperienceDB healed and reinitialised")
            else:
                logger.error("FormExperienceDB could not be healed: %s", report.errors)
                raise

    def _init_db(self):
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS form_experience (
                    domain TEXT PRIMARY KEY,
                    platform TEXT NOT NULL,
                    adapter TEXT NOT NULL,
                    pages_filled INTEGER NOT NULL,
                    field_types TEXT NOT NULL,
                    screening_questions TEXT NOT NULL,
                    time_seconds REAL NOT NULL,
                    success INTEGER NOT NULL,
                    apply_count INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_form_experience_platform
                ON form_experience (platform)
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS field_label_mappings (
                    domain TEXT,
                    field_label TEXT,
                    profile_key TEXT,
                    confidence REAL DEFAULT 1.0,
                    PRIMARY KEY (domain, field_label)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS fill_techniques (
                    domain TEXT NOT NULL,
                    field_label TEXT NOT NULL,
                    field_type TEXT NOT NULL,
                    technique TEXT NOT NULL,
                    value_used TEXT,
                    success INTEGER NOT NULL DEFAULT 1,
                    apply_count INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (domain, field_label)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS form_failure_reasons (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    domain TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    failure_type TEXT NOT NULL,
                    field_label TEXT,
                    selector TEXT,
                    details TEXT,
                    created_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_failures_domain
                ON form_failure_reasons (domain)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_failures_platform
                ON form_failure_reasons (platform)
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS container_selectors (
                    domain TEXT PRIMARY KEY,
                    selector TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS page_timings (
                    domain TEXT PRIMARY KEY,
                    avg_hydration_ms INTEGER NOT NULL,
                    avg_fill_ms INTEGER NOT NULL,
                    avg_transition_ms INTEGER NOT NULL,
                    sample_count INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS scan_strategy_preferences (
                    domain TEXT PRIMARY KEY,
                    preferred_strategy TEXT NOT NULL,
                    field_count INTEGER NOT NULL,
                    sample_count INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS field_confidence_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    domain TEXT NOT NULL,
                    field_label TEXT NOT NULL,
                    predicted_confidence REAL NOT NULL,
                    actual_correct INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_confidence_domain
                ON field_confidence_log (domain)
            """)
            # Migration: add content_hash column if missing
            try:
                conn.execute("SELECT content_hash FROM form_experience LIMIT 1")
            except sqlite3.OperationalError:
                conn.execute(
                    "ALTER TABLE form_experience ADD COLUMN content_hash TEXT DEFAULT ''"
                )
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_form_experience_content_hash
                ON form_experience (content_hash)
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS negative_exemplars (
                    domain TEXT NOT NULL,
                    field_label TEXT NOT NULL,
                    value_tried TEXT NOT NULL,
                    failure_reason TEXT NOT NULL,
                    platform TEXT NOT NULL DEFAULT '',
                    content_hash TEXT NOT NULL DEFAULT '',
                    attempt_count INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (domain, field_label, value_tried)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_neg_content_hash
                ON negative_exemplars (content_hash)
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS signal_corrections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    domain TEXT NOT NULL,
                    field_label TEXT NOT NULL,
                    signal_type TEXT NOT NULL,
                    error_message TEXT NOT NULL,
                    original_value TEXT NOT NULL,
                    corrected_value TEXT NOT NULL,
                    transform TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_sc_domain_field
                ON signal_corrections (domain, field_label)
            """)

    @property
    def _transfer_engine(self):
        if not hasattr(self, "_te"):
            from jobpulse.platform_transfer import PlatformTransferEngine
            self._te = PlatformTransferEngine(db_path=self._db_path)
        return self._te

    @staticmethod
    def normalize_domain(domain_or_url: str) -> str:
        if "://" in domain_or_url:
            parsed = urlparse(domain_or_url)
            return parsed.netloc.lower().removeprefix("www.")
        return domain_or_url.lower().removeprefix("www.")

    def record(
        self,
        domain: str,
        platform: str,
        adapter: str,
        pages_filled: int,
        field_types: list[str],
        screening_questions: list[str],
        time_seconds: float,
        success: bool,
    ) -> None:
        domain = self.normalize_domain(domain)
        now = datetime.now(UTC).isoformat()
        ft_json = json.dumps(field_types)
        sq_json = json.dumps(screening_questions)

        with sqlite3.connect(self._db_path) as conn:
            existing = conn.execute(
                "SELECT success FROM form_experience WHERE domain = ?", (domain,)
            ).fetchone()

            if existing and existing[0] == 1 and not success:
                conn.execute(
                    "UPDATE form_experience SET apply_count = apply_count + 1, updated_at = ? WHERE domain = ?",
                    (now, domain),
                )
            else:
                conn.execute(
                    """INSERT INTO form_experience
                       (domain, platform, adapter, pages_filled, field_types,
                        screening_questions, time_seconds, success, apply_count,
                        created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                       ON CONFLICT(domain) DO UPDATE SET
                           platform = excluded.platform,
                           adapter = excluded.adapter,
                           pages_filled = excluded.pages_filled,
                           field_types = excluded.field_types,
                           screening_questions = excluded.screening_questions,
                           time_seconds = excluded.time_seconds,
                           success = excluded.success,
                           apply_count = apply_count + 1,
                           updated_at = excluded.updated_at""",
                    (domain, platform, adapter, pages_filled, ft_json, sq_json,
                     time_seconds, int(success), now, now),
                )
        logger.info(
            "form_experience: recorded %s (platform=%s, pages=%d, success=%s, fields=%d)",
            domain, platform, pages_filled, success, len(field_types),
        )
        try:
            from shared.optimization import get_optimization_engine
            get_optimization_engine().emit(
                signal_type="success" if success else "failure",
                source_loop="form_experience",
                domain=domain,
                agent_name="form_filler",
                payload={"action": "record_experience", "adapter": adapter, "pages": pages_filled},
                session_id=f"fe_{domain}_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}",
            )
        except Exception as e:
            logger.debug("Optimization signal failed: %s", e)

    def store(
        self,
        domain: str,
        platform: str,
        adapter: str,
        pages_filled: int,
        field_types: dict | list,
        screening_questions: list[str],
        time_seconds: float,
        success: bool,
        content_hash: str = "",
    ) -> None:
        """Store form experience with optional content_hash for cross-domain matching.

        This is the PRAXIS-aware variant of record(). Accepts field_types as either
        a list (legacy) or dict (field_type -> count) and stores content_hash for
        structural page fingerprinting.
        """
        domain = self.normalize_domain(domain)
        now = datetime.now(UTC).isoformat()
        if isinstance(field_types, dict):
            ft_json = json.dumps(field_types)
        else:
            ft_json = json.dumps(field_types)
        sq_json = json.dumps(screening_questions)

        with sqlite3.connect(self._db_path) as conn:
            existing = conn.execute(
                "SELECT success FROM form_experience WHERE domain = ?", (domain,)
            ).fetchone()

            if existing and existing[0] == 1 and not success:
                conn.execute(
                    "UPDATE form_experience SET apply_count = apply_count + 1, updated_at = ? WHERE domain = ?",
                    (now, domain),
                )
            else:
                conn.execute(
                    """INSERT INTO form_experience
                       (domain, platform, adapter, pages_filled, field_types,
                        screening_questions, time_seconds, success, apply_count,
                        content_hash, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                       ON CONFLICT(domain) DO UPDATE SET
                           platform = excluded.platform,
                           adapter = excluded.adapter,
                           pages_filled = excluded.pages_filled,
                           field_types = excluded.field_types,
                           screening_questions = excluded.screening_questions,
                           time_seconds = excluded.time_seconds,
                           success = excluded.success,
                           content_hash = excluded.content_hash,
                           apply_count = apply_count + 1,
                           updated_at = excluded.updated_at""",
                    (domain, platform, adapter, pages_filled, ft_json, sq_json,
                     time_seconds, int(success), content_hash, now, now),
                )

    @observe_lookup("form_experience", "form_experience", key_arg=1)
    def lookup(self, domain_or_url: str) -> dict | None:
        domain = self.normalize_domain(domain_or_url)
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM form_experience WHERE domain = ?", (domain,)
            ).fetchone()
        return dict(row) if row else None

    @observe_lookup("form_experience", "form_experience.content_hash", key_arg=1)
    def lookup_by_content_hash(
        self, content_hash: str, exclude_domain: str = "",
    ) -> dict | None:
        """Find the most recent successful experience with this structural fingerprint.

        Excludes the given domain so callers get cross-domain matches only.
        """
        if not content_hash:
            return None
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """SELECT * FROM form_experience
                   WHERE content_hash = ? AND domain != ? AND success = 1
                   ORDER BY updated_at DESC LIMIT 1""",
                (content_hash, exclude_domain),
            ).fetchone()
        if row:
            return dict(row)
        return None

    def validate_against_live(
        self,
        domain_or_url: str,
        live_field_types: list[str],
        live_page_count: int | None = None,
        *,
        match_threshold: float = 0.8,
    ) -> dict:
        """Compare stored experience against live DOM scan.

        Args:
            domain_or_url: The URL or domain to look up.
            live_field_types: Field types discovered from the current page DOM.
            live_page_count: Page count from current DOM (if detectable).
            match_threshold: Minimum overlap ratio to trust stored experience.

        Returns:
            {"trusted": bool, "match_ratio": float, "stored": dict|None,
             "diverged_fields": list[str]}
        """
        stored = self.lookup(domain_or_url)
        if not stored or not stored.get("success"):
            return {"trusted": False, "match_ratio": 0.0, "stored": None,
                    "diverged_fields": []}

        stored_types = json.loads(stored["field_types"]) if isinstance(stored["field_types"], str) else stored["field_types"]

        if not stored_types and not live_field_types:
            return {"trusted": True, "match_ratio": 1.0, "stored": stored,
                    "diverged_fields": []}

        stored_set = set(stored_types)
        live_set = set(live_field_types)
        union = stored_set | live_set
        intersection = stored_set & live_set
        match_ratio = len(intersection) / len(union) if union else 1.0

        diverged = sorted(stored_set.symmetric_difference(live_set))

        trusted = match_ratio >= match_threshold
        if not trusted:
            logger.info(
                "form_experience: DIVERGENCE on %s — match %.0f%% (threshold %.0f%%), "
                "diverged fields: %s. Falling back to LLM detection.",
                self.normalize_domain(domain_or_url),
                match_ratio * 100, match_threshold * 100,
                diverged[:10],
            )

        if live_page_count is not None and stored.get("pages_filled"):
            if abs(live_page_count - stored["pages_filled"]) > 1:
                logger.info(
                    "form_experience: page count mismatch on %s — stored=%d, live=%d",
                    self.normalize_domain(domain_or_url),
                    stored["pages_filled"], live_page_count,
                )
                trusted = False

        return {"trusted": trusted, "match_ratio": match_ratio,
                "stored": stored, "diverged_fields": diverged}

    @observe_lookup("form_experience", "form_experience.platform_aggregate", key_arg=1)
    def get_platform_aggregate(self, platform: str) -> dict | None:
        """Aggregate form experience across ALL domains for a platform.

        Uses streaming aggregation instead of GROUP_CONCAT to avoid memory spikes.
        """
        from collections import Counter

        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT pages_filled, time_seconds, field_types, screening_questions
                   FROM form_experience
                   WHERE platform = ? AND success = 1""",
                (platform,),
            ).fetchall()

        if not rows:
            return None

        observation_count = len(rows)
        total_pages = 0.0
        total_time = 0.0
        field_type_counter: Counter = Counter()
        total_field_count = 0
        sq_counter: Counter = Counter()

        for row in rows:
            total_pages += row["pages_filled"] or 0
            total_time += row["time_seconds"] or 0

            ft_blob = row["field_types"]
            if ft_blob:
                try:
                    fields = json.loads(ft_blob) if isinstance(ft_blob, str) else ft_blob
                except (ValueError, TypeError):
                    fields = []
                field_type_counter.update(fields)
                total_field_count += len(fields)

            sq_blob = row["screening_questions"]
            if sq_blob:
                try:
                    questions = json.loads(sq_blob) if isinstance(sq_blob, str) else sq_blob
                except (ValueError, TypeError):
                    questions = []
                sq_counter.update(questions)

        avg_field_count = round(total_field_count / observation_count, 1) if observation_count else 0.0

        return {
            "platform": platform,
            "observation_count": observation_count,
            "avg_pages": round(total_pages / observation_count, 1),
            "avg_field_count": avg_field_count,
            "avg_time_seconds": round(total_time / observation_count, 1),
            "common_field_types": [ft for ft, _ in field_type_counter.most_common()],
            "field_type_frequencies": dict(field_type_counter),
            "common_screening_questions": sq_counter.most_common(),
        }

    @observe_lookup("form_experience", "form_experience.stats", key_arg=None)
    def get_stats(self) -> dict:
        with sqlite3.connect(self._db_path) as conn:
            total = conn.execute("SELECT COUNT(*) FROM form_experience").fetchone()[0]
            successful = conn.execute(
                "SELECT COUNT(*) FROM form_experience WHERE success = 1"
            ).fetchone()[0]
            failures = conn.execute(
                "SELECT COUNT(*) FROM form_failure_reasons"
            ).fetchone()[0]
        return {"total_domains": total, "successful_domains": successful, "recorded_failures": failures}

    def record_failure_reason(
        self,
        domain: str,
        platform: str,
        failure_type: str,
        field_label: str = "",
        selector: str = "",
        details: str = "",
    ) -> None:
        """Record why a form fill failed for a domain."""
        now = datetime.now(UTC).isoformat()
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO form_failure_reasons
                (domain, platform, failure_type, field_label, selector, details, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (self.normalize_domain(domain), platform, failure_type, field_label, selector, details, now),
            )

    @observe_lookup("form_experience", "form_failure_reasons", key_arg=1)
    def get_failure_reasons(self, domain_or_url: str, limit: int = 10) -> list[dict]:
        """Return recent failure reasons for a domain."""
        domain = self.normalize_domain(domain_or_url)
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT * FROM form_failure_reasons
                WHERE domain = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (domain, limit),
            ).fetchall()
        if rows:
            return [dict(r) for r in rows]
        transfer = self._transfer_engine.get_transfer_data(domain, "failure_patterns")
        if transfer:
            with sqlite3.connect(self._db_path) as conn:
                conn.row_factory = sqlite3.Row
                donor_rows = conn.execute(
                    """
                    SELECT * FROM form_failure_reasons
                    WHERE domain = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (transfer["donor_domain"], limit),
                ).fetchall()
            if donor_rows:
                return [dict(r) for r in donor_rows]
        return []

    @observe_lookup("form_experience", "form_failure_reasons.platform_stats", key_arg=1)
    def get_platform_failure_stats(self, platform: str) -> dict:
        """Return aggregated failure statistics for a platform."""
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT failure_type, COUNT(*) as cnt
                FROM form_failure_reasons
                WHERE platform = ?
                GROUP BY failure_type
                ORDER BY cnt DESC
                """,
                (platform,),
            ).fetchall()
        return {r["failure_type"]: r["cnt"] for r in rows}

    @observe_lookup("form_experience", "field_label_mappings", key_arg=1)
    def get_field_mappings(self, domain_or_url: str) -> dict[str, str]:
        """Return {field_label: profile_key} for a domain."""
        domain = self.normalize_domain(domain_or_url)
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute(
                "SELECT field_label, profile_key FROM field_label_mappings WHERE domain = ?",
                (domain,),
            ).fetchall()
        if rows:
            return {label: key for label, key in rows}
        transfer = self._transfer_engine.get_transfer_data(domain, "field_types")
        if transfer:
            with sqlite3.connect(self._db_path) as conn:
                donor_rows = conn.execute(
                    "SELECT field_label, profile_key FROM field_label_mappings WHERE domain = ?",
                    (transfer["donor_domain"],),
                ).fetchall()
            if donor_rows:
                return {label: key for label, key in donor_rows}
        return {}

    def record_fill_technique(
        self,
        domain_or_url: str,
        field_label: str,
        field_type: str,
        technique: str,
        value_used: str | None = None,
        success: bool = True,
    ) -> None:
        """Record how a field was filled (type-to-search, select-exact, etc.)."""
        domain = self.normalize_domain(domain_or_url)
        now = datetime.now(UTC).isoformat()
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """INSERT INTO fill_techniques
                   (domain, field_label, field_type, technique, value_used, success, apply_count, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, 1, ?)
                   ON CONFLICT(domain, field_label) DO UPDATE SET
                       technique = excluded.technique,
                       value_used = excluded.value_used,
                       field_type = excluded.field_type,
                       success = excluded.success,
                       apply_count = apply_count + 1,
                       updated_at = excluded.updated_at""",
                (domain, field_label, field_type, technique, value_used, int(success), now),
            )

    @observe_lookup("form_experience", "fill_techniques", key_arg=1)
    def get_fill_techniques(self, domain_or_url: str) -> dict[str, dict]:
        """Return {field_label: {technique, value_used, field_type}} for a domain."""
        domain = self.normalize_domain(domain_or_url)
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM fill_techniques WHERE domain = ? AND success = 1",
                (domain,),
            ).fetchall()
        return {r["field_label"]: dict(r) for r in rows}

    @observe_lookup("form_experience", "fill_techniques.platform", key_arg=1)
    def get_platform_fill_techniques(self, platform: str) -> list[dict]:
        """Return all successful fill techniques for a platform (cross-domain learning)."""
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT ft.* FROM fill_techniques ft
                   JOIN form_experience fe ON ft.domain = fe.domain
                   WHERE fe.platform = ? AND ft.success = 1
                   ORDER BY ft.apply_count DESC""",
                (platform,),
            ).fetchall()
        return [dict(r) for r in rows]

    def save_field_mappings(self, domain_or_url: str, mappings: dict[str, str]) -> None:
        """Persist {field_label: profile_key} for a domain."""
        domain = self.normalize_domain(domain_or_url)
        with sqlite3.connect(self._db_path) as conn:
            for field_label, profile_key in mappings.items():
                conn.execute(
                    """INSERT INTO field_label_mappings (domain, field_label, profile_key, confidence)
                       VALUES (?, ?, ?, 1.0)
                       ON CONFLICT(domain, field_label) DO UPDATE SET
                           profile_key = excluded.profile_key""",
                    (domain, field_label, profile_key),
                )
        logger.info("Saved %d field mappings for %s", len(mappings), domain)

    def store_container(self, domain_or_url: str, selector: str) -> None:
        domain = self.normalize_domain(domain_or_url)
        now = datetime.now(UTC).isoformat()
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """INSERT INTO container_selectors (domain, selector, updated_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(domain) DO UPDATE SET
                       selector = excluded.selector,
                       updated_at = excluded.updated_at""",
                (domain, selector, now),
            )

    @observe_lookup("form_experience", "container_selectors", key_arg=1)
    def get_container(self, domain_or_url: str) -> str | None:
        domain = self.normalize_domain(domain_or_url)
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT selector FROM container_selectors WHERE domain = ?",
                (domain,),
            ).fetchone()
        if row:
            return row[0]
        transfer = self._transfer_engine.get_transfer_data(domain, "container_selectors")
        if transfer:
            with sqlite3.connect(self._db_path) as conn:
                donor_row = conn.execute(
                    "SELECT selector FROM container_selectors WHERE domain = ?",
                    (transfer["donor_domain"],),
                ).fetchone()
            if donor_row:
                return donor_row[0]
        return None

    def delete_container(self, domain_or_url: str) -> None:
        domain = self.normalize_domain(domain_or_url)
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                "DELETE FROM container_selectors WHERE domain = ?", (domain,),
            )

    def store_timing(self, domain_or_url: str, hydration_ms: int, fill_ms: int, transition_ms: int) -> None:
        domain = self.normalize_domain(domain_or_url)
        now = datetime.now(UTC).isoformat()
        with sqlite3.connect(self._db_path) as conn:
            existing = conn.execute(
                "SELECT avg_hydration_ms, avg_fill_ms, avg_transition_ms, sample_count "
                "FROM page_timings WHERE domain = ?",
                (domain,),
            ).fetchone()
            if existing:
                n = existing[3]
                new_hydration = (existing[0] * n + hydration_ms) // (n + 1)
                new_fill = (existing[1] * n + fill_ms) // (n + 1)
                new_transition = (existing[2] * n + transition_ms) // (n + 1)
                conn.execute(
                    """UPDATE page_timings SET
                       avg_hydration_ms = ?, avg_fill_ms = ?, avg_transition_ms = ?,
                       sample_count = sample_count + 1, updated_at = ?
                       WHERE domain = ?""",
                    (new_hydration, new_fill, new_transition, now, domain),
                )
            else:
                conn.execute(
                    """INSERT INTO page_timings
                       (domain, avg_hydration_ms, avg_fill_ms, avg_transition_ms, sample_count, updated_at)
                       VALUES (?, ?, ?, ?, 1, ?)""",
                    (domain, hydration_ms, fill_ms, transition_ms, now),
                )

    @observe_lookup("form_experience", "page_timings", key_arg=1)
    def get_timing(self, domain_or_url: str) -> dict | None:
        domain = self.normalize_domain(domain_or_url)
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM page_timings WHERE domain = ?", (domain,),
            ).fetchone()
        if row:
            return dict(row)
        transfer = self._transfer_engine.get_transfer_data(domain, "timing_profile")
        if transfer:
            with sqlite3.connect(self._db_path) as conn:
                conn.row_factory = sqlite3.Row
                donor_row = conn.execute(
                    "SELECT * FROM page_timings WHERE domain = ?", (transfer["donor_domain"],),
                ).fetchone()
            if donor_row:
                result = dict(donor_row)
                result["_transfer"] = True
                result["_donor"] = transfer["donor_domain"]
                return result
        return None

    def store_scan_strategy(
        self, domain_or_url: str, strategy: str, field_count: int,
    ) -> None:
        domain = self.normalize_domain(domain_or_url)
        now = datetime.now(UTC).isoformat()
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """INSERT INTO scan_strategy_preferences
                   (domain, preferred_strategy, field_count, sample_count, updated_at)
                   VALUES (?, ?, ?, 1, ?)
                   ON CONFLICT(domain) DO UPDATE SET
                       preferred_strategy = excluded.preferred_strategy,
                       field_count = excluded.field_count,
                       sample_count = sample_count + 1,
                       updated_at = excluded.updated_at""",
                (domain, strategy, field_count, now),
            )
        logger.debug("Stored scan strategy %s for %s (%d fields)", strategy, domain, field_count)

    @observe_lookup("form_experience", "scan_strategy_preferences", key_arg=1)
    def get_scan_strategy(self, domain_or_url: str) -> dict | None:
        domain = self.normalize_domain(domain_or_url)
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM scan_strategy_preferences WHERE domain = ?",
                (domain,),
            ).fetchone()
        if row:
            return dict(row)
        transfer = self._transfer_engine.get_transfer_data(domain, "fill_techniques")
        if transfer:
            with sqlite3.connect(self._db_path) as conn:
                conn.row_factory = sqlite3.Row
                donor_row = conn.execute(
                    "SELECT * FROM scan_strategy_preferences WHERE domain = ?",
                    (transfer["donor_domain"],),
                ).fetchone()
            if donor_row:
                result = dict(donor_row)
                result["_transfer"] = True
                result["_donor"] = transfer["donor_domain"]
                return result
        return None

    def log_field_confidence(
        self, domain: str, field_label: str,
        predicted_confidence: float, actual_correct: bool,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """INSERT INTO field_confidence_log
                   (domain, field_label, predicted_confidence, actual_correct, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (domain, field_label, predicted_confidence, int(actual_correct), now),
            )

    @observe_lookup("form_experience", "field_confidence_log", key_arg=1)
    def get_confidence_calibration(self, domain: str) -> dict:
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                """SELECT COUNT(*), SUM(actual_correct)
                   FROM field_confidence_log WHERE domain = ?""",
                (domain,),
            ).fetchone()
        total = row[0] if row else 0
        correct = row[1] or 0
        return {"total": total, "correct": correct}

    def store_negative_exemplar(
        self,
        domain: str,
        field_label: str,
        value_tried: str,
        failure_reason: str,
        platform: str = "",
        content_hash: str = "",
    ) -> None:
        """Record a value that failed for a field — used by PRAXIS to avoid repeating mistakes."""
        now = datetime.now(UTC).isoformat()
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """INSERT INTO negative_exemplars
                   (domain, field_label, value_tried, failure_reason, platform,
                    content_hash, attempt_count, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
                   ON CONFLICT(domain, field_label, value_tried) DO UPDATE SET
                       attempt_count = attempt_count + 1,
                       failure_reason = excluded.failure_reason,
                       updated_at = excluded.updated_at""",
                (domain, field_label, value_tried, failure_reason, platform,
                 content_hash, now, now),
            )

    @observe_lookup("form_experience", "negative_exemplars", key_arg=1)
    def get_negative_exemplars(self, domain: str) -> list[dict]:
        """Return all failed field values for a domain, newest first."""
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM negative_exemplars WHERE domain = ? ORDER BY updated_at DESC",
                (domain,),
            ).fetchall()
        return [dict(r) for r in rows]

    @observe_lookup("form_experience", "negative_exemplars.content_hash", key_arg=1)
    def get_negative_exemplars_by_hash(self, content_hash: str) -> list[dict]:
        """Return all failed field values matching this structural fingerprint (cross-domain)."""
        if not content_hash:
            return []
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM negative_exemplars WHERE content_hash = ? ORDER BY updated_at DESC",
                (content_hash,),
            ).fetchall()
        return [dict(r) for r in rows]

    def store_signal_correction(
        self,
        domain: str,
        field_label: str,
        signal_type: str,
        error_message: str,
        original_value: str,
        corrected_value: str,
        transform: str,
    ) -> None:
        domain = self.normalize_domain(domain)
        now = datetime.now(UTC).isoformat()
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """INSERT INTO signal_corrections
                   (domain, field_label, signal_type, error_message,
                    original_value, corrected_value, transform, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (domain, field_label, signal_type, error_message,
                 original_value, corrected_value, transform, now),
            )
        logger.info(
            "signal_correction: stored %s/%s type=%s transform=%s",
            domain, field_label, signal_type, transform,
        )

    @observe_lookup("form_experience", "signal_corrections", key_arg=1)
    def get_signal_corrections(
        self, domain: str, field_label: str | None = None,
    ) -> list[dict]:
        domain = self.normalize_domain(domain)
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            if field_label:
                rows = conn.execute(
                    """SELECT * FROM signal_corrections
                       WHERE domain = ? AND field_label = ?
                       ORDER BY created_at DESC LIMIT 5""",
                    (domain, field_label),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT * FROM signal_corrections
                       WHERE domain = ?
                       ORDER BY created_at DESC LIMIT 20""",
                    (domain,),
                ).fetchall()
        return [dict(r) for r in rows]
