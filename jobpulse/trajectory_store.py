"""Application Trajectory Store — per-field decision journal + strategy summaries.

Records HOW each field was filled (strategy tier, confidence, time), not just
WHAT was filled. Enables post-application reflection, heuristic extraction,
and experience replay for future applications.

Architecture (arXiv 2603.10600 — Trajectory-Informed Memory Generation):
    1. Raw trajectory — per-field decision log (field_trajectories)
    2. Strategy summary — per-application aggregate (application_strategies)
    3. Distilled heuristics — reusable rules extracted via reflection

Sensitive field values (DEI, salary, visa) are encrypted at rest via the
same Fernet key used by ProfileStore.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from shared.logging_config import get_logger

from jobpulse.config import DATA_DIR

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)

_DEFAULT_DB = str(DATA_DIR / "trajectory.db")

# Fields whose values must be encrypted at rest
_SENSITIVE_FIELD_PATTERNS = frozenset({
    "gender", "ethnicity", "race", "disability", "religion", "orientation",
    "sexual", "veteran", "marital", "pronoun", "age", "salary", "compensation",
    "visa", "immigration", "right to work", "sponsorship",
})

_MAX_TRAJECTORIES_PER_DOMAIN = 500
_MAX_HEURISTICS_PER_DOMAIN = 50
_HEURISTIC_TTL_DAYS = 90
_CONFIDENCE_DECAY_RATE = 0.95  # per week


class StrategyTier(str, Enum):
    PATTERN_MATCH = "pattern_match"
    CACHE_HIT = "cache_hit"
    AGENT_RULE = "agent_rule"
    PROFILE_STORE = "profile_store"
    LLM_TIER3 = "llm_tier3"
    COGNITIVE_L2 = "cognitive_l2"
    VISION_TIER5 = "vision_tier5"
    USER_OVERRIDE = "user_override"
    HEURISTIC_REPLAY = "heuristic_replay"
    DEFAULT_FALLBACK = "default_fallback"


@dataclass
class FieldTrajectory:
    job_id: str
    domain: str
    page_index: int
    field_label: str
    field_type: str
    strategy: str
    value_filled: str
    confidence: float
    time_ms: int
    corrected: bool = False
    corrected_value: str = ""
    created_at: str = ""


@dataclass
class ApplicationStrategy:
    job_id: str
    domain: str
    platform: str
    adapter: str
    navigation_strategy: str
    fields_total: int
    fields_pattern: int
    fields_llm: int
    fields_cached: int
    fields_corrected: int
    total_time_seconds: float
    success: bool
    reflection: str = ""
    heuristics: str = "[]"
    created_at: str = ""


@dataclass
class Heuristic:
    trigger: str
    action: str
    confidence: float
    source_domain: str
    platform: str
    times_applied: int = 0
    times_succeeded: int = 0
    created_at: str = ""
    expires_at: str = ""


def _is_sensitive_field(label: str) -> bool:
    lower = label.lower()
    return any(pat in lower for pat in _SENSITIVE_FIELD_PATTERNS)


def _normalize_domain(domain_or_url: str) -> str:
    if "://" in domain_or_url:
        parsed = urlparse(domain_or_url)
        host = (parsed.hostname or parsed.netloc).lower()
        return host.removeprefix("www.")
    return domain_or_url.split(":")[0].lower().removeprefix("www.")


class TrajectoryStore:
    """Per-field decision journal + application strategy summaries.

    Thread-safe: uses connection-per-call pattern (no shared connection).
    Sensitive field values encrypted via ProfileStore's Fernet key.
    """

    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = db_path or _DEFAULT_DB
        self._lock = threading.Lock()
        self._fernet = None
        self._ensure_schema()
        self._get_fernet()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS field_trajectories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    page_index INTEGER DEFAULT 0,
                    field_label TEXT NOT NULL,
                    field_type TEXT DEFAULT '',
                    strategy TEXT NOT NULL,
                    value_filled TEXT DEFAULT '',
                    is_encrypted INTEGER DEFAULT 0,
                    confidence REAL DEFAULT 0.5,
                    time_ms INTEGER DEFAULT 0,
                    corrected INTEGER DEFAULT 0,
                    corrected_value TEXT DEFAULT '',
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_ft_job
                    ON field_trajectories(job_id);
                CREATE INDEX IF NOT EXISTS idx_ft_domain_label
                    ON field_trajectories(domain, field_label);

                CREATE TABLE IF NOT EXISTS application_strategies (
                    job_id TEXT PRIMARY KEY,
                    domain TEXT NOT NULL,
                    platform TEXT DEFAULT '',
                    adapter TEXT DEFAULT '',
                    navigation_strategy TEXT DEFAULT '',
                    fields_total INTEGER DEFAULT 0,
                    fields_pattern INTEGER DEFAULT 0,
                    fields_llm INTEGER DEFAULT 0,
                    fields_cached INTEGER DEFAULT 0,
                    fields_corrected INTEGER DEFAULT 0,
                    total_time_seconds REAL DEFAULT 0,
                    success INTEGER DEFAULT 0,
                    reflection TEXT DEFAULT '',
                    heuristics TEXT DEFAULT '[]',
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_as_domain_platform
                    ON application_strategies(domain, platform);

                CREATE TABLE IF NOT EXISTS heuristics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trigger_condition TEXT NOT NULL,
                    action TEXT NOT NULL,
                    confidence REAL DEFAULT 0.5,
                    source_domain TEXT NOT NULL,
                    platform TEXT DEFAULT '',
                    times_applied INTEGER DEFAULT 0,
                    times_succeeded INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_h_domain_platform
                    ON heuristics(source_domain, platform);
                CREATE INDEX IF NOT EXISTS idx_h_expires
                    ON heuristics(expires_at);
            """)

    # ------------------------------------------------------------------
    # Encryption helpers
    # ------------------------------------------------------------------

    def _get_fernet(self):
        if self._fernet is None:
            try:
                from shared.profile_store import get_profile_store
                ps = get_profile_store()
                from cryptography.fernet import Fernet
                key_bytes = ps._key_path.read_bytes().strip()
                self._fernet = Fernet(key_bytes)
            except Exception:
                logger.warning("TrajectoryStore: encryption unavailable, sensitive values will be redacted")
        return self._fernet

    def _encrypt_if_sensitive(self, field_label: str, value: str) -> tuple[str, bool]:
        if not _is_sensitive_field(field_label):
            return value, False
        f = self._get_fernet()
        if f is None:
            return "[REDACTED]", True
        return f.encrypt(value.encode()).decode(), True

    def _decrypt_value(self, value: str, is_encrypted: bool) -> str:
        if not is_encrypted:
            return value
        if value == "[REDACTED]":
            return value
        f = self._get_fernet()
        if f is None:
            return "[REDACTED]"
        try:
            return f.decrypt(value.encode()).decode()
        except Exception:
            return "[REDACTED]"

    # ------------------------------------------------------------------
    # Field trajectory CRUD
    # ------------------------------------------------------------------

    def log_field(
        self,
        job_id: str,
        domain: str,
        field_label: str,
        strategy: str | StrategyTier,
        value_filled: str = "",
        *,
        page_index: int = 0,
        field_type: str = "",
        confidence: float = 0.5,
        time_ms: int = 0,
    ) -> int:
        """Log a single field fill decision. Returns the row id."""
        domain = _normalize_domain(domain)
        tier = strategy.value if isinstance(strategy, StrategyTier) else strategy
        enc_value, is_enc = self._encrypt_if_sensitive(field_label, value_filled)
        now = datetime.now(UTC).isoformat()

        with self._lock:
            with self._connect() as conn:
                cur = conn.execute(
                    """INSERT INTO field_trajectories
                       (job_id, domain, page_index, field_label, field_type,
                        strategy, value_filled, is_encrypted, confidence,
                        time_ms, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (job_id, domain, page_index, field_label, field_type,
                     tier, enc_value, int(is_enc), confidence, time_ms, now),
                )
                return cur.lastrowid

    def mark_corrected(
        self, job_id: str, domain: str, field_label: str, corrected_value: str,
    ) -> bool:
        """Mark a trajectory field as corrected by the user. Returns True if found."""
        domain = _normalize_domain(domain)
        label_norm = field_label.strip().lower()
        enc_val, _ = self._encrypt_if_sensitive(field_label, corrected_value)

        with self._lock:
            with self._connect() as conn:
                cur = conn.execute(
                    """UPDATE field_trajectories
                       SET corrected = 1, corrected_value = ?
                       WHERE id = (
                           SELECT id FROM field_trajectories
                           WHERE job_id = ? AND domain = ?
                             AND LOWER(TRIM(field_label)) = ?
                           ORDER BY id DESC LIMIT 1
                       )""",
                    (enc_val, job_id, domain, label_norm),
                )
                return cur.rowcount > 0

    def get_trajectories(self, job_id: str) -> list[FieldTrajectory]:
        """Get all field trajectories for a job, decrypting values."""
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT * FROM field_trajectories
                   WHERE job_id = ? ORDER BY page_index, id""",
                (job_id,),
            ).fetchall()

        return [
            FieldTrajectory(
                job_id=r["job_id"],
                domain=r["domain"],
                page_index=r["page_index"],
                field_label=r["field_label"],
                field_type=r["field_type"],
                strategy=r["strategy"],
                value_filled=self._decrypt_value(r["value_filled"], bool(r["is_encrypted"])),
                confidence=r["confidence"],
                time_ms=r["time_ms"],
                corrected=bool(r["corrected"]),
                corrected_value=self._decrypt_value(r["corrected_value"], bool(r["is_encrypted"])) if r["corrected"] else "",
                created_at=r["created_at"],
            )
            for r in rows
        ]

    def get_domain_trajectories(self, domain: str, *, limit: int = 100) -> list[FieldTrajectory]:
        """Get recent trajectories for a domain (for reflection)."""
        domain = _normalize_domain(domain)
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT * FROM field_trajectories
                   WHERE domain = ? ORDER BY id DESC LIMIT ?""",
                (domain, limit),
            ).fetchall()

        return [
            FieldTrajectory(
                job_id=r["job_id"],
                domain=r["domain"],
                page_index=r["page_index"],
                field_label=r["field_label"],
                field_type=r["field_type"],
                strategy=r["strategy"],
                value_filled="[encrypted]" if r["is_encrypted"] else r["value_filled"],
                confidence=r["confidence"],
                time_ms=r["time_ms"],
                corrected=bool(r["corrected"]),
                corrected_value="",
                created_at=r["created_at"],
            )
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Application strategy CRUD
    # ------------------------------------------------------------------

    def save_strategy(self, strategy: ApplicationStrategy) -> None:
        """Save or update an application strategy summary."""
        now = datetime.now(UTC).isoformat()
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """INSERT INTO application_strategies
                       (job_id, domain, platform, adapter, navigation_strategy,
                        fields_total, fields_pattern, fields_llm, fields_cached,
                        fields_corrected, total_time_seconds, success,
                        reflection, heuristics, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(job_id) DO UPDATE SET
                           reflection = excluded.reflection,
                           heuristics = excluded.heuristics,
                           fields_corrected = excluded.fields_corrected,
                           success = excluded.success""",
                    (strategy.job_id, _normalize_domain(strategy.domain),
                     strategy.platform, strategy.adapter,
                     strategy.navigation_strategy,
                     strategy.fields_total, strategy.fields_pattern,
                     strategy.fields_llm, strategy.fields_cached,
                     strategy.fields_corrected, strategy.total_time_seconds,
                     int(strategy.success), strategy.reflection,
                     strategy.heuristics, strategy.created_at or now),
                )

    def get_strategy(self, job_id: str) -> ApplicationStrategy | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM application_strategies WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        if not row:
            return None
        return ApplicationStrategy(**{k: row[k] for k in row.keys()})

    def get_domain_strategies(
        self, domain: str, *, limit: int = 10,
    ) -> list[ApplicationStrategy]:
        domain = _normalize_domain(domain)
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT * FROM application_strategies
                   WHERE domain = ? ORDER BY created_at DESC LIMIT ?""",
                (domain, limit),
            ).fetchall()
        return [ApplicationStrategy(**{k: r[k] for k in r.keys()}) for r in rows]

    def get_platform_strategies(
        self, platform: str, *, limit: int = 20,
    ) -> list[ApplicationStrategy]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT * FROM application_strategies
                   WHERE platform = ? AND success = 1
                   ORDER BY created_at DESC LIMIT ?""",
                (platform, limit),
            ).fetchall()
        return [ApplicationStrategy(**{k: r[k] for k in r.keys()}) for r in rows]

    # ------------------------------------------------------------------
    # Aggregate trajectories → strategy summary
    # ------------------------------------------------------------------

    def aggregate_strategy(
        self, job_id: str, job_context: dict,
        trajectories: list[FieldTrajectory] | None = None,
    ) -> ApplicationStrategy:
        """Build an ApplicationStrategy from field_trajectories for a job."""
        if trajectories is None:
            trajectories = self.get_trajectories(job_id)
        if not trajectories:
            domain = _normalize_domain(job_context.get("url", ""))
            return ApplicationStrategy(
                job_id=job_id, domain=domain,
                platform=job_context.get("platform", ""),
                adapter=job_context.get("adapter", ""),
                navigation_strategy="",
                fields_total=0, fields_pattern=0, fields_llm=0,
                fields_cached=0, fields_corrected=0,
                total_time_seconds=0, success=False,
            )

        _PATTERN_TIERS = {"pattern_match", "heuristic_replay", "profile_store"}
        _LLM_TIERS = {"llm_tier3", "cognitive_l2", "vision_tier5"}
        _CACHE_TIERS = {"cache_hit", "agent_rule"}

        corrected = sum(1 for t in trajectories if t.corrected)
        total_ms = sum(t.time_ms for t in trajectories)
        pattern_count = sum(1 for t in trajectories if t.strategy in _PATTERN_TIERS)
        llm_count = sum(1 for t in trajectories if t.strategy in _LLM_TIERS)
        cached_count = sum(1 for t in trajectories if t.strategy in _CACHE_TIERS)

        domain = trajectories[0].domain
        return ApplicationStrategy(
            job_id=job_id,
            domain=domain,
            platform=job_context.get("platform", ""),
            adapter=job_context.get("adapter", "extension"),
            navigation_strategy=job_context.get("navigation_strategy", ""),
            fields_total=len(trajectories),
            fields_pattern=pattern_count,
            fields_llm=llm_count,
            fields_cached=cached_count,
            fields_corrected=corrected,
            total_time_seconds=total_ms / 1000.0,
            success=job_context.get("success", True),
        )

    # ------------------------------------------------------------------
    # Heuristics CRUD
    # ------------------------------------------------------------------

    def save_heuristics(self, heuristics: list[Heuristic]) -> int:
        """Save extracted heuristics with dedup on trigger+domain. Returns count saved."""
        now = datetime.now(UTC)
        saved = 0
        with self._lock:
            with self._connect() as conn:
                for h in heuristics:
                    domain = _normalize_domain(h.source_domain)
                    expires = h.expires_at or (now + timedelta(days=_HEURISTIC_TTL_DAYS)).isoformat()
                    existing = conn.execute(
                        "SELECT id FROM heuristics WHERE trigger_condition = ? AND source_domain = ?",
                        (h.trigger, domain),
                    ).fetchone()
                    if existing:
                        conn.execute(
                            """UPDATE heuristics
                               SET action = ?, confidence = MAX(confidence, ?), expires_at = ?
                               WHERE id = ?""",
                            (h.action, h.confidence, expires, existing["id"]),
                        )
                    else:
                        conn.execute(
                            """INSERT INTO heuristics
                               (trigger_condition, action, confidence, source_domain,
                                platform, times_applied, times_succeeded,
                                created_at, expires_at)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (h.trigger, h.action, h.confidence,
                             domain, h.platform,
                             h.times_applied, h.times_succeeded,
                             h.created_at or now.isoformat(), expires),
                        )
                    saved += 1
        return saved

    def get_heuristics(
        self, domain: str, *, platform: str = "", include_platform: bool = True,
    ) -> list[Heuristic]:
        """Get active heuristics for a domain, optionally including platform-wide ones."""
        domain = _normalize_domain(domain)
        now = datetime.now(UTC).isoformat()

        with self._connect() as conn:
            if include_platform and platform:
                rows = conn.execute(
                    """SELECT * FROM heuristics
                       WHERE (source_domain = ? OR platform = ?)
                         AND expires_at > ?
                       ORDER BY confidence DESC, times_succeeded DESC""",
                    (domain, platform, now),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT * FROM heuristics
                       WHERE source_domain = ? AND expires_at > ?
                       ORDER BY confidence DESC, times_succeeded DESC""",
                    (domain, now),
                ).fetchall()

        return [
            Heuristic(
                trigger=r["trigger_condition"],
                action=r["action"],
                confidence=r["confidence"],
                source_domain=r["source_domain"],
                platform=r["platform"],
                times_applied=r["times_applied"],
                times_succeeded=r["times_succeeded"],
                created_at=r["created_at"],
                expires_at=r["expires_at"],
            )
            for r in rows
        ]

    def record_heuristic_outcome(
        self, heuristic_id: int, succeeded: bool,
    ) -> None:
        """Record whether a replayed heuristic succeeded."""
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """UPDATE heuristics
                       SET times_applied = times_applied + 1,
                           times_succeeded = times_succeeded + ?
                       WHERE id = ?""",
                    (int(succeeded), heuristic_id),
                )

    def invalidate_stale_heuristics(self, domain: str, *, threshold: float = 0.6) -> int:
        """Invalidate heuristics with success rate below threshold. Returns count."""
        domain = _normalize_domain(domain)
        now = datetime.now(UTC).isoformat()
        with self._lock:
            with self._connect() as conn:
                cur = conn.execute(
                    """UPDATE heuristics
                       SET expires_at = ?
                       WHERE source_domain = ?
                         AND times_applied >= 3
                         AND CAST(times_succeeded AS REAL) / times_applied < ?
                         AND expires_at > ?""",
                    (now, domain, threshold, now),
                )
                return cur.rowcount

    def decay_confidence(self) -> int:
        """Apply weekly confidence decay to all heuristics. Run from cron."""
        with self._lock:
            with self._connect() as conn:
                cur = conn.execute(
                    """UPDATE heuristics
                       SET confidence = confidence * ?
                       WHERE confidence > 0.1""",
                    (_CONFIDENCE_DECAY_RATE,),
                )
                return cur.rowcount

    # ------------------------------------------------------------------
    # Pruning
    # ------------------------------------------------------------------

    def prune(self) -> dict[str, int]:
        """Remove old trajectories and expired heuristics. Returns counts."""
        now = datetime.now(UTC).isoformat()
        pruned: dict[str, int] = {}

        with self._lock:
            with self._connect() as conn:
                # Expire old heuristics
                cur = conn.execute(
                    "DELETE FROM heuristics WHERE expires_at <= ?", (now,),
                )
                pruned["heuristics_expired"] = cur.rowcount

                # Cap trajectories per domain
                domains = conn.execute(
                    """SELECT domain, COUNT(*) as cnt
                       FROM field_trajectories
                       GROUP BY domain HAVING cnt > ?""",
                    (_MAX_TRAJECTORIES_PER_DOMAIN,),
                ).fetchall()

                total_pruned = 0
                for row in domains:
                    cur = conn.execute(
                        """DELETE FROM field_trajectories
                           WHERE domain = ? AND id NOT IN (
                               SELECT id FROM field_trajectories
                               WHERE domain = ?
                               ORDER BY id DESC LIMIT ?
                           )""",
                        (row["domain"], row["domain"], _MAX_TRAJECTORIES_PER_DOMAIN),
                    )
                    total_pruned += cur.rowcount
                pruned["trajectories_pruned"] = total_pruned

                # Cap heuristics per domain
                h_domains = conn.execute(
                    """SELECT source_domain, COUNT(*) as cnt
                       FROM heuristics
                       GROUP BY source_domain HAVING cnt > ?""",
                    (_MAX_HEURISTICS_PER_DOMAIN,),
                ).fetchall()

                h_pruned = 0
                for row in h_domains:
                    cur = conn.execute(
                        """DELETE FROM heuristics
                           WHERE source_domain = ? AND id NOT IN (
                               SELECT id FROM heuristics
                               WHERE source_domain = ?
                               ORDER BY confidence DESC LIMIT ?
                           )""",
                        (row["source_domain"], row["source_domain"],
                         _MAX_HEURISTICS_PER_DOMAIN),
                    )
                    h_pruned += cur.rowcount
                pruned["heuristics_pruned"] = h_pruned

        return pruned

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def stats(self) -> dict[str, int]:
        with self._connect() as conn:
            ft = conn.execute("SELECT COUNT(*) FROM field_trajectories").fetchone()[0]
            _as = conn.execute("SELECT COUNT(*) FROM application_strategies").fetchone()[0]
            h = conn.execute("SELECT COUNT(*) FROM heuristics").fetchone()[0]
            h_active = conn.execute(
                "SELECT COUNT(*) FROM heuristics WHERE expires_at > ?",
                (datetime.now(UTC).isoformat(),),
            ).fetchone()[0]
        return {
            "field_trajectories": ft,
            "application_strategies": _as,
            "heuristics_total": h,
            "heuristics_active": h_active,
        }


# ------------------------------------------------------------------
# Singleton
# ------------------------------------------------------------------

_shared_store: TrajectoryStore | None = None
_store_lock = threading.Lock()


def get_trajectory_store(db_path: str | None = None) -> TrajectoryStore:
    global _shared_store
    if db_path is not None:
        return TrajectoryStore(db_path=db_path)
    if _shared_store is not None:
        return _shared_store
    with _store_lock:
        if _shared_store is None:
            _shared_store = TrajectoryStore()
    return _shared_store


def _reset_shared_store() -> None:
    """Reset singleton — for tests only."""
    global _shared_store
    _shared_store = None


# ------------------------------------------------------------------
# Heuristic reuse loop — call before each new application
# ------------------------------------------------------------------


def load_heuristics_for_application(
    domain: str,
    platform: str = "",
    *,
    store: TrajectoryStore | None = None,
) -> dict:
    """Load all relevant heuristics + past experience for a domain/platform.

    Three-tier lookup:
        1. Exact domain heuristics (highest confidence)
        2. Same-platform heuristics (cross-domain transfer)
        3. ExperienceMemory by domain (GRPO cross-application)

    Also invalidates stale heuristics (success rate < 60% with 3+ samples).

    Returns:
        {
            "domain_heuristics": [...],
            "platform_heuristics": [...],
            "experience_patterns": [...],
            "prompt_context": str,  # ready to inject into LLM prompts
        }
    """
    ts = store or get_trajectory_store()
    domain = _normalize_domain(domain)

    # Invalidate stale heuristics before loading
    ts.invalidate_stale_heuristics(domain)

    # Tier 1: exact domain
    domain_h = ts.get_heuristics(domain, platform=platform, include_platform=False)

    # Tier 2: same platform (excluding domain-specific to avoid dupes)
    platform_h = []
    if platform:
        all_platform = ts.get_heuristics(domain, platform=platform, include_platform=True)
        domain_triggers = {h.trigger for h in domain_h}
        platform_h = [h for h in all_platform if h.trigger not in domain_triggers]

    # Tier 3: ExperienceMemory (GRPO)
    experience_patterns = []
    try:
        from shared.experiential_learning import get_shared_experience_memory
        em = get_shared_experience_memory()
        experiences = em.retrieve("job_application", k=3)
        experience_patterns = [
            {"pattern": e.successful_pattern, "score": e.score}
            for e in experiences
        ]
    except Exception:
        pass

    # Build prompt-ready context
    lines = []
    if domain_h:
        lines.append("## Domain-Specific Heuristics (from past applications to this site)")
        for h in domain_h[:5]:
            lines.append(f"- When: {h.trigger} → Do: {h.action} (confidence: {h.confidence:.0%})")
    if platform_h:
        lines.append(f"\n## Platform Heuristics ({platform})")
        for h in platform_h[:5]:
            lines.append(f"- When: {h.trigger} → Do: {h.action} (confidence: {h.confidence:.0%})")
    if experience_patterns:
        lines.append("\n## Past Successful Strategies")
        for ep in experience_patterns[:3]:
            lines.append(f"- {ep['pattern'][:200]}")

    return {
        "domain_heuristics": domain_h,
        "platform_heuristics": platform_h,
        "experience_patterns": experience_patterns,
        "prompt_context": "\n".join(lines) if lines else "",
    }
