"""Tests for rejection email parser."""

from __future__ import annotations

import pytest

from jobpulse.rejection_email_parser import RejectionEmailParser, get_rejection_parser


class TestRejectionEmailParser:
    def test_skill_gap_detection(self):
        parser = RejectionEmailParser()
        body = (
            "Thank you for your interest. We have decided to proceed with candidates "
            "who have stronger backgrounds in Kubernetes and GCP. We were impressed by "
            "your experience but need someone with deeper expertise in microservices."
        )
        result = parser.parse(body)
        assert result.blocker == "skill_gap"
        assert result.confidence > 0.3
        assert "Kubernetes" in result.skill_gaps or "GCP" in result.skill_gaps
        assert any("Add projects" in r for r in result.recommendations)

    def test_experience_mismatch(self):
        parser = RejectionEmailParser()
        body = (
            "We have decided to move forward with candidates who have 5+ years of "
            "experience in senior engineering roles. Your background is strong but we "
            "need someone with more extensive leadership experience."
        )
        result = parser.parse(body)
        assert result.blocker == "experience_mismatch"
        assert any("5" in r and "years" in r for r in result.recommendations)

    def test_salary_mismatch(self):
        parser = RejectionEmailParser()
        body = (
            "Unfortunately, your salary expectations are outside our budget range for "
            "this position. We wish you the best in your search."
        )
        result = parser.parse(body)
        assert result.blocker == "salary_mismatch"
        assert result.escalate is True
        assert any("market rate" in r.lower() for r in result.recommendations)

    def test_visa_issue(self):
        parser = RejectionEmailParser()
        body = (
            "Thank you for applying. Unfortunately, we are unable to provide visa "
            "sponsorship for this role at this time."
        )
        result = parser.parse(body)
        assert result.blocker == "visa_issue"
        assert any("sponsor" in r.lower() for r in result.recommendations)

    def test_location_issue(self):
        parser = RejectionEmailParser()
        body = (
            "We have decided to proceed with local candidates only. The role requires "
            "you to be based in London and work on-site 3 days per week. We cannot "
            "consider remote applicants for this position."
        )
        result = parser.parse(body)
        assert result.blocker == "location_issue"

    def test_generic_rejection(self):
        parser = RejectionEmailParser()
        body = (
            "Thank you for your application. Unfortunately, we have decided to proceed with another "
            "candidate whose experience more closely matches our requirements."
        )
        result = parser.parse(body)
        assert result.blocker == "generic_rejection"

    def test_competition(self):
        parser = RejectionEmailParser()
        body = (
            "We received an exceptional number of highly qualified applicants for this "
            "position. This was a very difficult decision given the strong pool."
        )
        result = parser.parse(body)
        assert result.blocker == "competition"

    def test_ghosted(self):
        parser = RejectionEmailParser()
        body = (
            "This position is now closed. The requisition has been put on hold due to "
            "budget constraints."
        )
        result = parser.parse(body)
        assert result.blocker == "ghosted"

    def test_unclear(self):
        parser = RejectionEmailParser()
        body = "Hello, thanks for your email. Regards, HR"
        result = parser.parse(body)
        assert result.blocker == "unclear"
        assert result.confidence < 0.5

    def test_extracts_multiple_skills(self):
        parser = RejectionEmailParser()
        body = (
            "We need someone with stronger experience in TensorFlow, Kubernetes, "
            "and AWS Lambda. Your Python and Django background is good but not aligned."
        )
        result = parser.parse(body)
        assert "TensorFlow" in result.skill_gaps
        assert "Kubernetes" in result.skill_gaps

    def test_subject_included_in_analysis(self):
        parser = RejectionEmailParser()
        subject = "Application Update"
        body = "We have decided to proceed with another candidate."
        result = parser.parse(body, subject=subject)
        # Subject should not interfere with generic detection
        assert result.blocker == "generic_rejection"

    def test_singleton(self):
        p1 = get_rejection_parser()
        p2 = get_rejection_parser()
        assert p1 is p2

    def test_escalation_for_multiple_skill_gaps(self):
        parser = RejectionEmailParser()
        body = (
            "We need stronger experience with Python, Kubernetes, AWS, Terraform, "
            "and CI/CD pipelines. Your background doesn't match our requirements."
        )
        result = parser.parse(body)
        assert result.escalate is True
        assert len(result.skill_gaps) >= 3

    def test_recommendations_for_each_blocker(self):
        parser = RejectionEmailParser()
        for blocker_type, body in [
            ("skill_gap", "Need stronger background in Python and Kubernetes."),
            ("experience_mismatch", "Need 5+ years of experience."),
            ("salary_mismatch", "Salary expectations outside budget."),
            ("visa_issue", "Cannot provide visa sponsorship."),
            ("location_issue", "Need local candidates on-site."),
            ("competition", "Exceptional number of qualified applicants."),
            ("generic_rejection", "Proceeding with another candidate."),
            ("ghosted", "Position is closed."),
        ]:
            result = parser.parse(body)
            assert len(result.recommendations) >= 1, f"{blocker_type} has no recommendations"
