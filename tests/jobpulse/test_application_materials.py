"""Tests for deferred CV / lazy cover letter helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from jobpulse.models.application_models import JobListing


def _listing(job_id: str = "tid001") -> JobListing:
    return JobListing(
        job_id=job_id,
        title="Backend Engineer",
        company="Acme",
        platform="linkedin",
        url="https://linkedin.com/jobs/view/1",
        location="London",
        description_raw="Build APIs",
        found_at=datetime.now(timezone.utc),
        required_skills=["Python"],
        preferred_skills=["Docker"],
    )


def test_ensure_tailored_cv_for_job_calls_generate_and_updates_db(tmp_path, monkeypatch):
    monkeypatch.setattr("jobpulse.application_materials.DATA_DIR", tmp_path)
    from jobpulse.application_materials import ensure_tailored_cv_for_job
    from jobpulse.job_db import JobDB

    db = JobDB(db_path=tmp_path / "apps.db")
    listing = _listing()
    db.save_listing(listing)
    db.save_application(listing.job_id, status="Ready")

    out_pdf = tmp_path / "applications" / listing.job_id / "cv.pdf"
    out_pdf.parent.mkdir(parents=True, exist_ok=True)

    with patch(
        "jobpulse.cv_templates.generate_cv.generate_cv_pdf",
        return_value=out_pdf,
    ) as mock_pdf:
        result = ensure_tailored_cv_for_job(listing.job_id, db=db)

    assert result == out_pdf
    mock_pdf.assert_called_once()
    row = db.get_application(listing.job_id)
    assert row and row["cv_path"] == str(out_pdf)


def test_build_lazy_cover_letter_generator(tmp_path, monkeypatch):
    monkeypatch.setattr("jobpulse.application_materials.DATA_DIR", tmp_path)
    from jobpulse.application_materials import build_lazy_cover_letter_generator
    from jobpulse.job_db import JobDB

    db = JobDB(db_path=tmp_path / "apps2.db")
    listing = _listing("tid002")
    db.save_listing(listing)

    cl_pdf = tmp_path / "cl.pdf"
    cl_pdf.write_bytes(b"%PDF")

    gen = build_lazy_cover_letter_generator(listing.job_id, db=db)
    with patch(
        "jobpulse.cv_templates.generate_cover_letter.generate_cover_letter_pdf",
        return_value=cl_pdf,
    ) as mock_cl:
        assert gen() == cl_pdf
    mock_cl.assert_called_once()
