"""
Tests for jobpulse/ats_api_scanner.py — parsers and provider detection.
No real HTTP calls needed.
"""

import pytest

from jobpulse.ats_api_scanner import (
    detect_ats_provider,
    parse_greenhouse,
    parse_ashby,
    parse_lever,
)


class TestParseGreenhouse:
    def test_parses_jobs(self):
        data = {
            "jobs": [
                {
                    "title": "ML Engineer",
                    "absolute_url": "https://boards.greenhouse.io/acme/jobs/1",
                    "location": {"name": "London, UK"},
                },
                {
                    "title": "Data Scientist",
                    "absolute_url": "https://boards.greenhouse.io/acme/jobs/2",
                    "location": {"name": "Remote"},
                },
            ]
        }
        result = parse_greenhouse(data, "Acme")
        assert len(result) == 2
        assert result[0] == {
            "title": "ML Engineer",
            "url": "https://boards.greenhouse.io/acme/jobs/1",
            "company": "Acme",
            "location": "London, UK",
            "platform": "greenhouse",
        }
        assert result[1]["location"] == "Remote"

    def test_empty_jobs(self):
        assert parse_greenhouse({"jobs": []}, "Acme") == []
        assert parse_greenhouse({}, "Acme") == []


class TestParseAshby:
    def test_parses_jobs(self):
        data = {
            "jobs": [
                {
                    "title": "Backend Engineer",
                    "jobUrl": "https://jobs.ashbyhq.com/beta/12345",
                    "location": "New York, NY",
                },
            ]
        }
        result = parse_ashby(data, "Beta Corp")
        assert len(result) == 1
        assert result[0] == {
            "title": "Backend Engineer",
            "url": "https://jobs.ashbyhq.com/beta/12345",
            "company": "Beta Corp",
            "location": "New York, NY",
            "platform": "ashby",
        }


class TestParseLever:
    def test_parses_jobs(self):
        data = [
            {
                "text": "Product Manager",
                "hostedUrl": "https://jobs.lever.co/gamma/abc123",
                "applyUrl": "https://jobs.lever.co/gamma/abc123/apply",
                "categories": {"location": "San Francisco, CA"},
            }
        ]
        result = parse_lever(data, "Gamma")
        assert len(result) == 1
        assert result[0] == {
            "title": "Product Manager",
            "url": "https://jobs.lever.co/gamma/abc123",
            "company": "Gamma",
            "location": "San Francisco, CA",
            "platform": "lever",
        }

    def test_falls_back_to_apply_url(self):
        data = [
            {
                "text": "DevOps Engineer",
                "hostedUrl": "",
                "applyUrl": "https://jobs.lever.co/delta/xyz/apply",
                "categories": {"location": "Remote"},
            }
        ]
        result = parse_lever(data, "Delta")
        assert result[0]["url"] == "https://jobs.lever.co/delta/xyz/apply"


class TestDetectAtsProvider:
    def test_greenhouse(self):
        provider, slug = detect_ats_provider("https://boards.greenhouse.io/acme/jobs/123")
        assert provider == "greenhouse"
        assert slug == "acme"

    def test_greenhouse_eu(self):
        provider, slug = detect_ats_provider("https://boards.eu.greenhouse.io/acme/jobs/456")
        assert provider == "greenhouse"
        assert slug == "acme"

    def test_ashby(self):
        provider, slug = detect_ats_provider("https://jobs.ashbyhq.com/betacorp/posting/789")
        assert provider == "ashby"
        assert slug == "betacorp"

    def test_lever(self):
        provider, slug = detect_ats_provider("https://jobs.lever.co/gamma/abc123")
        assert provider == "lever"
        assert slug == "gamma"

    def test_unknown(self):
        provider, slug = detect_ats_provider("https://careers.example.com/jobs")
        assert provider is None
        assert slug is None
