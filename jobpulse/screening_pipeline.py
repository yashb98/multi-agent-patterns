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

import hashlib
import json
from typing import Any

from shared.logging_config import get_logger


# Free-text LLM-fallback answers must be at least this cosine-similar to
# their question to be accepted. Threshold derived from BGE-M3 measurements
# on real (Q, correct-answer) pairs vs (Q, observed-leak) pairs (S13):
#
#   on-topic prose answers      → 0.55–0.81  (mean ≈ 0.66)
#   off-topic orchestration     → 0.27–0.50  (mean ≈ 0.35)
#
# 0.40 cleanly separates the two distributions while keeping the lowest
# legitimate prose answer (machine-learning experience, sim 0.55) well
# above the floor. Short option-like answers (e.g. "Man" sim 0.43, "5 -
# Expert" sim 0.40) are NOT subject to this guard — they go through the
# option-aligned branch with ``OptionAligner``.
_LLM_ANSWER_RELEVANCE_THRESHOLD = 0.40


from jobpulse.screening_semantic_cache import ScreeningSemanticCache
from jobpulse.screening_intent import ScreeningIntentClassifier, ScreeningIntent
from jobpulse.screening_detector import ScreeningDetector
from jobpulse.screening_decomposer import QuestionDecomposer, AnswerRecombiner
from jobpulse.screening_option_aligner import OptionAligner, BoolFieldHandler, SalaryFieldHandler
from jobpulse.screening_session_state import SessionFillState
from jobpulse.screening_validator import ScreeningValidator
from jobpulse.screening_pattern_extractor import PatternExtractor

logger = get_logger(__name__)


# S26-follow-up-O-1: extract field references from an introspection
# question. The handler needs to know WHICH session-filled fields the
# question is asking about ("legal name" → First Name + Last Name).
# Embedding similarity catches paraphrases at the consumer level; the
# session_state.references_present() check is the final authority, so
# false-positive references (synonym matched but field not in session)
# collapse to "No" via the unfilled branch — the correct conservative
# answer.
_INTROSPECTION_REFERENCE_SYNONYMS: dict[str, list[str]] = {
    "legal name": ["First Name", "Last Name"],
    "full name": ["First Name", "Last Name"],
    "first name and last name": ["First Name", "Last Name"],
    "first and last name": ["First Name", "Last Name"],
    "surname": ["Last Name"],
    "given name": ["First Name"],
    "name and surname": ["First Name", "Last Name"],
    "contact information": ["Email", "Phone"],
    "contact details": ["Email", "Phone"],
    "email address": ["Email"],
    "phone number": ["Phone"],
    "resume": ["Resume"],
    "cv": ["Resume"],
    "cover letter": ["Cover Letter"],
}


def _extract_referenced_fields(question: str) -> list[str]:
    """Return canonical label tokens referenced by an introspection question."""
    if not question:
        return []
    q = question.lower()
    out: list[str] = []
    for synonym, labels in _INTROSPECTION_REFERENCE_SYNONYMS.items():
        if synonym in q:
            out.extend(labels)
    seen: set[str] = set()
    return [x for x in out if not (x in seen or seen.add(x))]


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
        # Audit S1 / TP-1: scope the screening cache to (profile, JD) context.
        # profile_state_hash is stable for the lifetime of this pipeline
        # instance — profile mutations between sessions yield a new hash
        # (and therefore a new cache row) on the next ScreeningPipeline.
        self._profile_state_hash = self._compute_profile_state_hash(profile)

    # Audit S1 / TP-1 helpers --------------------------------------------

    # Profile fields that genuinely determine the right screening answer.
    # Per dimensions.md → D9 decision-context table: visa, salary, notice,
    # relocation, languages. Excludes identity/links because their value
    # doesn't change visa/salary/notice/relocation answers.
    _PROFILE_HASH_FIELDS: tuple[str, ...] = (
        "visa_status",
        "visa_expiry",
        "visa_type",
        "right_to_work",
        "work_auth_type",
        "current_salary",
        "expected_salary",
        "salary_expectation",
        "notice_period",
        "location",
        "current_city",
        "willing_to_relocate",
        "languages",
        "english_proficiency",
    )

    @classmethod
    def _compute_profile_state_hash(cls, profile: dict[str, Any]) -> str:
        """Hash the screening-determining subset of the profile.

        Keeping the input set narrow protects cache hit-rate: irrelevant
        profile changes (LinkedIn URL, name) don't invalidate visa/salary
        decisions, while screening-relevant changes (visa renewed,
        notice period revised) correctly produce a new hash so the prior
        decision is regenerated on the next live run.
        """
        if not profile:
            return ""
        subset = {k: profile.get(k) for k in cls._PROFILE_HASH_FIELDS if profile.get(k) not in (None, "")}
        if not subset:
            return ""
        canonical = json.dumps(subset, sort_keys=True, default=str)
        return hashlib.sha256(canonical.encode()).hexdigest()[:16]

    @classmethod
    def _jd_context_hash(cls, job_context: dict[str, Any] | None) -> str:
        """Hash the JD-axes that determine screening answers per D9.

        Country drives visa/work-auth, currency drives salary expectation,
        role_level drives experience/seniority answers. Skills and
        required_languages don't determine these answers (the JD's
        country does, not its skill list), so they're excluded to keep
        the hash narrow.
        """
        if not job_context:
            return "empty"
        subset = {
            "country": (job_context.get("country") or job_context.get("location") or "").lower().strip(),
            "currency": (job_context.get("currency") or "").upper().strip(),
            "role_level": (job_context.get("role_level") or job_context.get("seniority") or "").lower().strip(),
        }
        if not any(subset.values()):
            return "empty"
        canonical = json.dumps(subset, sort_keys=True)
        return hashlib.sha256(canonical.encode()).hexdigest()[:16]

    def answer(
        self,
        question: str,
        field: dict[str, Any] | None = None,
        job_context: dict[str, Any] | None = None,
        session_state: "SessionFillState | None" = None,
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

        # ── S26-follow-up-O-1: introspection_confirmation short-circuit ───
        # "Have you added your full legal name?" et al. — answer depends
        # on what the current session has already filled, not on the
        # profile / JD / LLM. Must run BEFORE the semantic cache because
        # caching session-derived answers across runs would be wrong.
        if session_state is not None:
            intro = self._try_introspection_answer(question, field, session_state)
            if intro is not None:
                return self._finalise(intro, question, field)

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

    def _try_introspection_answer(
        self,
        question: str,
        field: dict[str, Any] | None,
        session_state: SessionFillState,
    ) -> dict[str, Any] | None:
        """If the question is an introspection_confirmation (e.g. "Have
        you added your legal name?"), answer Yes/No based on whether the
        referenced fields are present in this session. Returns None when
        the intent doesn't match (caller falls through to normal flow).
        """
        try:
            intent, intent_confidence = self._intent_classifier.classify(question)
        except Exception as exc:
            logger.debug("introspection intent classify failed: %s", exc)
            return None
        if intent != ScreeningIntent.INTROSPECTION_CONFIRMATION:
            return None

        refs = _extract_referenced_fields(question)
        options = (field or {}).get("options") or []
        # Resolve Yes/No against the field's options (semantic match) so
        # downstream form filling gets the exact string the dropdown
        # expects ("Yes", "Confirmed", "I agree", etc.).
        try:
            from jobpulse.form_engine.semantic_matcher import semantic_option_match
        except Exception:
            semantic_option_match = None  # type: ignore[assignment]

        if refs and session_state.references_present(refs):
            verdict = "yes"
            confidence = 0.95
        else:
            verdict = "no"
            confidence = 0.85

        answer_str: str
        if options and semantic_option_match is not None:
            matched = semantic_option_match(verdict, options)
            answer_str = matched or ("Yes" if verdict == "yes" else "No")
        else:
            answer_str = "Yes" if verdict == "yes" else "No"

        logger.info(
            "screening: introspection_confirmation %r → %s (refs=%s, session_present=%s)",
            question[:60], answer_str, refs, bool(
                refs and session_state.references_present(refs),
            ),
        )
        return {
            "answer": answer_str,
            "confidence": confidence,
            "source": "introspection_session_state",
            "intent": ScreeningIntent.INTROSPECTION_CONFIRMATION.value,
            "metadata": {
                "references": refs,
                "references_present": bool(
                    refs and session_state.references_present(refs),
                ),
                "intent_confidence": float(intent_confidence),
            },
        }

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
        jd_context_hash = self._jd_context_hash(job_context)
        cache_hit = self._semantic_cache.lookup(
            question,
            field_options=field_options,
            field_type=field_type,
            profile_state_hash=self._profile_state_hash,
            jd_context_hash=jd_context_hash,
        )
        if cache_hit:
            # Boost confidence for well-used cache entries
            times_bonus = min(cache_hit.times_used / 10.0, 0.15) if cache_hit.times_used else 0.0
            logger.info(
                "screening_cache: hit on %r (score=%.2f, intent=%s, option_aligned=%s) "
                "— skipping LLM alignment",
                question[:80], cache_hit.score, cache_hit.intent,
                bool(cache_hit.selected_option),
            )
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
                # Clamp to 1.0 — intent classifier cosine similarity in
                # float32 occasionally exceeds 1.0 by ~1e-7. The cache-hit
                # path already clamps via min(); the resolver path needs
                # the same protection so callers see a valid probability.
                # (Surfaced when slice S1 routed more callers here.)
                result["confidence"] = min(max(intent_confidence, 0.75), 1.0)
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
            # Parentheses required: `and` binds tighter than `or`, so
            # without them the empty-`my_loc` case short-circuits via
            # `"" in job_loc == True` and we wrongly claim "same area"
            # for users with no profile location set. Audit S4 B-2.
            if job_loc and my_loc and (job_loc in my_loc or my_loc in job_loc):
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
        """LLM fallback for unrecognised questions.

        When the field carries options (select, radio, multiselect, combobox),
        the prompt is constrained to those options so the LLM picks one
        instead of producing free text. Without this, asking "identify your
        race" against a 5-option dropdown returns a paragraph that the
        downstream option-aligner has to fuzzy-match — which sometimes lands
        on something reasonable and sometimes on nothing.
        """
        profile_summary = self._profile_summary()
        context = ""
        if job_context:
            context = f"\nJob context: {job_context}\n"

        # Option-bearing fields → constrain the LLM to pick one option.
        # This is the primary correctness path for selects/radios/multiselects.
        # The downstream OptionAligner remains as a safety net for near-misses.
        options = field.get("options") if field else None
        field_type = (field.get("type") or "").lower() if field else ""
        is_option_field = bool(options) and field_type in {
            "select", "radio", "checkbox", "combobox", "custom_dropdown",
            "multiselect",
        }
        logger.info(
            "DIAG _llm_answer: question=%r field_type=%r has_options=%s n_options=%d "
            "is_option_field=%s",
            (question or "")[:80],
            field_type,
            bool(options),
            len(options) if options else 0,
            is_option_field,
        )

        if is_option_field:
            options_block = "\n".join(f"- {opt}" for opt in options)
            multi = field_type == "multiselect"
            instruction = (
                "Return ONE or MORE options as a comma-separated list, "
                "using the EXACT option text from the list above."
                if multi
                else "Return EXACTLY ONE option, using the EXACT option text "
                     "from the list above. No commentary, no explanation."
            )
            # Audit S21 / TP-30: prompt the LLM AS the candidate, in first
            # person — not ABOUT the candidate. Pre-S21 the system prompt
            # said "answering on behalf of the candidate", which made the
            # LLM produce "As Yash Bishnoi, I have a strong preference..."
            # — third-person self-reference that recruiters instantly clock
            # as AI-generated.
            system_prompt = (
                "You ARE the job applicant. You are filling in a screening "
                "question on a job application form yourself. Answer in "
                "FIRST PERSON as yourself ('I am', 'I have', 'My...'). "
                "Never refer to yourself by name in third person (no "
                "'As [name], I...'). Never mention that you are an AI. "
                "The form field is a closed-set picker — pick the option "
                "that's true for you, based on your profile."
            )
            user_prompt = (
                f"Your profile:\n{profile_summary}\n"
                f"{context}"
                f"Screening question on the form: {question}\n\n"
                f"Available options:\n{options_block}\n\n"
                f"{instruction}"
            )
        else:
            # Audit S21 / TP-30: same first-person reframe as the option
            # branch. Free-text answers were the worst offenders for the
            # 'As Yash Bishnoi, I...' pattern because they're unconstrained.
            system_prompt = (
                "You ARE the job applicant. You are filling in a screening "
                "question on a job application form yourself. Answer in "
                "FIRST PERSON as yourself ('I am', 'I have', 'My...'). "
                "Never refer to yourself by name in third person (no "
                "'As [name], I...' or 'As an applicant'). Never mention "
                "that you are an AI. Be honest, based on your profile."
            )
            user_prompt = (
                f"Your profile:\n{profile_summary}\n"
                f"{context}"
                f"Screening question on the form: {question}\n\n"
                "Write your answer in 1-3 sentences. Start with 'I' or "
                "'My' — never with 'As [name]' or 'The applicant'."
            )

        import time as _time
        _t0 = _time.perf_counter()
        try:
            from shared.agents import cognitive_llm_call
            from shared.semantic_decisions import record_decision
            answer = cognitive_llm_call(
                task=f"SYSTEM: {system_prompt}\nUSER: {user_prompt}",
                domain="screening_answers",
                stakes="high",
            )
            _elapsed_ms = (_time.perf_counter() - _t0) * 1000.0
            if answer is None:
                record_decision(
                    agent_name="screening_pipeline",
                    call_site="_llm_answer:" + ("option" if is_option_field else "free_text"),
                    decision_type="llm_call",
                    mechanism="llm",
                    tier_reached="llm_returned_none",
                    input_value=question,
                    output_value=None,
                    confidence=0.0,
                    field_label=question[:120],
                    elapsed_ms=_elapsed_ms,
                )
                return None
            if any(phrase in answer.lower() for phrase in ("as an ai", "i don't have", "i cannot")):
                record_decision(
                    agent_name="screening_pipeline",
                    call_site="_llm_answer:" + ("option" if is_option_field else "free_text"),
                    decision_type="llm_call",
                    mechanism="llm",
                    tier_reached="rejected_ai_leak",
                    input_value=question,
                    output_value=answer,
                    confidence=0.0,
                    field_label=question[:120],
                    elapsed_ms=_elapsed_ms,
                )
                return None
            # When the field carries options, validate that the LLM picked one
            # of them. Cognitive routing has been seen to leak unrelated text
            # ("Enhanced swarm convergence: GRPO group sampling...") into the
            # answer slot — that text would silently be filed as the user's
            # screening answer otherwise. Align via the same OptionAligner the
            # cache lookup uses; if the answer doesn't fit any option, treat
            # the call as a miss and let the caller fall through.
            if is_option_field:
                from jobpulse.screening_option_aligner import OptionAligner
                aligner = OptionAligner()
                aligned = aligner.align_answer(answer, options, field_type)
                opts_lower = {(o or "").lower().strip() for o in options}
                if (aligned or "").lower().strip() not in opts_lower:
                    logger.warning(
                        "LLM fallback returned %r which does not align to any "
                        "option in %s — treating as miss",
                        (answer or "")[:60], [o[:25] for o in options[:5]],
                    )
                    record_decision(
                        agent_name="screening_pipeline",
                        call_site="_llm_answer:option",
                        decision_type="llm_call",
                        mechanism="llm",
                        tier_reached="rejected_option_mismatch",
                        input_value=question,
                        output_value=answer,
                        confidence=0.0,
                        field_label=question[:120],
                        elapsed_ms=_elapsed_ms,
                    )
                    return None
                record_decision(
                    agent_name="screening_pipeline",
                    call_site="_llm_answer:option",
                    decision_type="llm_call",
                    mechanism="llm",
                    tier_reached="ok_option_aligned",
                    input_value=question,
                    output_value=aligned,
                    confidence=0.85,
                    field_label=question[:120],
                    elapsed_ms=_elapsed_ms,
                )
                return aligned
            # Free-text branch: option alignment doesn't apply, but the
            # cognitive-routing leak ("Enhanced swarm convergence: GRPO
            # group sampling...") is just as poisonous here — without a
            # guard it would land in the screening_semantic_cache and
            # serve at score=1.00 on every subsequent matching apply.
            # The S13 root cause (cross-domain procedural-recall bleed)
            # is fixed in shared/memory_layer/_stores.py; this is the
            # defense-in-depth backstop for any LLM hallucination /
            # future cross-domain bug shape.
            from shared.semantic_utils import semantic_similarity
            try:
                relevance = semantic_similarity(question, answer)
            except Exception as exc:
                logger.debug(
                    "JD-relevance check failed (%s) — accepting answer "
                    "without similarity floor",
                    exc,
                )
                return answer
            if relevance < _LLM_ANSWER_RELEVANCE_THRESHOLD:
                logger.warning(
                    "LLM fallback returned %r which has cosine similarity "
                    "%.3f < %.2f to question %r — treating as miss "
                    "(S13 leak guard)",
                    (answer or "")[:80], relevance,
                    _LLM_ANSWER_RELEVANCE_THRESHOLD,
                    (question or "")[:60],
                )
                record_decision(
                    agent_name="screening_pipeline",
                    call_site="_llm_answer:free_text",
                    decision_type="llm_call",
                    mechanism="llm",
                    tier_reached="rejected_jd_relevance_low",
                    input_value=question,
                    output_value=answer,
                    confidence=float(relevance),
                    field_label=question[:120],
                    elapsed_ms=_elapsed_ms,
                )
                return None
            record_decision(
                agent_name="screening_pipeline",
                call_site="_llm_answer:free_text",
                decision_type="llm_call",
                mechanism="llm",
                tier_reached="ok_free_text",
                input_value=question,
                output_value=answer,
                confidence=0.85,
                field_label=question[:120],
                elapsed_ms=_elapsed_ms,
            )
            return answer
        except Exception as exc:
            logger.debug("LLM fallback failed: %s", exc)
            try:
                from shared.semantic_decisions import record_decision as _rd
                _rd(
                    agent_name="screening_pipeline",
                    call_site="_llm_answer:" + ("option" if is_option_field else "free_text"),
                    decision_type="llm_call",
                    mechanism="llm",
                    tier_reached="exception",
                    input_value=question,
                    output_value=repr(exc)[:200],
                    confidence=0.0,
                    field_label=question[:120],
                    elapsed_ms=(_time.perf_counter() - _t0) * 1000.0,
                )
            except Exception:
                pass
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
        job_context: dict[str, Any] | None = None,
    ) -> None:
        """Record the outcome of an answered question for learning.

        ``job_context`` is required so the recorded outcome and any
        cached answer are scoped to the same (profile, JD) context the
        decision used at lookup time. Callers that don't pass it record
        against the empty-context bucket (legacy behaviour).
        """
        jd_context_hash = self._jd_context_hash(job_context)
        self._semantic_cache.record_outcome(
            question,
            success,
            profile_state_hash=self._profile_state_hash,
            jd_context_hash=jd_context_hash,
        )
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
                    profile_state_hash=self._profile_state_hash,
                    jd_context_hash=jd_context_hash,
                )


