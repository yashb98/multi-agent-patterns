"""Self-healing utilities for database health and memory desync detection.

Provides:
- SQLite integrity_check runner
- DB corruption detection with fallback initialization
- Memory layer sync health verification
- Scheduled maintenance hooks
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Optional

from shared.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class DBHealthReport:
    db_path: str
    healthy: bool
    integrity_ok: bool
    size_bytes: int
    last_check: str
    errors: list[str]


def check_sqlite_integrity(db_path: str | Path) -> DBHealthReport:
    """Run PRAGMA integrity_check on a SQLite database.

    Returns a DBHealthReport with details. If the DB is corrupted,
    the report will contain the error messages.
    """
    db_path = Path(db_path)
    errors: list[str] = []
    integrity_ok = False
    size_bytes = 0

    try:
        size_bytes = db_path.stat().st_size
    except OSError as exc:
        errors.append(f"Cannot stat DB: {exc}")

    try:
        with sqlite3.connect(str(db_path), timeout=5.0) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            result = conn.execute("PRAGMA integrity_check").fetchone()
            if result and result[0] == "ok":
                integrity_ok = True
            else:
                errors.append(f"Integrity check failed: {result[0] if result else 'no result'}")
    except sqlite3.DatabaseError as exc:
        errors.append(f"DatabaseError: {exc}")
    except Exception as exc:
        errors.append(f"Unexpected error: {exc}")

    return DBHealthReport(
        db_path=str(db_path),
        healthy=integrity_ok and not errors,
        integrity_ok=integrity_ok,
        size_bytes=size_bytes,
        last_check=datetime.now(UTC).isoformat(),
        errors=errors,
    )


def heal_db_if_needed(db_path: str | Path, fallback_schema: str = "") -> DBHealthReport:
    """Check a DB and attempt recovery if corrupted.

    If the DB is corrupted and a fallback_schema is provided, the existing
    DB is backed up and a fresh one is initialised with the schema.
    """
    report = check_sqlite_integrity(db_path)
    if report.healthy:
        return report

    logger.warning("DB %s is unhealthy: %s", db_path, report.errors)

    if fallback_schema:
        db_path = Path(db_path)
        backup = db_path.with_suffix(f".db.bak.{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}")
        try:
            if db_path.exists():
                db_path.rename(backup)
                logger.info("Backed up corrupted DB to %s", backup)
        except OSError as exc:
            logger.error("Failed to backup corrupted DB: %s", exc)

        try:
            with sqlite3.connect(str(db_path)) as conn:
                conn.executescript(fallback_schema)
            logger.info("Reinitialised DB at %s with fallback schema", db_path)
            report = check_sqlite_integrity(db_path)
        except Exception as exc:
            report.errors.append(f"Reinitialisation failed: {exc}")

    return report


def check_memory_sync_health(sqlite_path: str, qdrant_store=None) -> dict:
    """Verify that SQLite and Qdrant are roughly in sync.

    Returns a dict with counts and a desync flag.
    """
    result = {
        "sqlite_count": 0,
        "qdrant_count": 0,
        "desync": False,
        "desync_ratio": 0.0,
        "errors": [],
    }

    try:
        with sqlite3.connect(str(sqlite_path)) as conn:
            row = conn.execute("SELECT COUNT(*) FROM memories WHERE is_tombstoned = 0").fetchone()
            result["sqlite_count"] = row[0] if row else 0
    except Exception as exc:
        result["errors"].append(f"SQLite count failed: {exc}")

    if qdrant_store is not None:
        try:
            from shared.memory_layer._entries import MemoryTier
            total = 0
            for tier in (MemoryTier.EPISODIC, MemoryTier.SEMANTIC, MemoryTier.PROCEDURAL, MemoryTier.EXPERIENCE):
                total += qdrant_store.count(tier)
            result["qdrant_count"] = total
        except Exception as exc:
            result["errors"].append(f"Qdrant count failed: {exc}")

    if qdrant_store is not None and result["sqlite_count"] > 0:
        result["desync_ratio"] = abs(result["sqlite_count"] - result["qdrant_count"]) / result["sqlite_count"]
        if result["desync_ratio"] > 0.2:
            result["desync"] = True

    return result


def run_maintenance(
    db_paths: list[str],
    qdrant_store=None,
    sqlite_memory_path: Optional[str] = None,
) -> dict:
    """Run a full maintenance sweep: integrity checks + sync health.

    Returns a summary dict suitable for logging or dashboards.
    """
    reports = []
    for path in db_paths:
        report = check_sqlite_integrity(path)
        reports.append({
            "path": report.db_path,
            "healthy": report.healthy,
            "size_mb": round(report.size_bytes / (1024 * 1024), 2),
            "errors": report.errors,
        })

    sync_health = {}
    if sqlite_memory_path:
        sync_health = check_memory_sync_health(sqlite_memory_path, qdrant_store)

    all_healthy = all(r["healthy"] for r in reports) and not sync_health.get("desync", False)

    summary = {
        "timestamp": datetime.now(UTC).isoformat(),
        "all_healthy": all_healthy,
        "dbs_checked": len(reports),
        "reports": reports,
        "sync_health": sync_health,
    }

    logger.info("Self-healing maintenance: %s", json.dumps(summary))
    return summary
