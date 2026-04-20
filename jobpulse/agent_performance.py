"""Agent vs Claude performance tracker — records per-application fill stats.

Tracks how many fields the AI agent filled autonomously vs how many needed
Claude Code intervention, per application. SQLite-backed for persistence.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from shared.logging_config import get_logger

logger = get_logger(__name__)

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "agent_performance.db"

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS fill_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    company TEXT NOT NULL,
    role TEXT,
    platform TEXT,
    url TEXT,
    agent_fields_attempted INTEGER DEFAULT 0,
    agent_fields_filled INTEGER DEFAULT 0,
    agent_fields_failed INTEGER DEFAULT 0,
    claude_fields_filled INTEGER DEFAULT 0,
    failed_labels TEXT,
    fill_time_seconds REAL,
    dry_run INTEGER DEFAULT 0,
    success INTEGER DEFAULT 0,
    notes TEXT
);
"""


class AgentPerformanceDB:
    def __init__(self, db_path: Path | str | None = None):
        self._db_path = Path(db_path) if db_path else DB_PATH
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_table()

    def _get_conn(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self._db_path))

    def _ensure_table(self) -> None:
        with self._get_conn() as conn:
            conn.executescript(_CREATE_SQL)

    def record_session(
        self,
        company: str,
        role: str | None = None,
        platform: str | None = None,
        url: str | None = None,
        agent_stats: dict[str, Any] | None = None,
        claude_fields_filled: int = 0,
        fill_time_seconds: float | None = None,
        dry_run: bool = False,
        success: bool = False,
        notes: str | None = None,
    ) -> int:
        stats = agent_stats or {}
        with self._get_conn() as conn:
            cursor = conn.execute(
                """INSERT INTO fill_sessions
                   (timestamp, company, role, platform, url,
                    agent_fields_attempted, agent_fields_filled, agent_fields_failed,
                    claude_fields_filled, failed_labels, fill_time_seconds,
                    dry_run, success, notes)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    datetime.now(timezone.utc).isoformat(),
                    company,
                    role,
                    platform,
                    url,
                    stats.get("fields_attempted", 0),
                    stats.get("fields_filled", 0),
                    stats.get("fields_failed", 0),
                    claude_fields_filled,
                    json.dumps(stats.get("failed_labels", [])),
                    fill_time_seconds,
                    int(dry_run),
                    int(success),
                    notes,
                ),
            )
            return cursor.lastrowid or 0

    def get_summary(self) -> dict[str, Any]:
        with self._get_conn() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM fill_sessions ORDER BY timestamp DESC"
            ).fetchall()

        if not rows:
            return {"total_sessions": 0}

        total_agent = sum(r["agent_fields_filled"] for r in rows)
        total_claude = sum(r["claude_fields_filled"] for r in rows)
        total_failed = sum(r["agent_fields_failed"] for r in rows)
        total_attempted = sum(r["agent_fields_attempted"] for r in rows)

        return {
            "total_sessions": len(rows),
            "agent_fields_filled": total_agent,
            "claude_fields_filled": total_claude,
            "agent_fields_failed": total_failed,
            "total_attempted": total_attempted,
            "agent_success_rate": round(total_agent / max(total_attempted, 1) * 100, 1),
            "recent": [
                {
                    "company": r["company"],
                    "role": r["role"],
                    "platform": r["platform"],
                    "agent_filled": r["agent_fields_filled"],
                    "claude_filled": r["claude_fields_filled"],
                    "failed": r["agent_fields_failed"],
                    "success": bool(r["success"]),
                    "timestamp": r["timestamp"],
                }
                for r in rows[:10]
            ],
        }

    def get_all(self) -> list[dict]:
        with self._get_conn() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM fill_sessions ORDER BY timestamp DESC"
            ).fetchall()
        return [dict(r) for r in rows]
