"""Tests for Gate 0 title relevance filter."""

import pytest


@pytest.fixture
def search_config():
    return {
        "titles": [
            "Graduate Data Scientist",
            "Junior ML Engineer",
            "Junior Software Engineer",
            "Data Science Intern",
        ],
        "exclude_keywords": [
            "senior", "lead", "principal", "staff", "10+ years",
            "8+ years", "7+ years", "5+ years", "director", "manager",
        ],
    }


class TestGate0:
    def test_matching_title_passes(self, search_config):
        from jobpulse.recruiter_screen import gate0_title_relevance
        assert gate0_title_relevance("Junior ML Engineer", "", search_config) is True

    def test_fuzzy_title_passes(self, search_config):
        from jobpulse.recruiter_screen import gate0_title_relevance
        assert gate0_title_relevance("ML Engineer - Graduate", "", search_config) is True

    def test_excluded_keyword_in_title_fails(self, search_config):
        from jobpulse.recruiter_screen import gate0_title_relevance
        assert gate0_title_relevance("Senior Data Scientist", "", search_config) is False

    def test_completely_unrelated_fails(self, search_config):
        from jobpulse.recruiter_screen import gate0_title_relevance
        assert gate0_title_relevance("Marketing Manager", "", search_config) is False

    def test_exclude_keyword_in_jd_body_fails(self, search_config):
        from jobpulse.recruiter_screen import gate0_title_relevance
        assert gate0_title_relevance(
            "Data Scientist", "Requirements: 7+ years of experience", search_config
        ) is False

    def test_empty_title_fails(self, search_config):
        from jobpulse.recruiter_screen import gate0_title_relevance
        assert gate0_title_relevance("", "", search_config) is False

    def test_data_science_variant_passes(self, search_config):
        from jobpulse.recruiter_screen import gate0_title_relevance
        assert gate0_title_relevance("Data Scientist Graduate Role", "", search_config) is True

    def test_software_engineer_passes(self, search_config):
        from jobpulse.recruiter_screen import gate0_title_relevance
        assert gate0_title_relevance("Software Engineer", "", search_config) is True
