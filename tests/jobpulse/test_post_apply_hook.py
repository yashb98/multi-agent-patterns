"""Tests for post_apply_hook — unified post-apply orchestration."""
import json
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from jobpulse.post_apply_hook import post_apply_hook


@pytest.fixture
def tmp_dbs(tmp_path):
    """Patch all DB paths to tmp_path."""
    return {
        "form_exp_db": str(tmp_path / "form_exp.db"),
        "nav_db": str(tmp_path / "nav.db"),
    }


@pytest.fixture
def mock_result():
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


@patch("jobpulse.post_apply_hook.upload_cv", return_value="https://drive.google.com/cv-link")
@patch("jobpulse.post_apply_hook.upload_cover_letter", return_value="https://drive.google.com/cl-link")
@patch("jobpulse.post_apply_hook.update_application_page", return_value=True)
def test_full_hook_flow(mock_notion, mock_cl_upload, mock_cv_upload,
                        mock_result, job_context, tmp_dbs):
    post_apply_hook(
        result=mock_result,
        job_context=job_context,
        form_exp_db_path=tmp_dbs["form_exp_db"],
    )

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
    assert call_kwargs["company"] == "TestCorp"
    assert call_kwargs["follow_up_date"] is not None


@patch("jobpulse.post_apply_hook.upload_cv", return_value=None)
@patch("jobpulse.post_apply_hook.upload_cover_letter", return_value=None)
@patch("jobpulse.post_apply_hook.update_application_page", return_value=True)
def test_hook_tolerates_drive_failure(mock_notion, mock_cl, mock_cv,
                                      mock_result, job_context, tmp_dbs):
    """Drive upload failure should not prevent Notion update."""
    post_apply_hook(
        result=mock_result,
        job_context=job_context,
        form_exp_db_path=tmp_dbs["form_exp_db"],
    )
    mock_notion.assert_called_once()
    call_kwargs = mock_notion.call_args[1]
    assert call_kwargs["cv_drive_link"] is None
    assert call_kwargs["cl_drive_link"] is None


@patch("jobpulse.post_apply_hook.upload_cv", return_value="https://drive.google.com/cv")
@patch("jobpulse.post_apply_hook.upload_cover_letter", return_value=None)
@patch("jobpulse.post_apply_hook.update_application_page", return_value=True)
def test_hook_skips_notion_when_no_page_id(mock_notion, mock_cl, mock_cv,
                                            mock_result, job_context, tmp_dbs):
    job_context["notion_page_id"] = None
    post_apply_hook(
        result=mock_result,
        job_context=job_context,
        form_exp_db_path=tmp_dbs["form_exp_db"],
    )
    mock_notion.assert_not_called()


def test_hook_records_form_experience(mock_result, job_context, tmp_dbs):
    with patch("jobpulse.post_apply_hook.upload_cv", return_value=None), \
         patch("jobpulse.post_apply_hook.upload_cover_letter", return_value=None), \
         patch("jobpulse.post_apply_hook.update_application_page", return_value=True):
        post_apply_hook(
            result=mock_result,
            job_context=job_context,
            form_exp_db_path=tmp_dbs["form_exp_db"],
        )

    from jobpulse.form_experience_db import FormExperienceDB
    db = FormExperienceDB(db_path=tmp_dbs["form_exp_db"])
    exp = db.lookup("boards.greenhouse.io")
    assert exp is not None
    assert exp["platform"] == "greenhouse"
    assert exp["pages_filled"] == 3
    assert json.loads(exp["field_types"]) == ["text", "select", "upload"]


def test_hook_no_op_on_failed_result(job_context, tmp_dbs):
    """Hook does nothing if result.success is False."""
    with patch("jobpulse.post_apply_hook.upload_cv") as mock_cv, \
         patch("jobpulse.post_apply_hook.update_application_page") as mock_notion:
        post_apply_hook(
            result={"success": False, "error": "CAPTCHA"},
            job_context=job_context,
            form_exp_db_path=tmp_dbs["form_exp_db"],
        )
    mock_cv.assert_not_called()
    mock_notion.assert_not_called()
