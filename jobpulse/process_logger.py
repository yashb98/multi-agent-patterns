"""Process Trail Logger — captures the full step-by-step process of every agent run.

Unlike event_logger (which captures WHAT happened), this captures HOW it happened:
every API call, LLM classification, decision branch, and entity extraction.

Each agent run gets a unique run_id. Steps are numbered sequentially within a run.
The frontend renders these as expandable timelines showing the full audit trail.
"""

import uuid
import time
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from jobpulse.config import DATA_DIR

DB_PATH = DATA_DIR / "mindgraph.db"


def _get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_process_db():
    """Create agent_process_trails table if it doesn't exist."""
    conn = _get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS agent_process_trails (
            id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            agent_name TEXT NOT NULL,
            task_trigger TEXT NOT NULL,
            step_number INTEGER NOT NULL,
            step_type TEXT NOT NULL,
            step_name TEXT NOT NULL,
            step_input TEXT,
            step_output TEXT,
            step_decision TEXT,
            duration_ms INTEGER,
            metadata TEXT DEFAULT '{}',
            status TEXT DEFAULT 'success',
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_trails_run ON agent_process_trails(run_id);
        CREATE INDEX IF NOT EXISTS idx_trails_agent ON agent_process_trails(agent_name);
        CREATE INDEX IF NOT EXISTS idx_trails_date ON agent_process_trails(created_at);
    """)
    conn.commit()
    conn.close()


class ProcessTrail:
    """Captures the full step-by-step process of one agent run."""

    def __init__(self, agent_name: str, task_trigger: str):
        self.run_id = uuid.uuid4().hex[:16]
        self.agent_name = agent_name
        self.task_trigger = task_trigger
        self.step_counter = 0
        self.start_time = time.time()

    def log_step(self, step_type: str, step_name: str,
                 step_input: str = None, step_output: str = None,
                 step_decision: str = None, metadata: dict = None,
                 status: str = "success", duration_ms: int = None):
        """Log a single step in the process trail."""
        self.step_counter += 1
        try:
            conn = _get_conn()
            conn.execute("""
                INSERT INTO agent_process_trails
                (id, run_id, agent_name, task_trigger, step_number, step_type,
                 step_name, step_input, step_output, step_decision,
                 duration_ms, metadata, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                uuid.uuid4().hex[:16], self.run_id, self.agent_name,
                self.task_trigger, self.step_counter, step_type, step_name,
                (step_input[:2000] if step_input else None),
                (step_output[:2000] if step_output else None),
                step_decision, duration_ms,
                json.dumps(metadata or {}), status,
                datetime.now().isoformat(),
            ))
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[ProcessTrail] Error logging step: {e}")

    @contextmanager
    def step(self, step_type: str, step_name: str, step_input: str = None):
        """Context manager that auto-captures duration and errors.

        Usage:
            with trail.step("api_call", "Fetch inbox") as s:
                result = fetch_inbox()
                s["output"] = f"Found {len(result)} emails"
                s["metadata"] = {"count": len(result)}
        """
        start = time.time()
        result = {"output": None, "decision": None, "metadata": {}}
        try:
            yield result
            duration = int((time.time() - start) * 1000)
            self.log_step(step_type, step_name, step_input,
                         result.get("output"), result.get("decision"),
                         result.get("metadata"), "success", duration)
        except Exception as e:
            duration = int((time.time() - start) * 1000)
            self.log_step(step_type, step_name, step_input,
                         str(e), None, {"error": str(e)}, "error", duration)
            raise

    def finalize(self, final_output: str):
        """Mark the run as complete with final output."""
        total_duration = int((time.time() - self.start_time) * 1000)
        self.log_step("output", "Final Result", None, final_output, None,
                      {"total_duration_ms": total_duration,
                       "total_steps": self.step_counter}, "success",
                      total_duration)


# ── Query Functions ──

def get_trail(run_id: str) -> list[dict]:
    """Get all steps for a run, ordered by step number."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM agent_process_trails WHERE run_id = ? ORDER BY step_number",
        (run_id,)
    ).fetchall()
    conn.close()
    return [_row_to_dict(r) for r in rows]


def get_recent_runs(agent_name: str = None, limit: int = 20) -> list[dict]:
    """Get recent agent runs (one entry per run_id with summary)."""
    conn = _get_conn()
    query = """
        SELECT run_id, agent_name, task_trigger,
               MIN(created_at) as started_at,
               MAX(step_number) as total_steps,
               SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) as error_count,
               MAX(CASE WHEN step_type = 'output' THEN step_output ELSE NULL END) as final_output
        FROM agent_process_trails
    """
    params = []
    if agent_name:
        query += " WHERE agent_name = ?"
        params.append(agent_name)
    query += " GROUP BY run_id ORDER BY started_at DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [_row_to_dict(r) for r in rows]


def get_runs_for_day(day_date: str) -> list[dict]:
    """Get all runs for a specific day."""
    conn = _get_conn()
    rows = conn.execute("""
        SELECT run_id, agent_name, task_trigger,
               MIN(created_at) as started_at,
               MAX(step_number) as total_steps,
               SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) as error_count,
               MAX(CASE WHEN step_type = 'output' THEN step_output ELSE NULL END) as final_output
        FROM agent_process_trails
        WHERE DATE(created_at) = ?
        GROUP BY run_id ORDER BY started_at DESC
    """, (day_date,)).fetchall()
    conn.close()
    return [_row_to_dict(r) for r in rows]


def get_agent_stats() -> list[dict]:
    """Get stats for each agent: run count, success rate, avg duration."""
    conn = _get_conn()
    rows = conn.execute("""
        SELECT agent_name,
               COUNT(DISTINCT run_id) as total_runs,
               AVG(CASE WHEN step_type = 'output' THEN
                   CAST(json_extract(metadata, '$.total_duration_ms') AS INTEGER)
                   ELSE NULL END) as avg_duration_ms,
               SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) as total_errors,
               MAX(created_at) as last_run
        FROM agent_process_trails
        GROUP BY agent_name
    """).fetchall()
    conn.close()
    return [_row_to_dict(r) for r in rows]


def _row_to_dict(row) -> dict:
    d = dict(row)
    if "metadata" in d and isinstance(d["metadata"], str):
        try:
            d["metadata"] = json.loads(d["metadata"])
        except (json.JSONDecodeError, TypeError):
            pass
    return d


# Initialize on import
init_process_db()
