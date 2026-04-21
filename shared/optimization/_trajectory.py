"""TrajectoryStore — structured action logging for all agent pipelines."""

import csv
import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from shared.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class TrajectoryStep:
    step_index: int
    action: str
    target: str
    input_value: str
    output_value: str
    outcome: str
    duration_ms: float
    metadata: dict = field(default_factory=dict)


@dataclass
class Trajectory:
    trajectory_id: str
    pipeline: str
    domain: str
    agent_name: str
    session_id: str
    steps: list[TrajectoryStep]
    final_outcome: str
    final_score: float
    total_duration_ms: float
    total_cost: float
    timestamp: str


class TrajectoryStore:
    """SQLite-backed trajectory storage with JSONL/CSV export."""

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trajectories (
                    trajectory_id TEXT PRIMARY KEY,
                    pipeline TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    agent_name TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    final_outcome TEXT NOT NULL DEFAULT '',
                    final_score REAL NOT NULL DEFAULT 0.0,
                    total_duration_ms REAL NOT NULL DEFAULT 0.0,
                    total_cost REAL NOT NULL DEFAULT 0.0,
                    timestamp TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trajectory_steps (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trajectory_id TEXT NOT NULL,
                    step_index INTEGER NOT NULL,
                    action TEXT NOT NULL,
                    target TEXT NOT NULL,
                    input_value TEXT NOT NULL,
                    output_value TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    duration_ms REAL NOT NULL,
                    metadata TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY (trajectory_id) REFERENCES trajectories(trajectory_id)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_traj_domain
                ON trajectories(domain)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_traj_pipeline
                ON trajectories(pipeline)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_traj_session
                ON trajectories(session_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_steps_traj
                ON trajectory_steps(trajectory_id)
            """)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def start(self, pipeline: str, domain: str, agent_name: str,
              session_id: str) -> str:
        tid = str(uuid.uuid4())
        ts = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO trajectories
                   (trajectory_id, pipeline, domain, agent_name, session_id, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (tid, pipeline, domain, agent_name, session_id, ts),
            )
        return tid

    def log_step(self, trajectory_id: str, step: TrajectoryStep):
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO trajectory_steps
                   (trajectory_id, step_index, action, target,
                    input_value, output_value, outcome, duration_ms, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    trajectory_id, step.step_index, step.action, step.target,
                    step.input_value, step.output_value, step.outcome,
                    step.duration_ms, json.dumps(step.metadata),
                ),
            )

    def complete(self, trajectory_id: str, final_outcome: str,
                 final_score: float, total_duration_ms: float = 0.0,
                 total_cost: float = 0.0) -> Trajectory:
        with self._connect() as conn:
            conn.execute(
                """UPDATE trajectories
                   SET final_outcome = ?, final_score = ?,
                       total_duration_ms = ?, total_cost = ?
                   WHERE trajectory_id = ?""",
                (final_outcome, final_score, total_duration_ms,
                 total_cost, trajectory_id),
            )
        return self._load(trajectory_id)

    def _load(self, trajectory_id: str) -> Trajectory:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM trajectories WHERE trajectory_id = ?",
                (trajectory_id,),
            ).fetchone()
            step_rows = conn.execute(
                """SELECT * FROM trajectory_steps
                   WHERE trajectory_id = ? ORDER BY step_index""",
                (trajectory_id,),
            ).fetchall()
        steps = [
            TrajectoryStep(
                step_index=s["step_index"], action=s["action"],
                target=s["target"], input_value=s["input_value"],
                output_value=s["output_value"], outcome=s["outcome"],
                duration_ms=s["duration_ms"],
                metadata=json.loads(s["metadata"]),
            )
            for s in step_rows
        ]
        return Trajectory(
            trajectory_id=row["trajectory_id"],
            pipeline=row["pipeline"], domain=row["domain"],
            agent_name=row["agent_name"], session_id=row["session_id"],
            steps=steps, final_outcome=row["final_outcome"],
            final_score=row["final_score"],
            total_duration_ms=row["total_duration_ms"],
            total_cost=row["total_cost"], timestamp=row["timestamp"],
        )

    def query(self, domain: str = "", pipeline: str = "",
              session_id: str = "", final_outcome: str = "",
              limit: int = 100) -> list[Trajectory]:
        clauses: list[str] = []
        params: list[str] = []
        if domain:
            clauses.append("domain = ?")
            params.append(domain)
        if pipeline:
            clauses.append("pipeline = ?")
            params.append(pipeline)
        if session_id:
            clauses.append("session_id = ?")
            params.append(session_id)
        if final_outcome:
            clauses.append("final_outcome = ?")
            params.append(final_outcome)
        where = " AND ".join(clauses) if clauses else "1=1"
        sql = f"""SELECT * FROM trajectories
                  WHERE {where} ORDER BY timestamp DESC LIMIT ?"""
        params.append(str(limit))
        with self._connect() as conn:
            traj_rows = conn.execute(sql, params).fetchall()
            if not traj_rows:
                return []
            tids = [r["trajectory_id"] for r in traj_rows]
            placeholders = ",".join("?" * len(tids))
            step_rows = conn.execute(
                f"""SELECT * FROM trajectory_steps
                    WHERE trajectory_id IN ({placeholders})
                    ORDER BY trajectory_id, step_index""",
                tids,
            ).fetchall()

        steps_by_tid: dict[str, list[TrajectoryStep]] = {}
        for s in step_rows:
            tid = s["trajectory_id"]
            steps_by_tid.setdefault(tid, []).append(TrajectoryStep(
                step_index=s["step_index"], action=s["action"],
                target=s["target"], input_value=s["input_value"],
                output_value=s["output_value"], outcome=s["outcome"],
                duration_ms=s["duration_ms"],
                metadata=json.loads(s["metadata"]),
            ))

        return [
            Trajectory(
                trajectory_id=r["trajectory_id"],
                pipeline=r["pipeline"], domain=r["domain"],
                agent_name=r["agent_name"], session_id=r["session_id"],
                steps=steps_by_tid.get(r["trajectory_id"], []),
                final_outcome=r["final_outcome"],
                final_score=r["final_score"],
                total_duration_ms=r["total_duration_ms"],
                total_cost=r["total_cost"], timestamp=r["timestamp"],
            )
            for r in traj_rows
        ]

    def prune(self, max_age_days: int = 90):
        cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()
        with self._connect() as conn:
            ids = conn.execute(
                "SELECT trajectory_id FROM trajectories WHERE timestamp < ?",
                (cutoff,),
            ).fetchall()
            tid_list = [r["trajectory_id"] for r in ids]
            if tid_list:
                placeholders = ",".join("?" * len(tid_list))
                conn.execute(
                    f"DELETE FROM trajectory_steps WHERE trajectory_id IN ({placeholders})",
                    tid_list,
                )
                conn.execute(
                    f"DELETE FROM trajectories WHERE trajectory_id IN ({placeholders})",
                    tid_list,
                )
        pruned_count = len(tid_list) if tid_list else 0
        logger.info("Pruned %d trajectories older than %d days", pruned_count, max_age_days)

    def export_jsonl(self, path: str, domain: str = "", pipeline: str = ""):
        trajectories = self.query(domain=domain, pipeline=pipeline, limit=10000)
        with open(path, "w") as f:
            for traj in trajectories:
                conversations = []
                for step in traj.steps:
                    conversations.append({
                        "from": "human",
                        "value": f"[{step.action}] {step.target}: {step.input_value}",
                    })
                    conversations.append({
                        "from": "gpt",
                        "value": f"[{step.outcome}] {step.output_value}",
                    })
                entry = {
                    "id": traj.trajectory_id,
                    "conversations": conversations,
                    "metadata": {
                        "pipeline": traj.pipeline,
                        "domain": traj.domain,
                        "score": traj.final_score,
                        "outcome": traj.final_outcome,
                    },
                }
                f.write(json.dumps(entry) + "\n")

    def export_csv(self, path: str, domain: str = "", pipeline: str = ""):
        trajectories = self.query(domain=domain, pipeline=pipeline, limit=10000)
        with open(path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "trajectory_id", "pipeline", "domain", "agent_name",
                "session_id", "final_outcome", "final_score",
                "total_duration_ms", "total_cost", "steps_count", "timestamp",
            ])
            for traj in trajectories:
                writer.writerow([
                    traj.trajectory_id, traj.pipeline, traj.domain,
                    traj.agent_name, traj.session_id, traj.final_outcome,
                    traj.final_score, traj.total_duration_ms, traj.total_cost,
                    len(traj.steps), traj.timestamp,
                ])
