"""Tests for the centralized prompt registry."""

from __future__ import annotations

import pytest

from shared.prompts import (
    get_prompt,
    list_prompts,
    reload_registry,
    PromptNotFoundError,
    PromptRenderError,
)


class TestPromptRegistry:
    def test_load_jobpulse_skill_extraction(self):
        prompt = get_prompt("jobpulse", "skill_extraction")
        assert prompt.name == "skill_extraction"
        assert prompt.domain == "jobpulse"
        assert prompt.version == "2.0.0"
        assert prompt.response_format == "json_object"
        assert "IGNORE all soft skills" in prompt.system_prompt

    def test_load_jobpulse_cv_scrutiny(self):
        prompt = get_prompt("jobpulse", "cv_scrutiny")
        assert prompt.name == "cv_scrutiny"
        assert prompt.max_tokens == 1500
        assert len(prompt.few_shot_examples) == 2

    def test_load_jobpulse_screening(self):
        prompt = get_prompt("jobpulse", "screening_answer")
        assert "NEVER mention that you are an AI" in prompt.system_prompt

    def test_load_jobpulse_field_mapping(self):
        prompt = get_prompt("jobpulse", "field_mapping")
        assert prompt.temperature == 0.0

    def test_load_jobpulse_outcome_analysis(self):
        prompt = get_prompt("jobpulse", "outcome_analysis")
        assert prompt.name == "outcome_analysis"

    def test_render_skill_extraction(self):
        prompt = get_prompt("jobpulse", "skill_extraction")
        result = prompt.render(jd_text="We need a Python developer with Django experience.")
        assert "messages" in result
        assert result["temperature"] == 0.0
        assert result["response_format"] == {"type": "json_object"}
        assert "Python developer with Django" in result["messages"][-1]["content"]

    def test_render_missing_variable_raises(self):
        prompt = get_prompt("jobpulse", "skill_extraction")
        with pytest.raises(PromptRenderError, match="missing required variables"):
            prompt.render()

    def test_render_cv_scrutiny(self):
        prompt = get_prompt("jobpulse", "cv_scrutiny")
        result = prompt.render(
            cv_text="Built a web app with Python.",
            job_title="Senior Engineer",
            company="TechCorp",
            required_skills=["Python", "FastAPI"],
            preferred_skills=["React"],
        )
        last_msg = result["messages"][-1]["content"]
        assert "Built a web app with Python" in last_msg
        assert "TechCorp" in last_msg

    def test_few_shot_retrieval(self):
        prompt = get_prompt("jobpulse", "skill_extraction")
        examples = prompt.few_shot_examples_for("Python Django PostgreSQL", k=1)
        assert len(examples) == 1
        assert "Python" in examples[0]["input"]

    def test_list_prompts(self):
        prompts = list_prompts("jobpulse")
        assert "skill_extraction" in prompts["jobpulse"]
        assert "cv_scrutiny" in prompts["jobpulse"]
        assert "screening_answer" in prompts["jobpulse"]
        assert "field_mapping" in prompts["jobpulse"]
        assert "outcome_analysis" in prompts["jobpulse"]

    def test_prompt_not_found_domain(self):
        with pytest.raises(PromptNotFoundError, match="Domain 'nonexistent'"):
            get_prompt("nonexistent", "test")

    def test_prompt_not_found_name(self):
        with pytest.raises(PromptNotFoundError, match="Prompt 'nonexistent'"):
            get_prompt("jobpulse", "nonexistent")

    def test_reload_registry(self):
        reload_registry()
        prompt = get_prompt("jobpulse", "skill_extraction")
        assert prompt is not None

    def test_render_with_custom_examples(self):
        prompt = get_prompt("jobpulse", "skill_extraction")
        custom_examples = [
            {"input": "Test JD", "output": '{"required_skills": ["Go"]}'},
        ]
        result = prompt.render(
            jd_text="Need Go developer",
            examples=custom_examples,
        )
        messages = result["messages"]
        # Should have: system, user(example), assistant(example), user(actual)
        assert len(messages) == 4
        assert messages[1]["content"] == "Test JD"
        assert messages[2]["content"] == '{"required_skills": ["Go"]}'

    def test_prompt_consistency_no_soft_skill_contradiction(self):
        """Critical: skill extraction prompt must NOT ask for soft skills."""
        prompt = get_prompt("jobpulse", "skill_extraction")
        system = prompt.system_prompt.lower()
        user = prompt.user_prompt_template.lower()
        combined = system + user

        # Must explicitly instruct to IGNORE soft skills
        assert "ignore all soft skills" in combined or "ignore soft skills" in combined

        # Must NOT ask to include soft skills
        assert "include both technical and soft skills" not in combined
        assert "include soft skills" not in combined

    def test_field_mapping_has_few_shot(self):
        prompt = get_prompt("jobpulse", "field_mapping")
        assert len(prompt.few_shot_examples) >= 2
        # Examples should demonstrate exact label matching
        for ex in prompt.few_shot_examples:
            assert "output" in ex
            # Output should look like JSON
            assert "{" in ex["output"]
