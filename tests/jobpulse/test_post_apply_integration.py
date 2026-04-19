"""Integration test: apply_job → post_apply_hook → FormExperienceDB + Notion."""
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from jobpulse.form_experience_db import FormExperienceDB


@pytest.fixture
def exp_db(tmp_path):
    return FormExperienceDB(db_path=str(tmp_path / "form_exp.db"))


@patch("jobpulse.applicator._call_fill_and_submit")
@patch("jobpulse.post_apply_hook.upload_cv", return_value="https://drive.google.com/cv")
@patch("jobpulse.post_apply_hook.upload_cover_letter", return_value="https://drive.google.com/cl")
@patch("jobpulse.post_apply_hook.update_application_page", return_value=True)
def test_apply_job_triggers_hook(mock_notion, mock_cl_up, mock_cv_up, mock_fill, tmp_path):
    """apply_job with job_context triggers the full post-apply chain."""
    mock_fill.return_value = {
        "success": True,
        "pages_filled": 2,
        "field_types": ["text", "select"],
        "screening_questions": ["Salary expectation?"],
    }

    # Patch rate limiter to allow the apply (RateLimiter is imported locally inside apply_job)
    with patch("jobpulse.rate_limiter.RateLimiter") as MockLimiter:
        limiter = MockLimiter.return_value
        limiter.can_apply.return_value = True
        limiter.get_remaining.return_value = {"linkedin": 10}
        limiter.get_total_today.return_value = 1
        limiter.should_take_break.return_value = False
        limiter.get_platform_count.return_value = 1

        # Patch the anti-detection sleep to avoid slow test
        with patch("jobpulse.applicator.time.sleep"):
            from jobpulse.applicator import apply_job

            # Patch the form experience DB path inside the hook
            with patch("jobpulse.post_apply_hook.FormExperienceDB") as MockExpDB:
                mock_db_instance = MagicMock()
                MockExpDB.return_value = mock_db_instance

                result = apply_job(
                    url="https://boards.greenhouse.io/testcorp/jobs/123",
                    ats_platform="greenhouse",
                    cv_path=Path("/tmp/test_cv.pdf"),
                    cover_letter_path=Path("/tmp/test_cl.pdf"),
                    job_context={
                        "job_id": "test-123",
                        "company": "TestCorp",
                        "title": "ML Engineer",
                        "notion_page_id": "notion-abc",
                        "match_tier": "auto",
                        "ats_score": 97.0,
                        "matched_projects": ["proj1"],
                    },
                )

    assert result["success"] is True

    # Verify hook ran: Drive upload called
    mock_cv_up.assert_called_once()
    mock_cl_up.assert_called_once()

    # Verify hook ran: Notion updated
    mock_notion.assert_called_once()
    notion_kwargs = mock_notion.call_args[1]
    assert notion_kwargs["status"] == "Applied"
    assert notion_kwargs["cv_drive_link"] == "https://drive.google.com/cv"

    # Verify hook ran: form experience recorded
    mock_db_instance.record.assert_called_once()
    record_kwargs = mock_db_instance.record.call_args[1]
    assert record_kwargs["platform"] == "greenhouse"
    assert record_kwargs["pages_filled"] == 2
