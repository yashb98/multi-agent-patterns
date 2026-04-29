"""Tests for DecisionExplainer."""

import pytest

from shared.explainability import DecisionExplainer, DecisionExplanation, _extract_reason


class TestExplainGateDecision:
    def test_passed_gate(self):
        exp = DecisionExplainer.explain_gate_decision(
            gate_name="Gate 3 (Skill Match)",
            passed=True,
            listing={"company": "Acme", "title": "Backend Engineer"},
        )
        assert exp.decision == "passed"
        assert "Approved" in exp.headline
        assert "Acme" in exp.headline

    def test_rejected_gate_with_reason(self):
        class FakeResult:
            reason = "Too few skills matched"
            weaknesses = ["Missing Kubernetes"]

        exp = DecisionExplainer.explain_gate_decision(
            gate_name="Gate 1 (JD Quality)",
            passed=False,
            listing={"company": "Acme", "title": "Backend Engineer", "skill_count": 3},
            gate_result=FakeResult(),
        )
        assert exp.decision == "rejected"
        assert "Blocked" in exp.headline
        assert "Too few skills matched" in exp.details
        assert exp.suggested_action is not None

    def test_rejected_gate_no_reason(self):
        exp = DecisionExplainer.explain_gate_decision(
            gate_name="Gate 4A",
            passed=False,
            listing={"company": "Acme", "title": "Backend Engineer"},
        )
        assert "did not meet criteria" in exp.details[0]


class TestExplainCVScrutiny:
    def test_passed_scrutiny(self):
        exp = DecisionExplainer.explain_cv_scrutiny(
            score=8.5,
            threshold=7.0,
            verdict="shortlist",
            strengths=["Strong metrics", "Clear impact"],
            breakdown={"relevance": 3, "evidence": 3, "presentation": 1, "standout": 1.5},
        )
        assert exp.decision == "passed"
        assert "8.5" in exp.headline
        assert "Strong metrics" in str(exp.details)

    def test_failed_scrutiny(self):
        exp = DecisionExplainer.explain_cv_scrutiny(
            score=5.0,
            threshold=7.0,
            verdict="maybe",
            weaknesses=["No metrics", "Generic wording"],
            breakdown={"relevance": 2, "evidence": 1, "presentation": 1, "standout": 1},
        )
        assert exp.decision == "rejected"
        assert "below threshold" in exp.details[0]
        assert "evidence" in str(exp.details).lower()
        assert exp.suggested_action is not None


class TestExplainScreeningAnswer:
    def test_pattern_match(self):
        exp = DecisionExplainer.explain_screening_answer(
            question="What is your expected salary?",
            answer="£60,000",
            source="pattern_match",
        )
        assert "pattern_match" in str(exp.details).lower() or "Matched a known" in str(exp.details)
        assert exp.confidence >= 0.9

    def test_llm_fallback(self):
        exp = DecisionExplainer.explain_screening_answer(
            question="Why do you want to work here?",
            answer="I love your tech stack...",
            source="llm_fallback",
            job_context={"salary": "£60-80k", "remote": True},
        )
        assert exp.confidence < 0.9
        assert any("remote" in d.lower() for d in exp.details)


class TestExplainProjectSelection:
    def test_basic_selection(self):
        exp = DecisionExplainer.explain_project_selection(
            selected_projects=["Project A", "Project B"],
            archetype="backend",
            jd_skills=["Python", "FastAPI", "PostgreSQL"],
        )
        assert "backend" in str(exp.details)
        assert "2 projects" in exp.headline

    def test_with_outcome_data(self):
        exp = DecisionExplainer.explain_project_selection(
            selected_projects=["Project A"],
            archetype="ml_engineer",
            jd_skills=["PyTorch"],
            outcome_data={"best_project": "Project A", "interview_rate": 0.6},
        )
        assert "60%" in str(exp.details)


class TestExplainCompanyReliability:
    def test_auto_skipped(self):
        exp = DecisionExplainer.explain_company_reliability(
            company="GhostCorp",
            total_applied=15,
            interview_rate=0.0,
            auto_skipped=True,
        )
        assert exp.decision == "blocked"
        assert "Auto-skipped" in exp.headline
        assert "15" in str(exp.details)

    def test_low_rate_warning(self):
        exp = DecisionExplainer.explain_company_reliability(
            company="LowRate Inc",
            total_applied=8,
            interview_rate=0.05,
            auto_skipped=False,
        )
        assert exp.decision == "warned"
        assert "5%" in exp.headline

    def test_reliable_company(self):
        exp = DecisionExplainer.explain_company_reliability(
            company="GoodTech",
            total_applied=10,
            interview_rate=0.4,
            auto_skipped=False,
        )
        assert exp.decision == "ok"
        assert "40%" in exp.headline


class TestFormatting:
    def test_markdown_output(self):
        exp = DecisionExplanation(
            decision_type="test",
            decision="pass",
            headline="Headline",
            details=["Detail 1", "Detail 2"],
            suggested_action="Do X",
        )
        md = exp.to_markdown()
        assert "Headline" in md
        assert "Detail 1" in md
        assert "Do X" in md

    def test_telegram_truncation(self):
        exp = DecisionExplanation(
            decision_type="test",
            decision="pass",
            headline="H" * 50,
            details=["D" * 200] * 5,
        )
        tg = exp.to_telegram()
        assert len(tg) <= 400


class TestExtractReason:
    def test_pydantic_reason(self):
        class R:
            reason = "Too short"
        assert _extract_reason(R()) == "Too short"

    def test_dict_reason(self):
        assert _extract_reason({"reason": "Bad fit"}) == "Bad fit"

    def test_weaknesses_fallback(self):
        class R:
            weaknesses = ["A", "B"]
        assert "A" in _extract_reason(R())

    def test_none(self):
        assert _extract_reason(None) is None
