"""Integrated v2 screening answer pipeline.

Ties together all screening subsystems:
  1. Compound Decomposition
  2. Semantic Cache (Qdrant)
  3. Intent Classification
  4. Intent Resolution (profile-driven)
  5. LLM Fallback
  6. Option Alignment
  7. Validation

Usage:
    pipeline = ScreeningPipeline(profile=my_profile)
    result = pipeline.answer(question, field, job_context)
"""

from __future__ import annotations

from typing import Any

from shared.logging_config import get_logger


from jobpulse.screening_semantic_cache import ScreeningSemanticCache
from jobpulse.screening_intent import ScreeningIntentClassifier, ScreeningIntent
from jobpulse.screening_detector import ScreeningDetector
from jobpulse.screening_decomposer import QuestionDecomposer, AnswerRecombiner
from jobpulse.screening_option_aligner import OptionAligner, BoolFieldHandler, SalaryFieldHandler
from jobpulse.screening_validator import ScreeningValidator
from jobpulse.screening_pattern_extractor import PatternExtractor

logger = get_logger(__name__)


class ScreeningPipeline:
    """End-to-end screening question answering pipeline."""

    def __init__(
        self,
        profile: dict[str, Any],
        semantic_cache: ScreeningSemanticCache | None = None,
        intent_classifier: ScreeningIntentClassifier | None = None,
        detector: ScreeningDetector | None = None,
        decomposer: QuestionDecomposer | None = None,
        option_aligner: OptionAligner | None = None,
        validator: ScreeningValidator | None = None,
        pattern_extractor: PatternExtractor | None = None,
    ) -> None:
        self._profile = profile
        self._semantic_cache = semantic_cache or ScreeningSemanticCache()
        self._intent_classifier = intent_classifier or ScreeningIntentClassifier()
        self._detector = detector or ScreeningDetector()
        self._decomposer = decomposer or QuestionDecomposer()
        self._option_aligner = option_aligner or OptionAligner()
        self._validator = validator or ScreeningValidator()
        self._pattern_extractor = pattern_extractor or PatternExtractor()

    def answer(
        self,
        question: str,
        field: dict[str, Any] | None = None,
        job_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Answer a screening question through the full v2 pipeline.

        Returns a dict with:
            - answer: the final answer string
            - confidence: 0.0-1.0 confidence score
            - source: which pipeline stage produced the answer
            - intent: classified intent
            - validation: validation result dict
            - metadata: additional pipeline info
        """
        result: dict[str, Any] = {
            "answer": "",
            "confidence": 0.0,
            "source": "unknown",
            "intent": None,
            "validation": {},
            "metadata": {},
        }

        if not question or not question.strip():
            result["answer"] = ""
            result["confidence"] = 0.0
            result["source"] = "empty_question"
            return result

        # ── Step 1: Compound Decomposition ──────────────────────────────────
        sub_questions = self._decomposer.decompose(question)
        if sub_questions:
            answers = []
            for sq in sub_questions:
                sub_result = self._answer_single(sq, field, job_context)
                answers.append((sq, sub_result["answer"]))
            combined = AnswerRecombiner.recombine(answers)
            result.update({
                "answer": combined,
                "confidence": 0.85,
                "source": "decomposed",
                "metadata": {"sub_questions": sub_questions, "sub_answers": answers},
            })
            return self._finalise(result, question, field)

        # ── Step 2-10: Single question pipeline ─────────────────────────────
        single = self._answer_single(question, field, job_context)
        result.update(single)
        return self._finalise(result, question, field)

    def _answer_single(
        self,
        question: str,
        field: dict[str, Any] | None,
        job_context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Answer a single (non-compound) question."""

        # Step 2: Semantic Cache (option-aware — passes field options for alignment)
        field_options = field.get("options") if field else None
        field_type = field.get("type", "") if field else ""
        cache_hit = self._semantic_cache.lookup(
            question,
            field_options=field_options,
            field_type=field_type,
        )
        if cache_hit:
            # Boost confidence for well-used cache entries
            times_bonus = min(cache_hit.times_used / 10.0, 0.15) if cache_hit.times_used else 0.0
            return {
                "answer": cache_hit.answer,
                "confidence": min(cache_hit.score + times_bonus, 1.0),
                "source": "semantic_cache",
                "intent": cache_hit.intent,
                "metadata": {
                    "score": cache_hit.score,
                    "times_used": cache_hit.times_used,
                    "option_aligned": bool(cache_hit.selected_option),
                },
            }

        # Step 3: Intent Classification
        intent, intent_confidence = self._intent_classifier.classify(question)
        result = {
            "answer": "",
            "confidence": intent_confidence,
            "source": "unknown",
            "intent": intent.value if intent else None,
            "metadata": {},
        }

        # Step 4: Intent Resolution (profile-driven + job context)
        if intent and intent != ScreeningIntent.UNKNOWN:
            resolved = self._resolve_intent_from_profile(intent, job_context)
            if resolved:
                result["answer"] = resolved
                result["confidence"] = max(intent_confidence, 0.75)
                result["source"] = "intent_resolver"
                return result

        # Step 7: Exact Cache Fallback (legacy)
        # This would check the old SQLite ats_answer_cache
        # Skipped here — caller can layer it in if needed

        # Step 8: LLM Fallback
        llm_answer = self._llm_answer(question, field, job_context)
        if llm_answer:
            result["answer"] = llm_answer
            result["confidence"] = 0.55
            result["source"] = "llm_fallback"
            return result

        # Ultimate fallback
        result["answer"] = ""
        result["confidence"] = 0.0
        result["source"] = "no_answer"
        return result

    def _finalise(
        self,
        result: dict[str, Any],
        question: str,
        field: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Apply option alignment and validation to the result."""
        answer = result.get("answer", "")
        if not answer:
            return result

        # Step 9: Option Alignment
        if field:
            options = field.get("options")
            field_type = field.get("type", "")

            if options and self._option_aligner.is_option_field(field):
                aligned = self._option_aligner.align_answer(answer, options, field_type)
                if aligned != answer:
                    result["answer"] = aligned
                    result["metadata"]["original_answer"] = answer
                    result["source"] = f"{result['source']}_aligned"

            # Boolean fields
            if BoolFieldHandler.is_boolean_field(field):
                bool_answer = BoolFieldHandler.resolve(answer, options or [])
                if bool_answer != answer:
                    result["answer"] = bool_answer
                    result["metadata"]["original_answer"] = answer

            # Salary range fields
            if result.get("intent") in ("salary_current", "salary_expected"):
                if options and SalaryFieldHandler.extract_numeric(answer):
                    salary_answer = SalaryFieldHandler.format_for_range(answer, options)
                    if salary_answer != answer:
                        result["answer"] = salary_answer
                        result["metadata"]["original_answer"] = answer

        # Step 10: Validation
        validation = self._validator.validate(
            result["answer"],
            question,
            field,
            self._profile,
        )
        result["validation"] = {
            "is_valid": validation.is_valid,
            "issues": validation.issues,
            "confidence": validation.confidence,
            "suggested_fix": validation.suggested_fix,
        }

        if not validation.is_valid and validation.suggested_fix:
            result["answer"] = validation.suggested_fix
            result["source"] = f"{result['source']}_fixed"
            result["confidence"] *= 0.7

        # Record pattern observation for learning
        if result.get("source") and result["source"] != "semantic_cache":
            intent_val = result.get("intent")
            try:
                intent_enum = ScreeningIntent(intent_val) if intent_val else ScreeningIntent.UNKNOWN
            except ValueError:
                intent_enum = ScreeningIntent.UNKNOWN
            self._pattern_extractor.observe(
                question,
                result["answer"],
                intent_enum,
                success=validation.is_valid,
            )

        return result

    def _resolve_intent_from_profile(
        self, intent: ScreeningIntent, job_context: dict[str, Any] | None = None,
    ) -> str | None:
        """Map a classified intent to a profile field value.

        Uses job_context to contextualize answers when the JD provides
        constraints (salary range, work mode, required experience).
        """
        mapping: dict[ScreeningIntent, list[str]] = {
            ScreeningIntent.WORK_AUTH_YES_NO: ["right_to_work"],
            ScreeningIntent.WORK_AUTH_TYPE: ["work_auth_type", "visa_type"],
            ScreeningIntent.VISA_STATUS: ["visa_status", "visa_type"],
            ScreeningIntent.SPONSORSHIP: ["visa_sponsorship_required"],
            ScreeningIntent.SALARY_CURRENT: ["current_salary"],
            ScreeningIntent.SALARY_EXPECTED: ["salary_expectation", "expected_salary"],
            ScreeningIntent.NOTICE_PERIOD: ["notice_period"],
            ScreeningIntent.START_DATE: ["earliest_start_date", "start_date"],
            ScreeningIntent.CURRENTLY_EMPLOYED: ["currently_employed"],
            ScreeningIntent.CURRENT_JOB_TITLE: ["current_job_title", "job_title"],
            ScreeningIntent.CURRENT_EMPLOYER: ["current_employer", "employer"],
            ScreeningIntent.REASON_LEAVING: ["reason_for_leaving"],
            ScreeningIntent.LOCATION_CURRENT: ["location", "current_location", "city"],
            ScreeningIntent.WILLING_RELOCATE: ["willing_to_relocate"],
            ScreeningIntent.COMMUTE: ["commute_distance", "commute_ok"],
            ScreeningIntent.REMOTE: ["remote_preference"],
            ScreeningIntent.OFFICE: ["office_preference"],
            ScreeningIntent.HYBRID: ["hybrid_preference"],
            ScreeningIntent.EXPERIENCE_YEARS: ["years_of_experience", "total_experience"],
            ScreeningIntent.EXPERIENCE_SKILL: ["skills"],
            ScreeningIntent.EDUCATION_LEVEL: ["highest_degree", "education_level"],
            ScreeningIntent.DEGREE_SUBJECT: ["degree_subject", "field_of_study"],
            ScreeningIntent.LANGUAGE_ENGLISH: ["english_proficiency"],
            ScreeningIntent.LANGUAGES: ["languages"],
            ScreeningIntent.DRIVING_LICENSE: ["has_driving_license"],
            ScreeningIntent.WILLING_TRAVEL: ["willing_to_travel"],
            ScreeningIntent.SECURITY_CLEARANCE: ["security_clearance"],
            ScreeningIntent.BACKGROUND_CHECK: ["background_check_consent"],
            ScreeningIntent.DIVERSITY_MONITORING: ["diversity_info"],
            ScreeningIntent.CONSENT_DATA: ["data_consent"],
        }

        # Contextual overrides based on job_context
        if intent == ScreeningIntent.SALARY_EXPECTED and job_context:
            salary_range = job_context.get("salary_range")
            if salary_range:
                sr_min = salary_range.get("min")
                sr_max = salary_range.get("max")
                if sr_min and sr_max:
                    # Answer with the midpoint, rounded
                    mid = int((sr_min + sr_max) / 2)
                    return str(mid)
                elif sr_min:
                    return str(int(sr_min * 1.1))  # 10% above min
                elif sr_max:
                    return str(int(sr_max * 0.9))  # 10% below max

        if intent == ScreeningIntent.REMOTE and job_context:
            work_mode = job_context.get("work_mode")
            if work_mode == "remote":
                return "Yes"

        if intent == ScreeningIntent.WILLING_RELOCATE and job_context:
            job_loc = job_context.get("location", "").lower()
            my_loc = self._profile.get("location", "").lower()
            if job_loc and my_loc and job_loc in my_loc or my_loc in job_loc:
                return "No"  # Already in the same area

        fields = mapping.get(intent, [])
        for field in fields:
            value = self._profile.get(field)
            if value is not None:
                if isinstance(value, bool):
                    return "Yes" if value else "No"
                return str(value)
        return None

    # ── Fallback Generators ─────────────────────────────────────────────────

    def _llm_answer(
        self,
        question: str,
        field: dict[str, Any] | None,
        job_context: dict[str, Any] | None,
    ) -> str | None:
        """LLM fallback for unrecognised questions."""
        system_prompt = (
            "You are answering a job application screening question. "
            "Answer concisely and honestly based on the candidate profile provided. "
            "Never mention that you are an AI. Give a direct, personal-sounding answer."
        )

        profile_summary = self._profile_summary()
        context = ""
        if job_context:
            context = f"\nJob context: {job_context}\n"

        user_prompt = (
            f"Candidate profile:\n{profile_summary}\n"
            f"{context}"
            f"Screening question: {question}\n\n"
            "Provide a concise answer (1-3 sentences max)."
        )

        try:
            # Route through CognitiveEngine (default-on) for structured screening answers
            from shared.agents import cognitive_llm_call
            answer = cognitive_llm_call(
                task=f"SYSTEM: {system_prompt}\nUSER: {user_prompt}",
                domain="screening_answers",
                stakes="high",
            )
            if answer is None:
                return None
            # Strip any AI disclaimers
            if any(phrase in answer.lower() for phrase in ("as an ai", "i don't have", "i cannot")):
                return None
            return answer
        except Exception as exc:
            logger.debug("LLM fallback failed: %s", exc)
            return None

    def _profile_summary(self) -> str:
        """Generate a concise text summary of the profile."""
        parts = []
        for key, value in self._profile.items():
            if value is not None and value != "":
                parts.append(f"- {key}: {value}")
        return "\n".join(parts) or "No profile information available."

    def record_outcome(
        self,
        question: str,
        answer: str,
        success: bool,
        *,
        field_options: list[str] | None = None,
        field_type: str = "",
        selected_option: str = "",
    ) -> None:
        """Record the outcome of an answered question for learning."""
        self._semantic_cache.record_outcome(question, success)
        intent, _ = self._intent_classifier.classify(question)
        if success:
            if intent and intent != ScreeningIntent.UNKNOWN:
                self._intent_classifier.add_intent_example(intent, question)
            # Cache the successful answer so future semantic lookups hit
            if answer:
                self._semantic_cache.cache(
                    question=question,
                    intent=intent.value if intent else "unknown",
                    answer=answer,
                    confidence=0.90,
                    selected_option=selected_option or answer if field_options else "",
                    field_type=field_type,
                    field_options=field_options,
                )
