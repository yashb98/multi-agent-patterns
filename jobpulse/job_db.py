"""SQLite storage layer for the Job Autopilot pipeline.

Tables: job_listings, applications, application_events, ats_answer_cache.

Follows the same connection pattern as mindgraph_app/storage.py:
- sqlite3.Row row_factory
- PRAGMA journal_mode=WAL
- PRAGMA foreign_keys=ON
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

from jobpulse.config import DATA_DIR
from jobpulse.models.application_models import JobListing

DEFAULT_DB_PATH: Path = DATA_DIR / "applications.db"

_DDL = """
CREATE TABLE IF NOT EXISTS job_listings (
    job_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    company TEXT NOT NULL,
    platform TEXT NOT NULL,
    url TEXT NOT NULL,
    salary_min REAL,
    salary_max REAL,
    location TEXT,
    remote BOOLEAN DEFAULT FALSE,
    seniority TEXT,
    required_skills TEXT,
    preferred_skills TEXT,
    description_raw TEXT,
    ats_platform TEXT,
    easy_apply BOOLEAN DEFAULT FALSE,
    found_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS applications (
    job_id TEXT PRIMARY KEY REFERENCES job_listings(job_id),
    status TEXT NOT NULL DEFAULT 'Found',
    ats_score REAL DEFAULT 0,
    match_tier TEXT DEFAULT 'skip',
    matched_projects TEXT,
    cv_path TEXT,
    cover_letter_path TEXT,
    applied_at TEXT,
    notion_page_id TEXT,
    follow_up_date TEXT,
    custom_answers TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS application_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT REFERENCES applications(job_id),
    event_type TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    details TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ats_answer_cache (
    question_hash TEXT PRIMARY KEY,
    question_text TEXT NOT NULL,
    answer TEXT NOT NULL,
    times_used INTEGER DEFAULT 1,
    created_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _hash_question(question: str) -> str:
    """SHA-256 of normalised (lower-stripped) question text."""
    normalised = question.strip().lower()
    return hashlib.sha256(normalised.encode()).hexdigest()


class JobDB:
    """SQLite wrapper for the Job Autopilot pipeline."""

    def __init__(self, db_path: Path = DEFAULT_DB_PATH) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(_DDL)

    # ------------------------------------------------------------------
    # Listings
    # ------------------------------------------------------------------

    def save_listing(self, listing: JobListing) -> None:
        """INSERT OR REPLACE a job listing."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO job_listings (
                    job_id, title, company, platform, url,
                    salary_min, salary_max, location, remote, seniority,
                    required_skills, preferred_skills, description_raw,
                    ats_platform, easy_apply, found_at
                ) VALUES (
                    ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?
                )
                """,
                (
                    listing.job_id,
                    listing.title,
                    listing.company,
                    listing.platform,
                    listing.url,
                    listing.salary_min,
                    listing.salary_max,
                    listing.location,
                    listing.remote,
                    listing.seniority,
                    json.dumps(listing.required_skills),
                    json.dumps(listing.preferred_skills),
                    listing.description_raw,
                    listing.ats_platform,
                    listing.easy_apply,
                    listing.found_at.isoformat() if hasattr(listing.found_at, "isoformat") else str(listing.found_at),
                ),
            )

    def get_listing(self, job_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM job_listings WHERE job_id = ?", (job_id,)
            ).fetchone()
        if row is None:
            return None
        return dict(row)

    def listing_exists(self, job_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM job_listings WHERE job_id = ?", (job_id,)
            ).fetchone()
        return row is not None

    def count_listings(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) FROM job_listings").fetchone()
        return row[0] if row else 0

    # ------------------------------------------------------------------
    # Applications
    # ------------------------------------------------------------------

    def save_application(
        self,
        job_id: str,
        status: str = "Found",
        ats_score: float = 0,
        match_tier: str = "skip",
        matched_projects: list[str] | None = None,
        cv_path: str | None = None,
        cover_letter_path: str | None = None,
        applied_at: str | None = None,
        notion_page_id: str | None = None,
        follow_up_date: str | None = None,
        custom_answers: dict[str, str] | None = None,
    ) -> None:
        """INSERT OR REPLACE an application record."""
        now = _now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO applications (
                    job_id, status, ats_score, match_tier, matched_projects,
                    cv_path, cover_letter_path, applied_at, notion_page_id,
                    follow_up_date, custom_answers, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    status,
                    ats_score,
                    match_tier,
                    json.dumps(matched_projects or []),
                    cv_path,
                    cover_letter_path,
                    applied_at,
                    notion_page_id,
                    follow_up_date,
                    json.dumps(custom_answers or {}),
                    now,
                    now,
                ),
            )

    def get_application(self, job_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM applications WHERE job_id = ?", (job_id,)
            ).fetchone()
        if row is None:
            return None
        return dict(row)

    def update_status(self, job_id: str, new_status: str) -> None:
        """Update the status of an application and log a status_change event."""
        current = self.get_application(job_id)
        old_status = current["status"] if current else ""
        now = _now()
        with self._connect() as conn:
            conn.execute(
                "UPDATE applications SET status = ?, updated_at = ? WHERE job_id = ?",
                (new_status, now, job_id),
            )
        self.log_event(
            job_id=job_id,
            event_type="status_change",
            old_value=old_status,
            new_value=new_status,
        )

    def get_applications_by_status(self, status: str) -> list[dict]:
        """Return applications matching the given status, joined with listing data."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT a.*, l.title, l.company, l.platform, l.url,
                       l.salary_min, l.salary_max, l.location, l.remote,
                       l.seniority, l.found_at
                FROM applications a
                JOIN job_listings l ON a.job_id = l.job_id
                WHERE a.status = ?
                """,
                (status,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_follow_ups_due(self, target_date: date) -> list[dict]:
        """Return Applied applications with follow_up_date matching target_date."""
        date_str = target_date.isoformat()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT a.*, l.title, l.company, l.platform, l.url
                FROM applications a
                JOIN job_listings l ON a.job_id = l.job_id
                WHERE a.follow_up_date = ?
                  AND a.status = 'Applied'
                """,
                (date_str,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def log_event(
        self,
        job_id: str,
        event_type: str,
        old_value: str = "",
        new_value: str = "",
        details: str = "",
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO application_events
                    (job_id, event_type, old_value, new_value, details, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (job_id, event_type, old_value, new_value, details, _now()),
            )

    def get_events(self, job_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM application_events WHERE job_id = ? ORDER BY id",
                (job_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Fuzzy deduplication
    # ------------------------------------------------------------------

    def fuzzy_match_exists(self, company: str, title: str) -> bool:
        """Return True if a non-skipped/non-withdrawn application exists for the same
        company (case-insensitive) with a title word overlap >= 0.8, within the last 30 days.
        """
        from datetime import timedelta

        cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%S")

        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT l.title
                FROM applications a
                JOIN job_listings l ON a.job_id = l.job_id
                WHERE LOWER(l.company) = LOWER(?)
                  AND a.status NOT IN ('Skipped', 'Withdrawn')
                  AND a.created_at >= ?
                """,
                (company, cutoff),
            ).fetchall()

        if not rows:
            return False

        def _word_set(text: str) -> set[str]:
            return {w.lower() for w in text.split() if w.isalpha()}

        query_words = _word_set(title)
        if not query_words:
            return False

        for row in rows:
            existing_words = _word_set(row["title"])
            if not existing_words:
                continue
            intersection = query_words & existing_words
            union = query_words | existing_words
            overlap = len(intersection) / len(union) if union else 0.0
            if overlap >= 0.8:
                return True

        return False

    def count_applications_for_company(self, company: str) -> int:
        """Return count of non-skipped applications for a company (case-insensitive)."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) as cnt
                FROM applications a
                JOIN job_listings l ON a.job_id = l.job_id
                WHERE LOWER(l.company) = LOWER(?)
                  AND a.status NOT IN ('Skipped', 'Withdrawn')
                """,
                (company,),
            ).fetchone()
        return row["cnt"] if row else 0

    # ------------------------------------------------------------------
    # ATS answer cache
    # ------------------------------------------------------------------

    def cache_answer(self, question: str, answer: str) -> None:
        """Store a cached answer. On duplicate question, increment times_used."""
        q_hash = _hash_question(question)
        now = _now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO ats_answer_cache (question_hash, question_text, answer, times_used, created_at)
                VALUES (?, ?, ?, 1, ?)
                ON CONFLICT(question_hash) DO UPDATE SET times_used = times_used + 1
                """,
                (q_hash, question.strip(), answer, now),
            )

    def get_cached_answer(self, question: str) -> str | None:
        q_hash = _hash_question(question)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT answer FROM ats_answer_cache WHERE question_hash = ?",
                (q_hash,),
            ).fetchone()
        return row["answer"] if row else None

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def get_today_stats(self) -> dict:
        """Return counts of applied/found/skipped today and the average ATS score for applied."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with self._connect() as conn:
            applied = conn.execute(
                "SELECT COUNT(*) FROM applications WHERE status = 'Applied' AND applied_at LIKE ?",
                (f"{today}%",),
            ).fetchone()[0]

            found = conn.execute(
                "SELECT COUNT(*) FROM applications WHERE status = 'Found' AND created_at LIKE ?",
                (f"{today}%",),
            ).fetchone()[0]

            skipped = conn.execute(
                "SELECT COUNT(*) FROM applications WHERE status = 'Skipped' AND created_at LIKE ?",
                (f"{today}%",),
            ).fetchone()[0]

            avg_row = conn.execute(
                "SELECT AVG(ats_score) FROM applications WHERE status = 'Applied' AND applied_at LIKE ?",
                (f"{today}%",),
            ).fetchone()
            avg_ats: float = avg_row[0] if avg_row and avg_row[0] is not None else 0.0

        return {
            "applied": applied,
            "found": found,
            "skipped": skipped,
            "avg_ats": avg_ats,
        }
