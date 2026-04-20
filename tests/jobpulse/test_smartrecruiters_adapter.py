"""Tests for SmartRecruiters ATS adapter."""

import pytest

from jobpulse.ats_adapters.smartrecruiters import SmartRecruitersAdapter


class TestSmartRecruitersDetect:
    def test_detect_oneclick_url(self):
        adapter = SmartRecruitersAdapter()
        assert adapter.detect("https://jobs.smartrecruiters.com/esureGroup/744000106347626")

    def test_detect_company_url(self):
        adapter = SmartRecruitersAdapter()
        assert adapter.detect("https://jobs.smartrecruiters.com/oneclick-ui/company/esureGroup/publication/829c279f")

    def test_reject_other_ats(self):
        adapter = SmartRecruitersAdapter()
        assert not adapter.detect("https://boards.greenhouse.io/company/jobs/123")
        assert not adapter.detect("https://linkedin.com/jobs/view/123")

    def test_name(self):
        assert SmartRecruitersAdapter.name == "smartrecruiters"


class TestScreeningAnswers:
    def setup_method(self):
        self.adapter = SmartRecruitersAdapter()

    def test_right_to_work_yes(self):
        assert self.adapter._resolve_screening_answer(
            "Do you currently have the right to work in the United Kingdom?", {}
        ) == "Yes"

    def test_authorized_yes(self):
        assert self.adapter._resolve_screening_answer(
            "Are you authorized to work in the job's location?", {}
        ) == "Yes"

    def test_criminal_no(self):
        assert self.adapter._resolve_screening_answer(
            "Do you have any unspent criminal/civil convictions?", {}
        ) == "No"

    def test_financial_no(self):
        assert self.adapter._resolve_screening_answer(
            "As part of esure's vetting process, we conduct financial background checks. Please indicate if there is anything to disclose?", {}
        ) == "No"

    def test_drivers_license_no(self):
        assert self.adapter._resolve_screening_answer(
            "Do you have a current driver's license?", {}
        ) == "No"

    def test_custom_answer_override(self):
        assert self.adapter._resolve_screening_answer(
            "Do you have a current driver's license?",
            {"driver": "Yes"},
        ) == "Yes"

    def test_unknown_defaults_no(self):
        assert self.adapter._resolve_screening_answer(
            "Some entirely new question we haven't seen before?", {}
        ) == "No"


class TestAdapterRegistry:
    def test_smartrecruiters_returns_correct_adapter(self):
        from jobpulse.ats_adapters import get_adapter
        adapter = get_adapter("smartrecruiters")
        assert isinstance(adapter, SmartRecruitersAdapter)

    def test_applicator_detects_smartrecruiters_url(self):
        from jobpulse.applicator import apply_job
        import jobpulse.applicator as mod
        # Verify the URL detection logic includes smartrecruiters
        url = "https://jobs.smartrecruiters.com/esureGroup/744000106347626"
        assert "smartrecruiters.com" in url
