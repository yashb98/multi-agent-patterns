import asyncio
from unittest.mock import AsyncMock, patch

import pytest


class TestNormalizeToJobListing:
    def test_normalizes_basic_fields(self):
        from jobpulse.job_scanners.google_jobs import normalize_to_job_listing

        row = {
            "title": "Data Scientist",
            "company": "Acme Corp",
            "location": "London, UK",
            "description": "Build ML models",
            "url": "https://example.com/job/123",
            "date_posted": "2026-04-14",
        }
        result = normalize_to_job_listing(row)
        assert result["title"] == "Data Scientist"
        assert result["company"] == "Acme Corp"
        assert result["source"] == "google_jobs"
        assert result["url"] == "https://example.com/job/123"
        assert result["apply_url"] == "https://example.com/job/123"

    def test_handles_missing_fields(self):
        from jobpulse.job_scanners.google_jobs import normalize_to_job_listing

        row = {"title": "Engineer", "company": "Co"}
        result = normalize_to_job_listing(row)
        assert result["title"] == "Engineer"
        assert result["location"] == ""
        assert result["description"] == ""


class TestScanGoogleJobs:
    def test_disabled_via_env(self, monkeypatch):
        from jobpulse.job_scanners.google_jobs import scan_google_jobs

        monkeypatch.setenv("GOOGLE_JOBS_ENABLED", "false")
        results = scan_google_jobs(["test"], "London")
        assert results == []

    def test_enabled_returns_normalized(self, monkeypatch):
        from jobpulse.job_scanners.google_jobs import scan_google_jobs

        monkeypatch.setenv("GOOGLE_JOBS_ENABLED", "true")
        raw = [
            {"title": "Dev", "company": "Co", "location": "London",
             "description": "dev work", "url": "https://x.com/1", "date_posted": "2 days ago"},
        ]

        async def fake_scan(*args, **kwargs):
            return raw

        with patch("jobpulse.job_scanners.google_jobs._scan_google_jobs_async", side_effect=fake_scan):
            results = scan_google_jobs(["developer"], "London")

        assert len(results) == 1
        assert results[0]["source"] == "google_jobs"
        assert results[0]["platform"] == "google_jobs"
        assert results[0]["title"] == "Dev"

    def test_returns_normalized_list(self, monkeypatch):
        from jobpulse.job_scanners.google_jobs import scan_google_jobs

        monkeypatch.setenv("GOOGLE_JOBS_ENABLED", "true")
        raw = [
            {"title": "ML Engineer", "company": "BigCo", "location": "London",
             "description": "ML work", "url": "https://example.com/1", "date_posted": "1 day ago"},
        ]

        async def fake_scan(*args, **kwargs):
            return raw

        with patch("jobpulse.job_scanners.google_jobs._scan_google_jobs_async", side_effect=fake_scan):
            results = scan_google_jobs(["machine learning"], "London")

        assert len(results) == 1
        assert results[0]["title"] == "ML Engineer"
        assert results[0]["url"] == "https://example.com/1"
