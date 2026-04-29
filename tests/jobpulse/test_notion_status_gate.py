"""Tests for the Notion status gate — verify jobs are checked against Notion before form fill."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def _mock_notion_api(monkeypatch):
    """Patch notion_api so no real Notion calls are made."""
    mock = MagicMock()
    monkeypatch.setattr("jobpulse.job_notion_sync._notion_api", mock)
    return mock


class TestGetNotionPageStatus:
    def test_returns_found_status(self, _mock_notion_api):
        from jobpulse.job_notion_sync import get_notion_page_status

        _mock_notion_api.return_value = {
            "properties": {
                "Status": {"status": {"name": "Found"}},
            },
        }
        assert get_notion_page_status("page-123") == "Found"
        _mock_notion_api.assert_called_once_with("GET", "/pages/page-123")

    def test_returns_applied_status(self, _mock_notion_api):
        from jobpulse.job_notion_sync import get_notion_page_status

        _mock_notion_api.return_value = {
            "properties": {
                "Status": {"status": {"name": "Applied"}},
            },
        }
        assert get_notion_page_status("page-456") == "Applied"

    def test_returns_none_on_empty_page_id(self, _mock_notion_api):
        from jobpulse.job_notion_sync import get_notion_page_status

        assert get_notion_page_status("") is None
        _mock_notion_api.assert_not_called()

    def test_returns_none_on_api_failure(self, _mock_notion_api):
        from jobpulse.job_notion_sync import get_notion_page_status

        _mock_notion_api.return_value = {}
        assert get_notion_page_status("page-789") is None

    def test_returns_none_when_status_missing(self, _mock_notion_api):
        from jobpulse.job_notion_sync import get_notion_page_status

        _mock_notion_api.return_value = {"properties": {}}
        assert get_notion_page_status("page-abc") is None


class TestApproveJobsNotionGate:
    """Verify approve_jobs() blocks jobs whose Notion status is not 'Found'."""

    @pytest.fixture(autouse=True)
    def _patch_dependencies(self, monkeypatch, tmp_path):
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        monkeypatch.setenv("JOB_DB_PATH", str(tmp_path / "jobs.db"))
        monkeypatch.setenv("DATA_DIR", str(tmp_path))

    def test_blocks_applied_status(self, monkeypatch):
        from jobpulse import job_autopilot

        pending = [{"job_id": "j1", "title": "ML Eng", "company": "Acme", "platform": "linkedin", "notion_page_id": "notion-page-1", "url": "https://example.com/jobs/1"}]
        monkeypatch.setattr(job_autopilot, "_load_actionable_pending", lambda: pending)

        fake_app = {"job_id": "j1", "notion_page_id": "notion-page-1", "cv_path": "/tmp/cv.pdf"}
        mock_db = MagicMock()
        mock_db.get_application.return_value = fake_app
        monkeypatch.setattr(job_autopilot, "JobDB", lambda: mock_db)

        monkeypatch.setattr(
            job_autopilot,
            "get_notion_page_status",
            lambda page_id: "Applied",
        )

        result = job_autopilot.approve_jobs("1")
        assert "Skipped" in result
        assert 'Notion status is "Applied"' in result

    def test_allows_found_status(self, monkeypatch):
        from jobpulse import job_autopilot

        pending = [{"job_id": "j2", "title": "DS", "company": "Beta", "platform": "reed", "notion_page_id": "notion-page-2", "url": "https://reed.co.uk/jobs/123"}]
        monkeypatch.setattr(job_autopilot, "_load_actionable_pending", lambda: pending)

        fake_app = {"job_id": "j2", "notion_page_id": "notion-page-2", "cv_path": "/tmp/cv.pdf"}
        fake_listing = {"url": "https://reed.co.uk/jobs/123", "ats_platform": "reed", "location": "London"}
        mock_db = MagicMock()
        mock_db.get_application.return_value = fake_app
        mock_db.get_application_by_notion_page_id.return_value = fake_app
        mock_db.get_listing.return_value = fake_listing
        monkeypatch.setattr(job_autopilot, "JobDB", lambda: mock_db)

        monkeypatch.setattr(
            job_autopilot,
            "get_notion_page_status",
            lambda page_id: "Found",
        )

        mock_start = MagicMock(return_value={"started": True, "session_id": "s1"})
        monkeypatch.setattr(
            "jobpulse.live_review_applicator.start_live_review",
            mock_start,
        )
        monkeypatch.setattr(
            "jobpulse.application_materials.ensure_tailored_cv_for_job",
            lambda jid, db=None: None,
        )

        result = job_autopilot.approve_jobs("1")
        assert "Starting live review" in result
        mock_start.assert_called_once()

    def test_proceeds_without_notion_page(self, monkeypatch):
        """Jobs without a Notion page_id should still proceed (graceful degradation)."""
        from jobpulse import job_autopilot

        pending = [{"job_id": "j3", "title": "SWE", "company": "Gamma", "platform": "indeed", "url": "https://indeed.com/jobs/456"}]
        monkeypatch.setattr(job_autopilot, "_load_actionable_pending", lambda: pending)

        fake_app = {"job_id": "j3", "notion_page_id": None, "cv_path": "/tmp/cv.pdf"}
        fake_listing = {"url": "https://indeed.com/jobs/456", "ats_platform": "indeed", "location": "UK"}
        mock_db = MagicMock()
        mock_db.get_application.return_value = fake_app
        mock_db.get_listing.return_value = fake_listing
        monkeypatch.setattr(job_autopilot, "JobDB", lambda: mock_db)

        mock_start = MagicMock(return_value={"started": True, "session_id": "s2"})
        monkeypatch.setattr(
            "jobpulse.live_review_applicator.start_live_review",
            mock_start,
        )
        monkeypatch.setattr(
            "jobpulse.application_materials.ensure_tailored_cv_for_job",
            lambda jid, db=None: None,
        )

        result = job_autopilot.approve_jobs("1")
        assert "Starting live review" in result
