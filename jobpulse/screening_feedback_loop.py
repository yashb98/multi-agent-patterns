"""Screening V2 feedback loop — corrections teach the pipeline.

When a user corrects a screening answer, this module updates all V2
subsystems so the same mistake doesn't happen again:

  1. Semantic Cache — marks the old answer as corrected, caches the right one
  2. Intent Classifier — adds the question as a learned prototype
  3. Option Aligner — learns correct option mappings
  4. Pattern Extractor — records the failure pattern for future avoidance

Usage:
    from jobpulse.screening_feedback_loop import ScreeningFeedbackLoop

    loop = ScreeningFeedbackLoop()
    loop.learn_from_correction(
        question="What is your current salary?",
        agent_answer="40000",
        user_answer="45000",
        field_options=["30000", "40000", "45000", "50000"],
        platform="greenhouse",
        domain="quantitative_trading",
    )
"""

from __future__ import annotations

from typing import Any

from shared.logging_config import get_logger

logger = get_logger(__name__)


class ScreeningFeedbackLoop:
    """Closes the feedback loop from user corrections to V2 pipeline learning."""

    def __init__(
        self,
        *,
        cache: Any = None,
        classifier: Any = None,
        aligner: Any = None,
        extractor: Any = None,
    ) -> None:
        self._cache = cache
        self._classifier = classifier
        self._aligner = aligner
        self._extractor = extractor
        self._ScreeningIntent = None

        if cache is None or classifier is None or aligner is None or extractor is None:
            self._init_subsystems()

    def _init_subsystems(self) -> None:
        """Lazy-init V2 subsystems."""
        if self._cache is None:
            try:
                from jobpulse.screening_semantic_cache import ScreeningSemanticCache
                self._cache = ScreeningSemanticCache()
            except Exception as exc:
                logger.debug("Semantic cache unavailable for feedback: %s", exc)

        if self._classifier is None:
            try:
                from jobpulse.screening_intent import ScreeningIntentClassifier, ScreeningIntent
                self._classifier = ScreeningIntentClassifier()
                self._ScreeningIntent = ScreeningIntent
            except Exception as exc:
                logger.debug("Intent classifier unavailable for feedback: %s", exc)

        if self._aligner is None:
            try:
                from jobpulse.screening_option_aligner import OptionAligner
                self._aligner = OptionAligner()
            except Exception as exc:
                logger.debug("Option aligner unavailable for feedback: %s", exc)

        if self._extractor is None:
            try:
                from jobpulse.screening_pattern_extractor import PatternExtractor
                self._extractor = PatternExtractor()
            except Exception as exc:
                logger.debug("Pattern extractor unavailable for feedback: %s", exc)

    def learn_from_correction(
        self,
        question: str,
        agent_answer: str,
        user_answer: str,
        *,
        field_options: list[str] | None = None,
        field_type: str = "",
        platform: str = "",
        domain: str = "",
        job_context: dict[str, Any] | None = None,
    ) -> dict:
        """Process a single correction and update all learnable subsystems.

        Returns a dict with what was updated.
        """
        result: dict[str, bool] = {
            "cache_updated": False,
            "intent_learned": False,
            "option_aligned": False,
            "pattern_recorded": False,
        }

        if not question or not question.strip():
            return result

        q = question.strip()

        # 1. Semantic Cache: cache the corrected answer with option context
        if self._cache is not None:
            try:
                if user_answer and str(user_answer).strip():
                    intent = self._infer_intent(q)
                    self._cache.cache(
                        question=q,
                        intent=intent,
                        answer=str(user_answer).strip(),
                        confidence=0.95,  # High confidence — user-verified
                        selected_option=str(user_answer).strip() if field_options else "",
                        field_type=field_type,
                        field_options=field_options,
                    )
                self._cache.record_outcome(q, success=False)
                result["cache_updated"] = True
            except Exception as exc:
                logger.debug("Cache feedback failed: %s", exc)

        # 2. Intent Classifier: add question as a prototype for inferred intent
        if self._classifier is not None and self._ScreeningIntent is not None:
            try:
                intent, _ = self._classifier.classify(q)
                if intent and intent != self._ScreeningIntent.UNKNOWN:
                    self._classifier.add_intent_example(intent, q)
                    result["intent_learned"] = True
            except Exception as exc:
                logger.debug("Intent feedback failed: %s", exc)

        # 3. Option Aligner: learn mapping from wrong to correct option
        if self._aligner is not None and field_options:
            try:
                aligned_wrong = self._aligner.align_answer(
                    str(agent_answer), field_options, field_type,
                )
                aligned_right = self._aligner.align_answer(
                    str(user_answer), field_options, field_type,
                )
                if aligned_wrong != aligned_right:
                    self._learn_option_mapping(
                        agent_answer, user_answer, field_options, field_type,
                    )
                    result["option_aligned"] = True
            except Exception as exc:
                logger.debug("Option aligner feedback failed: %s", exc)

        # 4. Pattern Extractor: record the failure for future avoidance
        if self._extractor is not None:
            try:
                # Audit S4 B-4: must default to UNKNOWN, never None.
                # PatternExtractor.observe() reads `intent.value` on its
                # input, so a None intent crashes silently inside the
                # outer try/except and the observation is dropped (rows
                # never persisted). Lazy-import here to keep the
                # module-level deps minimal.
                from jobpulse.screening_intent import ScreeningIntent
                intent_val: ScreeningIntent = ScreeningIntent.UNKNOWN
                if self._classifier is not None:
                    intent, _ = self._classifier.classify(q)
                    if intent is not None:
                        intent_val = intent
                self._extractor.observe(
                    question=q,
                    answer=str(agent_answer),
                    intent=intent_val,
                    success=False,
                )
                # Also observe the successful correction
                self._extractor.observe(
                    question=q,
                    answer=str(user_answer),
                    intent=intent_val,
                    success=True,
                )
                result["pattern_recorded"] = True
            except Exception as exc:
                logger.debug("Pattern extractor feedback failed: %s", exc)

        # 5. Cross-platform transfer: record the corrected mapping
        try:
            from jobpulse.cross_platform_field_transfer import CrossPlatformFieldTransfer
            transfer = CrossPlatformFieldTransfer()
            transfer.record_mapping(
                platform=platform or "unknown",
                field_label=q,
                value=str(user_answer).strip(),
                source="user_correction",
                success=True,
            )
        except Exception as exc:
            logger.debug("Cross-platform transfer feedback failed: %s", exc)

        logger.info(
            "screening_feedback: learned from correction on '%s...' "
            "(cache=%s intent=%s option=%s pattern=%s)",
            q[:50],
            result["cache_updated"],
            result["intent_learned"],
            result["option_aligned"],
            result["pattern_recorded"],
        )
        return result

    def _infer_intent(self, question: str) -> str:
        """Infer intent string for a question."""
        if self._classifier is not None:
            try:
                intent, _ = self._classifier.classify(question)
                if intent:
                    return intent.value
            except Exception:
                pass
        return "unknown"

    def _learn_option_mapping(
        self,
        agent_answer: str,
        user_answer: str,
        field_options: list[str],
        field_type: str,
    ) -> None:
        """Learn that agent_answer should map to user_answer for similar options.

        This is stored in a simple SQLite table that the OptionAligner can
        consult on future calls.
        """
        import sqlite3
        from datetime import UTC, datetime
        from jobpulse.config import DATA_DIR

        db_path = str(DATA_DIR / "option_alignment_learned.db")
        with sqlite3.connect(db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS learned_option_mappings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_answer_norm TEXT NOT NULL,
                    correct_option TEXT NOT NULL,
                    field_type TEXT,
                    times_seen INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    UNIQUE(agent_answer_norm, correct_option, field_type)
                )
            """)

            norm_agent = self._aligner._normalise(agent_answer) if self._aligner else agent_answer.lower().strip()
            now = datetime.now(UTC).isoformat()

            conn.execute(
                """INSERT INTO learned_option_mappings
                   (agent_answer_norm, correct_option, field_type, times_seen, created_at)
                   VALUES (?, ?, ?, 1, ?)
                   ON CONFLICT(agent_answer_norm, correct_option, field_type) DO UPDATE SET
                       times_seen = times_seen + 1""",
                (norm_agent, user_answer, field_type, now),
            )

    def batch_learn(
        self,
        corrections: list[dict[str, Any]],
    ) -> list[dict]:
        """Process multiple corrections in one call.

        corrections: list of dicts with keys:
            question, agent_answer, user_answer, field_options, platform, domain
        """
        results = []
        for corr in corrections:
            result = self.learn_from_correction(
                question=corr.get("question", ""),
                agent_answer=corr.get("agent_answer", ""),
                user_answer=corr.get("user_answer", ""),
                field_options=corr.get("field_options"),
                field_type=corr.get("field_type", ""),
                platform=corr.get("platform", ""),
                domain=corr.get("domain", ""),
                job_context=corr.get("job_context"),
            )
            results.append(result)
        return results
