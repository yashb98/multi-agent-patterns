"""Shared SQLite connection utility.

Replaces the 10 identical ``_get_conn()`` copies scattered across modules
with a single reusable helper that ensures consistent WAL mode, Row factory,
and parent-directory creation.
"""

import sqlite3
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
