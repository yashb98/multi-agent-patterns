"""Tests for S21 / TP-30: screening LLM prompts must frame the model AS
the candidate (first person), not ABOUT the candidate.

Pre-S21, the prompt said 'Answering on behalf of the candidate' / 'Candidate
profile:' / 'Applicant background:' — third-person framing that produced
answers like 'As Yash Bishnoi, I have a strong preference...' on the live
Anthropic Greenhouse run. Post-S21 the prompt says 'You ARE the job
applicant' / 'Your profile:' so the LLM writes in first person directly.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest


class TestScreeningPipelinePromptFraming:
    """jobpulse.screening_pipeline._llm_answer prompts."""

    def test_option_field_prompt_frames_llm_as_candidate(self):
        from jobpulse.screening_pipeline import ScreeningPipeline
        captured = {}

        def fake_llm(*, task, domain, stakes):
            captured["task"] = task
            return "Yes"

        pipeline = ScreeningPipeline(profile={"first_name": "Test"})
        with patch("shared.agents.cognitive_llm_call", fake_llm):
            pipeline._llm_answer(
                question="Are you authorised to work in the UK?",
                field={"type": "select", "options": ["Yes", "No"]},
                job_context=None,
            )
        task = captured["task"]
        # Pre-S21 framing — must NOT appear:
        assert "answering on behalf" not in task.lower()
        assert "Candidate profile:" not in task
        # Post-S21 framing — MUST appear:
        assert "You ARE the job applicant" in task
        assert "FIRST PERSON" in task
        assert "no 'As [name], I" in task or "no 'As [name]" in task
        assert "Your profile:" in task

    def test_free_text_prompt_frames_llm_as_candidate(self):
        from jobpulse.screening_pipeline import ScreeningPipeline
        captured = {}

        def fake_llm(*, task, domain, stakes):
            captured["task"] = task
            return "I am passionate about AI safety."

        pipeline = ScreeningPipeline(profile={"first_name": "Test"})
        with patch("shared.agents.cognitive_llm_call", fake_llm):
            pipeline._llm_answer(
                question="Why do you want to work here?",
                field={"type": "textarea", "options": []},
                job_context=None,
            )
        task = captured["task"]
        assert "Candidate profile:" not in task
        assert "Applicant background:" not in task
        assert "You ARE the job applicant" in task
        assert "FIRST PERSON" in task
        assert "Your profile:" in task

    def test_free_text_prompt_explicitly_forbids_third_person_self_ref(self):
        """The prompt must explicitly tell the LLM not to write 'As [name], I...'
        — that's the exact failure mode observed on the live Anthropic run."""
        from jobpulse.screening_pipeline import ScreeningPipeline
        captured = {}

        def fake_llm(*, task, domain, stakes):
            captured["task"] = task
            return "I am passionate."

        pipeline = ScreeningPipeline(profile={})
        with patch("shared.agents.cognitive_llm_call", fake_llm):
            pipeline._llm_answer(
                question="Why?",
                field={"type": "textarea", "options": []},
                job_context=None,
            )
        # The forbidden-pattern instruction must appear somewhere in the
        # prompt. Either the system_prompt portion or the user_prompt
        # portion — both are concatenated into `task`.
        task = captured["task"]
        assert (
            "As [name]" in task
            or "As [name], I" in task
        ), f"prompt must explicitly forbid 'As [name], I...' — got: {task[:500]}"


class TestScreeningAnswersGeneratorPromptFraming:
    """jobpulse.screening_answers.generate_answer prompts (the cognitive
    engine path used by NativeFormFiller's screening fallback)."""

    def test_generate_answer_task_frames_llm_as_candidate(self, monkeypatch):
        """generate_answer constructs `task` for the cognitive engine; the
        task string must frame the LLM as the candidate."""
        from jobpulse import screening_answers

        captured = {}

        def fake_engine_think(task, **kwargs):
            captured["task"] = task
            class _Result:
                content = "I am keen to apply."
                level = type("L", (), {"value": 1})()
                score = 0.9
                cost = 0.001
            return _Result()

        class _FakeEngine:
            def think_sync(self, task, **kwargs):
                return fake_engine_think(task, **kwargs)
            def flush_sync(self):
                pass

        monkeypatch.setattr(screening_answers, "_get_screening_engine",
                            lambda: _FakeEngine())
        monkeypatch.setattr(screening_answers, "_screening_prompt_profile",
                            lambda: {})
        monkeypatch.setattr(screening_answers, "_screening_profile_summary",
                            lambda profile: "experienced engineer, 2 years")
        # PII assertion is unrelated to the framing test — bypass it.
        monkeypatch.setattr(screening_answers, "assert_prompt_has_wrapped_pii",
                            lambda *a, **kw: None)

        screening_answers._generate_answer(
            question="Why are you interested?",
            job_context={"job_title": "Engineer", "company": "Acme"},
        )
        task = captured.get("task", "")
        assert "Applicant background:" not in task
        assert "You ARE the job applicant" in task
        assert "FIRST PERSON" in task
        assert "Your profile:" in task
