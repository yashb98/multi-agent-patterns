"""Tests for jobpulse/followup_tracker.py — all use tmp_path, never data/*.db."""

from datetime import date, timedelta

import pytest

from jobpulse.followup_tracker import (
    APPLIED_FIRST_DAYS,
    APPLIED_MAX_FOLLOWUPS,
    RESPONDED_INITIAL_DAYS,
    RESPONDED_SUBSEQUENT_DAYS,
    INTERVIEW_THANKYOU_DAYS,
    compute_urgency,
    get_followup_count,
    init_db,
    record_followup,
)


# ---------------------------------------------------------------------------
# compute_urgency
# ---------------------------------------------------------------------------

class TestComputeUrgency:
    def test_applied_overdue(self):
        past = date.today() - timedelta(days=APPLIED_FIRST_DAYS)
        assert compute_urgency("applied", past, followup_count=0) == "overdue"

    def test_applied_waiting(self):
        recent = date.today() - timedelta(days=APPLIED_FIRST_DAYS - 1)
        assert compute_urgency("applied", recent, followup_count=0) == "waiting"

    def test_applied_cold(self):
        any_date = date.today() - timedelta(days=30)
        assert compute_urgency("applied", any_date, followup_count=APPLIED_MAX_FOLLOWUPS) == "cold"

    def test_responded_urgent(self):
        today = date.today()
        assert compute_urgency("responded", today, followup_count=0) == "urgent"

    def test_responded_overdue(self):
        past = date.today() - timedelta(days=RESPONDED_SUBSEQUENT_DAYS)
        assert compute_urgency("responded", past, followup_count=1) == "overdue"

    def test_interview_overdue(self):
        past = date.today() - timedelta(days=INTERVIEW_THANKYOU_DAYS)
        assert compute_urgency("interview", past, followup_count=0) == "overdue"

    def test_interview_waiting(self):
        today = date.today()
        assert compute_urgency("interview", today, followup_count=0) == "waiting"


# ---------------------------------------------------------------------------
# record_followup + get_followup_count
# ---------------------------------------------------------------------------

class TestRecordFollowup:
    def test_records_and_retrieves(self, tmp_path):
        db = str(tmp_path / "followups.db")
        init_db(db)

        assert get_followup_count("job-001", db) == 0

        record_followup("job-001", "email", "recruiter@acme.com", "Sent intro", db)
        assert get_followup_count("job-001", db) == 1

        record_followup("job-001", "linkedin", "hr@acme.com", "LinkedIn nudge", db)
        assert get_followup_count("job-001", db) == 2

        # Different job_id should not affect count
        assert get_followup_count("job-999", db) == 0
