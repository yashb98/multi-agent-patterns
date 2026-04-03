"""Semantic answer cache for job application screening questions.

Uses sentence-transformer embeddings stored in SQLite for cosine-similarity matching.
Gracefully degrades to SHA-256 hash-based pseudo-embeddings when sentence-transformers
is not installed (fallback mode: only exact matches will score above threshold).
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from shared.logging_config import get_logger

logger = get_logger(__name__)

_DEFAULT_DB = Path("data/semantic_cache.db")
_MODEL_NAME = "all-MiniLM-L6-v2"


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two float vectors.

    Returns a value in [-1.0, 1.0]. Returns 0.0 if either vector is zero.
    """
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0.0 or mag_b == 0.0:
        return 0.0
    return dot / (mag_a * mag_b)


class SemanticAnswerCache:
    """Caches screening question answers keyed by semantic similarity.

    Stores question embeddings in SQLite. On lookup, performs a brute-force
    cosine scan over all cached rows and returns the best match above threshold.

    When sentence-transformers is unavailable, falls back to SHA-256 hash-based
    pseudo-embeddings (32 floats). In that mode only identical questions match.
    """

    def __init__(
        self,
        db_path: Path | str | None = None,
        threshold: float = 0.85,
    ) -> None:
        self.db_path = Path(db_path) if db_path is not None else _DEFAULT_DB
        self.threshold = threshold
        self._model: object | None = None
        self._model_loaded: bool = False
        self._fallback_mode: bool = False
        self._init_db()

    # ------------------------------------------------------------------
    # DB bootstrap
    # ------------------------------------------------------------------

    def _init_db(self) -> None:
        """Create the answer_cache table if it does not yet exist."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS answer_cache (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    question    TEXT    NOT NULL,
                    answer      TEXT    NOT NULL,
                    company     TEXT    NOT NULL DEFAULT '',
                    embedding   TEXT    NOT NULL,
                    times_used  INTEGER NOT NULL DEFAULT 1,
                    created_at  TEXT    NOT NULL
                )
                """
            )
            conn.commit()

    # ------------------------------------------------------------------
    # Embedding helpers
    # ------------------------------------------------------------------

    def _get_model(self) -> object | None:
        """Lazy-load the SentenceTransformer model.

        Sets _fallback_mode=True and returns None if the package is missing.
        """
        if self._model_loaded:
            return self._model

        self._model_loaded = True
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore[import]

            self._model = SentenceTransformer(_MODEL_NAME)
            logger.info("semantic_cache: loaded model %s", _MODEL_NAME)
        except Exception as exc:
            logger.warning(
                "semantic_cache: sentence-transformers unavailable (%s). "
                "Falling back to hash-based pseudo-embeddings — only exact matches will work.",
                exc,
            )
            self._fallback_mode = True
            self._model = None

        return self._model

    def _embed(self, text: str) -> list[float]:
        """Return a float embedding for *text*.

        Uses the sentence-transformer model when available; otherwise produces a
        32-float pseudo-embedding derived from the SHA-256 hash of the text.
        The pseudo-embedding is deterministic: identical text → identical vector,
        so only exact matches will clear the cosine threshold in fallback mode.
        """
        model = self._get_model()
        if model is not None:
            # SentenceTransformer.encode() returns a numpy array
            vec = model.encode(text, convert_to_numpy=True)  # type: ignore[attr-defined]
            return [float(v) for v in vec]

        # Fallback: SHA-256 → 32 floats in [0, 1]
        digest = hashlib.sha256(text.encode()).hexdigest()  # 64 hex chars
        return [int(digest[i : i + 2], 16) / 255.0 for i in range(0, 64, 2)]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def store(self, question: str, answer: str, company: str = "") -> None:
        """Store a question/answer pair with its embedding.

        If the exact same question text already exists, increments times_used
        instead of inserting a duplicate row.
        """
        with sqlite3.connect(self.db_path) as conn:
            existing = conn.execute(
                "SELECT id FROM answer_cache WHERE question = ? AND company = ?",
                (question, company),
            ).fetchone()

            if existing:
                conn.execute(
                    "UPDATE answer_cache SET times_used = times_used + 1 WHERE id = ?",
                    (existing[0],),
                )
                logger.debug(
                    "semantic_cache: incremented usage for cached question (id=%d)", existing[0]
                )
            else:
                embedding = self._embed(question)
                conn.execute(
                    """
                    INSERT INTO answer_cache
                        (question, answer, company, embedding, times_used, created_at)
                    VALUES (?, ?, ?, ?, 1, ?)
                    """,
                    (
                        question,
                        answer,
                        company,
                        json.dumps(embedding),
                        datetime.now(UTC).isoformat(),
                    ),
                )
                logger.debug("semantic_cache: stored new question (company=%r)", company)

            conn.commit()

    def find_similar(self, question: str, threshold: float | None = None) -> str | None:
        """Find the best-matching cached answer for *question*.

        Performs a brute-force cosine scan over all rows. Returns the answer
        string for the highest-scoring row if it meets *threshold*, else None.

        Args:
            question:  The screening question to look up.
            threshold: Override the instance-level threshold for this call.

        Returns:
            The cached answer string, or None if no match is good enough.
        """
        effective_threshold = threshold if threshold is not None else self.threshold

        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute("SELECT answer, embedding FROM answer_cache").fetchall()

        if not rows:
            return None

        query_emb = self._embed(question)
        best_score = -1.0
        best_answer: str | None = None

        for answer, embedding_json in rows:
            cached_emb: list[float] = json.loads(embedding_json)
            score = _cosine_similarity(query_emb, cached_emb)
            if score > best_score:
                best_score = score
                best_answer = answer

        if best_score >= effective_threshold:
            logger.debug(
                "semantic_cache: hit (score=%.4f, threshold=%.4f)", best_score, effective_threshold
            )
            return best_answer

        logger.debug(
            "semantic_cache: miss (best_score=%.4f, threshold=%.4f)",
            best_score,
            effective_threshold,
        )
        return None
