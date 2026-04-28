"""Single writer for all screening answer feedback signals.

Centralizes two types of feedback:
  1. record_fill()         — weak success: answer was used in a form field
  2. record_confirmation() — strong signal: user confirmed or corrected answers

All other code calls this recorder instead of touching cache counters directly.
"""

from __future__ import annotations

from typing import Any

from shared.logging_config import get_logger

logger = get_logger(__name__)


class ScreeningOutcomeRecorder:
    """Single writer for screening cache feedback (fill + confirmation)."""

    def __init__(self, cache: Any = None) -> None:
        self._cache = cache
        if cache is None:
            self._init_cache()

    def _init_cache(self) -> None:
        try:
            from jobpulse.screening_semantic_cache import get_screening_semantic_cache

            self._cache = get_screening_semantic_cache()
        except Exception as exc:
            logger.debug("ScreeningOutcomeRecorder: cache init failed: %s", exc)

    # ------------------------------------------------------------------
    # Public: fill-time signal
    # ------------------------------------------------------------------

    def record_fill(
        self,
        question: str,
        answer: str,
        field_options: list[str] | None,
        field_type: str,
        intent: str = "unknown",
    ) -> None:
        """Called by form filler after each screening field is filled.

        This is the "weak success" signal — the answer was actually used.
        Caches the answer (if not already present) and increments times_used.
        """
        if not question or not answer or self._cache is None:
            return

        self._cache.cache(
            question=question,
            intent=intent,
            answer=answer,
            confidence=0.0,
            selected_option="",
            field_type=field_type,
            field_options=field_options,
        )
        self._cache.increment_usage(question)

    # ------------------------------------------------------------------
    # Public: confirmation-time signal
    # ------------------------------------------------------------------

    def record_confirmation(
        self,
        screening_results: list[dict[str, Any]],
        corrections: dict[str, Any] | None,
    ) -> dict[str, int]:
        """Called by confirm_application() after user confirms.

        Args:
            screening_results: list of dicts with keys:
                question, answer, field_options, field_type, intent, strategy
            corrections: dict from CorrectionCapture with key "corrections"
                containing list of {"field": ..., "agent": ..., "user": ...}.
                May be None if no corrections were made.

        Returns:
            {"confirmed": N, "corrected": M}
        """
        confirmed = 0
        corrected = 0

        # Build a set of corrected field labels (case-insensitive) for fast lookup
        corrected_fields: dict[str, dict[str, str]] = {}
        if corrections and corrections.get("corrections"):
            for c in corrections["corrections"]:
                field = c.get("field", "")
                if field:
                    corrected_fields[field.strip().lower()] = c

        for sr in screening_results:
            q = sr.get("question", "")
            if not q:
                continue

            q_lower = q.strip().lower()

            if q_lower in corrected_fields:
                # User corrected this answer
                corrected += 1
                if self._cache is not None:
                    self._cache.record_outcome(q, success=False)
                # Teach the feedback loop about the correction
                c = corrected_fields[q_lower]
                self._teach_correction(
                    question=q,
                    agent_answer=c.get("agent", sr.get("answer", "")),
                    user_answer=c.get("user", ""),
                    field_options=sr.get("field_options"),
                    field_type=sr.get("field_type", ""),
                )
            else:
                # Answer was accepted — strong success signal
                confirmed += 1
                if self._cache is not None:
                    self._cache.record_outcome(q, success=True)
                    # Re-cache with higher confidence (user-verified)
                    self._cache.cache(
                        question=q,
                        intent=sr.get("intent", "unknown"),
                        answer=sr.get("answer", ""),
                        confidence=0.90,
                        selected_option="",
                        field_type=sr.get("field_type", ""),
                        field_options=sr.get("field_options"),
                    )

        return {"confirmed": confirmed, "corrected": corrected}

    # ------------------------------------------------------------------
    # Private: teach correction to feedback loop
    # ------------------------------------------------------------------

    def _teach_correction(
        self,
        question: str,
        agent_answer: str,
        user_answer: str,
        field_options: list[str] | None,
        field_type: str,
    ) -> None:
        """Forward corrections to ScreeningFeedbackLoop.learn_from_correction().

        Non-blocking: any failure is logged but does not propagate.
        """
        try:
            from jobpulse.screening_feedback_loop import ScreeningFeedbackLoop

            loop = ScreeningFeedbackLoop()
            loop.learn_from_correction(
                question=question,
                agent_answer=agent_answer,
                user_answer=user_answer,
                field_options=field_options,
                field_type=field_type,
            )
        except Exception as exc:
            logger.debug("_teach_correction failed: %s", exc)


# ------------------------------------------------------------------
# Singleton factory
# ------------------------------------------------------------------

_instance: ScreeningOutcomeRecorder | None = None


def get_screening_outcome_recorder() -> ScreeningOutcomeRecorder:
    """Return module-level singleton, lazy init."""
    global _instance
    if _instance is None:
        _instance = ScreeningOutcomeRecorder()
    return _instance
