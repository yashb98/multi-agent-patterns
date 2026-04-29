"""Tests for shared/self_healing.py database health utilities."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from shared.self_healing import (
    check_sqlite_integrity,
    heal_db_if_needed,
    check_memory_sync_health,
    run_maintenance,
)


def test_check_integrity_on_healthy_db(tmp_path: Path) -> None:
    db_path = tmp_path / "healthy.db"
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO t VALUES (1)")

    report = check_sqlite_integrity(db_path)
    assert report.healthy is True
    assert report.integrity_ok is True
    assert report.errors == []


def test_check_integrity_on_corrupted_db(tmp_path: Path) -> None:
    db_path = tmp_path / "corrupt.db"
    db_path.write_bytes(b"not a sqlite file")

    report = check_sqlite_integrity(db_path)
    assert report.healthy is False
    assert report.integrity_ok is False
    assert len(report.errors) > 0


def test_heal_db_reinitialises_with_schema(tmp_path: Path) -> None:
    db_path = tmp_path / "heal.db"
    db_path.write_bytes(b"corrupt data")

    schema = "CREATE TABLE recovered (id INTEGER PRIMARY KEY);"
    report = heal_db_if_needed(db_path, fallback_schema=schema)
    assert report.healthy is True

    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        assert any(r[0] == "recovered" for r in rows)


def test_heal_db_without_schema_leaves_corrupt(tmp_path: Path) -> None:
    db_path = tmp_path / "still_bad.db"
    db_path.write_bytes(b"corrupt data")

    report = heal_db_if_needed(db_path)
    assert report.healthy is False


def test_check_memory_sync_health(tmp_path: Path) -> None:
    db_path = tmp_path / "mem.db"
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("""
            CREATE TABLE memories (
                memory_id TEXT PRIMARY KEY,
                is_tombstoned INTEGER DEFAULT 0
            )
        """)
        conn.execute("INSERT INTO memories VALUES ('a', 0)")
        conn.execute("INSERT INTO memories VALUES ('b', 0)")
        conn.execute("INSERT INTO memories VALUES ('c', 1)")

    health = check_memory_sync_health(db_path, qdrant_store=None)
    assert health["sqlite_count"] == 2
    assert health["qdrant_count"] == 0
    assert health["desync"] is False  # qdrant is None


def test_run_maintenance(tmp_path: Path) -> None:
    db_path = tmp_path / "maint.db"
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("CREATE TABLE x (id INTEGER PRIMARY KEY)")

    summary = run_maintenance([str(db_path)])
    assert summary["all_healthy"] is True
    assert summary["dbs_checked"] == 1
    assert len(summary["reports"]) == 1
