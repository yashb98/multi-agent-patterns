"""Semantic cache for screening questions using Qdrant + SQLite shadow.

Eliminates exact-match cache misses by storing question embeddings and
searching by cosine similarity. Paraphrased questions (e.g. "What's your
salary?" vs "Current compensation?") hit the same cached answer.

Graceful degradation: if Qdrant is unavailable, falls back to SQLite-only.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Optional

from shared.logging_config import get_logger

logger = get_logger(__name__)

_COLLECTION_NAME = "screening_questions"
_DEFAULT_SQLITE_PATH = None  # resolved lazily


def _default_sqlite_path() -> str:
    from jobpulse.config import DATA_DIR
    return str(DATA_DIR / "screening_semantic_cache.db")


def _to_qdrant_id(text: str) -> int:
    """Stable unsigned 64-bit ID from text."""
    return int(hashlib.md5(text.encode()).hexdigest(), 16) % (2 ** 63)



@dataclass
class CacheHit:
    answer: str
    intent: str
    score: float  # cosine similarity 0.0-1.0
    times_used: int
    success_count: int
    correction_count: int
    selected_option: str = ""
    field_type: str = ""


class ScreeningSemanticCache:
    """Qdrant-backed semantic cache for screening questions.

    Usage:
        cache = ScreeningSemanticCache()
        cache.cache("What's your salary?", intent="salary_expected", answer="35000")
        hit = cache.lookup("Current compensation?")
        if hit:
            print(hit.answer)  # "35000"
    """

    def __init__(
        self,
        sqlite_path: str | None = None,
        qdrant_location: str | None = None,
        embedder: object | None = None,
    ) -> None:
        self._sqlite_path = sqlite_path or _default_sqlite_path()
        self._embedder = embedder
        self._qdrant: Optional[object] = None
        self._qdrant_available = False
        self._dims = 384  # MiniLM fallback default

        # Resolve embedder dims BEFORE creating Qdrant collection
        if self._embedder is None:
            try:
                from shared.semantic_utils import _get_embedder
                self._embedder = _get_embedder()
                if self._embedder:
                    self._dims = self._embedder.dims
            except Exception as exc:
                logger.warning("ScreeningSemanticCache: Embedder init failed (%s). Semantic search disabled.", exc)
                self._embedder = None
        else:
            self._dims = self._embedder.dims

        # Probe Qdrant (after embedder so dims are correct)
        qdrant_url = qdrant_location or _get_qdrant_url_from_env()
        if qdrant_url:
            try:
                from qdrant_client import QdrantClient
                self._qdrant = QdrantClient(url=qdrant_url)
                self._qdrant_available = True
                self._ensure_collection()
                logger.info("ScreeningSemanticCache: Qdrant connected at %s", qdrant_url)
            except Exception as exc:
                logger.warning("ScreeningSemanticCache: Qdrant unavailable (%s). SQLite-only mode.", exc)

        self._init_sqlite()

    # ------------------------------------------------------------------
    # Qdrant helpers
    # ------------------------------------------------------------------

    def _ensure_collection(self) -> None:
        if not self._qdrant_available or self._qdrant is None:
            return
        try:
            from qdrant_client import models as qm
            existing = {c.name for c in self._qdrant.get_collections().collections}
            if _COLLECTION_NAME not in existing:
                self._qdrant.create_collection(
                    collection_name=_COLLECTION_NAME,
                    vectors_config=qm.VectorParams(
                        size=self._dims,
                        distance=qm.Distance.COSINE,
                    ),
                )
                logger.info("Created Qdrant collection '%s' (dims=%d)", _COLLECTION_NAME, self._dims)
        except Exception as exc:
            logger.warning("Failed to ensure Qdrant collection: %s", exc)
            self._qdrant_available = False

    # ------------------------------------------------------------------
    # SQLite helpers
    # ------------------------------------------------------------------

    def _init_sqlite(self) -> None:
        with sqlite3.connect(self._sqlite_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS screening_semantic_cache (
                    qdrant_id TEXT PRIMARY KEY,
                    question_text TEXT NOT NULL,
                    intent TEXT NOT NULL DEFAULT 'unknown',
                    answer TEXT NOT NULL,
                    confidence REAL NOT NULL DEFAULT 0.0,
                    times_used INTEGER DEFAULT 0,
                    success_count INTEGER DEFAULT 0,
                    correction_count INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL,
                    last_used_at TEXT
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_screening_intent
                ON screening_semantic_cache(intent)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_screening_question
                ON screening_semantic_cache(question_text)
            """)

            # Migration: add option-aware + embedding columns
            existing = {r[1] for r in conn.execute("PRAGMA table_info(screening_semantic_cache)").fetchall()}
            for col, typ in [
                ("selected_option", "TEXT DEFAULT ''"),
                ("field_type", "TEXT DEFAULT ''"),
                ("field_options_json", "TEXT DEFAULT ''"),
                ("embedding_vector", "TEXT DEFAULT ''"),
            ]:
                if col not in existing:
                    conn.execute(f"ALTER TABLE screening_semantic_cache ADD COLUMN {col} {typ}")

    def _sqlite_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._sqlite_path)
        conn.row_factory = sqlite3.Row
        return conn

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def cache(
        self,
        question: str,
        intent: str,
        answer: str,
        confidence: float = 0.0,
        job_context_hash: str = "",
        selected_option: str = "",
        field_type: str = "",
        field_options: list[str] | None = None,
    ) -> None:
        """Store a question+answer pair in the semantic cache.

        For option-based fields (dropdowns, radios, checkboxes), also store
        the exact option text that was selected and the available options,
        so future lookups can align to different option sets.
        """
        if not question or not answer:
            return

        qid = str(_to_qdrant_id(question.strip().lower()))
        now = datetime.now(UTC).isoformat()
        options_json = json.dumps(field_options) if field_options else ""

        # Compute embedding once — reused for both SQLite and Qdrant
        vec_json = ""
        vector: list[float] | None = None
        if self._embedder is not None:
            try:
                vector = self._embedder.embed(question.strip())
                vec_json = json.dumps(vector)
            except Exception as exc:
                logger.debug("Embedding failed during cache: %s", exc)

        # SQLite upsert (includes pre-computed embedding vector)
        with self._sqlite_conn() as conn:
            conn.execute(
                """
                INSERT INTO screening_semantic_cache
                (qdrant_id, question_text, intent, answer, confidence, times_used,
                 created_at, last_used_at, selected_option, field_type, field_options_json,
                 embedding_vector)
                VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(qdrant_id) DO UPDATE SET
                    last_used_at = excluded.last_used_at,
                    answer = excluded.answer,
                    intent = excluded.intent,
                    confidence = excluded.confidence,
                    selected_option = CASE WHEN excluded.selected_option != '' THEN excluded.selected_option ELSE selected_option END,
                    field_type = CASE WHEN excluded.field_type != '' THEN excluded.field_type ELSE field_type END,
                    field_options_json = CASE WHEN excluded.field_options_json != '' THEN excluded.field_options_json ELSE field_options_json END,
                    embedding_vector = CASE WHEN excluded.embedding_vector != '' THEN excluded.embedding_vector ELSE embedding_vector END
                """,
                (qid, question.strip(), intent, answer, confidence, now, now,
                 selected_option, field_type, options_json, vec_json),
            )

        # Qdrant upsert (reuses pre-computed vector)
        if self._qdrant_available and vector is not None and self._qdrant is not None:
            try:
                from qdrant_client import models as qm
                self._qdrant.upsert(
                    collection_name=_COLLECTION_NAME,
                    points=[
                        qm.PointStruct(
                            id=_to_qdrant_id(question.strip().lower()),
                            vector=vector,
                            payload={
                                "qdrant_id": qid,
                                "question_text": question.strip(),
                                "intent": intent,
                                "answer": answer,
                                "confidence": confidence,
                                "job_context_hash": job_context_hash,
                                "selected_option": selected_option,
                                "field_type": field_type,
                                # Persist the options that were on the form
                                # when this answer was correct. Future hits
                                # use this list to align our cached answer
                                "field_options": field_options or [],
                            },
                        )
                    ],
                )
            except Exception as exc:
                logger.debug("Qdrant upsert failed: %s", exc)

    def lookup(
        self,
        question: str,
        min_score: float = 0.85,
        field_options: list[str] | None = None,
        field_type: str = "",
    ) -> CacheHit | None:
        """Search for a semantically similar question. Returns best match above threshold.

        If field_options are provided (dropdown/radio/checkbox), the returned
        answer is aligned to the best-matching option using the OptionAligner.
        """
        if not question or not question.strip():
            return None

        hit: CacheHit | None = None

        # 1. Try Qdrant first.
        # Gap 3: when the field has options, fetch the top-K matches (not just
        # top-1) and filter them down to entries whose stored answer/selected
        # option IS in the current field's options. This prevents a
        # semantically-similar but option-incompatible cache entry from
        # winning the lookup. Without this, a question like "Are you eligible
        # to work in the country..." matches "Country*" via shared "country"
        # tokens, and the cached "United Kingdom" answer leaks into a Yes/No
        # field. Filtering at search-time complements the align-time fix in
        # _align_to_options for defence-in-depth.
        if self._qdrant_available and self._embedder is not None and self._qdrant is not None:
            try:
                vector = self._embedder.embed(question.strip())
                from qdrant_client import models as qm
                limit = 10 if field_options else 1
                results = self._qdrant.query_points(
                    collection_name=_COLLECTION_NAME,
                    query=vector,
                    limit=limit,
                    score_threshold=min_score,
                )
                points = list(results.points)
                if points and field_options:
                    options_lower = {(o or "").lower().strip() for o in field_options}
                    points = [
                        p for p in points
                        if (p.payload.get("answer", "") or "").lower().strip() in options_lower
                        or (p.payload.get("selected_option", "") or "").lower().strip() in options_lower
                    ] or points[:1]  # if zero option-compatible, fall back to top — let _align_to_options decide
                if points:
                    point = points[0]
                    self._touch_sqlite(str(point.payload.get("qdrant_id", "")))
                    hit = CacheHit(
                        answer=point.payload.get("answer", ""),
                        intent=point.payload.get("intent", "unknown"),
                        score=point.score,
                        times_used=0,
                        success_count=0,
                        correction_count=0,
                        selected_option=point.payload.get("selected_option", ""),
                        field_type=point.payload.get("field_type", ""),
                    )
            except Exception as exc:
                logger.debug("Qdrant lookup failed: %s", exc)

        # 2. Fallback: cosine over pre-computed vectors in SQLite (one embed for query only)
        if hit is None and self._embedder is not None:
            try:
                query_vec = self._embedder.embed(question.strip())
                with self._sqlite_conn() as conn:
                    rows = conn.execute(
                        "SELECT qdrant_id, question_text, intent, answer, confidence,"
                        " selected_option, field_type, embedding_vector"
                        " FROM screening_semantic_cache"
                    ).fetchall()

                best: tuple[float, sqlite3.Row] | None = None
                backfill: list[tuple[str, str]] = []
                for row in rows:
                    vec_json = row["embedding_vector"]
                    if vec_json:
                        row_vec = json.loads(vec_json)
                    else:
                        # Legacy entry — embed once and queue for backfill
                        row_vec = self._embedder.embed(row["question_text"])
                        backfill.append((json.dumps(row_vec), row["qdrant_id"]))
                    import numpy as np
                    a = np.array(query_vec, dtype=np.float32)
                    b = np.array(row_vec, dtype=np.float32)
                    norm_a = np.linalg.norm(a)
                    norm_b = np.linalg.norm(b)
                    score = float(np.dot(a, b) / (norm_a * norm_b)) if norm_a > 0 and norm_b > 0 else 0.0
                    if score >= min_score and (best is None or score > best[0]):
                        best = (score, row)

                # Backfill legacy entries so future lookups skip embedding
                if backfill:
                    with self._sqlite_conn() as conn:
                        conn.executemany(
                            "UPDATE screening_semantic_cache SET embedding_vector = ? WHERE qdrant_id = ?",
                            backfill,
                        )

                if best:
                    score, row = best
                    self._touch_sqlite(row["qdrant_id"])
                    hit = CacheHit(
                        answer=row["answer"],
                        intent=row["intent"],
                        score=score,
                        times_used=0,
                        success_count=0,
                        correction_count=0,
                        selected_option=row["selected_option"] or "",
                        field_type=row["field_type"] or "",
                    )
            except Exception as exc:
                logger.debug("Brute-force semantic lookup failed: %s", exc)

        if hit is None:
            return None

        # 3. Option alignment: map cached answer to current field options
        if field_options:
            hit = self._align_to_options(hit, field_options, field_type)

        return hit

    def _align_to_options(
        self,
        hit: CacheHit,
        field_options: list[str],
        field_type: str,
    ) -> CacheHit | None:
        """Align a cache hit's answer to the current field's available options.

        Returns the aligned hit, or `None` when the aligned answer is not in
        `field_options` — the caller treats `None` as a cache miss and falls
        through to the LLM tier with an options constraint.
        """
        from jobpulse.screening_option_aligner import (
            OptionAligner, BoolFieldHandler, SalaryFieldHandler,
        )

        aligner = OptionAligner()
        options_lower = [o.lower().strip() for o in field_options]

        # Priority 1: cached selected_option matches current options exactly
        if hit.selected_option:
            sel_lower = hit.selected_option.lower().strip()
            if sel_lower in options_lower:
                idx = options_lower.index(sel_lower)
                hit.answer = field_options[idx]
                return hit

        # Priority 2: cached answer matches an option directly
        ans_lower = hit.answer.lower().strip()
        if ans_lower in options_lower:
            idx = options_lower.index(ans_lower)
            hit.answer = field_options[idx]
            return hit

        # Priority 3: salary range bracket matching
        if SalaryFieldHandler.extract_numeric(hit.answer):
            range_match = SalaryFieldHandler.format_for_range(hit.answer, field_options)
            if range_match != hit.answer:
                hit.selected_option = range_match
                hit.answer = range_match
                return hit

        # Priority 4: boolean resolution (yes/no → specific option text)
        is_bool = BoolFieldHandler.is_boolean_field({"options": field_options, "type": field_type})
        if is_bool:
            resolved = BoolFieldHandler.resolve(hit.answer, field_options)
            if resolved != hit.answer:
                hit.selected_option = resolved
                hit.answer = resolved
                return hit
            # Long affirmative/negative answer → infer boolean meaning
            inferred = _infer_boolean_from_text(hit.answer)
            if inferred is not None:
                target = field_options[0] if inferred else (field_options[1] if len(field_options) > 1 else field_options[0])
                hit.selected_option = target
                hit.answer = target
                return hit

        # Priority 5: fuzzy alignment via OptionAligner.
        aligned = aligner.align_answer(hit.answer, field_options, field_type)
        # OptionAligner returns the original answer unchanged when no option
        # is similar enough. That's a cache MISS for an option-bearing field —
        # we must not return a free-text answer for a closed-set picker.
        # Returning None forces the caller's V2 pipeline to fall through to
        # the LLM tier, which is option-constrained (Fix #4) and will pick
        # one of `field_options` exactly. Without this, semantic-similar
        # cache hits from unrelated questions (e.g. cached "Country*" →
        # "United Kingdom" matching against "Are you eligible to work in the
        # country...?" via the shared word "country") leak into option-only
        # fields, producing a country name as the answer to a Yes/No.
        options_lower_set = {o.lower().strip() for o in field_options}
        if aligned.lower().strip() not in options_lower_set:
            logger.info(
                "screening_cache: dropping non-option answer %r for field with "
                "options %s — forcing LLM-tier regeneration",
                hit.answer[:60], [o[:30] for o in field_options[:5]],
            )
            return None  # signal cache miss → V2 → LLM tier with options constraint
        if aligned != hit.answer:
            hit.selected_option = aligned
        hit.answer = aligned
        return hit

    def record_outcome(self, question: str, success: bool) -> None:
        """Update success/correction counters for a cached question."""
        qid = str(_to_qdrant_id(question.strip().lower()))
        with self._sqlite_conn() as conn:
            if success:
                conn.execute(
                    "UPDATE screening_semantic_cache SET success_count = success_count + 1 WHERE qdrant_id = ?",
                    (qid,),
                )
            else:
                conn.execute(
                    "UPDATE screening_semantic_cache SET correction_count = correction_count + 1 WHERE qdrant_id = ?",
                    (qid,),
                )

    def prune_stale(self, max_age_days: int = 90, min_success_rate: float = 0.1) -> int:
        """Remove old entries with poor success rates. Returns deleted count."""
        cutoff = (datetime.now(UTC) - __import__("datetime").timedelta(days=max_age_days)).isoformat()
        with self._sqlite_conn() as conn:
            # Delete very old entries
            cur = conn.execute(
                "DELETE FROM screening_semantic_cache WHERE created_at < ?",
                (cutoff,),
            )
            old_deleted = cur.rowcount

            # Delete low-success entries (sufficient sample size)
            cur = conn.execute(
                """
                DELETE FROM screening_semantic_cache
                WHERE times_used >= 5
                  AND CAST(success_count AS REAL) / times_used < ?
                """,
                (min_success_rate,),
            )
            bad_deleted = cur.rowcount

        total = old_deleted + bad_deleted
        if total > 0:
            logger.info("Pruned %d stale screening cache entries", total)
        return total

    def get_stats(self) -> dict:
        """Return cache statistics."""
        with self._sqlite_conn() as conn:
            total = conn.execute("SELECT COUNT(*) FROM screening_semantic_cache").fetchone()[0]
            intents = conn.execute(
                "SELECT intent, COUNT(*) as cnt FROM screening_semantic_cache GROUP BY intent"
            ).fetchall()
        return {
            "total_entries": total,
            "qdrant_available": self._qdrant_available,
            "embedder_available": self._embedder is not None,
            "intents": {r["intent"]: r["cnt"] for r in intents},
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _touch_sqlite(self, qdrant_id: str) -> None:
        now = datetime.now(UTC).isoformat()
        with self._sqlite_conn() as conn:
            conn.execute(
                "UPDATE screening_semantic_cache SET last_used_at = ? WHERE qdrant_id = ?",
                (now, qdrant_id),
            )

    def _qid_for(self, question: str) -> str:
        """Return the qdrant_id string for a question."""
        return str(_to_qdrant_id(question.strip().lower()))

    def increment_usage(self, question: str) -> None:
        """Increment times_used for a question. Only way to bump the counter."""
        qid = self._qid_for(question)
        with self._sqlite_conn() as conn:
            conn.execute(
                "UPDATE screening_semantic_cache SET times_used = times_used + 1 WHERE qdrant_id = ?",
                (qid,),
            )


# ------------------------------------------------------------------
# Singleton factory
# ------------------------------------------------------------------

_cached_instance: ScreeningSemanticCache | None = None


def get_screening_semantic_cache() -> ScreeningSemanticCache:
    """Return shared singleton."""
    global _cached_instance
    if _cached_instance is None:
        _cached_instance = ScreeningSemanticCache()
    return _cached_instance


def _infer_boolean_from_text(text: str) -> bool | None:
    """Infer yes/no meaning from a long-form answer using embedding similarity."""
    if not text or len(text.strip()) < 8:
        return None
    try:
        from shared.semantic_utils import semantic_similarity
        yes_score = semantic_similarity(text, "yes I do, I am, I have, I can, I agree")
        no_score = semantic_similarity(text, "no I do not, I am not, I cannot, I don't have")
        if yes_score > no_score and yes_score > 0.5:
            return True
        if no_score > yes_score and no_score > 0.5:
            return False
    except Exception:
        pass
    return None


def _get_qdrant_url_from_env() -> str:
    import os
    return os.environ.get("MEMORY_QDRANT_URL", "").strip()


def _get_qdrant_client():
    """Return a connected QdrantClient, or None if Qdrant is unavailable.

    Used by sibling subsystems (e.g. `cross_platform_field_transfer`) that
    need to share the same Qdrant configuration as the screening cache
    without instantiating a `ScreeningSemanticCache`. Audit S4 B-3 added
    this accessor — its absence had been silently breaking the
    cross-platform vector path.
    """
    url = _get_qdrant_url_from_env()
    if not url:
        return None
    try:
        from qdrant_client import QdrantClient
        return QdrantClient(url=url)
    except Exception as exc:
        logger.debug("_get_qdrant_client: Qdrant unavailable (%s)", exc)
        return None
