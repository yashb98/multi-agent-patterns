"""Cross-encoder reranker for search results.

Uses sentence-transformers CrossEncoder (ms-marco-MiniLM-L-12-v2) to rerank
candidates after RRF fusion. Gracefully degrades if not installed.

Usage:
    from shared.reranker import rerank_results
    reranked = rerank_results("auth tokens", candidates, top_k=5)
"""

from shared.logging_config import get_logger

logger = get_logger(__name__)

_cross_encoder = None
_import_failed = False


def _get_cross_encoder():
    """Lazy-load the cross-encoder model. Returns None if unavailable."""
    global _cross_encoder, _import_failed

    if _cross_encoder is not None:
        return _cross_encoder
    if _import_failed:
        return None

    try:
        from sentence_transformers import CrossEncoder

        _cross_encoder = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-12-v2")
        logger.info("Cross-encoder reranker loaded (ms-marco-MiniLM-L-12-v2)")
        return _cross_encoder
    except ImportError:
        _import_failed = True
        logger.info("sentence-transformers not installed — reranking disabled")
        return None
    except Exception as exc:
        _import_failed = True
        logger.warning("Cross-encoder load failed: %s", exc)
        return None


class Reranker:
    """Cross-encoder reranker with graceful fallback."""

    def rerank(
        self,
        query: str,
        candidates: list[dict],
        top_k: int = 5,
    ) -> list[dict]:
        """Rerank candidates using cross-encoder.

        Args:
            query: Search query string.
            candidates: List of dicts with at least "text" and "score" keys.
            top_k: Number of results to return.

        Returns:
            Reranked list of candidate dicts, with "rerank_score" added when
            the cross-encoder model is available.
        """
        if not candidates:
            return []

        model = _get_cross_encoder()
        if model is None:
            # Fallback: return top_k by original score
            sorted_cands = sorted(candidates, key=lambda c: c.get("score", 0), reverse=True)
            return sorted_cands[:top_k]

        pairs = [(query, c.get("text", "")) for c in candidates]
        try:
            scores = model.predict(pairs)
        except Exception as exc:
            logger.warning("Cross-encoder predict failed: %s", exc)
            sorted_cands = sorted(candidates, key=lambda c: c.get("score", 0), reverse=True)
            return sorted_cands[:top_k]

        for cand, score in zip(candidates, scores):
            cand["rerank_score"] = float(score)

        reranked = sorted(candidates, key=lambda c: c["rerank_score"], reverse=True)
        return reranked[:top_k]


def rerank_results(
    query: str,
    candidates: list[dict],
    top_k: int = 5,
) -> list[dict]:
    """Module-level convenience function."""
    return Reranker().rerank(query, candidates, top_k)
