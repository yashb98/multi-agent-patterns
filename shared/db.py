"""Shared SQLite connection utility.

Replaces the 10 identical ``_get_conn()`` copies scattered across modules
with a single reusable helper that ensures consistent WAL mode, Row factory,
and parent-directory creation.

Two flavors:

- :func:`get_db_conn` opens a fresh connection every call. Use for scripts,
  one-off migrations, or anywhere the caller explicitly wants to ``close()``.
- :func:`get_pooled_db_conn` returns a per-thread reusable connection keyed
  by db path. Use on the hot path (dispatchers, daemons) to avoid the
  ``sqlite3.connect`` + ``PRAGMA journal_mode=WAL`` round trip on every
  query. The connection stays open for the life of the thread and is closed
  via ``atexit``; callers must NOT call ``.close()`` on it.
"""

import atexit
import sqlite3
import threading
from pathlib import Path


def get_db_conn(db_path: Path, *, wal: bool = True, mkdir: bool = True) -> sqlite3.Connection:
    """Open a SQLite connection with project-standard settings.

    Parameters
    ----------
    db_path : Path
        Absolute or relative path to the ``.db`` file.
    wal : bool
        Enable WAL journal mode (default True).  Disable for read-only
        or ephemeral connections where WAL is unnecessary.
    mkdir : bool
        Create parent directories if they don't exist (default True).
    """
    if mkdir:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    if wal:
        conn.execute("PRAGMA journal_mode=WAL")
    return conn


# ─── POOLED (per-thread) CONNECTIONS ───────────────────────────
# SQLite ``Connection`` objects are not safe to share across threads without
# ``check_same_thread=False`` and a write-serializing lock. Keeping one
# connection PER THREAD PER DB gives us safe reuse without locking.

_thread_local = threading.local()
# All pooled connections ever created, so atexit can close them on shutdown.
_all_pooled_conns: list[sqlite3.Connection] = []
_all_pooled_lock = threading.Lock()


def get_pooled_db_conn(
    db_path: Path, *, wal: bool = True, mkdir: bool = True,
) -> sqlite3.Connection:
    """Return a thread-local reusable connection to ``db_path``.

    The connection is opened lazily on first call per (thread, path), kept
    alive for the life of the thread, and closed on interpreter shutdown.
    Callers must NOT call ``.close()`` on it — doing so will break the next
    query from that thread. Use a fresh :func:`get_db_conn` instead when
    explicit lifecycle control is required.
    """
    cache: dict[str, sqlite3.Connection] = getattr(_thread_local, "conns", None)
    if cache is None:
        cache = {}
        _thread_local.conns = cache

    key = str(Path(db_path).resolve())
    conn = cache.get(key)
    if conn is not None:
        return conn

    if mkdir:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    if wal:
        conn.execute("PRAGMA journal_mode=WAL")

    cache[key] = conn
    with _all_pooled_lock:
        _all_pooled_conns.append(conn)
    return conn


@atexit.register
def _close_all_pooled() -> None:
    with _all_pooled_lock:
        conns = list(_all_pooled_conns)
        _all_pooled_conns.clear()
    for c in conns:
        try:
            c.close()
        except Exception:
            pass
