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
    direct_url TEXT,
    found_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_listings_company ON job_listings (company COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_listings_platform ON job_listings (platform);
CREATE INDEX IF NOT EXISTS idx_listings_found_at ON job_listings (found_at);

CREATE TABLE IF NOT EXISTS applications (
    job_id TEXT PRIMARY KEY REFERENCES job_listings(job_id),
    status TEXT NOT NULL DEFAULT 'Found',
    ats_score REAL DEFAULT 0,
    match_tier TEXT DEFAULT 'skip',
    matched_projects TEXT,
    cv_path TEXT,
    cover_letter_path TEXT,
    cv_version TEXT,
    generation_strategy TEXT,
    applied_at TEXT,
    notion_page_id TEXT,
    follow_up_date TEXT,
    custom_answers TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_applications_status ON applications (status);
CREATE INDEX IF NOT EXISTS idx_applications_match_tier ON applications (match_tier);
CREATE INDEX IF NOT EXISTS idx_applications_applied_at ON applications (applied_at);
CREATE INDEX IF NOT EXISTS idx_applications_created_at ON applications (created_at);

CREATE TABLE IF NOT EXISTS application_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT REFERENCES applications(job_id),
    event_type TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    details TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_job_id ON application_events (job_id);

CREATE TABLE IF NOT EXISTS application_outcomes (
    job_id TEXT PRIMARY KEY REFERENCES applications(job_id),
    outcome TEXT NOT NULL DEFAULT 'pending',
    stage_reached TEXT DEFAULT 'applied',
    feedback TEXT,
    days_to_response INTEGER,
    outcome_date TEXT,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_outcomes_outcome ON application_outcomes (outcome);

CREATE TABLE IF NOT EXISTS gate_effectiveness (
    gate_name TEXT NOT NULL,
    decision TEXT NOT NULL,
    final_outcome TEXT NOT NULL,
    count INTEGER DEFAULT 0,
    PRIMARY KEY (gate_name, decision, final_outcome)
);

CREATE TABLE IF NOT EXISTS ats_answer_cache (
    question_hash TEXT PRIMARY KEY,
    question_text TEXT NOT NULL,
    answer TEXT NOT NULL,
    times_used INTEGER DEFAULT 1,
    success_count INTEGER DEFAULT 0,
    correction_count INTEGER DEFAULT 0,
    last_verified_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS company_reliability (
    company TEXT PRIMARY KEY,
    total_applied INTEGER DEFAULT 0,
    total_interview INTEGER DEFAULT 0,
    total_offer INTEGER DEFAULT 0,
    total_rejected INTEGER DEFAULT 0,
    total_ghosted INTEGER DEFAULT 0,
    avg_days_to_response REAL,
    last_applied_at TEXT,
    updated_at TEXT NOT NULL
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
        self._conn: sqlite3.Connection | None = None
        self._init_schema()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        if self._conn is not None:
            try:
                self._conn.execute("SELECT 1")
                return self._conn
            except sqlite3.ProgrammingError:
                self._conn = None
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        return self._conn

    def close(self) -> None:
        """Close the persistent connection. Call before discarding the instance."""
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    def __del__(self):
        self.close()

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(_DDL)
            existing = {r[1] for r in conn.execute("PRAGMA table_info(ats_answer_cache)").fetchall()}
            for col, typ in [
                ("success_count", "INTEGER DEFAULT 0"),
                ("correction_count", "INTEGER DEFAULT 0"),
                ("last_verified_at", "TEXT"),
            ]:
                if col not in existing:
                    conn.execute(f"ALTER TABLE ats_answer_cache ADD COLUMN {col} {typ}")
            listing_cols = {r[1] for r in conn.execute("PRAGMA table_info(job_listings)").fetchall()}
            if "direct_url" not in listing_cols:
                conn.execute("ALTER TABLE job_listings ADD COLUMN direct_url TEXT")

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
                    ats_platform, easy_apply, direct_url, found_at
                ) VALUES (
                    ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?, ?
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
                    listing.direct_url,
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
        cv_version: str | None = None,
        generation_strategy: str | None = None,
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
                    cv_path, cover_letter_path, cv_version, generation_strategy,
                    applied_at, notion_page_id,
                    follow_up_date, custom_answers, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    status,
                    ats_score,
                    match_tier,
                    json.dumps(matched_projects or []),
                    cv_path,
                    cover_letter_path,
                    cv_version,
                    generation_strategy,
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

    def get_application_by_notion_page_id(self, notion_page_id: str) -> dict | None:
        """Look up a local application record by its Notion page ID."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM applications WHERE notion_page_id = ?", (notion_page_id,)
            ).fetchone()
        if row is None:
            return None
        return dict(row)

    def find_application_by_company_title(self, company: str, title: str) -> dict | None:
        """Fuzzy match an application by company + title (case-insensitive)."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT a.* FROM applications a
                JOIN job_listings l ON a.job_id = l.job_id
                WHERE LOWER(l.company) = LOWER(?) AND LOWER(l.title) = LOWER(?)
                ORDER BY a.updated_at DESC LIMIT 1
                """,
                (company, title),
            ).fetchone()
        if row is None:
            return None
        return dict(row)

    def link_notion_page(self, job_id: str, notion_page_id: str) -> None:
        """Link an existing application record to a Notion page ID."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE applications SET notion_page_id = ?, updated_at = ? WHERE job_id = ?",
                (notion_page_id, _now(), job_id),
            )

    def get_listing_by_notion_page_id(self, notion_page_id: str) -> dict | None:
        """Look up a job listing by Notion page ID (via the applications table)."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT l.* FROM job_listings l
                JOIN applications a ON l.job_id = a.job_id
                WHERE a.notion_page_id = ?
                """,
                (notion_page_id,),
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

    def mark_applied(self, job_id: str) -> None:
        """Set status to Applied and stamp applied_at (successful submission)."""
        current = self.get_application(job_id)
        old_status = current["status"] if current else ""
        now = _now()
        with self._connect() as conn:
            conn.execute(
                (
                    "UPDATE applications SET status = 'Applied', "
                    "applied_at = ?, updated_at = ? WHERE job_id = ?"
                ),
                (now, now, job_id),
            )
        self.log_event(
            job_id=job_id,
            event_type="status_change",
            old_value=old_status,
            new_value="Applied",
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

    def get_ready_or_pending_found_on(self, day: date) -> list[dict]:
        """Ready / Pending Approval applications whose listing ``found_at`` is *day* (UTC date prefix)."""
        day_prefix = day.isoformat()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT a.*, l.title, l.company, l.platform, l.url,
                       l.salary_min, l.salary_max, l.location, l.remote,
                       l.seniority, l.found_at
                FROM applications a
                JOIN job_listings l ON a.job_id = l.job_id
                WHERE a.status IN ('Ready', 'Pending Approval')
                  AND substr(COALESCE(l.found_at, ''), 1, 10) = ?
                ORDER BY a.updated_at DESC, a.created_at DESC
                """,
                (day_prefix,),
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
        company (substring match) with title word containment >= 0.6, within the last 90 days.
        """
        from datetime import timedelta

        cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%S")
        company_norm = company.lower().split(".")[0].strip()

        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT l.title
                FROM applications a
                JOIN job_listings l ON a.job_id = l.job_id
                WHERE (LOWER(l.company) LIKE ? OR LOWER(l.company) LIKE ?)
                  AND a.status NOT IN ('Skipped', 'Withdrawn')
                  AND a.created_at >= ?
                """,
                (f"%{company_norm}%", f"%{company.lower()}%", cutoff),
            ).fetchall()

        if not rows:
            return False

        def _word_set(text: str) -> set[str]:
            return {w.lower() for w in text.split() if w.isalpha()}

        query_words = _word_set(title)
        if not query_words:
            return bool(rows)

        for row in rows:
            existing_words = _word_set(row["title"])
            if not existing_words:
                continue
            intersection = query_words & existing_words
            containment = len(intersection) / len(query_words)
            if containment >= 0.6:
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
                WHERE l.company = ? COLLATE NOCASE
                  AND a.status NOT IN ('Skipped', 'Withdrawn')
                """,
                (company,),
            ).fetchone()
        return row["cnt"] if row else 0

    # ------------------------------------------------------------------
    # Application outcomes (downstream learning)
    # ------------------------------------------------------------------

    def save_outcome(
        self,
        job_id: str,
        outcome: str,
        stage_reached: str = "applied",
        feedback: str = "",
        days_to_response: int | None = None,
    ) -> None:
        """Store or update the downstream hiring outcome for an application."""
        now = _now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO application_outcomes (job_id, outcome, stage_reached, feedback, days_to_response, outcome_date, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                    outcome = excluded.outcome,
                    stage_reached = excluded.stage_reached,
                    feedback = excluded.feedback,
                    days_to_response = excluded.days_to_response,
                    outcome_date = excluded.outcome_date,
                    updated_at = excluded.updated_at
                """,
                (job_id, outcome, stage_reached, feedback, days_to_response, now, now),
            )

        # Update CV scrutiny calibration with outcome
        try:
            from jobpulse.cv_templates.scrutiny_calibrator import ScrutinyCalibrator
            calibrator = ScrutinyCalibrator()
            got_interview = outcome in ("interview", "offer", "hired")
            got_offer = outcome in ("offer", "hired")
            calibrator.update_outcome(
                job_id, got_interview=got_interview, got_offer=got_offer
            )
        except Exception:
            pass  # Non-blocking

    def get_outcome(self, job_id: str) -> dict | None:
        """Retrieve the outcome record for a job."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM application_outcomes WHERE job_id = ?", (job_id,)
            ).fetchone()
        return dict(row) if row else None

    def get_outcome_stats(self) -> dict:
        """Return aggregate outcome statistics."""
        with self._connect() as conn:
            total = conn.execute("SELECT COUNT(*) FROM application_outcomes").fetchone()[0]
            interview = conn.execute(
                """SELECT COUNT(*) FROM application_outcomes
                   WHERE stage_reached IN ('phone_screen', 'technical', 'final_round', 'offer')
                      OR outcome IN ('offer_accepted', 'offer_declined', 'interview')"""
            ).fetchone()[0]
            offer = conn.execute(
                "SELECT COUNT(*) FROM application_outcomes WHERE outcome IN ('offer_accepted', 'offer_declined')"
            ).fetchone()[0]
            avg_days = conn.execute(
                "SELECT AVG(days_to_response) FROM application_outcomes WHERE days_to_response IS NOT NULL"
            ).fetchone()[0]
        return {
            "total_outcomes": total,
            "interview_rate": interview / total if total else 0.0,
            "offer_rate": offer / total if total else 0.0,
            "avg_days_to_response": avg_days or 0.0,
        }

    def record_gate_decision(self, gate_name: str, decision: str, final_outcome: str) -> None:
        """Increment the effectiveness counter for a gate decision."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO gate_effectiveness (gate_name, decision, final_outcome, count)
                VALUES (?, ?, ?, 1)
                ON CONFLICT(gate_name, decision, final_outcome) DO UPDATE SET count = count + 1
                """,
                (gate_name, decision, final_outcome),
            )

    def get_gate_effectiveness(self, gate_name: str) -> list[dict]:
        """Return effectiveness breakdown for a specific gate."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM gate_effectiveness WHERE gate_name = ? ORDER BY count DESC",
                (gate_name,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_all_gate_effectiveness(self) -> dict[str, list[dict]]:
        """Return all gate effectiveness data grouped by gate."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM gate_effectiveness ORDER BY gate_name, count DESC"
            ).fetchall()
        result: dict[str, list[dict]] = {}
        for r in rows:
            result.setdefault(r["gate_name"], []).append(dict(r))
        return result

    # ------------------------------------------------------------------
    # Company reliability (auto-evolving blacklist/whitelist)
    # ------------------------------------------------------------------

    def update_company_reliability(
        self,
        company: str,
        outcome: str,
        days_to_response: int | None = None,
    ) -> None:
        """Update reliability stats for a company based on an outcome."""
        now = _now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO company_reliability (company, total_applied, last_applied_at, updated_at)
                VALUES (?, 1, ?, ?)
                ON CONFLICT(company) DO UPDATE SET
                    total_applied = total_applied + 1,
                    last_applied_at = ?,
                    updated_at = ?
                """,
                (company, now, now, now, now),
            )
            col = {
                "interview": "total_interview",
                "offer_accepted": "total_offer",
                "offer_declined": "total_offer",
                "rejected_no_interview": "total_rejected",
                "rejected_after_phone": "total_rejected",
                "ghost": "total_ghosted",
            }.get(outcome)
            if col:
                conn.execute(
                    f"UPDATE company_reliability SET {col} = {col} + 1 WHERE company = ?",
                    (company,),
                )
            if days_to_response is not None:
                conn.execute(
                    """
                    UPDATE company_reliability
                    SET avg_days_to_response = COALESCE(
                        (avg_days_to_response * (total_applied - 1) + ?) / total_applied, ?
                    )
                    WHERE company = ?
                    """,
                    (days_to_response, days_to_response, company),
                )

    def get_company_reliability(self, company: str) -> dict | None:
        """Return reliability stats for a company."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM company_reliability WHERE company = ? COLLATE NOCASE", (company,)
            ).fetchone()
        return dict(row) if row else None

    def get_unreliable_companies(self, min_applied: int = 3, ghost_threshold: float = 0.5) -> list[dict]:
        """Return companies with high ghost rates."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *, CAST(total_ghosted AS REAL) / total_applied as ghost_rate
                FROM company_reliability
                WHERE total_applied >= ?
                  AND CAST(total_ghosted AS REAL) / total_applied >= ?
                ORDER BY ghost_rate DESC
                """,
                (min_applied, ghost_threshold),
            ).fetchall()
        return [dict(r) for r in rows]

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

    def record_answer_verification(self, question: str, success: bool) -> None:
        """Update success/correction counters for a cached answer."""
        q_hash = _hash_question(question)
        now = _now()
        with self._connect() as conn:
            if success:
                conn.execute(
                    "UPDATE ats_answer_cache SET success_count = success_count + 1, last_verified_at = ? WHERE question_hash = ?",
                    (now, q_hash),
                )
            else:
                conn.execute(
                    "UPDATE ats_answer_cache SET correction_count = correction_count + 1, last_verified_at = ? WHERE question_hash = ?",
                    (now, q_hash),
                )

    def get_answer_quality(self, question: str) -> dict | None:
        """Return quality metrics for a cached answer."""
        q_hash = _hash_question(question)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT times_used, success_count, correction_count, last_verified_at FROM ats_answer_cache WHERE question_hash = ?",
                (q_hash,),
            ).fetchone()
        if not row:
            return None
        verifications = (row["success_count"] or 0) + (row["correction_count"] or 0)
        total = verifications if verifications > 0 else (row["times_used"] or 1)
        return {
            "times_used": row["times_used"],
            "success_count": row["success_count"],
            "correction_count": row["correction_count"],
            "success_rate": (row["success_count"] or 0) / total,
            "last_verified_at": row["last_verified_at"],
        }

    def get_cached_answer(self, question: str) -> str | None:
        q_hash = _hash_question(question)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT answer FROM ats_answer_cache WHERE question_hash = ?",
                (q_hash,),
            ).fetchone()
        return row["answer"] if row else None

    def get_all_cached_answers(self) -> dict[str, str]:
        """Return all cached screening answers as {question_text_lower: answer}."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT question_text, answer FROM ats_answer_cache"
            ).fetchall()
        return {row["question_text"].lower().strip(): row["answer"] for row in rows}

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def _today_range(self) -> tuple[str, str]:
        """Return ISO datetime range for today (UTC) suitable for indexed range queries."""
        today = datetime.now(timezone.utc)
        start = today.strftime("%Y-%m-%dT00:00:00")
        end = (today + __import__("datetime").timedelta(days=1)).strftime("%Y-%m-%dT00:00:00")
        return start, end

    def get_today_stats(self) -> dict:
        """Return counts of applied/found/skipped today and the average ATS score for applied."""
        start, end = self._today_range()
        with self._connect() as conn:
            applied = conn.execute(
                "SELECT COUNT(*) FROM applications WHERE status = 'Applied' AND applied_at >= ? AND applied_at < ?",
                (start, end),
            ).fetchone()[0]

            found = conn.execute(
                "SELECT COUNT(*) FROM applications WHERE status = 'Found' AND created_at >= ? AND created_at < ?",
                (start, end),
            ).fetchone()[0]

            skipped = conn.execute(
                "SELECT COUNT(*) FROM applications WHERE status = 'Skipped' AND created_at >= ? AND created_at < ?",
                (start, end),
            ).fetchone()[0]

            avg_row = conn.execute(
                "SELECT AVG(ats_score) FROM applications WHERE status = 'Applied' AND applied_at >= ? AND applied_at < ?",
                (start, end),
            ).fetchone()
            avg_ats: float = avg_row[0] if avg_row and avg_row[0] is not None else 0.0

        return {
            "applied": applied,
            "found": found,
            "skipped": skipped,
            "avg_ats": avg_ats,
        }
