"""Auto-pattern extractor for learning from successful screening answers.

Observes answered questions over time, clusters semantically similar ones,
and extracts reusable answer patterns that improve future auto-answers.

Usage:
    extractor = PatternExtractor()
    extractor.observe(question, answer, intent, success)
    patterns = extractor.extract_patterns(intent=ScreeningIntent.WORK_AUTH)
"""

from __future__ import annotations

import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from shared.logging_config import get_logger
from shared.memory_layer._embedder import MemoryEmbedder
from shared.memory_layer._qdrant_store import QdrantStore
from shared.paths import DATA_DIR
from jobpulse.screening_intent import ScreeningIntent

logger = get_logger(__name__)

_SCREENING_TIER = "screening_patterns"


@dataclass
class AnswerPattern:
    """A learned answer pattern for an intent category."""

    pattern_id: str
    intent: ScreeningIntent
    pattern: str  # e.g., "I require visa sponsorship for {country}"
    slot_names: list[str] = field(default_factory=list)
    source_count: int = 0
    success_rate: float = 0.0
    confidence: float = 0.0


class PatternExtractor:
    """Extracts and manages reusable answer patterns from historical data."""

    def __init__(self, qdrant_url: str | None = None, embedder=None) -> None:
        self._embedder = embedder or MemoryEmbedder()
        self._qdrant = QdrantStore(location=qdrant_url or "", dims=self._embedder.dims)
        self._db_path = str(DATA_DIR / "screening_patterns.db")
        self._ensure_db()
        self._ensure_collection()

    # ── Database ──────────────────────────────────────────────────────────────

    def _ensure_db(self) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS answer_patterns (
                    pattern_id    TEXT PRIMARY KEY,
                    intent        TEXT NOT NULL,
                    pattern       TEXT NOT NULL,
                    slot_names    TEXT,  -- JSON array
                    source_count  INTEGER DEFAULT 0,
                    success_rate  REAL DEFAULT 0.0,
                    confidence    REAL DEFAULT 0.0,
                    created_at    TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS pattern_observations (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    pattern_id    TEXT,
                    intent        TEXT,
                    question      TEXT NOT NULL,
                    answer        TEXT NOT NULL,
                    success       INTEGER DEFAULT 1,
                    observed_at   TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (pattern_id) REFERENCES answer_patterns(pattern_id)
                )
            """)
            # Migrate: add intent column if missing (older schema)
            try:
                conn.execute("SELECT intent FROM pattern_observations LIMIT 1")
            except sqlite3.OperationalError:
                conn.execute("ALTER TABLE pattern_observations ADD COLUMN intent TEXT")

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_patterns_intent
                ON answer_patterns(intent)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_observations_intent
                ON pattern_observations(intent)
            """)
            conn.commit()

    def _ensure_collection(self) -> None:
        try:
            self._qdrant.create_collection(_SCREENING_TIER)
        except Exception:
            pass  # Already exists

    # ── Observation ───────────────────────────────────────────────────────────

    def observe(
        self,
        question: str,
        answer: str,
        intent: ScreeningIntent,
        success: bool = True,
        job_context: str = "",
    ) -> None:
        """Record an observation for future pattern extraction."""
        try:
            vec = self._embedder.embed(question)
            obs_id = _hash_question(question)
            self._qdrant.upsert(
                obs_id,
                _SCREENING_TIER,
                vec,
                {
                    "question": question,
                    "answer": answer,
                    "intent": intent.value,
                    "success": int(success),
                    "job_context": job_context,
                },
            )
            # Also store in SQLite for easy querying
            with sqlite3.connect(self._db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO pattern_observations
                    (intent, question, answer, success, observed_at)
                    VALUES (?, ?, ?, ?, datetime('now'))
                    """,
                    (intent.value, question, answer, int(success)),
                )
                conn.commit()
        except Exception as exc:
            logger.debug("Pattern observation failed: %s", exc)

    # ── Extraction ────────────────────────────────────────────────────────────

    def extract_patterns(
        self,
        intent: ScreeningIntent | None = None,
        min_observations: int = 3,
        min_success_rate: float = 0.75,
    ) -> list[AnswerPattern]:
        """Extract patterns from clusters of similar questions."""
        patterns: list[AnswerPattern] = []

        # Get observations for this intent
        observations = self._get_observations(intent)
        if len(observations) < min_observations:
            return patterns

        # Cluster by answer similarity (simple: exact match clustering)
        answer_clusters = self._cluster_by_answer(observations)

        for answers, questions in answer_clusters.items():
            if len(questions) < min_observations:
                continue

            # Check success rate
            successes = sum(1 for q, a, s in observations if a == answers)
            total = len(questions)
            success_rate = successes / total

            if success_rate < min_success_rate:
                continue

            # Extract pattern with slots
            pattern, slots = self._extract_template(questions, answers)
            if pattern and slots:
                patterns.append(
                    AnswerPattern(
                        pattern_id=_hash_question(pattern)[:16],
                        intent=intent or ScreeningIntent.UNKNOWN,
                        pattern=pattern,
                        slot_names=slots,
                        source_count=total,
                        success_rate=success_rate,
                        confidence=min(success_rate, 0.95),
                    )
                )

        return patterns

    def _get_observations(
        self,
        intent: ScreeningIntent | None,
    ) -> list[tuple[str, str, bool]]:
        """Get (question, answer, success) tuples from SQLite."""
        query = "SELECT question, answer, success FROM pattern_observations"
        params: tuple = ()
        if intent:
            # Need to filter by intent — join with qdrant metadata would be expensive,
            # so we store intent in the observation table for now
            query += " WHERE intent = ?"
            params = (intent.value,)
        query += " ORDER BY observed_at DESC LIMIT 1000"

        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute(query, params).fetchall()
        return [(q, a, bool(s)) for q, a, s in rows]

    def _cluster_by_answer(
        self,
        observations: list[tuple[str, str, bool]],
    ) -> dict[str, list[str]]:
        """Cluster observations by normalised answer text."""
        clusters: dict[str, list[str]] = defaultdict(list)
        for question, answer, _ in observations:
            norm = self._normalise_answer(answer)
            clusters[norm].append(question)
        return dict(clusters)

    @staticmethod
    def _normalise_answer(answer: str) -> str:
        """Normalise an answer for clustering."""
        t = str(answer).lower().strip()
        # Replace specific values with placeholders
        t = re.sub(r"\d+", "{N}", t)
        t = re.sub(r"\b(uk|us|usa|eu|gb|england|london)\b", "{LOCATION}", t)
        t = re.sub(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}\b", "{DATE}", t)
        t = re.sub(r"\b\d{1,2}\s+(week|month|day|year)s?\b", "{DURATION}", t)
        return t

    def _extract_template(
        self,
        questions: list[str],
        answer: str,
    ) -> tuple[str | None, list[str]]:
        """Extract a pattern with slots from a cluster of questions."""
        if not questions:
            return None, []

        # Find common prefix/suffix
        prefix = _common_prefix(questions)
        suffix = _common_suffix(questions)

        # Extract variable parts as slots
        slots: list[str] = []
        template = answer

        # Try to identify company/role/location from question differences
        for i, q in enumerate(questions):
            # Extract differences from common prefix/suffix
            middle = q[len(prefix):len(q) - len(suffix)] if suffix else q[len(prefix):]
            middle = middle.strip()
            if middle:
                slot_name = f"slot_{i}"
                slots.append(slot_name)
                # We can't create a real template without more sophisticated NLP
                # For now, return the answer as-is and let the caller handle it
                return answer, slots

        return answer, []

    # ── Pattern Application ───────────────────────────────────────────────────

    def find_matching_pattern(
        self,
        question: str,
        intent: ScreeningIntent,
    ) -> AnswerPattern | None:
        """Find a pattern that matches the given question."""
        patterns = self.extract_patterns(intent, min_observations=2, min_success_rate=0.6)
        if not patterns:
            return None

        # Try semantic similarity with pattern's anchor questions
        try:
            vec = self._embedder.embed(question)
            results = self._qdrant.search(
                _SCREENING_TIER,
                vec,
                top_k=5,
                score_threshold=0.80,
            )
            if results:
                # Find the pattern with highest success rate among matches
                best_pattern = max(patterns, key=lambda p: p.success_rate)
                return best_pattern
        except Exception as exc:
            logger.debug("Pattern matching failed: %s", exc)

        return None


def _hash_question(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _common_prefix(strings: list[str]) -> str:
    if not strings:
        return ""
    prefix = strings[0]
    for s in strings[1:]:
        while not s.startswith(prefix):
            prefix = prefix[:-1]
            if not prefix:
                return ""
    return prefix


def _common_suffix(strings: list[str]) -> str:
    if not strings:
        return ""
    suffix = strings[0]
    for s in strings[1:]:
        while not s.endswith(suffix):
            suffix = suffix[1:]
            if not suffix:
                return ""
    return suffix
