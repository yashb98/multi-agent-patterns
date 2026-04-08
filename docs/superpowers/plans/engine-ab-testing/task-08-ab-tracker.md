# Task 8: ABTracker — SQLite Tracking Database

**Files:**
- Create: `jobpulse/tracked_driver.py` (ABTracker class only — TrackedDriver in Task 9)
- Test: `tests/jobpulse/test_ab_tracker.py`

**Why:** SQLite database that stores per-field events, per-application outcomes, and daily learning snapshots. This is the data layer for the A/B comparison dashboard.

---

- [ ] **Step 1: Write failing test**

```python
"""tests/jobpulse/test_ab_tracker.py"""
import pytest
from jobpulse.tracked_driver import ABTracker

def test_log_field_and_query(tmp_path):
    db_path = str(tmp_path / "ab_test.db")
    tracker = ABTracker(db_path=db_path)
    tracker.log_field(
        application_id="app_1", engine="playwright", platform="greenhouse",
        action="fill", selector="#email", success=True, value_verified=True,
        duration_ms=120, error=None, retry_count=0,
    )
    stats = tracker.get_engine_stats("playwright", days=7)
    assert stats["total_fields"] == 1
    assert stats["fields_verified"] == 1

def test_log_outcome(tmp_path):
    db_path = str(tmp_path / "ab_test.db")
    tracker = ABTracker(db_path=db_path)
    tracker.log_outcome(
        app_id="app_1", engine="extension", platform="lever",
        domain="jobs.lever.co", total_fields=10, fields_filled=9,
        fields_verified=8, validation_errors=0, outcome="submitted",
        total_duration_s=180.5, pages_navigated=3,
        fixes_applied=1, fixes_learned=0,
    )
    stats = tracker.get_engine_stats("extension", days=7)
    assert stats["applications"] == 1
    assert stats["submit_success"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/jobpulse/test_ab_tracker.py -v`
Expected: FAIL — `ImportError: cannot import name 'ABTracker'`

- [ ] **Step 3: Implement ABTracker**

```python
"""A/B engine tracking — SQLite storage for per-field and per-application metrics."""
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

from shared.logging_config import get_logger
from jobpulse.config import DATA_DIR

logger = get_logger(__name__)

_DEFAULT_DB = str(DATA_DIR / "ab_engine_tracking.db")


class ABTracker:
    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or _DEFAULT_DB
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS field_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    app_id TEXT NOT NULL, engine TEXT NOT NULL,
                    platform TEXT, action TEXT NOT NULL, selector TEXT,
                    success BOOLEAN NOT NULL, value_verified BOOLEAN,
                    retry_count INTEGER DEFAULT 0, duration_ms INTEGER,
                    error TEXT, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS application_outcomes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    app_id TEXT NOT NULL UNIQUE, engine TEXT NOT NULL,
                    platform TEXT, domain TEXT,
                    total_fields INTEGER DEFAULT 0, fields_filled INTEGER DEFAULT 0,
                    fields_verified INTEGER DEFAULT 0, validation_errors INTEGER DEFAULT 0,
                    outcome TEXT, total_duration_s REAL,
                    pages_navigated INTEGER DEFAULT 0,
                    fixes_applied INTEGER DEFAULT 0, fixes_learned INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS engine_learning (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    engine TEXT NOT NULL, date TEXT NOT NULL,
                    applications INTEGER DEFAULT 0,
                    first_try_success INTEGER DEFAULT 0,
                    total_fixes INTEGER DEFAULT 0, fix_success_rate REAL,
                    gotcha_count INTEGER DEFAULT 0,
                    UNIQUE(engine, date)
                );
            """)

    def log_field(self, *, application_id, engine, action, selector=None,
                  success, value_verified=None, duration_ms=None, error=None,
                  retry_count=0, platform=None):
        now = datetime.now(UTC).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO field_events (app_id,engine,platform,action,selector,success,value_verified,retry_count,duration_ms,error,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (application_id, engine, platform, action, selector, success, value_verified, retry_count, duration_ms, error, now),
            )

    def log_outcome(self, *, app_id, engine, platform, domain, total_fields,
                    fields_filled, fields_verified, validation_errors, outcome,
                    total_duration_s, pages_navigated, fixes_applied, fixes_learned):
        now = datetime.now(UTC).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO application_outcomes
                   (app_id,engine,platform,domain,total_fields,fields_filled,fields_verified,
                    validation_errors,outcome,total_duration_s,pages_navigated,fixes_applied,fixes_learned,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (app_id, engine, platform, domain, total_fields, fields_filled, fields_verified,
                 validation_errors, outcome, total_duration_s, pages_navigated, fixes_applied, fixes_learned, now),
            )

    def get_engine_stats(self, engine: str, days: int = 7) -> dict:
        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            r = conn.execute(
                "SELECT COUNT(*), SUM(CASE WHEN success THEN 1 ELSE 0 END), SUM(CASE WHEN value_verified THEN 1 ELSE 0 END) FROM field_events WHERE engine=? AND created_at>?",
                (engine, cutoff),
            ).fetchone()
            o = conn.execute(
                "SELECT COUNT(*), SUM(CASE WHEN outcome='submitted' THEN 1 ELSE 0 END) FROM application_outcomes WHERE engine=? AND created_at>?",
                (engine, cutoff),
            ).fetchone()
            return {
                "total_fields": r[0] or 0, "fields_filled": r[1] or 0,
                "fields_verified": r[2] or 0, "applications": o[0] or 0,
                "submit_success": o[1] or 0,
            }
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/jobpulse/test_ab_tracker.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add jobpulse/tracked_driver.py tests/jobpulse/test_ab_tracker.py
git commit -m "feat: ABTracker — SQLite storage for engine A/B field events and outcomes"
```
