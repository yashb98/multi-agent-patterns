"""Tests for reranker integration with HybridSearch."""

from unittest.mock import patch, MagicMock
from shared.hybrid_search import HybridSearch


def test_reranker_called_after_rrf(tmp_path):
    """Reranker should be called on RRF results when available."""
    db_path = str(tmp_path / "test.db")
    hs = HybridSearch(db_path=db_path)

    # Add some documents
    hs.add("doc1", "Python authentication with JWT tokens")
    hs.add("doc2", "Building REST APIs with FastAPI framework")
    hs.add("doc3", "JWT token validation and refresh")

    mock_reranker = MagicMock()
    mock_reranker.rerank.return_value = [
        {"id": "doc3", "text": "JWT token validation and refresh", "score": 0.95},
        {"id": "doc1", "text": "Python authentication with JWT tokens", "score": 0.80},
    ]

    with patch("shared.hybrid_search.get_reranker", return_value=mock_reranker):
        results = hs.query("JWT auth", top_k=2)
        mock_reranker.rerank.assert_called_once()


def test_reranker_graceful_fallback(tmp_path):
    """When reranker unavailable, fall back to RRF-only results."""
    db_path = str(tmp_path / "test.db")
    hs = HybridSearch(db_path=db_path)
    hs.add("doc1", "Python authentication tokens")

    with patch("shared.hybrid_search.get_reranker", return_value=None):
        results = hs.query("Python authentication", top_k=5)
        assert len(results) >= 1  # Still works without reranker
