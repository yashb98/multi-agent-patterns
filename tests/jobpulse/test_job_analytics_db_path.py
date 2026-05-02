"""job_analytics must read from applications.db where the applications table actually lives."""
import sqlite3
import pytest
from jobpulse.job_analytics import _DB_PATH, get_conversion_funnel


def test_db_path_points_at_applications_db():
    assert _DB_PATH.endswith("applications.db"), (
        f"job_analytics._DB_PATH should target applications.db (where the applications "
        f"table lives), not {_DB_PATH}"
    )


def test_conversion_funnel_returns_nonzero_for_real_db(tmp_path):
    """With a populated applications table, the funnel should not be all zeros."""
    db_path = tmp_path / "applications.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE applications (
                job_id TEXT PRIMARY KEY,
                status TEXT,
                ats_score REAL,
                created_at TEXT
            )
        """)
        from datetime import datetime, timezone
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        conn.executemany(
            "INSERT INTO applications (job_id, status, ats_score, created_at) VALUES (?, ?, ?, ?)",
            [
                ("j1", "Applied", 90.0, ts),
                ("j2", "Applied", 85.0, ts),
                ("j3", "Rejected", 60.0, ts),
                ("j4", "Skipped", 50.0, ts),
            ],
        )
    funnel = get_conversion_funnel(days=30, db_path=str(db_path))
    # We just want to confirm the function CAN read non-zero data when pointed
    # at a real applications table. Any non-zero count proves the path resolves.
    total = sum(v for v in funnel.values() if isinstance(v, (int, float)))
    assert total > 0, f"funnel returned all zeros even with 4 rows: {funnel}"
