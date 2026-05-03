"""Pre-submit quality gate — LLM-as-judge for filled application correctness.

Two complementary checks:

1. ``review()`` — recruiter-perspective overall quality score (existing)
2. ``check_semantic_correctness()`` — per-field deterministic checks +
   cross-field consistency + LLM-judge for semantic answers (new, addresses
   the "wrong values that pass read-back" gap)

Background: read-back verification confirms a field accepted a value, not
that the value was the correct answer. LLM-as-judge with a rubric closes
that gap. Per 2026 research, LLM judges achieve ~80% agreement with human
preferences at 500-5000x lower cost than human review.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

from pydantic import BaseModel
from shared.logging_config import get_logger

if TYPE_CHECKING:
    from jobpulse.perplexity import CompanyResearch

logger = get_logger(__name__)


class GateResult(BaseModel):
    """Result of pre-submit quality review."""

    passed: bool
    score: float = 0.0
    weaknesses: list[str] = []
    suggestions: list[str] = []


def _yes_no(value: str | None) -> bool | None:
    """Parse common yes/no/true/false answers. Returns None on ambiguous."""
    if not value:
        return None
    v = str(value).strip().lower()
    if v in ("yes", "true", "y", "1", "✓", "checked"):
        return True
    if v in ("no", "false", "n", "0", "✗", "unchecked"):
        return False
    return None


def _deterministic_consistency_checks(
    filled: dict[str, str],
    profile: dict[str, str] | None = None,
) -> list[str]:
    """Cross-field consistency + profile alignment. No LLM, fast."""
    issues: list[str] = []
    norm = {k.lower().strip(): v for k, v in filled.items()}

    # 1. Visa / sponsorship consistency
    work_auth_keys = ("right to work", "right_to_work", "authorized to work",
                      "eligible to work", "work authorization")
    sponsor_keys = ("require sponsorship", "requires_sponsorship",
                    "need sponsorship", "visa sponsorship", "require visa")

    work_auth = next((_yes_no(norm[k]) for k in norm if any(p in k for p in work_auth_keys)), None)
    sponsor = next((_yes_no(norm[k]) for k in norm if any(p in k for p in sponsor_keys)), None)

    if work_auth is True and sponsor is True:
        issues.append(
            "Contradiction: filled 'right to work = Yes' AND 'requires sponsorship = Yes'. "
            "These are usually mutually exclusive — review the answers."
        )

    # 2. Profile alignment (if profile provided)
    if profile:
        # Name match — common silent failure where the agent misclassifies a field
        for fname_key in ("first name", "first_name", "given name"):
            agent_first = next((str(v) for k, v in norm.items() if fname_key in k.lower()), None)
            if agent_first and profile.get("first_name"):
                if agent_first.strip().lower() != profile["first_name"].strip().lower():
                    issues.append(
                        f"Profile mismatch: 'First Name' filled as {agent_first!r} "
                        f"but profile says {profile['first_name']!r}"
                    )
                break

        for email_key in ("email", "email address"):
            agent_email = next((str(v) for k, v in norm.items() if email_key in k.lower()), None)
            if agent_email and profile.get("email"):
                if agent_email.strip().lower() != profile["email"].strip().lower():
                    issues.append(
                        f"Profile mismatch: 'Email' filled as {agent_email!r} "
                        f"but profile says {profile['email']!r}"
                    )
                break

    # 3. Format sanity — empty required-looking fields
    for label, value in filled.items():
        if label.startswith("_"):  # internal keys
            continue
        if not value or str(value).strip() in ("?", "TODO", "TBD", "FROM_PROFILE", "null"):
            issues.append(f"Field {label!r} has placeholder/empty value: {value!r}")

    return issues


class PreSubmitGate:
    """Reviews the filled application before submission."""

    PASS_THRESHOLD = 7.0
    MAX_ITERATIONS = 2

    def review(
        self,
        filled_answers: dict[str, str],
        jd_keywords: list[str],
        company_research: CompanyResearch,
    ) -> GateResult:
        """Score the application 0-10. Block if < 7."""
        prompt = (
            f"You are a FAANG recruiter reviewing this job application for "
            f"{company_research.company}.\n\n"
            f"JD keywords: {', '.join(jd_keywords)}\n"
            f"Company: {company_research.description}\n\n"
            f"Filled answers:\n"
        )
        for label, answer in filled_answers.items():
            prompt += f"  {label}: {answer}\n"

        prompt += (
            "\nScore 0-10 and return ONLY valid JSON:\n"
            '{"score": N, "weaknesses": ["..."], "suggestions": ["..."]}\n'
            "Focus on: generic/copy-pasted text, missing JD keywords, "
            "tone mismatches, factual errors."
        )

        try:
            from shared.agents import cognitive_llm_call
            raw = cognitive_llm_call(
                task=prompt,
                domain="pre_submit_review",
                stakes="high",
                response_format={"type": "json_object"},
            )
            if raw is None:
                logger.warning("PreSubmitGate: LLM returned None — blocking for human review")
                return GateResult(passed=False, score=0.0, weaknesses=["LLM review unavailable"])
            cleaned = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`").strip()
            data = json.loads(cleaned)
            score = float(data.get("score", 0))
            logger.info(
                "THRESHOLD_OBS: pre_submit_review threshold=%.1f score=%.1f decision=%s",
                self.PASS_THRESHOLD, score,
                "passed" if score >= self.PASS_THRESHOLD else "blocked",
            )
            return GateResult(
                passed=score >= self.PASS_THRESHOLD,
                score=score,
                weaknesses=data.get("weaknesses", []),
                suggestions=data.get("suggestions", []),
            )
        except Exception as exc:
            logger.warning("PreSubmitGate review failed: %s — blocking for human review", exc)
            return GateResult(passed=False, score=0.0, weaknesses=[f"Review error: {exc}"])

    def check_semantic_correctness(
        self,
        filled_answers: dict[str, str],
        jd_keywords: list[str] | None = None,
        profile: dict[str, str] | None = None,
        run_llm_judge: bool = True,
    ) -> GateResult:
        """Per-field semantic correctness check — addresses the 'wrong value passes
        read-back' gap.

        Read-back verifies a field *accepted* a value. This method verifies the
        value was the *correct* answer:

        - Deterministic: cross-field consistency (visa/sponsor contradiction),
          profile alignment (name/email match the actual profile), placeholder
          detection.
        - LLM-as-judge (optional): per-field semantic check given JD context,
          with explicit reasoning trace.

        Each issue costs 2 points. Score < PASS_THRESHOLD blocks submission.
        """
        # 1. Deterministic checks (no LLM, fast)
        issues = _deterministic_consistency_checks(filled_answers, profile)

        # 2. LLM judge (optional, can be disabled for cost-sensitive paths)
        if run_llm_judge and filled_answers:
            try:
                llm_issues = self._llm_field_judge(filled_answers, jd_keywords or [], profile)
                issues.extend(llm_issues)
            except Exception as exc:
                logger.debug("PreSubmitGate.check_semantic_correctness: LLM judge failed: %s", exc)

        # Score: each issue costs 2 points, floor at 0
        score = max(0.0, 10.0 - len(issues) * 2.0)
        logger.info(
            "THRESHOLD_OBS: pre_submit_semantic_correctness threshold=%.1f score=%.1f decision=%s",
            self.PASS_THRESHOLD, score,
            "passed" if score >= self.PASS_THRESHOLD else "blocked",
        )
        return GateResult(
            passed=score >= self.PASS_THRESHOLD,
            score=score,
            weaknesses=issues,
            suggestions=[],
        )

    def _llm_field_judge(
        self,
        filled_answers: dict[str, str],
        jd_keywords: list[str],
        profile: dict[str, str] | None,
    ) -> list[str]:
        """LLM-as-judge: scan filled answers for semantic incorrectness given JD + profile.

        Returns a list of human-readable issues. Empty list if no issues found.
        """
        # Build a compact rubric
        profile_summary = ""
        if profile:
            keys_to_show = ("first_name", "last_name", "email", "phone",
                             "location", "visa_type", "salary_expected", "notice_period")
            profile_summary = "\n".join(
                f"  {k}: {profile[k]}"
                for k in keys_to_show if k in profile and profile[k]
            )

        answers_summary = "\n".join(
            f"  {label}: {value}"
            for label, value in filled_answers.items()
            if not label.startswith("_") and value
        )

        prompt = (
            "You are auditing a filled job-application form for semantic correctness. "
            "The fields all accepted their values (read-back passed) — your job is to "
            "catch answers that are *technically valid but semantically wrong* given "
            "the JD requirements and the applicant's actual profile.\n\n"
            f"JD keywords: {', '.join(jd_keywords[:20])}\n\n"
            f"Applicant profile:\n{profile_summary or '  (not provided)'}\n\n"
            f"Filled answers:\n{answers_summary}\n\n"
            "Return ONLY valid JSON:\n"
            '{"issues": ["short description of each problem"], "reasoning": "..."}\n\n'
            "Only flag clear semantic errors (wrong values, contradictions, "
            "answers that disagree with profile). Do NOT flag stylistic issues. "
            "Empty issues list = clean."
        )

        try:
            from shared.agents import get_llm, smart_llm_call
            from langchain_core.messages import HumanMessage
            llm = get_llm(temperature=0, max_tokens=400, agent_name="pre_submit_field_judge")
            response = smart_llm_call(llm, [HumanMessage(content=prompt)])
            text = response.content if hasattr(response, "content") else str(response)

            text = re.sub(r"```(?:json)?\s*", "", text).strip().rstrip("`").strip()
            if "{" in text:
                text = text[text.index("{"):text.rindex("}") + 1]
            data = json.loads(text)
            issues = data.get("issues", [])
            return [str(i) for i in issues if i][:10]  # cap at 10
        except Exception as exc:
            logger.debug("_llm_field_judge: parse/call failed: %s", exc)
            return []
