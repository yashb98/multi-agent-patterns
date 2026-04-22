"""Tests for :func:`shared.db.get_pooled_db_conn`.

Phase 0, item 4. The swarm dispatcher used to reopen SQLite + re-run
``PRAGMA journal_mode=WAL`` on every query path, costing 30-120 ms per
dispatch. The pool returns a per-thread connection the first time it's
opened and reuses it thereafter — these tests pin down:

- same-thread repeat calls return the same object
- different paths → different connections
- distinct threads get distinct connections (SQLite isn't thread-safe)
- the pooled connection is usable (basic round trip)
- closing the pooled connection from outside breaks subsequent use —
  documented as "do not close"
"""

from __future__ import annotations

import sqlite3
import threading

import pytest

from shared.db import get_pooled_db_conn, get_db_conn


@pytest.fixture
def tmp_db(tmp_path):
    return tmp_path / "pool.db"


def test_same_thread_same_path_returns_same_connection(tmp_db):
    c1 = get_pooled_db_conn(tmp_db)
    c2 = get_pooled_db_conn(tmp_db)
    assert c1 is c2


def test_different_paths_return_different_connections(tmp_path):
    db_a = tmp_path / "a.db"
    db_b = tmp_path / "b.db"
    c1 = get_pooled_db_conn(db_a)
    c2 = get_pooled_db_conn(db_b)
    assert c1 is not c2


def test_different_threads_get_different_connections(tmp_db):
    """SQLite connections aren't thread-safe — the pool must NOT share
    a single connection across threads."""
    main_conn = get_pooled_db_conn(tmp_db)
    other_conn: list[sqlite3.Connection] = []

    def _in_thread():
        other_conn.append(get_pooled_db_conn(tmp_db))

    t = threading.Thread(target=_in_thread)
    t.start()
    t.join()

    assert len(other_conn) == 1
    assert other_conn[0] is not main_conn


def test_pooled_connection_round_trips(tmp_db):
    conn = get_pooled_db_conn(tmp_db)
    conn.execute("CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT)")
    conn.execute("INSERT OR REPLACE INTO kv VALUES (?, ?)", ("foo", "bar"))
    conn.commit()

    row = get_pooled_db_conn(tmp_db).execute(
        "SELECT v FROM kv WHERE k = ?", ("foo",),
    ).fetchone()
    assert row["v"] == "bar"


def test_pool_and_fresh_conn_are_independent(tmp_db):
    """A ``get_db_conn`` caller can freely ``close()`` its connection without
    invalidating the pool's connection to the same DB."""
    pooled = get_pooled_db_conn(tmp_db)
    pooled.execute("CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT)")
    pooled.commit()

    fresh = get_db_conn(tmp_db)
    fresh.close()

    # Pool still usable after the fresh conn closed.
    row = pooled.execute("SELECT COUNT(*) AS n FROM kv").fetchone()
    assert row["n"] == 0


def test_pooled_conn_wal_mode_enabled(tmp_db):
    conn = get_pooled_db_conn(tmp_db)
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0].lower()
    assert mode == "wal"
