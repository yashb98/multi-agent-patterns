"""Compound question decomposer for screening questions.

Splits questions like "How many years of Python and SQL experience?"
into atomic sub-questions that can be resolved independently.

Usage:
    decomposer = QuestionDecomposer()
    subs = decomposer.decompose("How many years of Python and SQL experience?")
    # -> ["How many years of Python experience?", "How many years of SQL experience?"]
"""

from __future__ import annotations

import json
import re
from typing import Optional

from shared.logging_config import get_logger
from shared.agents import get_openai_client, get_model_name

logger = get_logger(__name__)

# Fast heuristic: compound indicators
_COMPOUND_INDICATORS = re.compile(
    r"\b(and|or)\b|"           # explicit conjunctions
    r"[,/]|"                    # punctuation separators
    r"\b(both|either|neither)\b",
    re.IGNORECASE,
)

# Skill-like tokens that suggest a compound experience question
_SKILL_LIKE = re.compile(
    r"\b(python|sql|java|javascript|react|aws|docker|kubernetes|"
    r"machine learning|data science|analytics|cloud|devops|frontend|backend|"
    r"typescript|go|ruby|scala|spark|hadoop|tensorflow|pytorch)\b",
    re.IGNORECASE,
)

# Minimum number of skill-like tokens to consider decomposition
_MIN_SKILL_COUNT = 2


class QuestionDecomposer:
    """Decompose compound screening questions into atomic sub-questions."""

    def __init__(self, llm_enabled: bool = True) -> None:
        self._llm_enabled = llm_enabled

    def decompose(self, question: str) -> list[str] | None:
        """Return sub-questions if compound, else None.

        Uses a fast heuristic first; falls back to LLM for ambiguous cases.
        """
        if not question or not question.strip():
            return None

        q = question.strip()

        # Fast heuristic check
        if not self._is_likely_compound(q):
            return None

        # Try heuristic decomposition first (zero cost)
        heuristic = self._heuristic_decompose(q)
        if heuristic and len(heuristic) > 1:
            logger.debug("Heuristic decomposition: '%s...' -> %d parts", q[:50], len(heuristic))
            return heuristic

        # LLM decomposition for ambiguous cases
        if self._llm_enabled:
            llm_result = self._llm_decompose(q)
            if llm_result and len(llm_result) > 1:
                logger.debug("LLM decomposition: '%s...' -> %d parts", q[:50], len(llm_result))
                return llm_result

        return None

    def _is_likely_compound(self, question: str) -> bool:
        """Fast heuristic: does this look like a compound question?"""
        # Must have compound indicators
        if not _COMPOUND_INDICATORS.search(question):
            return False

        # For experience questions, need multiple skills
        skills = _SKILL_LIKE.findall(question)
        if len(skills) >= _MIN_SKILL_COUNT:
            return True

        # For non-skill questions, check for list patterns
        list_patterns = [
            r"\b(list|describe|mention|name)\b.*\b(and|or)\b",
            r"\d+.*\b(and|or)\b.*\d+",
        ]
        for pat in list_patterns:
            if re.search(pat, question, re.IGNORECASE):
                return True

        return False

    def _heuristic_decompose(self, question: str) -> list[str] | None:
        """Rule-based decomposition for common patterns."""
        q = question.strip()

        # Pattern: "experience with X and Y"  OR  "X and Y experience"
        patterns = [
            # "experience with/in X and Y"
            (r"(.*experience(?:\s+do you have)?(?:\s+with|in)?)\s+(.+?)[\?\.]?$", r"{} {}?"),
            # "proficient in/with X and Y"
            (r"(.*proficient(?:\s+in|with)?)\s+(.+?)[\?\.]?$", r"{} {}?"),
            # "familiar with X, Y, and Z"
            (r"(.*familiar(?:\s+with)?)\s+(.+?)[\?\.]?$", r"{} {}?"),
            # "years of X and Y experience"
            (r"(.*years\s+of)\s+(.+?)\s+experience[\?\.]?$", r"{} {} experience?"),
            # "X and Y experience do you have?"  (items before "experience")
            (r"(.*?)\s+((?:\w+\s+(?:and|or)\s+)?\w+)\s+experience(?:\s+do you have)?[\?\.]?$", r"{} {} experience?"),
            # "How many years of X and Y experience do you have?"
            (r"(.*years\s+of)\s+(.+?)\s+experience\s+do you have[\?\.]?$", r"{} {} experience?"),
        ]

        for pattern, template in patterns:
            m = re.search(pattern, q, re.IGNORECASE)
            if m:
                prefix = m.group(1).strip()
                items_str = m.group(2)
                items = self._split_items(items_str)
                if len(items) > 1:
                    return [template.format(prefix, item) for item in items]

        return None

    def _split_items(self, text: str) -> list[str]:
        """Split a comma/and/or separated list into items."""
        # Replace 'and' / 'or' with comma for uniform splitting
        text = re.sub(r"\s+and\s+", ", ", text, flags=re.IGNORECASE)
        text = re.sub(r"\s+or\s+", ", ", text, flags=re.IGNORECASE)
        items = [item.strip() for item in text.split(",")]
        # Filter out empty and generic filler words
        items = [i for i in items if i and i.lower() not in {"", "etc", "etc.", "and so on"}]
        return items

    def _llm_decompose(self, question: str) -> list[str] | None:
        """LLM-based decomposition for ambiguous compound questions."""
        prompt = (
            "Break this job application screening question into the smallest possible atomic sub-questions.\n"
            "If it is already a single atomic question, return it unchanged as a single-item array.\n"
            "Each sub-question must be self-contained and answerable on its own.\n"
            "Do NOT add extra text, explanations, or numbering.\n\n"
            f"Question: {question}\n\n"
            "Return a JSON array of strings only."
        )
        try:
            from shared.agents import cognitive_llm_call
            raw = cognitive_llm_call(
                task=prompt,
                domain="screening_decomposition",
                stakes="medium",
            )
            if not raw:
                return None
            # Strip markdown code fences
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            sub_questions = json.loads(raw)
            if isinstance(sub_questions, list) and len(sub_questions) > 1:
                # Validate: each sub-question should be a string and look like a question
                cleaned = [str(sq).strip() for sq in sub_questions if str(sq).strip()]
                if len(cleaned) > 1:
                    return cleaned
            return None
        except Exception as exc:
            logger.debug("LLM decomposition failed: %s", exc)
            return None


class AnswerRecombiner:
    """Recombine answers from decomposed sub-questions into a single answer."""

    @staticmethod
    def recombine(answers: list[tuple[str, str]]) -> str:
        """Recombine sub-question answers.

        Args:
            answers: List of (sub_question, answer) tuples.

        Returns:
            Combined answer string.
        """
        if not answers:
            return ""
        if len(answers) == 1:
            return answers[0][1]

        # Try to extract skill names for formatting
        parts = []
        for sq, ans in answers:
            skill = _extract_skill_name(sq)
            if skill:
                parts.append(f"{skill}: {ans}")
            else:
                parts.append(ans)

        if parts:
            return "; ".join(parts)
        return "; ".join(a for _, a in answers)


def _extract_skill_name(question: str) -> str | None:
    """Extract the skill/technology name from a decomposed experience question."""
    patterns = [
        r"experience(?:\s+do you have)?(?:\s+with|in)\s+(.+?)[\?\.]?$",
        r"proficient(?:\s+in|with)\s+(.+?)[\?\.]?$",
        r"familiar(?:\s+with)\s+(.+?)[\?\.]?$",
        r"years\s+of\s+(.+?)\s+experience[\?\.]?$",
    ]
    for pat in patterns:
        m = re.search(pat, question, re.IGNORECASE)
        if m:
            return m.group(1).strip().rstrip("?.")
    return None
