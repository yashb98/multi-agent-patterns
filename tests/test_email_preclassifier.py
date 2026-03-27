"""Tests for email pre-classifier — rule engine, confidence, evidence attribution, audit, graduation."""

import os
import pytest
from unittest.mock import patch, MagicMock

os.environ["JOBPULSE_TEST_MODE"] = "1"


class TestSenderOtherRules:
    """Tier 1A: Obvious OTHER emails by sender pattern."""

    def test_noreply_classified_as_other(self):
        from jobpulse.email_preclassifier import preclassify
        result = preclassify("noreply@company.com", "Your weekly update", "")
        assert result.category == "OTHER"
        assert result.confidence >= 0.9
        assert result.evidence["rule_name"] == "noreply_sender"

    def test_newsletter_domain_classified_as_other(self):
        from jobpulse.email_preclassifier import preclassify
        result = preclassify("hello@substack.com", "New post from blog", "")
        assert result.category == "OTHER"
        assert result.confidence >= 0.9

    def test_marketing_sender_classified_as_other(self):
        from jobpulse.email_preclassifier import preclassify
        result = preclassify("marketing@store.com", "50% off sale", "Buy now")
        assert result.category == "OTHER"
        assert result.confidence >= 0.9

    def test_mailer_daemon_classified_as_other(self):
        from jobpulse.email_preclassifier import preclassify
        result = preclassify("mailer-daemon@server.com", "Delivery failure", "")
        assert result.category == "OTHER"
        assert result.confidence >= 0.95


class TestSubjectOtherRules:
    """Tier 1A: Obvious OTHER emails by subject pattern."""

    def test_order_confirmation_classified_as_other(self):
        from jobpulse.email_preclassifier import preclassify
        result = preclassify("orders@amazon.co.uk", "Order confirmation #123", "")
        assert result.category == "OTHER"
        assert result.confidence >= 0.9

    def test_verify_email_classified_as_other(self):
        from jobpulse.email_preclassifier import preclassify
        result = preclassify("auth@service.com", "Verify your email address", "")
        assert result.category == "OTHER"
        assert result.confidence >= 0.9


class TestRecruiterHintRules:
    """Tier 1B: Likely recruiter — still goes to LLM but with hint."""

    def test_ats_domain_flagged_as_recruiter(self):
        from jobpulse.email_preclassifier import preclassify
        result = preclassify("no-reply@greenhouse.io", "Application update", "")
        assert result.category is None  # goes to LLM
        assert result.likely_recruiter is True
        assert "greenhouse_ats" in result.evidence["rule_name"]

    def test_recruiter_sender_keyword(self):
        from jobpulse.email_preclassifier import preclassify
        result = preclassify("talent@bigcorp.com", "Exciting role", "")
        assert result.likely_recruiter is True

    def test_interview_subject_flagged(self):
        from jobpulse.email_preclassifier import preclassify
        result = preclassify("jane@company.com", "Interview scheduling", "")
        assert result.likely_recruiter is True


class TestDualMatchRules:
    """Tier 1C/1D: REJECTED and SELECTED by dual subject+body match."""

    def test_rejection_dual_match(self):
        from jobpulse.email_preclassifier import preclassify
        result = preclassify(
            "hr@company.com",
            "Unfortunately about your application",
            "We have decided to move forward with other candidates"
        )
        assert result.category == "REJECTED"
        assert result.confidence >= 0.9
        assert len(result.evidence["matched_patterns"]) == 2

    def test_rejection_single_keyword_passes_through(self):
        from jobpulse.email_preclassifier import preclassify
        result = preclassify(
            "hr@company.com",
            "Unfortunately we need to reschedule",
            "Can we find another time?"
        )
        # Single keyword in subject but not body — should not auto-reject
        # hr@ triggers recruiter hint instead
        assert result.category != "REJECTED"

    def test_selected_dual_match(self):
        from jobpulse.email_preclassifier import preclassify
        result = preclassify(
            "hr@company.com",
            "Congratulations on your application",
            "We'd like to invite you to the next round"
        )
        assert result.category == "SELECTED_NEXT_ROUND"
        assert result.confidence >= 0.85

    def test_selected_single_keyword_passes_through(self):
        from jobpulse.email_preclassifier import preclassify
        result = preclassify(
            "friend@gmail.com",
            "Congratulations on your birthday!",
            "Hope you have a great day"
        )
        # "congratulations" in subject but no selection body pattern
        assert result.category != "SELECTED_NEXT_ROUND"


class TestEvidenceAttribution:
    """Every classification includes traceable evidence."""

    def test_evidence_has_required_fields(self):
        from jobpulse.email_preclassifier import preclassify
        result = preclassify("noreply@spam.com", "Weekly deals", "")
        assert "rule_name" in result.evidence
        assert "matched_patterns" in result.evidence
        assert "reasoning" in result.evidence

    def test_passthrough_has_empty_evidence(self):
        from jobpulse.email_preclassifier import preclassify
        result = preclassify("john@personalmail.com", "Quick question", "Hey, how are you?")
        assert result.category is None
        assert result.confidence == 0.0


class TestConfidenceThresholds:
    """Confidence scoring determines action."""

    def test_high_confidence_skips_llm(self):
        from jobpulse.email_preclassifier import preclassify
        result = preclassify("noreply@company.com", "Your receipt", "")
        assert result.confidence >= 0.9
        assert result.skip_llm is True

    def test_low_confidence_goes_to_llm(self):
        from jobpulse.email_preclassifier import preclassify
        result = preclassify("john@company.com", "Following up", "Wanted to check in")
        assert result.confidence < 0.6
        assert result.skip_llm is False

    def test_ambiguous_email_not_classified(self):
        from jobpulse.email_preclassifier import preclassify
        result = preclassify("unknown@newdomain.xyz", "Hello there", "Some random content")
        assert result.skip_llm is False


class TestAdaptiveAuditDecay:
    """Audit rate decreases as system learns."""

    def test_initial_audit_rate_is_50_percent(self):
        from jobpulse.email_preclassifier import get_audit_rate
        with patch("jobpulse.db.get_preclassifier_state", return_value={"total_processed": 0}):
            assert get_audit_rate() == 0.50

    def test_calibrating_audit_rate_is_30_percent(self):
        from jobpulse.email_preclassifier import get_audit_rate
        with patch("jobpulse.db.get_preclassifier_state", return_value={"total_processed": 200}):
            assert get_audit_rate() == 0.30

    def test_tuning_audit_rate_is_20_percent(self):
        from jobpulse.email_preclassifier import get_audit_rate
        with patch("jobpulse.db.get_preclassifier_state", return_value={"total_processed": 700}):
            assert get_audit_rate() == 0.20

    def test_stable_audit_rate_is_10_percent(self):
        from jobpulse.email_preclassifier import get_audit_rate
        with patch("jobpulse.db.get_preclassifier_state", return_value={"total_processed": 1500}):
            assert get_audit_rate() == 0.10

    def test_mid_confidence_always_audited(self):
        from jobpulse.email_preclassifier import should_audit, PreClassification
        pre = PreClassification(category="OTHER", confidence=0.75)
        assert should_audit(pre) is True

    def test_low_confidence_not_audited(self):
        from jobpulse.email_preclassifier import should_audit, PreClassification
        pre = PreClassification(category=None, confidence=0.3)
        assert should_audit(pre) is False


class TestAutoGraduation:
    """System exits learning phase when accuracy > 95%."""

    def test_does_not_graduate_with_few_emails(self):
        from jobpulse.email_preclassifier import check_graduation
        with patch("jobpulse.db.get_preclassifier_state",
                   return_value={"graduated": 0, "total_processed": 50, "total_audited": 10}):
            assert check_graduation() is False

    def test_does_not_graduate_with_few_audits(self):
        from jobpulse.email_preclassifier import check_graduation
        with patch("jobpulse.db.get_preclassifier_state",
                   return_value={"graduated": 0, "total_processed": 200, "total_audited": 5}):
            assert check_graduation() is False

    def test_graduates_with_high_accuracy(self):
        from jobpulse.email_preclassifier import check_graduation
        with patch("jobpulse.db.get_preclassifier_state",
                   return_value={"graduated": 0, "total_processed": 200, "total_audited": 30}), \
             patch("jobpulse.db.get_audit_accuracy", return_value=0.97), \
             patch("jobpulse.db.update_preclassifier_state"):
            assert check_graduation() is True

    def test_does_not_graduate_with_low_accuracy(self):
        from jobpulse.email_preclassifier import check_graduation
        with patch("jobpulse.db.get_preclassifier_state",
                   return_value={"graduated": 0, "total_processed": 200, "total_audited": 30}), \
             patch("jobpulse.db.get_audit_accuracy", return_value=0.85):
            assert check_graduation() is False

    def test_already_graduated_returns_true(self):
        from jobpulse.email_preclassifier import check_graduation
        with patch("jobpulse.db.get_preclassifier_state",
                   return_value={"graduated": 1, "total_processed": 500, "total_audited": 100}):
            assert check_graduation() is True
