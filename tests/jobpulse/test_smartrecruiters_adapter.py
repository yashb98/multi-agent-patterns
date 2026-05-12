"""Tests for SmartRecruiters thin platform strategy."""
import pytest
from jobpulse.ats_adapters.smartrecruiters import SmartRecruitersStrategy


class TestSmartRecruitersStrategy:
    def test_detect_oneclick_url(self):
        strategy = SmartRecruitersStrategy()
        assert strategy.detect("https://jobs.smartrecruiters.com/esureGroup/744000106347626")

    def test_detect_company_url(self):
        strategy = SmartRecruitersStrategy()
        assert strategy.detect("https://jobs.smartrecruiters.com/oneclick-ui/company/esureGroup/publication/829c279f")

    def test_reject_other_ats(self):
        strategy = SmartRecruitersStrategy()
        assert not strategy.detect("https://boards.greenhouse.io/company/jobs/123")
        assert not strategy.detect("https://linkedin.com/jobs/view/123")

    def test_name(self):
        assert SmartRecruitersStrategy.name == "smartrecruiters"

    def test_min_page_time(self):
        assert SmartRecruitersStrategy().min_page_time == 5.0


class TestAdapterRegistry:
    def test_get_adapter_returns_playwright(self):
        from jobpulse.ats_adapters import get_adapter
        from jobpulse.playwright_adapter import PlaywrightAdapter

        assert isinstance(get_adapter(), PlaywrightAdapter)

    def test_strategy_registry_has_smartrecruiters(self):
        from jobpulse.ats_adapters.strategy import get_strategy

        strategy = get_strategy("smartrecruiters")
        assert strategy.name == "smartrecruiters"
