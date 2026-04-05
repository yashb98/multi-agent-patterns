"""Tests for build_extra_skills and get_role_profile wiring in the CV pipeline."""

import inspect

import pytest

from jobpulse.cv_templates.generate_cv import (
    build_extra_skills,
    get_role_profile,
    BASE_SKILLS,
)


# ---------------------------------------------------------------------------
# build_extra_skills
# ---------------------------------------------------------------------------

class TestBuildExtraSkills:
    def test_returns_also_proficient_key(self):
        result = build_extra_skills(["Spark", "Databricks"], [])
        assert "Also proficient in:" in result
        assert "Spark" in result["Also proficient in:"]

    def test_deduplicates_base_skills(self):
        result = build_extra_skills(["Python", "SQL", "Docker", "Spark"], [])
        value = result.get("Also proficient in:", "")
        assert "Python" not in value
        assert "SQL" not in value
        assert "Docker" not in value
        assert "Spark" in value

    def test_synonym_dedup_aws(self):
        result = build_extra_skills(["Amazon Web Services"], [])
        assert result == {}

    def test_synonym_dedup_kubernetes(self):
        # Kubernetes is in BASE_SKILLS DevOps; "K8s" is a synonym — should be excluded
        result = build_extra_skills(["K8s"], [])
        assert result == {}

    def test_filters_soft_skills(self):
        result = build_extra_skills(["Teamwork", "Communication", "Spark"], [])
        value = result.get("Also proficient in:", "")
        assert "Teamwork" not in value
        assert "Communication" not in value
        assert "Spark" in value

    def test_empty_skills_returns_empty_dict(self):
        assert build_extra_skills([], []) == {}

    def test_preferred_skills_included(self):
        result = build_extra_skills([], ["Airflow", "dbt"])
        value = result.get("Also proficient in:", "")
        assert "Airflow" in value

    def test_no_duplicates_between_required_and_preferred(self):
        result = build_extra_skills(["Spark"], ["Spark"])
        value = result.get("Also proficient in:", "")
        assert value.count("Spark") == 1

    def test_capped_at_12_skills(self):
        skills = [f"Skill{i}" for i in range(20)]
        result = build_extra_skills(skills, [])
        value = result.get("Also proficient in:", "")
        assert len(value.split(" | ")) <= 12

    def test_key_is_not_jd_match(self):
        """The old buggy key 'JD Match:' must never appear."""
        result = build_extra_skills(["Spark"], [])
        assert "JD Match:" not in result


# ---------------------------------------------------------------------------
# get_role_profile
# ---------------------------------------------------------------------------

class TestGetRoleProfile:
    def test_data_scientist_match(self):
        profile = get_role_profile("Senior Data Scientist")
        assert "tagline" in profile
        assert "summary" in profile

    def test_data_analyst_match(self):
        profile = get_role_profile("Data Analyst - Marketing")
        assert "tagline" in profile
        assert "3+ YOE" in profile["tagline"]

    def test_ml_engineer_match(self):
        profile = get_role_profile("ML Engineer")
        assert "tagline" in profile

    def test_ai_engineer_match(self):
        profile = get_role_profile("AI Engineer - LLM Systems")
        assert "tagline" in profile

    def test_fallback_empty_dict(self):
        profile = get_role_profile("Retail Assistant")
        assert profile == {}

    def test_returns_tagline_and_summary_keys(self):
        profile = get_role_profile("Data Scientist")
        assert "tagline" in profile
        assert "summary" in profile
        assert len(profile["summary"]) > 20

    def test_case_insensitive(self):
        profile = get_role_profile("DATA ANALYST")
        assert "tagline" in profile

    def test_get_returns_none_safely(self):
        profile = get_role_profile("Unknown Role")
        assert profile.get("tagline") is None
        assert profile.get("summary") is None


# ---------------------------------------------------------------------------
# Integration: job_autopilot uses build_extra_skills + get_role_profile
# ---------------------------------------------------------------------------

class TestAutopilotCVWiring:
    def test_import_exports_both_functions(self):
        from jobpulse.cv_templates import generate_cv as mod
        assert hasattr(mod, "build_extra_skills")
        assert hasattr(mod, "get_role_profile")

    def test_job_autopilot_imports_build_extra_skills(self):
        import jobpulse.job_autopilot as mod
        assert hasattr(mod, "build_extra_skills")

    def test_job_autopilot_imports_get_role_profile(self):
        import jobpulse.job_autopilot as mod
        assert hasattr(mod, "get_role_profile")

    def test_no_jd_match_key_in_source(self):
        """The old 'JD Match:' key must not appear in job_autopilot.py source."""
        import jobpulse.job_autopilot as mod
        source = inspect.getsource(mod)
        assert '"JD Match:"' not in source
