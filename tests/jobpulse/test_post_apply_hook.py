"""Tests for post_apply_hook — unified post-apply orchestration.

JobDB is real, backed by tmp_path. Drive uploads (Google API) and
update_application_page (Notion API) remain patched as Category C external
boundaries — invoking them in CI means real auth + real network. Behavior
of those services is exercised in their dedicated test files.
"""
import json
import sqlite3
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

from jobpulse.post_apply_hook import post_apply_hook
from jobpulse.job_db import JobDB
from jobpulse.models.application_models import JobListing
from datetime import datetime, timezone


@pytest.fixture
def tmp_dbs(tmp_path, monkeypatch):
    """Real DB paths via tmp_path. Redirects the JobDB symbol inside
    post_apply_hook so JobDB() (no-arg) writes to tmp_path instead of the
    production applications.db. JobDB itself is the real class — only the
    db_path it gets is overridden."""
    apps_db = tmp_path / "applications.db"

    def _tmp_jobdb():
        return JobDB(db_path=apps_db)

    monkeypatch.setattr("jobpulse.post_apply_hook.JobDB", _tmp_jobdb)
    return {
        "form_exp_db": str(tmp_path / "form_exp.db"),
        "nav_db": str(tmp_path / "nav.db"),
        "apps_db": apps_db,
    }


@pytest.fixture
def mock_result():
    """Synthesized fill-result dict — represents what an adapter returns.
    The result dict is the contract input to post_apply_hook, not a mock
    of any system."""
    return {
        "success": True,
        "pages_filled": 3,
        "field_types": ["text", "select", "upload"],
        "screening_questions": ["Do you require visa sponsorship?"],
    }


@pytest.fixture
def job_context():
    return {
        "job_id": "abc123",
        "company": "TestCorp",
        "title": "Data Engineer",
        "url": "https://boards.greenhouse.io/testcorp/jobs/123",
        "platform": "greenhouse",
        "ats_platform": "greenhouse",
        "notion_page_id": "notion-page-123",
        "cv_path": "/tmp/cv.pdf",
        "cover_letter_path": "/tmp/cl.pdf",
        "match_tier": "auto",
        "ats_score": 96.5,
        "matched_projects": ["multi_agent_patterns", "JobPulse"],
    }


def _seed_application_row(apps_db_path: Path, job_id: str) -> None:
    """Seed a real applications row so mark_applied has something to update."""
    db = JobDB(db_path=apps_db_path)
    listing = JobListing(
        job_id=job_id,
        title="Data Engineer",
        company="TestCorp",
        platform="generic",  # JobListing.platform Literal — ATS is in ats_platform
        url="https://boards.greenhouse.io/testcorp/jobs/123",
        location="London",
        description_raw="Test JD",
        ats_platform="greenhouse",
        found_at=datetime.now(timezone.utc),
    )
    db.save_listing(listing)
    db.save_application(job_id=job_id, status="Pending")
    db.close()


def _read_application_status(apps_db_path: Path, job_id: str) -> str | None:
    with sqlite3.connect(apps_db_path) as conn:
        row = conn.execute(
            "SELECT status FROM applications WHERE job_id = ?",
            (job_id,),
        ).fetchone()
    return row[0] if row else None


@patch("jobpulse.post_apply_hook.upload_cv", return_value="https://drive.google.com/cv-link")
@patch("jobpulse.post_apply_hook.upload_cover_letter", return_value="https://drive.google.com/cl-link")
@patch("jobpulse.post_apply_hook.update_application_page", return_value=True)
def test_full_hook_flow(
    mock_notion,
    mock_cl_upload,
    mock_cv_upload,
    mock_result,
    job_context,
    tmp_dbs,
):
    _seed_application_row(tmp_dbs["apps_db"], "abc123")

    post_apply_hook(
        result=mock_result,
        job_context=job_context,
        form_exp_db_path=tmp_dbs["form_exp_db"],
    )

    # Real DB row was marked Applied (no mock-call assertion)
    assert _read_application_status(tmp_dbs["apps_db"], "abc123") == "Applied"

    # Drive uploads called
    mock_cv_upload.assert_called_once_with(Path("/tmp/cv.pdf"), "TestCorp")
    mock_cl_upload.assert_called_once_with(Path("/tmp/cl.pdf"), "TestCorp")

    # Notion updated with all required fields
    mock_notion.assert_called_once()
    call_kwargs = mock_notion.call_args[1]
    assert call_kwargs["status"] == "Applied"
    assert call_kwargs["applied_date"] == date.today()
    assert "applied_time" in call_kwargs
    assert call_kwargs["cv_drive_link"] == "https://drive.google.com/cv-link"
    assert call_kwargs["cl_drive_link"] == "https://drive.google.com/cl-link"
    assert call_kwargs["follow_up_date"] is not None
    assert call_kwargs["manually_applied"] is True


@patch("jobpulse.post_apply_hook.upload_cv", return_value=None)
@patch("jobpulse.post_apply_hook.upload_cover_letter", return_value=None)
@patch("jobpulse.post_apply_hook.update_application_page", return_value=True)
def test_hook_tolerates_drive_failure(
    mock_notion,
    mock_cl,
    mock_cv,
    mock_result,
    job_context,
    tmp_dbs,
):
    """Drive upload failure should not prevent the real Notion call or DB update."""
    _seed_application_row(tmp_dbs["apps_db"], "abc123")

    post_apply_hook(
        result=mock_result,
        job_context=job_context,
        form_exp_db_path=tmp_dbs["form_exp_db"],
    )
    mock_notion.assert_called_once()
    call_kwargs = mock_notion.call_args[1]
    assert call_kwargs["cv_drive_link"] is None
    assert call_kwargs["cl_drive_link"] is None
    assert _read_application_status(tmp_dbs["apps_db"], "abc123") == "Applied"


@patch("jobpulse.post_apply_hook.upload_cv", return_value="https://drive.google.com/cv")
@patch("jobpulse.post_apply_hook.upload_cover_letter", return_value=None)
@patch("jobpulse.post_apply_hook.update_application_page", return_value=True)
def test_hook_skips_notion_when_no_page_id(
    mock_notion,
    mock_cl,
    mock_cv,
    mock_result,
    job_context,
    tmp_dbs,
):
    _seed_application_row(tmp_dbs["apps_db"], "abc123")
    job_context["notion_page_id"] = None

    post_apply_hook(
        result=mock_result,
        job_context=job_context,
        form_exp_db_path=tmp_dbs["form_exp_db"],
    )
    mock_notion.assert_not_called()
    assert _read_application_status(tmp_dbs["apps_db"], "abc123") == "Applied"


def test_hook_records_form_experience(mock_result, job_context, tmp_dbs):
    _seed_application_row(tmp_dbs["apps_db"], "abc123")

    with patch("jobpulse.post_apply_hook.upload_cv", return_value=None), \
         patch("jobpulse.post_apply_hook.upload_cover_letter", return_value=None), \
         patch("jobpulse.post_apply_hook.update_application_page", return_value=True):
        post_apply_hook(
            result=mock_result,
            job_context=job_context,
            form_exp_db_path=tmp_dbs["form_exp_db"],
        )
    assert _read_application_status(tmp_dbs["apps_db"], "abc123") == "Applied"

    # Real FormExperienceDB query — verify the row was actually written
    from jobpulse.form_experience_db import FormExperienceDB
    db = FormExperienceDB(db_path=tmp_dbs["form_exp_db"])
    exp = db.lookup("boards.greenhouse.io")
    assert exp is not None
    assert exp["platform"] == "greenhouse"
    assert exp["pages_filled"] == 3
    assert json.loads(exp["field_types"]) == ["text", "select", "upload"]


def test_hook_no_op_on_failed_result(job_context, tmp_dbs):
    """Hook does NOT mark applied when result.success is False, but DOES record
    the failure into FormExperienceDB."""
    _seed_application_row(tmp_dbs["apps_db"], "abc123")

    with patch("jobpulse.post_apply_hook.upload_cv") as mock_cv, \
         patch("jobpulse.post_apply_hook.update_application_page") as mock_notion:
        post_apply_hook(
            result={"success": False, "error": "CAPTCHA"},
            job_context=job_context,
            form_exp_db_path=tmp_dbs["form_exp_db"],
        )
    mock_cv.assert_not_called()
    mock_notion.assert_not_called()
    # Real DB row should remain Pending (mark_applied wasn't called)
    assert _read_application_status(tmp_dbs["apps_db"], "abc123") == "Pending"


@patch("jobpulse.post_apply_hook.upload_cv", return_value="https://drive.google.com/cv")
@patch("jobpulse.post_apply_hook.upload_cover_letter", return_value="https://drive.google.com/cl")
@patch("jobpulse.post_apply_hook.update_application_page", return_value=True)
def test_optimization_before_after_records_meaningful_delta(
    mock_notion, mock_cl, mock_cv,
    mock_result, job_context, tmp_dbs, tmp_path, monkeypatch,
):
    """Audit S5 B-2 reproducer.

    The previous instrumentation snapshotted form-fill metrics
    (`fields_filled`, `pages_filled`, `time_seconds`) on both sides of
    the hook even though the hook never mutates them — so deltas were
    always 0. The Drive / Notion / nav booleans we *do* change had no
    `_before` counterpart and were dropped by the tracker's
    `set(before) & set(after)` intersection.

    This test routes through the real OptimizationEngine and asserts
    that at least one delta is non-zero (Drive upload 0→1, Notion 0→1,
    nav 0→1) — i.e. the after-block actually carries new information
    relative to the before-block.
    """
    from shared.optimization._engine import OptimizationEngine

    opt_db = str(tmp_path / "optimization.db")
    real_engine = OptimizationEngine(db_path=opt_db)
    monkeypatch.setattr(
        "shared.optimization.get_optimization_engine",
        lambda: real_engine,
    )
    _seed_application_row(tmp_dbs["apps_db"], "abc123")

    post_apply_hook(
        result=mock_result,
        job_context=job_context,
        form_exp_db_path=tmp_dbs["form_exp_db"],
    )

    actions = real_engine._tracker.get_recent_actions(limit=10)
    post_actions = [a for a in actions if a["loop_name"] == "post_apply"]
    assert post_actions, "post_apply learning_action row should exist"
    action = post_actions[0]

    before = action["before_metrics"]
    after = action["after_metrics"]
    common_keys = set(before) & set(after)
    assert "drive_cv_uploaded" in common_keys, (
        "Outcome key drive_cv_uploaded must appear on both sides — pre-fix "
        "_before only had form-fill metrics so this was dropped by the "
        "tracker's common-keys intersection"
    )
    assert "notion_updated" in common_keys
    assert "nav_learned" in common_keys

    nonzero_deltas = [
        k for k in common_keys
        if (after.get(k) or 0) - (before.get(k) or 0) != 0
    ]
    assert nonzero_deltas, (
        f"At least one delta should be non-zero; got before={before}, "
        f"after={after}. Pre-fix every delta was 0 because both sides "
        f"snapshotted unchanged form-fill metrics."
    )
