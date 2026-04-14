import pytest


class TestNormalizeToJobListing:
    def test_normalizes_basic_fields(self):
        from jobpulse.job_scanners.google_jobs import normalize_to_job_listing

        row = {
            "title": "Data Scientist",
            "company": "Acme Corp",
            "location": "London, UK",
            "description": "Build ML models",
            "job_url": "https://example.com/job/123",
            "date_posted": "2026-04-14",
        }
        result = normalize_to_job_listing(row)
        assert result["title"] == "Data Scientist"
        assert result["company"] == "Acme Corp"
        assert result["source"] == "google_jobs"
        assert result["url"] == "https://example.com/job/123"

    def test_handles_missing_fields(self):
        from jobpulse.job_scanners.google_jobs import normalize_to_job_listing

        row = {"title": "Engineer", "company": "Co"}
        result = normalize_to_job_listing(row)
        assert result["title"] == "Engineer"
        assert result["location"] == ""
        assert result["description"] == ""


class TestScanGoogleJobs:
    def test_disabled_by_default(self, monkeypatch):
        from jobpulse.job_scanners.google_jobs import scan_google_jobs

        monkeypatch.delenv("GOOGLE_JOBS_ENABLED", raising=False)
        results = scan_google_jobs(["test"], "London")
        assert results == []

    def test_enabled_via_env(self, monkeypatch):
        import pandas as pd
        from jobpulse.job_scanners.google_jobs import scan_google_jobs

        monkeypatch.setenv("GOOGLE_JOBS_ENABLED", "true")
        mock_df = pd.DataFrame([
            {"title": "Dev", "company": "Co", "location": "London",
             "description": "dev work", "job_url": "https://x.com/1", "date_posted": "2026-04-14"},
        ])
        monkeypatch.setattr("jobpulse.job_scanners.google_jobs.scrape_jobs", lambda **kw: mock_df)

        results = scan_google_jobs(["developer"], "London")
        assert len(results) == 1
        assert results[0]["source"] == "google_jobs"

    def test_returns_list_with_mocked_jobspy(self, monkeypatch):
        import pandas as pd
        from jobpulse.job_scanners.google_jobs import scan_google_jobs

        monkeypatch.setenv("GOOGLE_JOBS_ENABLED", "true")
        mock_df = pd.DataFrame([
            {"title": "ML Engineer", "company": "BigCo", "location": "London",
             "description": "ML work", "job_url": "https://example.com/1", "date_posted": "2026-04-14"},
        ])
        monkeypatch.setattr("jobpulse.job_scanners.google_jobs.scrape_jobs", lambda **kw: mock_df)

        results = scan_google_jobs(["machine learning"], "London")
        assert len(results) == 1
        assert results[0]["title"] == "ML Engineer"
