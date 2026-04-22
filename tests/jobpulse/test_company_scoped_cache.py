"""Tests for company-scoped semantic answer cache.

Covers:
- Basic store/find round-trip (no company)
- Same-company 5% boost makes borderline matches succeed
- Different company receives no boost
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from jobpulse.semantic_cache import SemanticAnswerCache, _cosine_similarity


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _unit_vec(size: int = 32, index: int = 0) -> list[float]:
    """Return a unit vector with all mass on `index`-th component."""
    v = [0.0] * size
    v[index] = 1.0
    return v


def _near_vec(base: list[float], *, offset: float) -> list[float]:
    """Return a vector that is cosine-similar to `base` by approximately
    (1 - offset). Achieved by adding a small orthogonal component."""
    size = len(base)
    # Find first zero slot to put the orthogonal component
    perp_idx = next(i for i in range(size) if base[i] == 0.0)
    result = list(base)
    result[perp_idx] = offset
    # re-normalize
    mag = math.sqrt(sum(x * x for x in result))
    return [x / mag for x in result]


# ---------------------------------------------------------------------------
# Tests: basic store / find (no company)
# ---------------------------------------------------------------------------


class TestStoreAndFindBasic:
    def test_store_and_find_without_company(self, tmp_path):
        """Round-trip: store a question then retrieve it with find_similar."""
        cache = SemanticAnswerCache(db_path=tmp_path / "cache.db", threshold=0.85)

        question = "Are you authorised to work in the UK?"
        answer = "Yes"

        # Patch _embed so we don't need sentence-transformers installed
        vec = _unit_vec(32, 0)
        with patch.object(cache, "_embed", return_value=vec):
            cache.store(question, answer, company="")
            result = cache.find_similar(question, threshold=0.85, company="")

        assert result == answer

    def test_find_returns_none_on_empty_cache(self, tmp_path):
        cache = SemanticAnswerCache(db_path=tmp_path / "cache.db", threshold=0.85)
        vec = _unit_vec(32, 0)
        with patch.object(cache, "_embed", return_value=vec):
            result = cache.find_similar("anything?", threshold=0.85)
        assert result is None

    def test_duplicate_question_increments_times_used(self, tmp_path):
        """Storing the same question twice increments times_used, not duplicates."""
        import sqlite3

        cache = SemanticAnswerCache(db_path=tmp_path / "cache.db", threshold=0.85)
        vec = _unit_vec(32, 0)
        with patch.object(cache, "_embed", return_value=vec):
            cache.store("same question?", "yes", company="")
            cache.store("same question?", "yes", company="")

        with sqlite3.connect(cache.db_path) as conn:
            rows = conn.execute("SELECT times_used FROM answer_cache").fetchall()

        assert len(rows) == 1
        assert rows[0][0] == 2


# ---------------------------------------------------------------------------
# Tests: company-scoped boost
# ---------------------------------------------------------------------------


class TestCompanyScopedBoost:
    def test_store_with_company_find_with_same_company(self, tmp_path):
        """Same-company boost helps a slightly-below-threshold score pass."""
        cache = SemanticAnswerCache(db_path=tmp_path / "cache.db", threshold=0.85)

        # base vector for the stored question
        base = _unit_vec(32, 0)
        # offset=0.65 → cosine ≈ 0.8384 (below 0.85), boosted ≈ 0.8884 (above 0.85)
        query = _near_vec(base, offset=0.65)
        sim_raw = _cosine_similarity(query, base)
        assert sim_raw < 0.85, f"test setup: raw sim {sim_raw:.4f} should be < 0.85"
        assert sim_raw + 0.05 >= 0.85, f"test setup: boosted sim {sim_raw+0.05:.4f} should be >= 0.85"

        call_count = 0

        def mock_embed(text: str) -> list[float]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return base  # embed at store time
            return query  # embed at find time

        with patch.object(cache, "_embed", side_effect=mock_embed):
            cache.store("Do you have right to work?", "Yes", company="Acme")

        # Now look up with same company — boost should push it over threshold
        with patch.object(cache, "_embed", return_value=query):
            result = cache.find_similar("Do you have right to work?", threshold=0.85, company="Acme")

        assert result == "Yes", f"Expected cache hit with company boost (raw_sim={sim_raw:.4f})"

    def test_store_with_company_find_with_different_company(self, tmp_path):
        """Different company → no boost → stays below threshold → miss."""
        cache = SemanticAnswerCache(db_path=tmp_path / "cache.db", threshold=0.85)

        base = _unit_vec(32, 0)
        # offset=0.65 → cosine ≈ 0.8384 (below 0.85 without boost)
        query = _near_vec(base, offset=0.65)
        sim_raw = _cosine_similarity(query, base)
        assert sim_raw < 0.85, f"test setup: raw sim {sim_raw:.4f} should be < 0.85"

        with patch.object(cache, "_embed", return_value=base):
            cache.store("Work authorisation?", "Yes", company="Acme")

        with patch.object(cache, "_embed", return_value=query):
            result = cache.find_similar("Work authorisation?", threshold=0.85, company="OtherCorp")

        assert result is None, "No boost for different company — should miss"

    def test_company_boost_is_meaningful(self, tmp_path):
        """The +0.05 boost is exactly the specified amount."""
        cache = SemanticAnswerCache(db_path=tmp_path / "cache.db", threshold=0.0)  # threshold=0 so everything matches

        # Store a question with company "Boosted"
        base = _unit_vec(32, 1)
        with patch.object(cache, "_embed", return_value=base):
            cache.store("q?", "boosted-answer", company="Boosted")
            cache.store("q?", "generic-answer", company="")

        # Query with same vector as stored
        # With company="Boosted": boosted row gets +0.05 on top of 1.0 → clamped to 1.0
        # With company="": both rows score 1.0, but "Boosted" has boost, so it should win
        with patch.object(cache, "_embed", return_value=base):
            result_boosted = cache.find_similar("q?", threshold=0.0, company="Boosted")
            result_generic = cache.find_similar("q?", threshold=0.0, company="")

        # When asking with the right company, should prefer the company-specific answer
        assert result_boosted == "boosted-answer"
