"""Validation layer for screening answer quality.

Runs post-generation checks to catch hallucinations, format mismatches,
consistency issues, and safety violations before submission.

Usage:
    validator = ScreeningValidator()
    result = validator.validate(answer, question, field, profile)
    if not result.is_valid:
        # result.issues contains specific problems; result.suggested_fix may help
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from shared.logging_config import get_logger

logger = get_logger(__name__)

# Maximum lengths for common field types
_MAX_LENGTHS = {
    "text": 500,
    "textarea": 2000,
    "input": 250,
    "select": 100,
    "radio": 100,
    "checkbox": 100,
}

# Dangerous patterns that should never be in answers
_FORBIDDEN_PATTERNS = [
    r"\b(as an AI|as a language model|I don't have|I cannot|I'm an AI|"
    r"I am an artificial intelligence|I don't have personal)\b",
    r"\b(I was trained|my training data|my knowledge cutoff)\b",
    r"\b(hallucinat|made up|fabricated|not real|doesn't exist)\b",
    r"<script[^>]*>",
    r"javascript:",
    r"on\w+\s*=",
]

# PII patterns to avoid
_PII_PATTERNS = [
    r"\b\d{3}-\d{2}-\d{4}\b",  # SSN
    r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b",  # Credit card
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",  # Email
]


@dataclass
class ValidationResult:
    """Result of a validation check."""

    is_valid: bool = True
    issues: list[str] = field(default_factory=list)
    confidence: float = 1.0
    suggested_fix: str | None = None

    def add_issue(self, issue: str, severity: str = "error") -> None:
        self.issues.append(f"[{severity.upper()}] {issue}")
        if severity == "error":
            self.is_valid = False
            self.confidence = min(self.confidence, 0.3)
        elif severity == "warning":
            self.confidence = min(self.confidence, 0.7)


class ScreeningValidator:
    """Validates screening answers before form submission."""

    def validate(
        self,
        answer: str,
        question: str,
        field: dict[str, Any] | None = None,
        profile: dict[str, Any] | None = None,
    ) -> ValidationResult:
        """Run all validation checks on an answer.

        Args:
            answer: The generated answer text.
            question: The original screening question.
            field: Optional field metadata (type, options, required, etc.).
            profile: Optional user profile for consistency checks.

        Returns:
            ValidationResult with is_valid, issues, and suggested_fix.
        """
        result = ValidationResult()

        if not answer or not str(answer).strip():
            result.add_issue("Answer is empty", "error")
            return result

        answer_str = str(answer).strip()

        # 1. Check for AI self-references / hallucinations
        self._check_ai_references(answer_str, result)

        # 2. Check length constraints
        self._check_length(answer_str, field, result)

        # 3. Check option alignment (if field has options)
        self._check_option_alignment(answer_str, field, result)

        # 4. Check profile consistency
        self._check_profile_consistency(answer_str, question, profile, result)

        # 5. Check for PII leakage
        self._check_pii(answer_str, result)

        # 6. Check for suspicious patterns
        self._check_suspicious_patterns(answer_str, result)

        # Generate suggested fix if invalid
        if not result.is_valid and result.suggested_fix is None:
            result.suggested_fix = self._suggest_fix(answer_str, question, field, result.issues)

        return result

    # ── Individual Checkers ───────────────────────────────────────────────────

    def _check_ai_references(self, answer: str, result: ValidationResult) -> None:
        """Detect AI self-references that would be embarrassing if submitted."""
        for pattern in _FORBIDDEN_PATTERNS:
            if re.search(pattern, answer, re.IGNORECASE):
                result.add_issue(
                    f"Answer contains AI self-reference or hallucination indicator: '{pattern}'",
                    "error",
                )

    def _check_length(
        self,
        answer: str,
        field: dict[str, Any] | None,
        result: ValidationResult,
    ) -> None:
        """Check answer length against field constraints."""
        if field is None:
            return

        field_type = field.get("type", "text").lower()
        max_len = field.get("maxlength") or field.get("max_length")
        if max_len is None:
            max_len = _MAX_LENGTHS.get(field_type, 500)

        if len(answer) > max_len:
            result.add_issue(
                f"Answer length ({len(answer)}) exceeds maximum ({max_len})",
                "error",
            )
            result.suggested_fix = answer[:max_len]
        elif len(answer) > max_len * 0.9:
            result.add_issue(
                f"Answer length ({len(answer)}) is near maximum ({max_len})",
                "warning",
            )

    def _check_option_alignment(
        self,
        answer: str,
        field: dict[str, Any] | None,
        result: ValidationResult,
    ) -> None:
        """Check that the answer matches available options."""
        if field is None:
            return

        options = field.get("options")
        if not options:
            return

        field_type = field.get("type", "").lower()
        if field_type not in {"select", "combobox", "radio", "checkbox", "dropdown"}:
            return

        options_lower = [str(o).lower().strip() for o in options]
        answer_lower = answer.lower().strip()

        if answer_lower not in options_lower:
            # Check if any option contains the answer or vice versa
            partial_match = any(
                answer_lower in opt or opt in answer_lower
                for opt in options_lower
            )
            if not partial_match:
                result.add_issue(
                    f"Answer '{answer[:50]}...' does not match any option: "
                    f"{[o[:30] for o in options]}",
                    "error",
                )

    def _check_profile_consistency(
        self,
        answer: str,
        question: str,
        profile: dict[str, Any] | None,
        result: ValidationResult,
    ) -> None:
        """Check that the answer is consistent with known profile facts."""
        if profile is None:
            return

        answer_lower = answer.lower()

        # Work auth check
        if "visa" in question.lower() or "sponsor" in question.lower():
            profile_sponsorship = profile.get("visa_sponsorship_required")
            if profile_sponsorship is not None:
                needs_sponsorship = bool(profile_sponsorship)
                answer_says_yes = any(
                    word in answer_lower
                    for word in ("yes", "require", "need", "sponsorship")
                )
                answer_says_no = any(
                    word in answer_lower
                    for word in ("no", "not required", "don't need", "citizen", "resident")
                )
                if needs_sponsorship and answer_says_no:
                    result.add_issue(
                        "Answer contradicts profile: visa sponsorship is required",
                        "error",
                    )
                elif not needs_sponsorship and answer_says_yes:
                    result.add_issue(
                        "Answer contradicts profile: visa sponsorship is NOT required",
                        "error",
                    )

        # Salary check
        if "salary" in question.lower() or "compensation" in question.lower():
            profile_salary = profile.get("salary_expectation")
            if profile_salary:
                # Extract numeric from answer
                answer_nums = re.findall(r"[\£\$\€]?\s*(\d{2,3}(?:,\d{3})?)", answer)
                if answer_nums:
                    answer_val = int(answer_nums[0].replace(",", ""))
                    profile_val = self._extract_numeric_salary(str(profile_salary))
                    if profile_val and abs(answer_val - profile_val) > profile_val * 0.5:
                        result.add_issue(
                            f"Salary answer ({answer_val}) differs significantly from "
                            f"profile expectation ({profile_val})",
                            "warning",
                        )

        # Notice period check
        if "notice" in question.lower():
            profile_notice = profile.get("notice_period")
            if profile_notice:
                # Simple string containment check
                if str(profile_notice).lower() not in answer_lower:
                    result.add_issue(
                        f"Notice period answer may not match profile ({profile_notice})",
                        "warning",
                    )

    def _check_pii(self, answer: str, result: ValidationResult) -> None:
        """Check for potential PII leakage."""
        for pattern in _PII_PATTERNS:
            if re.search(pattern, answer):
                result.add_issue(
                    "Answer may contain PII (email, SSN, credit card). "
                    "Review before submission.",
                    "warning",
                )

    def _check_suspicious_patterns(self, answer: str, result: ValidationResult) -> None:
        """Check for other suspicious patterns."""
        # Repeated words (possible generation glitch)
        repeated = re.findall(r"\b(\w+)\s+\1\b", answer, re.IGNORECASE)
        if repeated:
            result.add_issue(
                f"Answer contains repeated words: {repeated}",
                "warning",
            )

        # Excessive punctuation
        if re.search(r"[!?]{3,}", answer):
            result.add_issue(
                "Answer has excessive punctuation",
                "warning",
            )

    # ── Suggested Fixes ───────────────────────────────────────────────────────

    def _suggest_fix(
        self,
        answer: str,
        question: str,
        field: dict[str, Any] | None,
        issues: list[str],
    ) -> str | None:
        """Generate a suggested fix based on validation issues."""
        # If AI reference detected, strip it and regenerate placeholder
        if any("AI self-reference" in i for i in issues):
            return "[Please provide a personal answer to this question]"

        # If length issue, truncate
        if field and any("exceeds maximum" in i for i in issues):
            max_len = field.get("maxlength") or field.get("max_length") or 500
            return answer[:max_len]

        # If option mismatch, try semantic matching before fallback
        if any("does not match any option" in i for i in issues):
            if field and field.get("options"):
                try:
                    from jobpulse.form_engine.field_resolver import _best_option_match
                    matched = _best_option_match(question, answer, field["options"])
                    if matched:
                        return matched
                except Exception:
                    pass
                return field["options"][0] if field["options"] else answer

        return None

    @staticmethod
    def _extract_numeric_salary(text: str) -> int | None:
        """Extract numeric salary value from text."""
        m = re.search(r"[\£\$\€]?\s*(\d{2,3}(?:,\d{3})?)", text)
        if m:
            return int(m.group(1).replace(",", ""))
        return None
