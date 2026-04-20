"""5-tier form intelligence router for answering job application form fields.

Resolution order:
    Tier 1 — Pattern Match  (instant, free)
    Tier 2 — Semantic Cache (fast, free after first hit)
    Tier 3 — Gemini Nano   (async only, on-device, free)
    Tier 4 — LLM           (OpenAI, ~$0.001/call)
    Tier 5 — Vision        (async only, screenshot analysis)
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from shared.logging_config import get_logger

from jobpulse.ext_models import FieldAnswer
from jobpulse.screening_answers import COMMON_ANSWERS, _generate_answer, _resolve_placeholder

if TYPE_CHECKING:
    from jobpulse.correction_capture import CorrectionCapture
    from jobpulse.extension_bridge import ExtensionBridge
    from jobpulse.field_audit import FieldAuditDB
    from jobpulse.semantic_cache import SemanticAnswerCache

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Tier name mapping
# ---------------------------------------------------------------------------

_TIER_NAMES: dict[int, str] = {
    1: "pattern",
    2: "semantic_cache",
    3: "nano",
    4: "llm",
    5: "vision",
}


# ---------------------------------------------------------------------------
# Thin wrapper — exists solely so tests can mock LLM calls
# ---------------------------------------------------------------------------


def _generate_answer_llm(question: str, job_context: dict | None = None) -> str:
    """Wrapper around _generate_answer for mockability in tests."""
    return _generate_answer(question, job_context)


# ---------------------------------------------------------------------------
# FormIntelligence
# ---------------------------------------------------------------------------


class FormIntelligence:
    """Orchestrates the 5-tier answer resolution for form fields."""

    _ESCALATION_THRESHOLD = 0.5
    _ESCALATION_MIN_SAMPLES = 5

    def __init__(
        self,
        semantic_cache: SemanticAnswerCache | None = None,
        bridge: ExtensionBridge | None = None,
        field_audit: FieldAuditDB | None = None,
        correction_db: CorrectionCapture | None = None,
    ) -> None:
        self._cache = semantic_cache
        self._bridge = bridge
        self._field_audit = field_audit
        self._correction_db = correction_db

    # ------------------------------------------------------------------
    # Correction escalation check
    # ------------------------------------------------------------------

    def _should_escalate(self, question: str) -> bool:
        """Check if this field has a high correction rate and should be escalated."""
        if self._correction_db is None or self._field_audit is None:
            return False
        label = question.strip().lower()
        total = self._field_audit.get_field_fill_count(label)
        rate = self._correction_db.get_correction_rate(
            label, total, min_samples=self._ESCALATION_MIN_SAMPLES,
        )
        if rate is not None and rate >= self._ESCALATION_THRESHOLD:
            logger.info(
                "Escalating field '%s' — correction rate %.0f%% (%d samples)",
                label, rate * 100, total,
            )
            return True
        return False

    # ------------------------------------------------------------------
    # Audit logging
    # ------------------------------------------------------------------

    def _log_audit(
        self,
        question: str,
        result: FieldAnswer,
        *,
        application_url: str = "",
        domain: str = "",
        platform: str = "",
    ) -> None:
        if self._field_audit is None:
            return
        model = ""
        if result.tier in (4, 5):
            try:
                from shared.agents import get_model_name
                model = get_model_name()
            except Exception:
                pass
        try:
            self._field_audit.record_fill(
                application_url=application_url,
                domain=domain,
                platform=platform,
                field_label=question,
                value=result.answer,
                method=result.tier_name,
                tier=result.tier,
                confidence=result.confidence,
                model=model,
            )
        except Exception as exc:
            logger.debug("Field audit logging failed: %s", exc)

    # ------------------------------------------------------------------
    # Tier 1 helper — pattern match
    # ------------------------------------------------------------------

    def _try_pattern(
        self,
        question: str,
        job_context: dict | None,
        *,
        input_type: str | None,
        platform: str | None,
        db: object | None,
    ) -> FieldAnswer | None:
        """Attempt Tier 1: regex pattern match against COMMON_ANSWERS."""
        normalised = question.strip()
        for pattern, answer in COMMON_ANSWERS.items():
            if re.search(pattern, normalised, re.IGNORECASE):
                if answer is not None:
                    resolved = _resolve_placeholder(
                        answer,
                        normalised,
                        job_context,
                        input_type=input_type,
                        platform=platform,
                        db=db,  # type: ignore[arg-type]
                    )
                    logger.debug(
                        "Tier 1 pattern match '%s' -> '%s'", normalised[:60], resolved[:80]
                    )
                    return FieldAnswer(
                        answer=resolved,
                        tier=1,
                        confidence=1.0,
                        tier_name=_TIER_NAMES[1],
                    )
                # Matched but answer is None — LLM required (skip to Tier 4)
                logger.debug("Tier 1 LLM-required pattern '%s'", normalised[:60])
                return None
        return None

    # ------------------------------------------------------------------
    # Tier 2 helper — semantic cache
    # ------------------------------------------------------------------

    def _try_semantic_cache(self, question: str, company: str = "") -> FieldAnswer | None:
        """Attempt Tier 2: semantic similarity cache lookup."""
        if self._cache is None:
            return None
        try:
            result = self._cache.find_similar(question, company=company)
            if result is not None:
                # find_similar returns str | None (not a tuple)
                logger.debug("Tier 2 cache hit '%s' (company=%r)", question[:60], company)
                return FieldAnswer(
                    answer=result,
                    tier=2,
                    confidence=0.85,
                    tier_name=_TIER_NAMES[2],
                )
        except Exception as exc:
            logger.warning("Tier 2 semantic cache error: %s", exc)
        return None

    # ------------------------------------------------------------------
    # Tier 4 helper — LLM
    # ------------------------------------------------------------------

    def _try_llm(
        self,
        question: str,
        job_context: dict | None,
        company: str = "",
    ) -> FieldAnswer:
        """Tier 4: LLM generation (always succeeds — falls back to generic)."""
        answer = _generate_answer_llm(question, job_context)
        # Store in semantic cache for future reuse (with company for scoped retrieval)
        if self._cache is not None:
            try:
                self._cache.store(question, answer, company=company)
            except Exception as exc:
                logger.warning("Failed to store LLM answer in cache: %s", exc)
        logger.debug("Tier 4 LLM answer for '%s'", question[:60])
        return FieldAnswer(
            answer=answer,
            tier=4,
            confidence=0.7,
            tier_name=_TIER_NAMES[4],
        )

    # ------------------------------------------------------------------
    # Public: sync resolve (Tiers 1, 2, 4)
    # ------------------------------------------------------------------

    def resolve(
        self,
        question: str,
        job_context: dict | None = None,
        *,
        input_type: str | None = None,
        platform: str | None = None,
        db: object | None = None,
        application_url: str = "",
    ) -> FieldAnswer:
        """Sync path — Tiers 1, 2, 4.

        Use for backward-compatible call sites that cannot await.
        Tiers 3 (Nano) and 5 (Vision) are async-only and skipped here.
        """
        if not question or not question.strip():
            return FieldAnswer(answer="", tier=1, confidence=0.0, tier_name=_TIER_NAMES[1])

        # Correction-weighted escalation: flag fields that users frequently override
        if self._should_escalate(question):
            return FieldAnswer(
                answer="", tier=0, confidence=0.3, tier_name="escalated",
            )

        company = (job_context or {}).get("company", "")

        # Tier 1 — pattern
        result = self._try_pattern(
            question, job_context, input_type=input_type, platform=platform, db=db
        )
        if result is not None:
            self._log_audit(question, result, application_url=application_url, platform=platform or "")
            return result

        # Tier 2 — semantic cache (company-scoped)
        result = self._try_semantic_cache(question, company=company)
        if result is not None:
            self._log_audit(question, result, application_url=application_url, platform=platform or "")
            return result

        # Tier 4 — LLM
        result = self._try_llm(question, job_context, company=company)
        self._log_audit(question, result, application_url=application_url, platform=platform or "")
        return result

    # ------------------------------------------------------------------
    # Public: async resolve (Tiers 1, 2, 3, 4, 5)
    # ------------------------------------------------------------------

    async def resolve_async(
        self,
        question: str,
        job_context: dict | None = None,
        *,
        input_type: str | None = None,
        platform: str | None = None,
        db: object | None = None,
        screenshot_b64: str | None = None,
        application_url: str = "",
    ) -> FieldAnswer:
        """Async path — all 5 tiers.

        Args:
            question: The form field label / question text.
            job_context: Optional dict with ``job_title``, ``company``, ``location``.
            input_type: HTML input type.
            platform: ATS platform name.
            db: Optional JobDB instance.
            screenshot_b64: Base64-encoded screenshot for Tier 5 vision fallback.
            application_url: Job URL for audit logging.
        """
        if not question or not question.strip():
            return FieldAnswer(answer="", tier=1, confidence=0.0, tier_name=_TIER_NAMES[1])

        if self._should_escalate(question):
            return FieldAnswer(
                answer="", tier=0, confidence=0.3, tier_name="escalated",
            )

        company = (job_context or {}).get("company", "")

        # Tier 1 — pattern
        result = self._try_pattern(
            question, job_context, input_type=input_type, platform=platform, db=db
        )
        if result is not None:
            self._log_audit(question, result, application_url=application_url, platform=platform or "")
            return result

        # Tier 2 — semantic cache (company-scoped)
        result = self._try_semantic_cache(question, company=company)
        if result is not None:
            self._log_audit(question, result, application_url=application_url, platform=platform or "")
            return result

        # Tier 3 — Gemini Nano (on-device, via extension bridge)
        if self._bridge is not None:
            try:
                nano_answer = await self._bridge.analyze_field_locally(
                    question=question,
                    input_type=input_type or "text",
                    options=[],
                    job_context=job_context,
                )
                if nano_answer:
                    logger.debug("Tier 3 Nano answer for '%s'", question[:60])
                    result = FieldAnswer(
                        answer=nano_answer,
                        tier=3,
                        confidence=0.8,
                        tier_name=_TIER_NAMES[3],
                    )
                    self._log_audit(question, result, application_url=application_url, platform=platform or "")
                    return result
            except Exception as exc:
                logger.warning("Tier 3 Nano error: %s", exc)

        # Tier 4 — LLM
        result = self._try_llm(question, job_context, company=company)

        # Tier 5 — Vision (only when screenshot provided and LLM gave a weak answer)
        if screenshot_b64 is not None and result.confidence < 0.8:
            try:
                from jobpulse.vision_tier import analyze_field_screenshot

                vision_answer = await analyze_field_screenshot(
                    screenshot_b64, question, job_context
                )
                if vision_answer:
                    logger.debug("Tier 5 Vision answer for '%s'", question[:60])
                    result = FieldAnswer(
                        answer=vision_answer,
                        tier=5,
                        confidence=0.85,
                        tier_name=_TIER_NAMES[5],
                    )
                    self._log_audit(question, result, application_url=application_url, platform=platform or "")
                    return result
            except Exception as exc:
                logger.warning("Tier 5 Vision error: %s", exc)

        self._log_audit(question, result, application_url=application_url, platform=platform or "")
        return result
