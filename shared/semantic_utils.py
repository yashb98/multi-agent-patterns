"""Shared semantic analysis utilities.

Singleton embedder, numpy cosine similarity, and semantic matching functions
used by all semantic analysis components.
"""
from __future__ import annotations

import sqlite3
from functools import lru_cache
from pathlib import Path

import numpy as np

from shared.logging_config import get_logger

logger = get_logger(__name__)

_embedder_instance = None
_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "adaptive_weights.db"


def _get_embedder():
    """Lazy singleton MemoryEmbedder."""
    global _embedder_instance
    if _embedder_instance is None:
        try:
            from shared.memory_layer._embedder import MemoryEmbedder
            _embedder_instance = MemoryEmbedder()
        except Exception as exc:
            logger.warning("SemanticUtils: embedder unavailable (%s)", exc)
            return None
    return _embedder_instance


@lru_cache(maxsize=2048)
def _cached_embed(text: str) -> tuple[float, ...] | None:
    """Embed text and cache as tuple (hashable for LRU)."""
    embedder = _get_embedder()
    if embedder is None:
        return None
    try:
        vec = embedder.embed(text.strip())
        return tuple(vec)
    except Exception as exc:
        logger.debug("Embedding failed for '%s': %s", text[:50], exc)
        return None


def _to_numpy(vec: tuple[float, ...] | None) -> np.ndarray | None:
    if vec is None:
        return None
    return np.array(vec, dtype=np.float32)


def semantic_similarity(a: str, b: str) -> float:
    """Cosine similarity between two texts. Cached embeddings, numpy ops."""
    vec_a = _to_numpy(_cached_embed(a))
    vec_b = _to_numpy(_cached_embed(b))
    if vec_a is None or vec_b is None:
        return 0.0
    norm_a = np.linalg.norm(vec_a)
    norm_b = np.linalg.norm(vec_b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(vec_a, vec_b) / (norm_a * norm_b))


def best_semantic_match(
    query: str,
    candidates: list[str],
    min_score: float = 0.75,
) -> tuple[str | None, float]:
    """Find the best matching candidate by embedding similarity."""
    if not candidates or not query or not query.strip():
        return None, 0.0
    query_vec = _to_numpy(_cached_embed(query))
    if query_vec is None:
        return None, 0.0

    best_candidate: str | None = None
    best_score = 0.0
    norm_q = np.linalg.norm(query_vec)
    if norm_q == 0:
        return None, 0.0

    for candidate in candidates:
        cand_vec = _to_numpy(_cached_embed(candidate))
        if cand_vec is None:
            continue
        norm_c = np.linalg.norm(cand_vec)
        if norm_c == 0:
            continue
        score = float(np.dot(query_vec, cand_vec) / (norm_q * norm_c))
        if score > best_score:
            best_score = score
            best_candidate = candidate

    if best_score >= min_score:
        return best_candidate, best_score
    return None, best_score


def rank_semantic_matches(
    query: str,
    candidates: list[str],
    top_k: int = 5,
) -> list[tuple[str, float]]:
    """Rank candidates by descending cosine similarity."""
    if not candidates or not query or not query.strip():
        return []
    query_vec = _to_numpy(_cached_embed(query))
    if query_vec is None:
        return []

    norm_q = np.linalg.norm(query_vec)
    if norm_q == 0:
        return []
    scored: list[tuple[str, float]] = []
    for candidate in candidates:
        cand_vec = _to_numpy(_cached_embed(candidate))
        if cand_vec is None:
            continue
        norm_c = np.linalg.norm(cand_vec)
        if norm_c == 0:
            continue
        score = float(np.dot(query_vec, cand_vec) / (norm_q * norm_c))
        scored.append((candidate, score))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:top_k]


# ---------------------------------------------------------------------------
# Adaptive Weights
# ---------------------------------------------------------------------------

def _ensure_weights_db(db_path: str) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS adaptive_weights (
                component TEXT NOT NULL,
                signal_name TEXT NOT NULL,
                weight REAL NOT NULL,
                success_count INTEGER DEFAULT 0,
                failure_count INTEGER DEFAULT 0,
                PRIMARY KEY (component, signal_name)
            )
        """)


def get_adaptive_weights(
    component: str,
    defaults: dict[str, float],
    db_path: str | None = None,
) -> dict[str, float]:
    """Load adaptive weights. Initializes from defaults if first call."""
    path = db_path or str(_DB_PATH)
    _ensure_weights_db(path)
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT signal_name, weight FROM adaptive_weights WHERE component = ?",
            (component,),
        ).fetchall()
    if rows:
        return {r["signal_name"]: r["weight"] for r in rows}
    with sqlite3.connect(path) as conn:
        for signal, weight in defaults.items():
            conn.execute(
                "INSERT OR IGNORE INTO adaptive_weights (component, signal_name, weight) VALUES (?, ?, ?)",
                (component, signal, weight),
            )
    return dict(defaults)


def record_weight_outcome(
    component: str,
    signal_contributions: dict[str, float],
    success: bool,
    db_path: str | None = None,
) -> None:
    """Multiplicative update: +5% success, -5% failure, renormalize."""
    path = db_path or str(_DB_PATH)
    _ensure_weights_db(path)
    col = "success_count" if success else "failure_count"
    multiplier = 1.05 if success else 0.95

    with sqlite3.connect(path) as conn:
        for signal, contribution in signal_contributions.items():
            if contribution <= 0:
                continue
            conn.execute(
                f"UPDATE adaptive_weights SET weight = weight * ?, {col} = {col} + 1 WHERE component = ? AND signal_name = ?",
                (multiplier, component, signal),
            )
        rows = conn.execute(
            "SELECT signal_name, weight FROM adaptive_weights WHERE component = ?",
            (component,),
        ).fetchall()
        if rows:
            total = sum(r[1] for r in rows)
            if total > 0:
                for r in rows:
                    conn.execute(
                        "UPDATE adaptive_weights SET weight = ? WHERE component = ? AND signal_name = ?",
                        (r[1] / total, component, r[0]),
                    )
