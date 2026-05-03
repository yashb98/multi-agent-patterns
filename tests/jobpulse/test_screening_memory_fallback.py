"""Tests for query_memory_for_similar_answer — MemoryManager-backed fallback.

Uses real production data from data/screening_semantic_cache.db to validate
that the helper doesn't crash on real question shapes and behaves correctly at
the score threshold boundary.

DB access: read-only queries against production screening_semantic_cache.db
(not a write path — safe per testing rules; no tmp_path needed for a read).
"""

from __future__ import annotations

import sqlite3
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCREENING_DB = REPO_ROOT / "data" / "screening_semantic_cache.db"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_real_screening_questions(limit: int = 10) -> list[tuple[str, str, str]]:
    """Pull real production rows from screening_semantic_cache.db.

    Returns list of (question_text, answer, intent) tuples.
    Skips rows with empty answers so every returned row is usable.
    """
    if not SCREENING_DB.exists():
        return []
    with sqlite3.connect(SCREENING_DB) as conn:
        rows = conn.execute(
            "SELECT question_text, answer, intent FROM screening_semantic_cache "
            "WHERE answer != '' LIMIT ?",
            (limit,),
        ).fetchall()
    return rows


def _make_memory_entry(content: str, decay_score: float) -> types.SimpleNamespace:
    """Build a minimal MemoryEntry-shaped object for mocking."""
    return types.SimpleNamespace(content=content, decay_score=decay_score)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestQueryMemoryForSimilarAnswer:
    def test_static_import(self):
        """Helper must be importable at module level without side effects."""
        from jobpulse.screening_pipeline import query_memory_for_similar_answer  # noqa: F401

    def test_returns_none_when_memory_empty(self):
        """No results from MemoryManager → None (not an exception)."""
        from jobpulse.screening_pipeline import query_memory_for_similar_answer

        with patch("jobpulse.screening_pipeline._get_memory_manager") as mock_mm:
            mock_mm.return_value.query = MagicMock(return_value=[])
            result = query_memory_for_similar_answer("Do you have the right to work in the UK?")
        assert result is None

    def test_returns_none_when_best_score_below_threshold(self):
        """decay_score < min_decay_score → None even when content exists."""
        from jobpulse.screening_pipeline import query_memory_for_similar_answer

        low_score_entry = _make_memory_entry("screening_answer: Yes", decay_score=0.4)

        with patch("jobpulse.screening_pipeline._get_memory_manager") as mock_mm:
            mock_mm.return_value.query = MagicMock(return_value=[low_score_entry])
            result = query_memory_for_similar_answer(
                "Are you authorized to work in the UK?", min_decay_score=0.7
            )
        assert result is None

    def test_returns_answer_when_score_meets_threshold(self):
        """decay_score >= min_decay_score → returns parsed answer string."""
        from jobpulse.screening_pipeline import query_memory_for_similar_answer

        # Storage convention: "screening_answer: <answer>" (single prefix, one colon split)
        entry = _make_memory_entry(
            "screening_answer: I have a visa which permits me to work in the UK",
            decay_score=0.85,
        )

        with patch("jobpulse.screening_pipeline._get_memory_manager") as mock_mm:
            mock_mm.return_value.query = MagicMock(return_value=[entry])
            result = query_memory_for_similar_answer(
                "Are you authorized to work in the UK?", min_decay_score=0.7
            )
        assert result == "I have a visa which permits me to work in the UK"

    def test_picks_first_result_as_best(self):
        """query() returns results sorted by decay_score desc; first is best."""
        from jobpulse.screening_pipeline import query_memory_for_similar_answer

        best = _make_memory_entry("screening_answer: salary: 35000", decay_score=0.9)
        worse = _make_memory_entry("screening_answer: salary: 28000", decay_score=0.5)

        with patch("jobpulse.screening_pipeline._get_memory_manager") as mock_mm:
            mock_mm.return_value.query = MagicMock(return_value=[best, worse])
            result = query_memory_for_similar_answer(
                "What is your expected salary?", min_decay_score=0.7
            )
        # Should return the first (best) entry's answer
        assert result == "salary: 35000"

    def test_strips_leading_tag_prefix(self):
        """Content stored with 'screening_answer: <answer>' loses the prefix."""
        from jobpulse.screening_pipeline import query_memory_for_similar_answer

        entry = _make_memory_entry("screening_answer: 1 month", decay_score=0.8)

        with patch("jobpulse.screening_pipeline._get_memory_manager") as mock_mm:
            mock_mm.return_value.query = MagicMock(return_value=[entry])
            result = query_memory_for_similar_answer("What is your notice period?", min_decay_score=0.7)

        assert result == "1 month"

    def test_content_without_colon_returned_stripped(self):
        """If no colon in content, entire content returned stripped."""
        from jobpulse.screening_pipeline import query_memory_for_similar_answer

        entry = _make_memory_entry("  Yes  ", decay_score=0.9)

        with patch("jobpulse.screening_pipeline._get_memory_manager") as mock_mm:
            mock_mm.return_value.query = MagicMock(return_value=[entry])
            result = query_memory_for_similar_answer("Are you willing to relocate?", min_decay_score=0.7)

        assert result == "Yes"

    def test_returns_none_when_content_empty(self):
        """Entry with high score but empty content → None."""
        from jobpulse.screening_pipeline import query_memory_for_similar_answer

        entry = _make_memory_entry("", decay_score=0.95)

        with patch("jobpulse.screening_pipeline._get_memory_manager") as mock_mm:
            mock_mm.return_value.query = MagicMock(return_value=[entry])
            result = query_memory_for_similar_answer("Some question?", min_decay_score=0.7)

        assert result is None

    def test_query_exception_returns_none(self):
        """If MemoryManager.query raises, helper returns None (graceful degradation)."""
        from jobpulse.screening_pipeline import query_memory_for_similar_answer

        with patch("jobpulse.screening_pipeline._get_memory_manager") as mock_mm:
            mock_mm.return_value.query = MagicMock(side_effect=RuntimeError("qdrant down"))
            result = query_memory_for_similar_answer("Any question here?")

        assert result is None

    def test_jd_context_included_in_search_text(self):
        """JD context is appended to search_text (verifiable via call args)."""
        from jobpulse.screening_pipeline import query_memory_for_similar_answer

        with patch("jobpulse.screening_pipeline._get_memory_manager") as mock_mm:
            mock_mm.return_value.query = MagicMock(return_value=[])
            query_memory_for_similar_answer(
                "Do you need visa sponsorship?",
                jd_context="Senior Data Engineer at ASOS, London",
            )
            call_args = mock_mm.return_value.query.call_args[0][0]
            assert "ASOS" in call_args.semantic_query

    def test_jd_context_truncated_to_200_chars(self):
        """Long JD context is truncated to 200 chars before embedding."""
        from jobpulse.screening_pipeline import query_memory_for_similar_answer

        long_context = "x" * 500
        with patch("jobpulse.screening_pipeline._get_memory_manager") as mock_mm:
            mock_mm.return_value.query = MagicMock(return_value=[])
            query_memory_for_similar_answer("question", jd_context=long_context)
            semantic_query = mock_mm.return_value.query.call_args[0][0].semantic_query
            # The JD context slice is [:200], total query length is bounded
            assert len(semantic_query) <= len("screening_answer: question context: ") + 200

    def test_custom_min_decay_score_threshold(self):
        """min_decay_score=0.0 accepts any non-empty result."""
        from jobpulse.screening_pipeline import query_memory_for_similar_answer

        entry = _make_memory_entry("screening_answer: Graduate Visa", decay_score=0.05)

        with patch("jobpulse.screening_pipeline._get_memory_manager") as mock_mm:
            mock_mm.return_value.query = MagicMock(return_value=[entry])
            result = query_memory_for_similar_answer("visa type?", min_decay_score=0.0)

        assert result == "Graduate Visa"


class TestMemoryFallbackOnRealQuestions:
    """Uses real production data from screening_semantic_cache.db.

    These tests validate that the helper handles actual production question
    shapes without crashing.  MemoryManager is still mocked (no live
    Qdrant/Neo4j required), but the *input* is real.
    """

    def test_helper_handles_real_question_shapes(self):
        """Helper must not crash on real production question patterns."""
        from jobpulse.screening_pipeline import query_memory_for_similar_answer

        questions = _load_real_screening_questions()
        assert questions, (
            f"Need real production data from {SCREENING_DB} to validate. "
            "Run from repo root with the DB present."
        )

        for q_text, _answer, _intent in questions[:5]:
            with patch("jobpulse.screening_pipeline._get_memory_manager") as mock_mm:
                mock_mm.return_value.query = MagicMock(return_value=[])
                result = query_memory_for_similar_answer(q_text)
                # empty results → None, no crash
                assert result is None

    def test_real_questions_with_high_score_return_answer(self):
        """Injecting a high-score result for a real question returns the answer."""
        from jobpulse.screening_pipeline import query_memory_for_similar_answer

        questions = _load_real_screening_questions(limit=3)
        assert questions, f"Production DB required at {SCREENING_DB}"

        for q_text, real_answer, _intent in questions:
            entry = _make_memory_entry(
                f"screening_answer: {real_answer}", decay_score=0.92
            )
            with patch("jobpulse.screening_pipeline._get_memory_manager") as mock_mm:
                mock_mm.return_value.query = MagicMock(return_value=[entry])
                result = query_memory_for_similar_answer(q_text, min_decay_score=0.7)
            assert result is not None
            assert result == real_answer

    def test_real_questions_below_threshold_return_none(self):
        """Even with a result, decay_score below threshold → None."""
        from jobpulse.screening_pipeline import query_memory_for_similar_answer

        questions = _load_real_screening_questions(limit=3)
        assert questions, f"Production DB required at {SCREENING_DB}"

        for q_text, real_answer, _intent in questions:
            entry = _make_memory_entry(
                f"screening_answer: {real_answer}", decay_score=0.3
            )
            with patch("jobpulse.screening_pipeline._get_memory_manager") as mock_mm:
                mock_mm.return_value.query = MagicMock(return_value=[entry])
                result = query_memory_for_similar_answer(q_text, min_decay_score=0.7)
            assert result is None
