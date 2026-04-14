"""Tests for jobpulse/interview_prep.py."""

import pytest

from jobpulse.interview_prep import (
    map_skills_to_stories,
    build_star_story,
    generate_prep_report,
    format_prep_telegram,
)


PROJECTS = [
    {
        "name": "JobPulse",
        "description": "Autonomous job application agent",
        "skills": ["Python", "LangGraph", "PostgreSQL"],
    },
    {
        "name": "MindGraph",
        "description": "Code review graph with risk scoring",
        "skills": ["Python", "Neo4j", "FastAPI"],
    },
    {
        "name": "DataPipeline",
        "description": "ETL pipeline for analytics",
        "skills": ["SQL", "dbt", "Airflow"],
    },
]


class TestMapSkillsToStories:
    def test_maps_matching_skills(self):
        required = ["Python", "LangGraph"]
        result = map_skills_to_stories(required, PROJECTS)

        assert "Python" in result
        assert "LangGraph" in result
        assert result["LangGraph"]["project"] == "JobPulse"

    def test_case_insensitive_match(self):
        required = ["python", "LANGGRAPH"]
        result = map_skills_to_stories(required, PROJECTS)

        assert "python" in result
        assert "LANGGRAPH" in result

    def test_unmapped_skills(self):
        required = ["Python", "Kubernetes"]
        result = map_skills_to_stories(required, PROJECTS)

        assert "Python" in result
        assert "Kubernetes" not in result

    def test_returns_empty_when_no_match(self):
        required = ["Rust", "Go"]
        result = map_skills_to_stories(required, PROJECTS)
        assert result == {}

    def test_returns_empty_for_empty_skills(self):
        result = map_skills_to_stories([], PROJECTS)
        assert result == {}

    def test_picks_project_with_most_overlap(self):
        # Both JobPulse and MindGraph have Python.
        # With required = [Python, LangGraph], JobPulse has 2 overlaps vs MindGraph 1.
        required = ["Python", "LangGraph"]
        result = map_skills_to_stories(required, PROJECTS)
        assert result["Python"]["project"] == "JobPulse"

    def test_result_contains_project_and_description(self):
        required = ["Python"]
        result = map_skills_to_stories(required, PROJECTS)

        assert "project" in result["Python"]
        assert "description" in result["Python"]


class TestBuildStarStory:
    def test_builds_story_structure(self):
        story = build_star_story("Python", "JobPulse", "Autonomous job application agent")

        assert story["skill"] == "Python"
        assert story["project"] == "JobPulse"
        assert "situation" in story
        assert "task" in story
        assert "action" in story
        assert "result" in story
        assert "reflection" in story

    def test_situation_includes_project_name(self):
        story = build_star_story("LangGraph", "JobPulse", "An agent system")
        assert "JobPulse" in story["situation"]

    def test_task_includes_skill(self):
        story = build_star_story("FastAPI", "MindGraph", "Code review tool")
        assert "FastAPI" in story["task"]

    def test_reflection_includes_skill(self):
        story = build_star_story("Neo4j", "MindGraph", "Graph DB project")
        assert "Neo4j" in story["reflection"]

    def test_empty_description_handled(self):
        story = build_star_story("SQL", "DataPipeline", "")
        assert "DataPipeline" in story["situation"]


class TestGeneratePrepReport:
    def test_report_structure(self):
        report = generate_prep_report("Acme", "ML Engineer", ["Python", "LangGraph"], PROJECTS)

        assert report["company"] == "Acme"
        assert report["role"] == "ML Engineer"
        assert "skill_coverage" in report
        assert "mapped_skills" in report
        assert "star_stories" in report
        assert "unmapped_skills" in report
        assert "gap_mitigation" in report

    def test_skill_coverage_format(self):
        report = generate_prep_report("Acme", "ML Engineer", ["Python", "Kubernetes"], PROJECTS)
        assert report["skill_coverage"] == "1/2"

    def test_unmapped_skills_listed(self):
        report = generate_prep_report("Acme", "ML Engineer", ["Python", "Kubernetes"], PROJECTS)
        assert "Kubernetes" in report["unmapped_skills"]
        assert "Python" not in report["unmapped_skills"]

    def test_gap_mitigation_per_unmapped(self):
        report = generate_prep_report("Acme", "ML Engineer", ["Rust", "Go"], PROJECTS)
        assert len(report["gap_mitigation"]) == 2

    def test_star_stories_count_matches_mapped(self):
        required = ["Python", "LangGraph", "Kubernetes"]
        report = generate_prep_report("Acme", "ML Engineer", required, PROJECTS)
        assert len(report["star_stories"]) == len(report["mapped_skills"])


class TestFormatPrepTelegram:
    def test_contains_company_and_role(self):
        report = generate_prep_report("DeepMind", "Research Engineer", ["Python"], PROJECTS)
        text = format_prep_telegram(report)
        assert "DeepMind" in text
        assert "Research Engineer" in text

    def test_contains_skill_coverage(self):
        report = generate_prep_report("DeepMind", "Research Engineer", ["Python"], PROJECTS)
        text = format_prep_telegram(report)
        assert "Skill Coverage" in text

    def test_contains_gap_section_when_unmapped(self):
        report = generate_prep_report("DeepMind", "Research Engineer", ["Python", "Rust"], PROJECTS)
        text = format_prep_telegram(report)
        assert "Skill Gaps" in text
        assert "Rust" in text

    def test_no_gap_section_when_all_mapped(self):
        report = generate_prep_report("Acme", "Engineer", ["Python", "LangGraph"], PROJECTS)
        text = format_prep_telegram(report)
        assert "Skill Gaps" not in text
