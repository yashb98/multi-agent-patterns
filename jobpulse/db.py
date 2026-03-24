"""SQLite database for email tracking and state management."""

import sqlite3
from datetime import datetime
from jobpulse.config import DB_PATH


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    """Create tables if they don't exist."""
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS processed_emails (
            email_id TEXT PRIMARY KEY,
            sender TEXT NOT NULL,
            subject TEXT NOT NULL,
            category TEXT NOT NULL,
            snippet TEXT DEFAULT '',
            received_at TEXT NOT NULL,
            processed_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS gmail_check_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            last_check_ts TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_emails_category ON processed_emails(category);
        CREATE INDEX IF NOT EXISTS idx_emails_received ON processed_emails(received_at);
    """)
    # Seed last_check if empty
    row = conn.execute("SELECT last_check_ts FROM gmail_check_state WHERE id=1").fetchone()
    if not row:
        yesterday = datetime.now().replace(hour=0, minute=0, second=0).isoformat()
        conn.execute("INSERT INTO gmail_check_state (id, last_check_ts) VALUES (1, ?)", (yesterday,))
    conn.commit()
    conn.close()


def is_email_processed(email_id: str) -> bool:
    conn = get_conn()
    row = conn.execute("SELECT 1 FROM processed_emails WHERE email_id=?", (email_id,)).fetchone()
    conn.close()
    return row is not None


def store_email(email_id: str, sender: str, subject: str, category: str,
                snippet: str, received_at: str):
    conn = get_conn()
    conn.execute(
        "INSERT OR IGNORE INTO processed_emails (email_id, sender, subject, category, snippet, received_at, processed_at) VALUES (?,?,?,?,?,?,?)",
        (email_id, sender, subject, category, snippet, received_at, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()


def get_emails_since(since_date: str, categories: list[str] = None) -> list[dict]:
    conn = get_conn()
    if categories:
        placeholders = ",".join("?" for _ in categories)
        rows = conn.execute(
            f"SELECT * FROM processed_emails WHERE received_at >= ? AND category IN ({placeholders}) ORDER BY received_at DESC",
            [since_date] + categories
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM processed_emails WHERE received_at >= ? ORDER BY received_at DESC",
            (since_date,)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_last_check_ts() -> str:
    conn = get_conn()
    row = conn.execute("SELECT last_check_ts FROM gmail_check_state WHERE id=1").fetchone()
    conn.close()
    return row["last_check_ts"] if row else datetime.now().isoformat()


def update_last_check_ts(ts: str):
    conn = get_conn()
    conn.execute("UPDATE gmail_check_state SET last_check_ts=? WHERE id=1", (ts,))
    conn.commit()
    conn.close()


# Initialize on import
init_db()
