"""Tests for shared semantic utility functions."""
from __future__ import annotations

import pytest
import numpy as np
from unittest.mock import MagicMock, patch


@pytest.fixture
def mock_embedder():
    """Mock MemoryEmbedder that returns deterministic vectors."""
    embedder = MagicMock()
    embedder.dims = 4

    vectors = {
        "male": [1.0, 0.0, 0.0, 0.0],
        "man": [0.95, 0.05, 0.0, 0.0],
        "woman": [0.0, 1.0, 0.0, 0.0],
        "female": [0.05, 0.95, 0.0, 0.0],
        "yes": [0.0, 0.0, 1.0, 0.0],
        "no": [0.0, 0.0, 0.0, 1.0],
        "united kingdom": [0.7, 0.3, 0.0, 0.0],
        "uk": [0.72, 0.28, 0.0, 0.0],
        "cat": [0.0, 0.0, 0.5, 0.5],
    }

    def fake_embed(text):
        key = text.strip().lower()
        return vectors.get(key, [0.25, 0.25, 0.25, 0.25])

    def fake_embed_batch(texts):
        return [fake_embed(t) for t in texts]

    embedder.embed.side_effect = fake_embed
    embedder.embed_batch.side_effect = fake_embed_batch
    return embedder


class TestSemanticSimilarity:
    def test_identical_strings(self, mock_embedder):
        from shared.semantic_utils import semantic_similarity, _cached_embed
        _cached_embed.cache_clear()
        with patch("shared.semantic_utils._get_embedder", return_value=mock_embedder):
            score = semantic_similarity("male", "male")
            assert score > 0.99

    def test_similar_strings(self, mock_embedder):
        from shared.semantic_utils import semantic_similarity, _cached_embed
        _cached_embed.cache_clear()
        with patch("shared.semantic_utils._get_embedder", return_value=mock_embedder):
            score = semantic_similarity("male", "man")
            assert score > 0.9

    def test_dissimilar_strings(self, mock_embedder):
        from shared.semantic_utils import semantic_similarity, _cached_embed
        _cached_embed.cache_clear()
        with patch("shared.semantic_utils._get_embedder", return_value=mock_embedder):
            score = semantic_similarity("yes", "no")
            assert score < 0.1

    def test_returns_zero_on_embedder_failure(self):
        from shared.semantic_utils import semantic_similarity, _cached_embed
        _cached_embed.cache_clear()
        with patch("shared.semantic_utils._get_embedder", return_value=None):
            score = semantic_similarity("hello", "world")
            assert score == 0.0


class TestBestSemanticMatch:
    def test_finds_best_match(self, mock_embedder):
        from shared.semantic_utils import best_semantic_match, _cached_embed
        _cached_embed.cache_clear()
        with patch("shared.semantic_utils._get_embedder", return_value=mock_embedder):
            match, score = best_semantic_match("male", ["Man", "Woman", "Other"])
            assert match == "Man"
            assert score > 0.9

    def test_returns_none_below_threshold(self, mock_embedder):
        from shared.semantic_utils import best_semantic_match, _cached_embed
        _cached_embed.cache_clear()
        with patch("shared.semantic_utils._get_embedder", return_value=mock_embedder):
            match, score = best_semantic_match("cat", ["Man", "Woman"], min_score=0.8)
            assert match is None

    def test_empty_candidates(self, mock_embedder):
        from shared.semantic_utils import best_semantic_match, _cached_embed
        _cached_embed.cache_clear()
        with patch("shared.semantic_utils._get_embedder", return_value=mock_embedder):
            match, score = best_semantic_match("male", [])
            assert match is None

    def test_returns_none_on_embedder_failure(self):
        from shared.semantic_utils import best_semantic_match, _cached_embed
        _cached_embed.cache_clear()
        with patch("shared.semantic_utils._get_embedder", return_value=None):
            match, score = best_semantic_match("hello", ["world"])
            assert match is None
            assert score == 0.0


class TestRankSemanticMatches:
    def test_ranks_by_similarity(self, mock_embedder):
        from shared.semantic_utils import rank_semantic_matches, _cached_embed
        _cached_embed.cache_clear()
        with patch("shared.semantic_utils._get_embedder", return_value=mock_embedder):
            ranked = rank_semantic_matches("male", ["Man", "Woman", "Other"], top_k=3)
            assert len(ranked) == 3
            assert ranked[0][0] == "Man"
            assert ranked[0][1] > ranked[1][1]

    def test_top_k_limits_results(self, mock_embedder):
        from shared.semantic_utils import rank_semantic_matches, _cached_embed
        _cached_embed.cache_clear()
        with patch("shared.semantic_utils._get_embedder", return_value=mock_embedder):
            ranked = rank_semantic_matches("male", ["Man", "Woman", "Other"], top_k=1)
            assert len(ranked) == 1


class TestAdaptiveWeights:
    def test_get_defaults_on_fresh_db(self, tmp_path):
        from shared.semantic_utils import get_adaptive_weights
        db = str(tmp_path / "weights.db")
        defaults = {"signal_a": 0.5, "signal_b": 0.3}
        result = get_adaptive_weights("test_component", defaults, db_path=db)
        assert result == defaults

    def test_record_outcome_adjusts_weights(self, tmp_path):
        from shared.semantic_utils import get_adaptive_weights, record_weight_outcome
        db = str(tmp_path / "weights.db")
        defaults = {"signal_a": 0.5, "signal_b": 0.5}
        get_adaptive_weights("test_component", defaults, db_path=db)
        for _ in range(10):
            record_weight_outcome(
                "test_component",
                {"signal_a": 1.0, "signal_b": 0.0},
                success=True,
                db_path=db,
            )
        weights = get_adaptive_weights("test_component", defaults, db_path=db)
        assert weights["signal_a"] > weights["signal_b"]


class TestEmbeddingCache:
    def test_caches_embeddings(self, mock_embedder):
        from shared.semantic_utils import semantic_similarity, _cached_embed
        _cached_embed.cache_clear()
        with patch("shared.semantic_utils._get_embedder", return_value=mock_embedder):
            semantic_similarity("male", "man")
            semantic_similarity("male", "woman")
            male_calls = [
                c for c in mock_embedder.embed.call_args_list
                if c[0][0].strip().lower() == "male"
            ]
            assert len(male_calls) == 1
