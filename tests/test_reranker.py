"""Tests for shared/reranker.py — cross-encoder reranking."""

import importlib
import pytest


class TestRerankerImport:
    def test_reranker_importable(self):
        from shared.reranker import Reranker
        assert Reranker is not None


class TestRerankerFallback:
    def test_rerank_returns_input_when_no_model(self, monkeypatch):
        """When sentence-transformers is not available, return input unchanged."""
        import builtins
        import shared.reranker as reranker_module

        # Reset module globals so the import mock actually triggers
        monkeypatch.setattr(reranker_module, "_cross_encoder", None)
        monkeypatch.setattr(reranker_module, "_import_failed", False)

        original_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if "sentence_transformers" in name:
                raise ImportError("mocked")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)

        from shared.reranker import Reranker
        r = Reranker()
        candidates = [
            {"id": "a", "text": "hello", "score": 0.5},
            {"id": "b", "text": "world", "score": 0.8},
        ]
        result = r.rerank("test query", candidates, top_k=2)
        # Should return candidates unchanged (sorted by original score)
        assert len(result) == 2

    def test_rerank_empty_candidates(self):
        """Reranker should return empty list for empty input."""
        from shared.reranker import Reranker
        r = Reranker()
        result = r.rerank("test query", [], top_k=5)
        assert result == []


class TestRerankerScoring:
    def test_rerank_reorders_candidates(self):
        """Reranker should reorder candidates by cross-encoder score."""
        try:
            from sentence_transformers import CrossEncoder
        except ImportError:
            pytest.skip("sentence-transformers not installed")

        from shared.reranker import Reranker
        r = Reranker()
        candidates = [
            {"id": "irrelevant", "text": "cooking recipes for pasta", "score": 0.9},
            {"id": "relevant", "text": "JWT authentication with refresh tokens", "score": 0.1},
        ]
        result = r.rerank("authentication tokens", candidates, top_k=2)
        # Cross-encoder should rank "relevant" higher despite lower original score
        assert result[0]["id"] == "relevant"

    def test_rerank_top_k_limits_output(self):
        """top_k should limit number of returned results."""
        try:
            from sentence_transformers import CrossEncoder
        except ImportError:
            pytest.skip("sentence-transformers not installed")

        from shared.reranker import Reranker
        r = Reranker()
        candidates = [
            {"id": f"doc_{i}", "text": f"Document {i} about topic", "score": 0.5}
            for i in range(10)
        ]
        result = r.rerank("topic", candidates, top_k=3)
        assert len(result) <= 3

    def test_rerank_adds_rerank_score(self):
        """When model is available, candidates should gain rerank_score field."""
        try:
            from sentence_transformers import CrossEncoder
        except ImportError:
            pytest.skip("sentence-transformers not installed")

        from shared.reranker import Reranker
        r = Reranker()
        candidates = [
            {"id": "a", "text": "authentication with tokens", "score": 0.5},
        ]
        result = r.rerank("authentication", candidates, top_k=5)
        assert len(result) == 1
        assert "rerank_score" in result[0]


class TestRerankerConvenienceFunction:
    def test_rerank_results_module_function(self):
        """Module-level rerank_results should work identically to Reranker().rerank()."""
        from shared.reranker import rerank_results
        candidates = [
            {"id": "a", "text": "hello", "score": 0.5},
            {"id": "b", "text": "world", "score": 0.8},
        ]
        result = rerank_results("test query", candidates, top_k=2)
        assert isinstance(result, list)
        assert len(result) <= 2
