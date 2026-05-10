"""Per-decision audit log for semantic-analysis decisions.

Companion to ``shared/db_observability.py`` which wraps DB lookups.
``semantic_decisions`` wraps the *semantic* decisions on top of those
lookups: which mechanism fired (embedding / LLM / semantic_matcher /
hardcoded / learned), which tier in the cascade produced the answer,
what the input was, what the output was, what context the decision
ran under (profile_state_hash + jd_context_hash so SG1 / SG3 can be
audited cross-context).

Pre-S3 every PASS in the semantic-analysis audit depended on log
mining — searching ``logs/live_e2e/run_final_*.log`` for specific
phrases like ``"screening answer 'No' did not align to any option"``
or ``"screening_cache: hit"``. That's slow, error-prone (logs rotate),
and gives no replay capability. Dimension H1 in
``.claude/skills/audit-semantic-analysis/dimensions.md`` requires a
per-decision audit log that survives log rotation and supports replay.

Architecture
------------
1. ``record_decision(call_site, decision_type, mechanism, tier_reached,
   input_repr, output_repr, confidence, ...)`` writes one row.
2. Same test-mode short-circuit as db_observability: ``JOBPULSE_TEST_MODE=1``
   or ``set_test_mode(False)`` for tests that need the writer.
3. ``query_decisions(...)`` is the read API for auditors / live-evidence
   scripts. Returns dataclass rows.
4. Schema is additive: new columns added via ``ALTER TABLE`` in
   ``_ensure_schema`` so existing rows survive upgrades.

Invariants
----------
- Caller NEVER blocks on this — SQLite writes are best-effort. Failure
  logs to debug and continues; audit logging must never break the apply
  pipeline.
- Inputs / outputs are length-bounded (``_REPR_LIMIT`` = 300 chars) and
  passed through ``repr`` so a string ``"No"`` lands as ``"'No'"`` in
  the row — preserves the distinction between empty-string and None.
- PII redaction: callers are responsible for not passing full profile
  text. ``input_repr`` should already be redacted/truncated by the
  caller; this module makes no PII guarantees beyond the length cap.

Closes audit dimension H1 (per-decision audit log) for the call sites
that wire through ``record_decision``. Audit-slice S3.
"""

from __future__ import annotations

import atexit
import hashlib
import json
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from shared.logging_config import get_logger

logger = get_logger(__name__)

_PROJECT_DIR = Path(__file__).resolve().parent.parent
_DEFAULT_DB_PATH = _PROJECT_DIR / "data" / "semantic_decisions.db"

_REPR_LIMIT = 300

# Closed enums — extend in-place, don't introduce free-form strings.
DECISION_TYPES = frozenset({
    "llm_call",
    "option_align",
    "intent_classify",
    "semantic_match",
    "page_reasoning",
    "screening_outcome",
})

MECHANISMS = frozenset({
    "embedding",
    "llm",
    "semantic_matcher",
    "regex",
    "hardcoded",
    "learned",
    "cache_hit",
    "structural",  # DOM/a11y/format checks
})


_db_path: Path = _DEFAULT_DB_PATH
_db_lock = threading.Lock()
_test_mode_override: bool | None = None
_schema_initialised = False


@dataclass
class Decision:
    """One row in semantic_decisions.db."""

    decision_id: int
    ts: float
    agent_name: str
    call_site: str
    decision_type: str
    mechanism: str
    tier_reached: str
    input_repr: str
    input_hash: str
    output_repr: str | None
    confidence: float | None
    profile_state_hash: str | None
    jd_context_hash: str | None
    field_label: str | None
    elapsed_ms: float | None
    trajectory_id: str | None


# ── Configuration ────────────────────────────────────────────────────


def set_decisions_db_path(path: str | Path) -> None:
    """Override the SQLite destination (used by tests)."""

    global _db_path, _schema_initialised
    _db_path = Path(path)
    _db_path.parent.mkdir(parents=True, exist_ok=True)
    _schema_initialised = False
    _ensure_schema()


def set_test_mode(enabled: bool | None) -> None:
    """Programmatically toggle test-mode short-circuit (None = read env)."""

    global _test_mode_override
    _test_mode_override = enabled


def _is_test_mode() -> bool:
    if _test_mode_override is not None:
        return _test_mode_override
    return os.environ.get("JOBPULSE_TEST_MODE") == "1"


# ── SQLite writer ────────────────────────────────────────────────────


def _ensure_schema() -> None:
    global _schema_initialised
    if _is_test_mode():
        return
    if _schema_initialised:
        return
    with _db_lock:
        if _schema_initialised:
            return
        _db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(_db_path))
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS decisions (
                    decision_id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts                 REAL NOT NULL,
                    agent_name         TEXT NOT NULL,
                    call_site          TEXT NOT NULL,
                    decision_type      TEXT NOT NULL,
                    mechanism          TEXT NOT NULL,
                    tier_reached       TEXT NOT NULL,
                    input_repr         TEXT,
                    input_hash         TEXT,
                    output_repr        TEXT,
                    confidence         REAL,
                    profile_state_hash TEXT,
                    jd_context_hash    TEXT,
                    field_label        TEXT,
                    elapsed_ms         REAL,
                    trajectory_id      TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_decisions_ts
                    ON decisions(ts);
                CREATE INDEX IF NOT EXISTS idx_decisions_agent_site
                    ON decisions(agent_name, call_site);
                CREATE INDEX IF NOT EXISTS idx_decisions_decision_type
                    ON decisions(decision_type);
                CREATE INDEX IF NOT EXISTS idx_decisions_input_hash
                    ON decisions(input_hash);
                CREATE INDEX IF NOT EXISTS idx_decisions_profile_jd
                    ON decisions(profile_state_hash, jd_context_hash);
                """
            )
            conn.commit()
        finally:
            conn.close()
        _schema_initialised = True


def _truncate_repr(value: Any) -> str:
    try:
        s = repr(value)
    except Exception:
        s = f"<{type(value).__name__}>"
    if len(s) > _REPR_LIMIT:
        s = s[: _REPR_LIMIT - 3] + "..."
    return s


def _hash_input(value: Any) -> str:
    try:
        if isinstance(value, (str, bytes)):
            payload = value.encode() if isinstance(value, str) else value
        else:
            payload = json.dumps(value, sort_keys=True, default=str).encode()
    except Exception:
        payload = repr(value).encode()
    return hashlib.sha256(payload).hexdigest()[:16]


# ── Public API ───────────────────────────────────────────────────────


def record_decision(
    *,
    agent_name: str,
    call_site: str,
    decision_type: str,
    mechanism: str,
    tier_reached: str,
    input_value: Any,
    output_value: Any = None,
    confidence: float | None = None,
    profile_state_hash: str | None = None,
    jd_context_hash: str | None = None,
    field_label: str | None = None,
    elapsed_ms: float | None = None,
    trajectory_id: str | None = None,
) -> int:
    """Record one semantic decision. Returns the row id, or -1 on
    failure / test-mode skip.

    Validates ``decision_type`` and ``mechanism`` against the closed
    enums above. Unknown values log a warning and are stored as-is —
    callers should add new values to the enum rather than silently
    introducing new vocabulary.
    """

    if decision_type not in DECISION_TYPES:
        logger.warning(
            "semantic_decisions: unknown decision_type %r; expected one of %s",
            decision_type, sorted(DECISION_TYPES),
        )
    if mechanism not in MECHANISMS:
        logger.warning(
            "semantic_decisions: unknown mechanism %r; expected one of %s",
            mechanism, sorted(MECHANISMS),
        )

    if _is_test_mode():
        return -1

    try:
        _ensure_schema()
        with _db_lock:
            conn = sqlite3.connect(str(_db_path))
            try:
                cur = conn.execute(
                    """
                    INSERT INTO decisions (
                        ts, agent_name, call_site, decision_type, mechanism,
                        tier_reached, input_repr, input_hash, output_repr,
                        confidence, profile_state_hash, jd_context_hash,
                        field_label, elapsed_ms, trajectory_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        time.time(),
                        agent_name,
                        call_site,
                        decision_type,
                        mechanism,
                        tier_reached,
                        _truncate_repr(input_value),
                        _hash_input(input_value),
                        _truncate_repr(output_value) if output_value is not None else None,
                        confidence,
                        profile_state_hash,
                        jd_context_hash,
                        field_label,
                        elapsed_ms,
                        trajectory_id,
                    ),
                )
                conn.commit()
                return int(cur.lastrowid or 0)
            finally:
                conn.close()
    except Exception as exc:
        logger.debug("semantic_decisions: write failed: %s", exc)
        return -1


def query_decisions(
    *,
    agent_name: str | None = None,
    call_site: str | None = None,
    decision_type: str | None = None,
    input_hash: str | None = None,
    profile_state_hash: str | None = None,
    jd_context_hash: str | None = None,
    field_label: str | None = None,
    since_ts: float | None = None,
    limit: int = 100,
) -> list[Decision]:
    """Read decisions for the audit. All filters are optional and ANDed.

    Used by ``scripts/audit_*_live_evidence.py`` to replace log mining.
    """

    if _is_test_mode():
        return []
    try:
        _ensure_schema()
    except Exception as exc:
        logger.debug("semantic_decisions: schema init failed in query: %s", exc)
        return []

    clauses = []
    params: list[Any] = []
    for field_, value in [
        ("agent_name", agent_name),
        ("call_site", call_site),
        ("decision_type", decision_type),
        ("input_hash", input_hash),
        ("profile_state_hash", profile_state_hash),
        ("jd_context_hash", jd_context_hash),
        ("field_label", field_label),
    ]:
        if value is not None:
            clauses.append(f"{field_} = ?")
            params.append(value)
    if since_ts is not None:
        clauses.append("ts >= ?")
        params.append(since_ts)

    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = (
        "SELECT decision_id, ts, agent_name, call_site, decision_type, "
        "mechanism, tier_reached, input_repr, input_hash, output_repr, "
        "confidence, profile_state_hash, jd_context_hash, field_label, "
        "elapsed_ms, trajectory_id "
        f"FROM decisions{where} ORDER BY ts DESC LIMIT ?"
    )
    params.append(limit)

    try:
        with _db_lock:
            conn = sqlite3.connect(str(_db_path))
            try:
                rows = conn.execute(sql, params).fetchall()
            finally:
                conn.close()
    except Exception as exc:
        logger.debug("semantic_decisions: query failed: %s", exc)
        return []

    return [Decision(*row) for row in rows]


def _atexit_log() -> None:
    """No buffer to flush (writes are synchronous) — but log one line
    on exit so daemons leave a footprint of how many decisions were
    logged this run. Test-mode skips so unit tests don't pollute."""

    if _is_test_mode():
        return
    try:
        _ensure_schema()
        with _db_lock:
            conn = sqlite3.connect(str(_db_path))
            try:
                row = conn.execute(
                    "SELECT COUNT(*) FROM decisions WHERE ts >= ?",
                    (time.time() - 3600.0,),
                ).fetchone()
                if row and row[0]:
                    logger.debug(
                        "semantic_decisions: %d decisions logged in last hour",
                        row[0],
                    )
            finally:
                conn.close()
    except Exception:
        pass


atexit.register(_atexit_log)
