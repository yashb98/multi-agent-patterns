"""Tests for WAL mode enforcement."""

import sqlite3


def test_get_db_conn_uses_wal(tmp_path):
    """Every connection from get_db_conn must be in WAL mode."""
    from shared.db import get_db_conn

    db_path = tmp_path / "test.db"
    conn = get_db_conn(str(db_path))
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    conn.close()
    assert mode == "wal"


def test_wal_survives_reconnect(tmp_path):
    """WAL should persist across connections."""
    from shared.db import get_db_conn

    db_path = tmp_path / "test2.db"
    conn1 = get_db_conn(str(db_path))
    conn1.execute("CREATE TABLE t (id INTEGER)")
    conn1.close()

    conn2 = get_db_conn(str(db_path))
    mode = conn2.execute("PRAGMA journal_mode").fetchone()[0]
    conn2.close()
    assert mode == "wal"
