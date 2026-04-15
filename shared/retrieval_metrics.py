"""Retrieval quality metrics: MRR, NDCG@k, recall@k.

Used to benchmark hybrid search quality and detect regressions.
"""

import math


def mrr(relevant_ids: list[str], ranked_lists: list[list[str]]) -> float:
    """Mean Reciprocal Rank across multiple queries."""
    total = 0.0
    for rel_id, ranked in zip(relevant_ids, ranked_lists):
        for i, doc_id in enumerate(ranked):
            if doc_id == rel_id:
                total += 1.0 / (i + 1)
                break
    return total / len(ranked_lists) if ranked_lists else 0.0


def dcg_at_k(ranked: list[str], relevance: dict[str, float], k: int) -> float:
    """Discounted Cumulative Gain at rank k."""
    score = 0.0
    for i, doc_id in enumerate(ranked[:k]):
        rel = relevance.get(doc_id, 0)
        score += rel / math.log2(i + 2)
    return score


def ndcg_at_k(ranked: list[str], relevance: dict[str, float], k: int) -> float:
    """Normalized DCG — ratio of actual to ideal ranking."""
    actual = dcg_at_k(ranked, relevance, k)
    ideal_ranked = sorted(relevance.keys(), key=lambda d: relevance[d], reverse=True)
    ideal = dcg_at_k(ideal_ranked, relevance, k)
    return actual / ideal if ideal > 0 else 0.0


def recall_at_k(ranked: list[str], relevant: set[str], k: int) -> float:
    """Fraction of relevant docs found in top k results."""
    found = sum(1 for doc_id in ranked[:k] if doc_id in relevant)
    return found / len(relevant) if relevant else 0.0
