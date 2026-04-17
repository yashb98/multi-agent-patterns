"""Tests for follow-up draft generation (F8 extension)."""
import pytest
from datetime import date


class TestGenerateDraft:
    def test_generates_email_draft(self):
        from jobpulse.followup_tracker import generate_followup_draft

        draft = generate_followup_draft(
            company="Anthropic",
            role="ML Engineer",
            status="Applied",
            followup_count=0,
            channel="email",
        )
        assert "Anthropic" in draft
        assert "ML Engineer" in draft
        assert len(draft) > 50

    def test_second_followup_differs(self):
        from jobpulse.followup_tracker import generate_followup_draft

        draft1 = generate_followup_draft("Co", "Role", "Applied", 0, "email")
        draft2 = generate_followup_draft("Co", "Role", "Applied", 1, "email")
        assert draft1 != draft2

    def test_linkedin_is_shorter(self):
        from jobpulse.followup_tracker import generate_followup_draft

        email = generate_followup_draft("Co", "Role", "Applied", 0, "email")
        linkedin = generate_followup_draft("Co", "Role", "Applied", 0, "linkedin")
        assert len(linkedin) <= 300
