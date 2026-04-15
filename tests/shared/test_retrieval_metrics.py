"""Tests for retrieval quality metrics."""

from shared.retrieval_metrics import mrr, ndcg_at_k, recall_at_k


def test_mrr_perfect():
    """Relevant doc at rank 1 → MRR = 1.0."""
    assert mrr(["doc1"], [["doc1", "doc2", "doc3"]]) == 1.0


def test_mrr_rank_two():
    """Relevant doc at rank 2 → MRR = 0.5."""
    assert mrr(["doc1"], [["doc2", "doc1", "doc3"]]) == 0.5


def test_mrr_not_found():
    """Relevant doc not in results → MRR = 0.0."""
    assert mrr(["doc1"], [["doc2", "doc3"]]) == 0.0


def test_ndcg_perfect():
    """Perfect ranking → NDCG = 1.0."""
    relevance = {"doc1": 3, "doc2": 2, "doc3": 1}
    ranked = ["doc1", "doc2", "doc3"]
    assert ndcg_at_k(ranked, relevance, k=3) == 1.0


def test_ndcg_reversed():
    """Worst ranking → NDCG < 1.0."""
    relevance = {"doc1": 3, "doc2": 2, "doc3": 1}
    ranked = ["doc3", "doc2", "doc1"]
    assert ndcg_at_k(ranked, relevance, k=3) < 1.0


def test_recall_at_k():
    """2 of 3 relevant docs in top 5 → recall = 2/3."""
    relevant = {"doc1", "doc2", "doc3"}
    ranked = ["doc1", "doc4", "doc2", "doc5", "doc6"]
    assert abs(recall_at_k(ranked, relevant, k=5) - 2 / 3) < 0.01
