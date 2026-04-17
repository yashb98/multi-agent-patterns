"""Tests for archetype detection — keyword scoring + profile lookup."""
import pytest


class TestDetectArchetype:
    def test_agentic_jd(self):
        from jobpulse.archetype_engine import detect_archetype

        jd = "Build multi-agent orchestration systems with LangGraph and HITL flows"
        skills = ["Python", "LangGraph", "Agent", "Orchestration"]
        result = detect_archetype(jd, skills)
        assert result.primary == "agentic"
        assert result.confidence >= 0.5

    def test_data_analyst_jd(self):
        from jobpulse.archetype_engine import detect_archetype

        jd = "Create dashboards and reports for stakeholders using SQL and Power BI"
        skills = ["SQL", "Power BI", "Dashboards", "Reporting", "Stakeholder Management"]
        result = detect_archetype(jd, skills)
        assert result.primary == "data_analyst"

    def test_data_scientist_jd(self):
        from jobpulse.archetype_engine import detect_archetype

        jd = "Design A/B tests, build statistical models, run experiments"
        skills = ["Python", "Statistics", "A/B Testing", "Modeling", "Experiments"]
        result = detect_archetype(jd, skills)
        assert result.primary == "data_scientist"

    def test_data_platform_jd(self):
        from jobpulse.archetype_engine import detect_archetype

        jd = "Build ML pipelines with observability, evals, and monitoring in production"
        skills = ["MLOps", "Pipelines", "Observability", "Monitoring", "Python"]
        result = detect_archetype(jd, skills)
        assert result.primary == "data_platform"

    def test_unknown_jd_falls_back_to_general(self):
        from jobpulse.archetype_engine import detect_archetype

        jd = "We need someone to do various tasks in the office"
        skills = ["Communication", "Teamwork"]
        result = detect_archetype(jd, skills)
        assert result.primary == "general"
        assert result.confidence < 0.5

    def test_hybrid_role_has_secondary(self):
        from jobpulse.archetype_engine import detect_archetype

        jd = (
            "Build multi-agent systems for ML pipeline orchestration. "
            "Experience with LangGraph, MLOps, model monitoring, and agent architectures."
        )
        skills = ["LangGraph", "MLOps", "Agents", "Monitoring", "Pipelines"]
        result = detect_archetype(jd, skills)
        assert result.secondary is not None
        assert result.primary != result.secondary


class TestGetArchetypeProfile:
    def test_returns_profile_for_known_archetype(self):
        from jobpulse.archetype_engine import get_archetype_profile

        profile = get_archetype_profile("agentic")
        assert "tagline" in profile
        assert "summary_angle" in profile
        assert "project_priority" in profile

    def test_returns_default_for_unknown(self):
        from jobpulse.archetype_engine import get_archetype_profile

        profile = get_archetype_profile("nonexistent")
        assert "tagline" in profile
