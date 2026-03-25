"""Simulation Event Logger — captures EVERY agent action for the knowledge graph.

Every agent action in the system logs here. This creates a complete daily
timeline of everything JobPulse does, which feeds into:
  1. The MindGraph knowledge extraction pipeline
  2. The D3 timeline bar (day-by-day browsing)
  3. The morning briefing (temporal search for yesterday)
  4. GraphRAG retrieval (agents query past events for context)
"""

import json
import uuid
import sqlite3
from datetime import datetime, date
from pathlib import Path
from jobpulse.config import DATA_DIR
from shared.logging_config import get_logger

logger = get_logger(__name__)

# Use the same mindgraph.db so knowledge graph and events live together
DB_PATH = DATA_DIR / "mindgraph.db"


def _get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_events_db():
    """Create simulation_events table if it doesn't exist."""
    conn = _get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS simulation_events (
            id TEXT PRIMARY KEY,
            event_type TEXT NOT NULL,
            agent_name TEXT DEFAULT '',
            target_agent_name TEXT DEFAULT '',
            action TEXT DEFAULT '',
            content TEXT DEFAULT '',
            metadata TEXT DEFAULT '{}',
            day_date TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_sim_events_day ON simulation_events(day_date);
        CREATE INDEX IF NOT EXISTS idx_sim_events_type ON simulation_events(event_type);
        CREATE INDEX IF NOT EXISTS idx_sim_events_agent ON simulation_events(agent_name);
    """)
    conn.commit()
    conn.close()


def log_event(
    event_type: str,
    action: str = "",
    content: str = "",
    agent_name: str = "",
    target_agent_name: str = "",
    metadata: dict = None,
) -> str:
    """Log a simulation event. Returns the event ID.

    event_type: one of:
        agent_action, agent_communication, email_classified,
        calendar_event, github_activity, notion_task,
        research_paper, knowledge_extracted, briefing_sent,
        budget_transaction, task_created, task_completed, error
    """
    event_id = uuid.uuid4().hex[:16]
    now = datetime.now()

    conn = _get_conn()
    conn.execute(
        "INSERT INTO simulation_events (id, event_type, agent_name, target_agent_name, action, content, metadata, day_date, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (
            event_id,
            event_type,
            agent_name,
            target_agent_name,
            action,
            content[:2000],  # cap content length
            json.dumps(metadata or {}),
            now.strftime("%Y-%m-%d"),
            now.isoformat(),
        ),
    )
    conn.commit()
    conn.close()
    return event_id


def get_events_for_day(day: str) -> list[dict]:
    """Get all events for a specific day."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM simulation_events WHERE day_date=? ORDER BY created_at ASC",
        (day,),
    ).fetchall()
    conn.close()
    return [_row_to_dict(r) for r in rows]


def get_events_for_agent(agent_name: str, limit: int = 50) -> list[dict]:
    """Get recent events for a specific agent."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM simulation_events WHERE agent_name=? ORDER BY created_at DESC LIMIT ?",
        (agent_name, limit),
    ).fetchall()
    conn.close()
    return [_row_to_dict(r) for r in rows]


def get_events_mentioning(entity_name: str, limit: int = 20) -> list[dict]:
    """Get events that mention a specific entity in content or metadata."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM simulation_events WHERE content LIKE ? OR metadata LIKE ? ORDER BY created_at DESC LIMIT ?",
        (f"%{entity_name}%", f"%{entity_name}%", limit),
    ).fetchall()
    conn.close()
    return [_row_to_dict(r) for r in rows]


def get_timeline_summary() -> list[dict]:
    """Get day-by-day summary for the timeline bar."""
    conn = _get_conn()
    rows = conn.execute(
        """SELECT day_date, COUNT(*) as event_count,
           SUM(CASE WHEN event_type='email_classified' AND metadata LIKE '%SELECTED%' THEN 1 ELSE 0 END) as positive,
           SUM(CASE WHEN event_type='email_classified' AND metadata LIKE '%REJECTED%' THEN 1 ELSE 0 END) as negative,
           GROUP_CONCAT(DISTINCT event_type) as event_types
           FROM simulation_events
           GROUP BY day_date
           ORDER BY day_date DESC
           LIMIT 30""",
    ).fetchall()
    conn.close()
    return [_row_to_dict(r) for r in rows]


def get_event_stats() -> dict:
    """Get overall event statistics."""
    conn = _get_conn()
    total = conn.execute("SELECT COUNT(*) FROM simulation_events").fetchone()[0]
    by_type = conn.execute(
        "SELECT event_type, COUNT(*) as count FROM simulation_events GROUP BY event_type ORDER BY count DESC"
    ).fetchall()
    today_count = conn.execute(
        "SELECT COUNT(*) FROM simulation_events WHERE day_date=?",
        (date.today().isoformat(),),
    ).fetchone()[0]
    conn.close()
    return {
        "total_events": total,
        "today_events": today_count,
        "by_type": {r["event_type"]: r["count"] for r in by_type},
    }


def cleanup_old_events(retention_days: int = 90):
    """Delete simulation events older than retention_days. Prevents unbounded DB growth."""
    from datetime import timedelta
    cutoff = (datetime.now() - timedelta(days=retention_days)).strftime("%Y-%m-%d")
    conn = _get_conn()
    cursor = conn.execute("DELETE FROM simulation_events WHERE day_date < ?", (cutoff,))
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    if deleted > 0:
        logger.info("Cleaned up %d events older than %d days", deleted, retention_days)
    return deleted


def _row_to_dict(row) -> dict:
    d = dict(row)
    if "metadata" in d and isinstance(d["metadata"], str):
        try:
            d["metadata"] = json.loads(d["metadata"])
        except (json.JSONDecodeError, TypeError):
            pass
    return d


# Initialize on import — create table + prune old data
init_events_db()
cleanup_old_events(90)
