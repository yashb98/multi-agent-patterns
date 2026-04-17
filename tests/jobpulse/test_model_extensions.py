"""Verify new JobListing fields are Optional with safe defaults."""
import pytest
from datetime import datetime


class TestJobListingNewFields:
    def test_new_fields_default_to_none(self):
        from jobpulse.models.application_models import JobListing

        listing = JobListing(
            job_id="test123",
            title="Data Analyst",
            company="TestCo",
            platform="reed",
            url="https://example.com/job/1",
            description_raw="Test JD",
            location="London",
            found_at=datetime.utcnow(),
        )
        assert listing.ghost_tier is None
        assert listing.archetype is None
        assert listing.archetype_secondary is None
        assert listing.archetype_confidence == 0.0
        assert listing.locale_market is None
        assert listing.locale_language is None
        assert listing.posted_at is None

    def test_new_fields_accept_values(self):
        from jobpulse.models.application_models import JobListing

        listing = JobListing(
            job_id="test456",
            title="ML Engineer",
            company="AICo",
            platform="linkedin",
            url="https://example.com/job/2",
            description_raw="Build ML pipelines",
            location="Remote",
            found_at=datetime.utcnow(),
            ghost_tier="high_confidence",
            archetype="agentic",
            archetype_secondary="data_platform",
            archetype_confidence=0.92,
            locale_market="uk",
            locale_language="en",
            posted_at="2026-04-15T10:00:00Z",
        )
        assert listing.archetype == "agentic"
        assert listing.archetype_confidence == 0.92
        assert listing.ghost_tier == "high_confidence"
