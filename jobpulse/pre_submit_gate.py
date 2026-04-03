"""Pre-submit quality gate — LLM reviews filled application as a recruiter."""

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


def _get_openai_client():
    """Return OpenAI client, or None."""
    try:
        from jobpulse.config import OPENAI_API_KEY

        if not OPENAI_API_KEY:
            return None
        from openai import OpenAI

        return OpenAI(api_key=OPENAI_API_KEY)
    except Exception:
        return None


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
        client = _get_openai_client()
        if client is None:
            logger.warning("PreSubmitGate: no OpenAI client — passing by default")
            return GateResult(passed=True, score=0.0)

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
            response = client.chat.completions.create(
                model="gpt-4.1-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
            )
            raw = response.choices[0].message.content or ""
            cleaned = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`").strip()
            data = json.loads(cleaned)
            score = float(data.get("score", 0))
            return GateResult(
                passed=score >= self.PASS_THRESHOLD,
                score=score,
                weaknesses=data.get("weaknesses", []),
                suggestions=data.get("suggestions", []),
            )
        except Exception as exc:
            logger.warning("PreSubmitGate review failed: %s — passing by default", exc)
            return GateResult(passed=True, score=0.0)
