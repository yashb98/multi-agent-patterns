"""Tests for Workday ATS parser — parser and detection only, no HTTP."""
import pytest


class TestDetectWorkday:
    def test_detects_workday_url(self):
        from jobpulse.ats_api_scanner import detect_ats_provider

        provider, slug = detect_ats_provider(
            "https://acme.wd3.myworkdayjobs.com/en-US/acme_careers/job/London/Data-Analyst_R12345"
        )
        assert provider == "workday"
        assert slug == "acme"

    def test_detects_workday_alt_shard(self):
        from jobpulse.ats_api_scanner import detect_ats_provider

        provider, slug = detect_ats_provider(
            "https://bigcorp.wd1.myworkdayjobs.com/BigCorpJobs"
        )
        assert provider == "workday"
        assert slug == "bigcorp"


class TestParseWorkday:
    def test_parses_jobs(self):
        from jobpulse.ats_api_scanner import parse_workday

        data = {
            "jobPostings": [
                {
                    "title": "Data Scientist",
                    "externalPath": "/en-US/jobs/job/London/Data-Scientist_R001",
                    "locationsText": "London, UK",
                    "postedOn": "Posted 3 Days Ago",
                },
                {
                    "title": "ML Engineer",
                    "externalPath": "/en-US/jobs/job/Remote/ML-Engineer_R002",
                    "locationsText": "Remote",
                    "postedOn": "Posted 7 Days Ago",
                },
            ]
        }
        result = parse_workday(data, "Acme", "acme.wd3.myworkdayjobs.com", "acme_careers")
        assert len(result) == 2
        assert result[0]["title"] == "Data Scientist"
        assert result[0]["company"] == "Acme"
        assert result[0]["location"] == "London, UK"
        assert result[0]["platform"] == "workday"
        assert "acme.wd3.myworkdayjobs.com" in result[0]["url"]

    def test_empty_response(self):
        from jobpulse.ats_api_scanner import parse_workday

        assert parse_workday({}, "Acme", "x.wd1.myworkdayjobs.com", "jobs") == []
        assert parse_workday({"jobPostings": []}, "Acme", "x.wd1.myworkdayjobs.com", "jobs") == []
