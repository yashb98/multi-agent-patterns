"""Tests for draft-applicator startup resume behavior."""

from __future__ import annotations

from queue import Queue
from unittest.mock import MagicMock, patch


def test_hydrate_resume_job_rebuilds_payload():
    from jobpulse import draft_applicator as da

    draft_row = {
        "draft_id": "abc12345",
        "job_id": "job-1",
        "title": "ML Engineer",
        "company": "Acme",
        "platform": "linkedin",
        "url": "https://example.com/job",
    }

    fake_db = MagicMock()
    fake_db.get_application.return_value = {
        "cv_path": "/tmp/cv.pdf",
        "cover_letter_path": "/tmp/cl.pdf",
        "custom_answers": "{\"visa\": \"No\"}",
        "ats_score": 88.5,
        "notion_page_id": "n123",
    }
    fake_db.get_listing.return_value = {
        "ats_platform": "greenhouse",
        "location": "London",
        "url": "https://example.com/job",
    }

    with patch("jobpulse.job_db.JobDB", return_value=fake_db):
        payload = da._hydrate_resume_job(draft_row)

    assert payload is not None
    assert payload["_resume_draft_id"] == "abc12345"
    assert payload["cv_path"] == "/tmp/cv.pdf"
    assert payload["custom_answers"]["visa"] == "No"
    assert payload["custom_answers"]["_job_context"]["company"] == "Acme"


def test_resume_pending_drafts_runs_once(monkeypatch):
    from jobpulse import draft_applicator as da

    monkeypatch.setattr(da, "_startup_resume_done", False)
    monkeypatch.setattr(da, "_PENDING_QUEUE", Queue())

    fake_queue = MagicMock()
    fake_queue.get_resumable_drafts.return_value = [{"draft_id": "d1"}]
    fake_queue.update_draft.return_value = True
    monkeypatch.setattr(da, "DraftQueue", lambda: fake_queue)
    monkeypatch.setattr(
        da,
        "_hydrate_resume_job",
        lambda row: {
            "job_id": "job-1",
            "url": "https://example.com",
            "platform": "linkedin",
            "_resume_draft_id": row["draft_id"],
        },
    )

    first = da._resume_pending_drafts_once()
    second = da._resume_pending_drafts_once()

    assert first == 1
    assert second == 0
    assert da._PENDING_QUEUE.qsize() == 1

