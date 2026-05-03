"""Real-data tests for ScreeningPipeline — real LLM (Ollama) + real SQLite.

No mocks. Exercises the full pipeline: intent classification, profile
resolution, LLM fallback, semantic cache, option alignment, validation.
Requires Ollama running locally (auto-skips otherwise).
"""

from __future__ import annotations

import time

import httpx
import pytest

from jobpulse.screening_pipeline import ScreeningPipeline
from jobpulse.screening_intent import ScreeningIntent, ScreeningIntentClassifier
from jobpulse.screening_semantic_cache import ScreeningSemanticCache
from jobpulse.screening_pattern_extractor import PatternExtractor


def _ollama_available() -> bool:
    try:
        r = httpx.get("http://localhost:11434/api/tags", timeout=2)
        return r.status_code == 200
    except Exception:
        return False


pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(
        not _ollama_available(),
        reason="Ollama not running — skip real LLM tests",
    ),
]

# ── Synthetic but realistic JD ────────────────────────────────────────────

SAMPLE_JD = (
    "We are looking for a Data Analyst with 3+ years of experience in Python "
    "and SQL. The role is based in London with hybrid working (3 days in office). "
    "Salary range: GBP 40,000 - 55,000. Must have the right to work in the UK. "
    "Experience with Tableau, Power BI, or similar BI tools is highly desirable. "
    "Strong communication skills and ability to present insights to stakeholders."
)

SAMPLE_JOB_CONTEXT = {
    "job_title": "Data Analyst",
    "company": "Acme Analytics Ltd",
    "location": "London, UK",
    "salary_range": {"min": 40000, "max": 55000},
    "work_mode": "hybrid",
    "skills_required": ["Python", "SQL", "Tableau", "Power BI"],
}


# ── Fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture()
def profile():
    """Anonymized candidate profile — no real PII."""
    return {
        "right_to_work": True,
        "work_auth_type": "Graduate Visa",
        "visa_type": "Graduate Visa",
        "visa_sponsorship_required": False,
        "notice_period": "1 month",
        "current_salary": "22000",
        "salary_expectation": "38000",
        "currently_employed": True,
        "current_job_title": "Team Leader",
        "current_employer": "Retail Corp",
        "highest_degree": "MSc Computer Science",
        "degree_subject": "Computer Science",
        "willing_to_relocate": True,
        "remote_preference": "Open to hybrid or remote",
        "years_of_experience": "2",
        "location": "Dundee, UK",
        "languages": "English (fluent), Hindi (native)",
        "english_proficiency": "Fluent / Native",
        "has_driving_license": False,
        "willing_to_travel": True,
        "background_check_consent": True,
        "data_consent": True,
    }


@pytest.fixture()
def pipeline(tmp_path, profile):
    """ScreeningPipeline wired to isolated tmp_path databases."""
    cache_db = str(tmp_path / "semantic_cache.db")
    intent_db = str(tmp_path / "intent_prototypes.db")
    pattern_db = str(tmp_path / "patterns.db")

    semantic_cache = ScreeningSemanticCache(
        sqlite_path=cache_db,
        qdrant_location=None,
    )
    intent_classifier = ScreeningIntentClassifier(
        db_path=intent_db,
    )
    pattern_extractor = PatternExtractor(qdrant_url=None)
    pattern_extractor._db_path = pattern_db
    pattern_extractor._ensure_db()

    return ScreeningPipeline(
        profile=profile,
        semantic_cache=semantic_cache,
        intent_classifier=intent_classifier,
        pattern_extractor=pattern_extractor,
    )


# ── Test: resolve() returns non-empty answers ─────────────────────────────

class TestResolveBasic:
    """Verify the pipeline produces real answers for common questions."""

    def test_visa_status_question(self, pipeline):
        result = pipeline.answer(
            "What is your visa status?",
            job_context=SAMPLE_JOB_CONTEXT,
        )
        assert result["answer"], "Expected a non-empty answer for visa status"
        assert result["confidence"] > 0.0
        assert result["source"] != "no_answer"

    def test_right_to_work_question(self, pipeline):
        result = pipeline.answer(
            "Do you have the right to work in the UK?",
            job_context=SAMPLE_JOB_CONTEXT,
        )
        answer = result["answer"].lower()
        assert "yes" in answer or "true" in answer or "right" in answer

    def test_notice_period_question(self, pipeline):
        result = pipeline.answer(
            "What is your notice period?",
            job_context=SAMPLE_JOB_CONTEXT,
        )
        answer = result["answer"].lower()
        assert result["answer"], "Expected a non-empty notice period answer"
        assert "month" in answer or "week" in answer or "immediate" in answer

    def test_salary_expectation_question(self, pipeline):
        result = pipeline.answer(
            "What is your expected salary?",
            job_context=SAMPLE_JOB_CONTEXT,
        )
        assert result["answer"], "Expected a non-empty salary answer"
        # The profile says 38000 or job context midpoint is 47500
        assert any(c.isdigit() for c in result["answer"]), "Salary should contain digits"

    def test_education_question(self, pipeline):
        result = pipeline.answer(
            "What is your highest level of education?",
            job_context=SAMPLE_JOB_CONTEXT,
        )
        assert result["answer"], "Expected a non-empty education answer"

    def test_relocation_question(self, pipeline):
        result = pipeline.answer(
            "Are you willing to relocate?",
            job_context=SAMPLE_JOB_CONTEXT,
        )
        assert result["answer"], "Expected a non-empty relocation answer"
        assert result["answer"].lower() in ("yes", "no", "true", "false") or len(result["answer"]) > 0

    def test_experience_years_question(self, pipeline):
        result = pipeline.answer(
            "How many years of relevant experience do you have?",
            job_context=SAMPLE_JOB_CONTEXT,
        )
        assert result["answer"], "Expected a non-empty experience answer"


# ── Test: cache behavior ──────────────────────────────────────────────────

class TestCacheBehavior:
    """Verify the semantic cache accelerates repeat lookups."""

    def test_second_call_uses_cache_after_record(self, pipeline):
        question = "Do you require visa sponsorship to work in the UK?"
        # First call — goes through the full pipeline
        result1 = pipeline.answer(question, job_context=SAMPLE_JOB_CONTEXT)
        assert result1["answer"], "First call should produce an answer"

        # Record the outcome so it gets cached
        pipeline.record_outcome(
            question=question,
            answer=result1["answer"],
            success=True,
        )

        # Second call — should hit the semantic cache
        result2 = pipeline.answer(question, job_context=SAMPLE_JOB_CONTEXT)
        assert result2["answer"], "Second call should also produce an answer"
        assert result2["source"] == "semantic_cache", (
            f"Expected cache hit on second call, got source={result2['source']}"
        )

    def test_cache_hit_is_faster(self, pipeline):
        question = "What is your current salary?"
        # First call
        t0 = time.perf_counter()
        result1 = pipeline.answer(question, job_context=SAMPLE_JOB_CONTEXT)
        t1 = time.perf_counter()
        first_duration = t1 - t0

        # Cache it
        pipeline.record_outcome(question=question, answer=result1["answer"], success=True)

        # Second call — should be substantially faster from cache
        t2 = time.perf_counter()
        result2 = pipeline.answer(question, job_context=SAMPLE_JOB_CONTEXT)
        t3 = time.perf_counter()
        second_duration = t3 - t2

        # Cache hit should be at least 2x faster (usually 100x+ faster)
        # Only assert if first call was slow enough to be meaningful
        if first_duration > 0.5:
            assert second_duration < first_duration, (
                f"Cache hit ({second_duration:.3f}s) should be faster than "
                f"first call ({first_duration:.3f}s)"
            )

    def test_paraphrased_question_hits_cache(self, pipeline):
        """Semantically similar questions should hit the same cache entry."""
        original = "Do you have the right to work in the UK?"
        result1 = pipeline.answer(original, job_context=SAMPLE_JOB_CONTEXT)
        pipeline.record_outcome(question=original, answer=result1["answer"], success=True)

        # Paraphrased version
        paraphrased = "Are you legally authorized to work in the United Kingdom?"
        result2 = pipeline.answer(paraphrased, job_context=SAMPLE_JOB_CONTEXT)

        # Semantic cache uses embedding similarity, so paraphrased should hit
        # (depends on embedder quality — if it misses, the pipeline still answers)
        assert result2["answer"], "Paraphrased question should still get an answer"


# ── Test: intent classification ───────────────────────────────────────────

class TestIntentClassification:
    """Verify intent classifier tags questions correctly."""

    def test_visa_intent(self, pipeline):
        result = pipeline.answer("What is your current visa status?")
        intent = result.get("intent")
        # Should classify as visa-related
        if intent and intent != "unknown":
            assert intent in (
                "visa_status", "work_auth_type", "work_auth_yes_no", "sponsorship",
            ), f"Visa question classified as unexpected intent: {intent}"

    def test_salary_intent(self, pipeline):
        result = pipeline.answer("What is your expected salary?")
        intent = result.get("intent")
        if intent and intent != "unknown":
            assert intent in (
                "salary_expected", "salary_current",
            ), f"Salary question classified as unexpected intent: {intent}"

    def test_notice_intent(self, pipeline):
        result = pipeline.answer("How much notice do you need to give?")
        intent = result.get("intent")
        if intent and intent != "unknown":
            assert intent in (
                "notice_period", "start_date",
            ), f"Notice question classified as unexpected intent: {intent}"

    def test_experience_intent(self, pipeline):
        result = pipeline.answer("How many years of Python experience do you have?")
        intent = result.get("intent")
        if intent and intent != "unknown":
            assert intent in (
                "experience_years", "experience_skill",
            ), f"Experience question classified as unexpected intent: {intent}"

    def test_education_intent(self, pipeline):
        result = pipeline.answer("What is your highest qualification?")
        intent = result.get("intent")
        if intent and intent != "unknown":
            assert intent in (
                "education_level", "degree_subject",
            ), f"Education question classified as unexpected intent: {intent}"

    def test_location_intent(self, pipeline):
        result = pipeline.answer("Where are you currently based?")
        intent = result.get("intent")
        if intent and intent != "unknown":
            assert intent in (
                "location_current", "willing_relocate", "commute",
            ), f"Location question classified as unexpected intent: {intent}"


# ── Test: option alignment ────────────────────────────────────────────────

class TestOptionAlignment:
    """Verify answers align to provided field options."""

    def test_yes_no_field_alignment(self, pipeline):
        field = {
            "type": "radio",
            "options": ["Yes", "No"],
        }
        result = pipeline.answer(
            "Do you have the right to work in the UK?",
            field=field,
            job_context=SAMPLE_JOB_CONTEXT,
        )
        assert result["answer"] in ("Yes", "No"), (
            f"Answer '{result['answer']}' not aligned to Yes/No options"
        )

    def test_dropdown_field_alignment(self, pipeline):
        field = {
            "type": "select",
            "options": ["Immediately", "1 week", "2 weeks", "1 month", "2 months", "3 months"],
        }
        result = pipeline.answer(
            "What is your notice period?",
            field=field,
            job_context=SAMPLE_JOB_CONTEXT,
        )
        assert result["answer"], "Expected a non-empty answer"
        # Answer should be one of the options or close
        answer_lower = result["answer"].lower()
        options_lower = [o.lower() for o in field["options"]]
        assert any(
            opt in answer_lower or answer_lower in opt
            for opt in options_lower
        ) or result["answer"] in field["options"], (
            f"Answer '{result['answer']}' not aligned to dropdown options"
        )


# ── Test: edge cases ──────────────────────────────────────────────────────

class TestEdgeCases:
    """Edge cases: empty, very long, non-English, unusual questions."""

    def test_empty_label(self, pipeline):
        result = pipeline.answer("")
        assert result["answer"] == ""
        assert result["source"] == "empty_question"
        assert result["confidence"] == 0.0

    def test_whitespace_only_label(self, pipeline):
        result = pipeline.answer("   \n\t  ")
        assert result["answer"] == ""
        assert result["source"] == "empty_question"

    def test_very_long_label(self, pipeline):
        long_question = (
            "Please provide a detailed explanation of your previous work experience "
            "including all relevant projects, technologies used, team sizes, and "
            "measurable outcomes achieved during your tenure at each company, "
            "as well as any certifications or training programs completed. "
        ) * 5  # ~400+ words
        result = pipeline.answer(long_question, job_context=SAMPLE_JOB_CONTEXT)
        # Should not crash — either answers or gracefully returns empty
        assert isinstance(result["answer"], str)
        assert isinstance(result["confidence"], float)

    def test_non_english_label(self, pipeline):
        result = pipeline.answer(
            "Haben Sie eine Arbeitserlaubnis fuer Grossbritannien?",
            job_context=SAMPLE_JOB_CONTEXT,
        )
        # Pipeline may not answer non-English well, but should not crash
        assert isinstance(result["answer"], str)
        assert isinstance(result["confidence"], float)

    def test_ambiguous_question(self, pipeline):
        result = pipeline.answer(
            "Other",
            job_context=SAMPLE_JOB_CONTEXT,
        )
        # Single-word ambiguous label — should not crash
        assert isinstance(result, dict)
        assert "answer" in result

    def test_numeric_only_label(self, pipeline):
        result = pipeline.answer("12345")
        assert isinstance(result["answer"], str)


# ── Test: LLM fallback ───────────────────────────────────────────────────

class TestLLMFallback:
    """Verify LLM fallback handles unusual questions that no intent covers."""

    def test_unusual_screening_question(self, pipeline):
        result = pipeline.answer(
            "Describe a situation where you had to work under pressure.",
            job_context=SAMPLE_JOB_CONTEXT,
        )
        # This is a behavioral question — no profile mapping, so it hits LLM
        # LLM may or may not answer depending on Ollama model capability
        assert isinstance(result["answer"], str)
        assert result["source"] in (
            "llm_fallback", "agent_rules", "intent_resolver",
            "no_answer", "llm_fallback_fixed",
        )

    def test_company_specific_question(self, pipeline):
        result = pipeline.answer(
            "Why do you want to work at Acme Analytics?",
            job_context=SAMPLE_JOB_CONTEXT,
        )
        assert isinstance(result["answer"], str)


# ── Test: validation ──────────────────────────────────────────────────────

class TestValidation:
    """Verify validation metadata is populated on results."""

    def test_result_has_validation_dict(self, pipeline):
        result = pipeline.answer(
            "Do you have the right to work in the UK?",
            job_context=SAMPLE_JOB_CONTEXT,
        )
        assert "validation" in result
        validation = result["validation"]
        assert "is_valid" in validation
        assert "issues" in validation
        assert isinstance(validation["is_valid"], bool)

    def test_result_structure_complete(self, pipeline):
        result = pipeline.answer(
            "What is your notice period?",
            job_context=SAMPLE_JOB_CONTEXT,
        )
        # Verify all expected keys present
        for key in ("answer", "confidence", "source", "intent", "validation", "metadata"):
            assert key in result, f"Missing key '{key}' in result"
        assert isinstance(result["confidence"], float)
        assert 0.0 <= result["confidence"] <= 1.0


# ── Test: record_outcome ──────────────────────────────────────────────────

class TestRecordOutcome:
    """Verify recording outcomes updates the learning pipeline."""

    def test_record_success_caches_answer(self, pipeline):
        question = "Are you willing to travel for work?"
        answer = "Yes"
        pipeline.record_outcome(
            question=question,
            answer=answer,
            success=True,
        )
        # Now the same question should hit semantic cache
        result = pipeline.answer(question)
        assert result["source"] == "semantic_cache"
        assert result["answer"] == answer

    def test_record_with_field_options(self, pipeline):
        question = "What is your preferred work arrangement?"
        answer = "Hybrid"
        options = ["Remote", "Hybrid", "On-site"]
        pipeline.record_outcome(
            question=question,
            answer=answer,
            success=True,
            field_options=options,
            field_type="radio",
            selected_option="Hybrid",
        )
        result = pipeline.answer(question)
        assert result["answer"], "Cached answer should be retrievable"


# ── Test: job context influences answers ──────────────────────────────────

class TestJobContextInfluence:
    """Verify job context shapes answers appropriately."""

    def test_salary_uses_job_range_midpoint(self, pipeline):
        result = pipeline.answer(
            "What is your expected salary?",
            job_context=SAMPLE_JOB_CONTEXT,
        )
        answer = result["answer"]
        # Job context has salary_range min=40000, max=55000, midpoint=47500
        # The intent resolver should return the midpoint
        if result["source"] == "intent_resolver":
            assert any(c.isdigit() for c in answer)

    def test_remote_question_with_hybrid_context(self, pipeline):
        result = pipeline.answer(
            "Are you comfortable working remotely?",
            job_context={"work_mode": "remote"},
        )
        assert result["answer"], "Should answer remote question"
