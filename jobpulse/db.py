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

        CREATE TABLE IF NOT EXISTS preclassifier_audits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email_id TEXT NOT NULL,
            rule_category TEXT,
            rule_confidence REAL,
            rule_name TEXT,
            llm_category TEXT,
            user_category TEXT,
            is_correct INTEGER,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (email_id) REFERENCES processed_emails(email_id)
        );

        CREATE TABLE IF NOT EXISTS preclassifier_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            total_processed INTEGER DEFAULT 0,
            total_correct INTEGER DEFAULT 0,
            total_audited INTEGER DEFAULT 0,
            learning_phase INTEGER DEFAULT 1,
            graduated INTEGER DEFAULT 0,
            updated_at TEXT DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_audit_email ON preclassifier_audits(email_id);
        CREATE INDEX IF NOT EXISTS idx_audit_correct ON preclassifier_audits(is_correct);
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


def store_audit(email_id: str, rule_category: str, rule_confidence: float,
                rule_name: str, llm_category: str = None, user_category: str = None,
                is_correct: int = None):
    conn = get_conn()
    conn.execute(
        "INSERT INTO preclassifier_audits (email_id, rule_category, rule_confidence, rule_name, llm_category, user_category, is_correct) VALUES (?,?,?,?,?,?,?)",
        (email_id, rule_category, rule_confidence, rule_name, llm_category, user_category, is_correct)
    )
    conn.commit()
    conn.close()


def get_preclassifier_state() -> dict:
    conn = get_conn()
    row = conn.execute("SELECT * FROM preclassifier_state WHERE id=1").fetchone()
    if not row:
        conn.execute("INSERT INTO preclassifier_state (id, total_processed, total_correct, total_audited, learning_phase, graduated) VALUES (1, 0, 0, 0, 1, 0)")
        conn.commit()
        row = conn.execute("SELECT * FROM preclassifier_state WHERE id=1").fetchone()
    conn.close()
    return dict(row)


def update_preclassifier_state(**kwargs):
    conn = get_conn()
    sets = ", ".join(f"{k}=?" for k in kwargs)
    vals = list(kwargs.values())
    conn.execute(f"UPDATE preclassifier_state SET {sets}, updated_at=datetime('now') WHERE id=1", vals)
    conn.commit()
    conn.close()


def get_audit_accuracy(limit: int = 100) -> float:
    """Return accuracy of last N audited pre-classifications."""
    conn = get_conn()
    row = conn.execute(
        "SELECT COUNT(*) as total, SUM(CASE WHEN is_correct=1 THEN 1 ELSE 0 END) as correct FROM (SELECT is_correct FROM preclassifier_audits WHERE is_correct IS NOT NULL ORDER BY created_at DESC LIMIT ?)",
        (limit,)
    ).fetchone()
    conn.close()
    if not row or row["total"] == 0:
        return 0.0
    return row["correct"] / row["total"]


# Initialize on import
init_db()
