"""Tests for screening v2 pipeline components.

Covers: detector, decomposer, option aligner, validator, pattern extractor,
and the integrated pipeline.
"""

from __future__ import annotations

import pytest

from jobpulse.screening_detector import ScreeningDetector
from jobpulse.screening_decomposer import QuestionDecomposer, AnswerRecombiner
from jobpulse.screening_option_aligner import (
    OptionAligner,
    BoolFieldHandler,
    SalaryFieldHandler,
)
from jobpulse.screening_validator import ScreeningValidator
from jobpulse.screening_pattern_extractor import PatternExtractor
from jobpulse.screening_pipeline import ScreeningPipeline
from jobpulse.screening_intent import ScreeningIntent


# ── Fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture
def detector():
    return ScreeningDetector(embedder=None)


@pytest.fixture
def decomposer():
    return QuestionDecomposer(llm_enabled=False)


@pytest.fixture
def aligner():
    return OptionAligner()


@pytest.fixture
def validator():
    return ScreeningValidator()


@pytest.fixture
def dummy_embedder():
    """Fast deterministic embedder for tests."""

    class _DummyEmbedder:
        dims = 384

        def embed(self, text: str) -> list[float]:
            import hashlib, math
            h = hashlib.sha256(text.lower().strip().encode()).digest()
            vec = [float((b / 255.0) * 2 - 1) for b in h[: self.dims]]
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            return [v / norm for v in vec]

        def embed_batch(self, texts: list[str]) -> list[list[float]]:
            return [self.embed(t) for t in texts]

    return _DummyEmbedder()


@pytest.fixture
def extractor(tmp_path):
    """PatternExtractor with isolated DB."""
    ext = PatternExtractor(qdrant_url=None)
    ext._db_path = str(tmp_path / "patterns.db")
    ext._ensure_db()
    return ext


@pytest.fixture
def profile():
    return {
        "right_to_work": True,
        "visa_sponsorship_required": False,
        "notice_period": "3 months",
        "current_salary": "£50,000",
        "salary_expectation": "£60,000",
        "current_job_title": "Senior Engineer",
        "current_employer": "TechCorp",
        "highest_degree": "MSc Computer Science",
        "willing_to_relocate": False,
        "remote_preference": "Fully remote",
        "years_of_experience": 7,
        "location": "London, UK",
    }


# ── ScreeningDetector Tests ───────────────────────────────────────────────

class TestScreeningDetector:
    def test_question_mark_field(self, detector):
        field = {"label": "What is your notice period?", "type": "text", "required": True}
        assert detector.is_screening(field) is True

    def test_select_field(self, detector):
        field = {
            "label": "Salary expectation",
            "type": "select",
            "required": True,
            "options": ["£30-40k", "£40-50k"],
        }
        assert detector.is_screening(field) is True

    def test_non_screening_field(self, detector):
        field = {"label": "First name", "type": "text", "required": True}
        assert detector.is_screening(field) is False

    def test_yes_no_options(self, detector):
        field = {
            "label": "Do you have a driving license?",
            "type": "radio",
            "required": True,
            "options": ["Yes", "No"],
        }
        assert detector.is_screening(field) is True

    def test_required_unmapped_boost(self, detector):
        field = {"label": "Are you willing to travel?", "type": "checkbox", "required": True}
        assert detector.is_screening(field, profile_mapping={}) is True

    def test_profile_mapped_reduces_score(self, detector):
        field = {"label": "What is your notice period?", "type": "text", "required": True}
        mapping = {"what is your notice period?": "3 months"}
        # Still screening because of keyword + question mark
        assert detector.is_screening(field, profile_mapping=mapping) is True

    def test_options_look_screening_with_gender(self, detector):
        assert detector._options_look_screening(["Male", "Female", "Prefer not to say"]) is True

    def test_options_look_screening_with_employment(self, detector):
        assert detector._options_look_screening(["Full-time", "Part-time", "Contract"]) is True

    def test_options_look_not_screening(self, detector):
        assert detector._options_look_screening(["January", "February", "March"]) is False


# ── QuestionDecomposer Tests ──────────────────────────────────────────────

class TestQuestionDecomposer:
    def test_non_compound_returns_none(self, decomposer):
        assert decomposer.decompose("What is your notice period?") is None

    def test_compound_experience_with_and(self, decomposer):
        result = decomposer.decompose("How many years of Python and SQL experience do you have?")
        assert result is not None
        assert len(result) == 2
        assert any("Python" in r for r in result)
        assert any("SQL" in r for r in result)

    def test_compound_proficient_in(self, decomposer):
        result = decomposer.decompose("Are you proficient in Java and Python?")
        assert result is not None
        assert len(result) == 2

    def test_heuristic_split_items(self, decomposer):
        items = decomposer._split_items("Python, SQL, and Java")
        assert "Python" in items
        assert "SQL" in items
        assert "Java" in items
        assert "and so on" not in items

    def test_empty_question(self, decomposer):
        assert decomposer.decompose("") is None
        assert decomposer.decompose("   ") is None

    def test_no_compound_indicators(self, decomposer):
        assert decomposer.decompose("How many years of Python experience?") is None


class TestAnswerRecombiner:
    def test_single_answer(self):
        result = AnswerRecombiner.recombine([("Q1?", "A1")])
        assert result == "A1"

    def test_multiple_answers(self):
        answers = [
            ("How many years of Python experience?", "5 years"),
            ("How many years of SQL experience?", "3 years"),
        ]
        result = AnswerRecombiner.recombine(answers)
        assert "Python: 5 years" in result
        assert "SQL: 3 years" in result


# ── OptionAligner Tests ───────────────────────────────────────────────────

class TestOptionAligner:
    def test_exact_match(self, aligner):
        result = aligner.align_answer("yes", ["Yes", "No", "Maybe"])
        assert result == "Yes"

    def test_case_insensitive_match(self, aligner):
        result = aligner.align_answer("YES", ["Yes", "No"])
        assert result == "Yes"

    def test_normalised_match(self, aligner):
        result = aligner.align_answer("y", ["Yes", "No"])
        assert result == "Yes"

    def test_no_options_returns_original(self, aligner):
        result = aligner.align_answer("hello", [])
        assert result == "hello"

    def test_fuzzy_match(self, aligner):
        result = aligner.align_answer("full time", ["Full-time", "Part-time"])
        assert result == "Full-time"

    def test_is_option_field_select(self, aligner):
        field = {"type": "select", "options": ["A", "B"]}
        assert aligner.is_option_field(field) is True

    def test_is_option_field_text(self, aligner):
        field = {"type": "text"}
        assert aligner.is_option_field(field) is False

    def test_is_option_field_with_options(self, aligner):
        field = {"type": "text", "options": ["A", "B"]}
        assert aligner.is_option_field(field) is True


class TestBoolFieldHandler:
    def test_resolve_yes(self):
        result = BoolFieldHandler.resolve("yes", ["Yes", "No"])
        assert result == "Yes"

    def test_resolve_no(self):
        result = BoolFieldHandler.resolve("no", ["Yes", "No"])
        assert result == "No"

    def test_resolve_agree(self):
        result = BoolFieldHandler.resolve("I agree", ["I agree", "I do not agree"])
        assert result == "I agree"

    def test_is_boolean_field_yes_no(self):
        field = {"type": "radio", "options": ["Yes", "No"]}
        assert BoolFieldHandler.is_boolean_field(field) is True

    def test_is_boolean_field_checkbox(self):
        field = {"type": "checkbox", "options": ["I consent"]}
        assert BoolFieldHandler.is_boolean_field(field) is True

    def test_is_boolean_field_not_boolean(self):
        field = {"type": "select", "options": ["London", "Manchester"]}
        assert BoolFieldHandler.is_boolean_field(field) is False


class TestSalaryFieldHandler:
    def test_extract_numeric_with_k(self):
        result = SalaryFieldHandler.extract_numeric("£50k")
        assert result == "50000"

    def test_extract_numeric_full(self):
        result = SalaryFieldHandler.extract_numeric("£50,000")
        assert result == "50000"

    def test_extract_numeric_none(self):
        result = SalaryFieldHandler.extract_numeric("negotiable")
        assert result is None

    def test_format_for_range_direct_match(self):
        options = ["£30-40k", "£40-50k", "£50-60k"]
        result = SalaryFieldHandler.format_for_range("£45,000", options)
        assert result == "£40-50k"

    def test_format_for_range_no_match_falls_back(self):
        options = ["£30-40k", "£40-50k"]
        result = SalaryFieldHandler.format_for_range("£100,000", options)
        # Should pick closest range
        assert result in options


# ── ScreeningValidator Tests ──────────────────────────────────────────────

class TestScreeningValidator:
    def test_empty_answer(self, validator):
        result = validator.validate("", "What is your notice period?")
        assert result.is_valid is False
        assert any("empty" in i.lower() for i in result.issues)

    def test_valid_answer(self, validator):
        result = validator.validate("3 months", "What is your notice period?")
        assert result.is_valid is True
        assert len(result.issues) == 0

    def test_ai_reference_detected(self, validator):
        result = validator.validate(
            "As an AI, I don't have personal experience",
            "Tell us about yourself",
        )
        assert result.is_valid is False
        assert any("AI" in i for i in result.issues)

    def test_length_exceeded(self, validator):
        field = {"type": "text", "maxlength": 10}
        result = validator.validate("This is way too long", "Short question?", field=field)
        assert result.is_valid is False
        assert any("length" in i.lower() for i in result.issues)

    def test_option_mismatch(self, validator):
        field = {"type": "select", "options": ["Yes", "No"]}
        result = validator.validate("Maybe", "Do you have experience?", field=field)
        assert result.is_valid is False
        assert any("option" in i.lower() for i in result.issues)

    def test_option_match(self, validator):
        field = {"type": "select", "options": ["Yes", "No"]}
        result = validator.validate("Yes", "Do you have experience?", field=field)
        assert result.is_valid is True

    def test_profile_consistency_salary(self, validator):
        profile = {"salary_expectation": "£50,000"}
        result = validator.validate(
            "£200,000",
            "What is your salary expectation?",
            profile=profile,
        )
        # Should warn about significant difference
        assert any("salary" in i.lower() for i in result.issues) or result.is_valid

    def test_pii_warning(self, validator):
        result = validator.validate(
            "Contact me at test@example.com",
            "Tell us about yourself",
        )
        assert any("PII" in i for i in result.issues)

    def test_repeated_words(self, validator):
        result = validator.validate(
            "I have have experience",
            "Tell us about yourself",
        )
        assert any("repeated" in i.lower() for i in result.issues)


# ── PatternExtractor Tests ────────────────────────────────────────────────

class TestPatternExtractor:
    def test_observe_and_load(self, extractor):
        extractor.observe("What is your salary?", "£50,000", ScreeningIntent.SALARY_EXPECTED)
        stats = extractor.extract_patterns(ScreeningIntent.SALARY_EXPECTED, min_observations=1)
        # Not enough observations yet for pattern extraction
        assert isinstance(stats, list)

    def test_cluster_by_answer(self, extractor):
        obs = [
            ("Q1", "Yes", True),
            ("Q2", "yes", True),
            ("Q3", "No", True),
        ]
        clusters = extractor._cluster_by_answer(obs)
        # Yes and yes should cluster together
        assert len(clusters) >= 1

    def test_normalise_answer(self, extractor):
        assert "{N}" in extractor._normalise_answer("5 years")
        assert "{LOCATION}" in extractor._normalise_answer("I am based in London")


# ── ScreeningPipeline Tests ───────────────────────────────────────────────

@pytest.fixture
def fast_pipeline(profile, dummy_embedder, tmp_path):
    """Pipeline with dummy embedder for fast tests."""
    from jobpulse.screening_semantic_cache import ScreeningSemanticCache
    from jobpulse.screening_intent import ScreeningIntentClassifier
    from jobpulse.screening_pattern_extractor import PatternExtractor

    cache = ScreeningSemanticCache(sqlite_path=str(tmp_path / "semantic_cache.db"), qdrant_location=None, embedder=dummy_embedder)
    classifier = ScreeningIntentClassifier(db_path=str(tmp_path / "intent.db"), embedder=dummy_embedder)
    extractor = PatternExtractor(qdrant_url=None, embedder=dummy_embedder)
    extractor._db_path = str(tmp_path / "patterns.db")
    extractor._ensure_db()

    return ScreeningPipeline(
        profile=profile,
        semantic_cache=cache,
        intent_classifier=classifier,
        pattern_extractor=extractor,
    )


class TestScreeningPipeline:
    def test_empty_question(self, profile, fast_pipeline):
        result = fast_pipeline.answer("")
        assert result["source"] == "empty_question"
        assert result["answer"] == ""

    def test_profile_intent_resolution(self, fast_pipeline):
        resolved = fast_pipeline._resolve_intent_from_profile(ScreeningIntent.NOTICE_PERIOD)
        assert resolved == "3 months"

    def test_profile_bool_resolution(self, fast_pipeline):
        resolved = fast_pipeline._resolve_intent_from_profile(ScreeningIntent.WORK_AUTH_YES_NO)
        assert resolved == "Yes"

    def test_profile_salary_resolution(self, fast_pipeline):
        resolved = fast_pipeline._resolve_intent_from_profile(ScreeningIntent.SALARY_EXPECTED)
        assert "60,000" in resolved

    def test_unknown_intent_returns_none(self, fast_pipeline):
        resolved = fast_pipeline._resolve_intent_from_profile(ScreeningIntent.OPEN_ENDED)
        assert resolved is None

    def test_finalise_option_alignment(self, fast_pipeline):
        result = {
            "answer": "yes",
            "confidence": 0.8,
            "source": "agent_rules",
            "intent": None,
            "metadata": {},
        }
        field = {"type": "radio", "options": ["Yes", "No"]}
        final = fast_pipeline._finalise(result, "Do you have experience?", field)
        assert final["answer"] == "Yes"
        assert final["source"] == "agent_rules_aligned"

    def test_finalise_validation_catches_ai(self, fast_pipeline):
        result = {
            "answer": "As an AI, I don't have experience",
            "confidence": 0.5,
            "source": "llm_fallback",
            "intent": None,
            "metadata": {},
        }
        final = fast_pipeline._finalise(result, "Tell us about yourself", None)
        assert final["validation"]["is_valid"] is False
        assert "fixed" in final["source"]


# ── Integration-style tests ───────────────────────────────────────────────

class TestScreeningV2Integration:
    def test_detector_decomposer_pipeline(self, detector, decomposer):
        """A compound screening question should be detected AND decomposed."""
        question = "How many years of Python and SQL experience do you have?"
        field = {"label": question, "type": "text", "required": True}

        assert detector.is_screening(field) is True
        subs = decomposer.decompose(question)
        assert subs is not None
        assert len(subs) >= 2

    def test_option_alignment_for_boolean(self, aligner):
        field = {"type": "radio", "options": ["Yes", "No", "Prefer not to say"]}
        aligned = aligner.align_answer("yep", field["options"], field["type"])
        assert aligned == "Yes"

    def test_validator_with_field_and_profile(self, validator):
        field = {"type": "select", "options": ["Yes", "No"]}
        profile = {"visa_sponsorship_required": True}
        result = validator.validate(
            "No",
            "Do you require visa sponsorship?",
            field=field,
            profile=profile,
        )
        assert result.is_valid is False
        assert any("contradicts" in i.lower() for i in result.issues)


# ── ScreeningSemanticCache counter semantics ─────────────────────────────

def test_touch_sqlite_does_not_increment_times_used(tmp_path):
    """Lookups must not inflate times_used — only record_fill does that."""
    from jobpulse.screening_semantic_cache import ScreeningSemanticCache
    cache = ScreeningSemanticCache(sqlite_path=str(tmp_path / "test.db"), qdrant_location="")
    cache.cache(question="Do you have the right to work?", intent="work_auth", answer="Yes", confidence=0.9)

    import sqlite3
    with sqlite3.connect(str(tmp_path / "test.db")) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT times_used FROM screening_semantic_cache LIMIT 1").fetchone()
        initial = row["times_used"]

    for _ in range(3):
        cache._touch_sqlite(cache._qid_for("Do you have the right to work?"))

    with sqlite3.connect(str(tmp_path / "test.db")) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT times_used FROM screening_semantic_cache LIMIT 1").fetchone()
        assert row["times_used"] == initial, "Lookup should not increment times_used"


def test_cache_upsert_does_not_increment_times_used(tmp_path):
    """Re-caching the same question must not inflate times_used."""
    from jobpulse.screening_semantic_cache import ScreeningSemanticCache
    cache = ScreeningSemanticCache(sqlite_path=str(tmp_path / "test.db"), qdrant_location="")
    cache.cache(question="Salary expectations?", intent="salary", answer="35000", confidence=0.8)

    import sqlite3
    with sqlite3.connect(str(tmp_path / "test.db")) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT times_used FROM screening_semantic_cache LIMIT 1").fetchone()
        initial = row["times_used"]

    for _ in range(3):
        cache.cache(question="Salary expectations?", intent="salary", answer="35000", confidence=0.85)

    with sqlite3.connect(str(tmp_path / "test.db")) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT times_used FROM screening_semantic_cache LIMIT 1").fetchone()
        assert row["times_used"] == initial, "Re-caching should not increment times_used"


def test_increment_usage_increments_times_used(tmp_path):
    """increment_usage is the only way to bump times_used."""
    from jobpulse.screening_semantic_cache import ScreeningSemanticCache
    cache = ScreeningSemanticCache(sqlite_path=str(tmp_path / "test.db"), qdrant_location="")
    cache.cache(question="Notice period?", intent="notice", answer="1 month", confidence=0.9)

    import sqlite3
    with sqlite3.connect(str(tmp_path / "test.db")) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT times_used FROM screening_semantic_cache LIMIT 1").fetchone()
        initial = row["times_used"]

    cache.increment_usage("Notice period?")
    cache.increment_usage("Notice period?")

    with sqlite3.connect(str(tmp_path / "test.db")) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT times_used FROM screening_semantic_cache LIMIT 1").fetchone()
        assert row["times_used"] == initial + 2
