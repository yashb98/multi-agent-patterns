"""Option aligner for screening questions with predefined choices.

Ensures generated answers match available options (yes/no, select dropdowns, etc.)
rather than free-text answers that break form submission.

Usage:
    aligner = OptionAligner()
    aligned = aligner.align_answer("yes", options=["Yes", "No"])
    # -> "Yes"
"""

from __future__ import annotations

import re
from typing import Any

from shared.logging_config import get_logger

logger = get_logger(__name__)

# Normalization map for common option variants
_OPTION_NORMALISATION: dict[str, str] = {
    "yes": "yes",
    "y": "yes",
    "yep": "yes",
    "yeah": "yes",
    "true": "yes",
    "1": "yes",
    "no": "no",
    "n": "no",
    "nope": "no",
    "false": "no",
    "0": "no",
    "prefer not to say": "prefer_not_to_say",
    "prefer not to answer": "prefer_not_to_say",
    "n/a": "n/a",
    "not applicable": "n/a",
    "n/a - don't have one": "n/a",
}

# Flags that indicate a field has options
_OPTION_FIELD_TYPES = {
    "select", "combobox", "radio", "checkbox", "dropdown",
    "select-one", "searchable_dropdown", "radio_yes_no",
    "multiselect_skills", "textbox",
}


class OptionAligner:
    """Aligns free-text answers to predefined option sets."""

    def align_answer(
        self,
        answer: str,
        options: list[str],
        field_type: str = "",
    ) -> str:
        """Align an answer to the closest available option.

        Args:
            answer: The raw answer text.
            options: Available options from the form field.
            field_type: Optional field type hint (select, radio, etc.).

        Returns:
            The aligned option string, or the original answer if no match.
        """
        if not options:
            return answer.strip()

        if not answer or not str(answer).strip():
            return answer

        # Check learned corrections first
        learned = self._lookup_learned_mapping(answer, field_type)
        if learned:
            # Verify the learned answer is still in options
            learned_norm = self._normalise(learned)
            for opt, opt_norm in [(opt, self._normalise(opt)) for opt in options]:
                if opt_norm == learned_norm:
                    return opt

        answer_norm = self._normalise(answer)
        options_norm = [(opt, self._normalise(opt)) for opt in options]

        # Exact match first
        for opt, opt_norm in options_norm:
            if opt_norm == answer_norm:
                return opt  # Return original casing

        # Normalised match
        for opt, opt_norm in options_norm:
            if opt_norm == answer_norm:
                return opt

        # Embedding similarity (primary semantic tier)
        try:
            from shared.semantic_utils import best_semantic_match
            emb_match, emb_score = best_semantic_match(answer.strip(), options, min_score=0.70)
            if emb_match is not None:
                logger.debug("Embedding aligned '%s' -> '%s' (score=%.2f)", answer[:50], emb_match, emb_score)
                return emb_match
        except Exception:
            pass

        # Fuzzy prefix / contains match
        best_match: str | None = None
        best_score = 0
        for opt, opt_norm in options_norm:
            score = self._fuzzy_score(answer_norm, opt_norm)
            if score > best_score:
                best_score = score
                best_match = opt

        threshold = 0.75 if field_type in _OPTION_FIELD_TYPES else 0.60
        if best_match and best_score >= threshold:
            logger.debug(
                "Aligned '%s' -> '%s' (score=%.2f)",
                answer[:50],
                best_match,
                best_score,
            )
            return best_match

        # Default to original if no good match
        logger.debug(
            "No option alignment for '%s...' in %s",
            answer[:50],
            [o[:30] for o in options],
        )
        return answer.strip()

    def is_option_field(self, field: dict[str, Any]) -> bool:
        """Return True if the field has selectable options."""
        return (
            field.get("type", "").lower() in _OPTION_FIELD_TYPES
            or bool(field.get("options"))
        )

    @staticmethod
    def _lookup_learned_mapping(answer: str, field_type: str = "") -> str | None:
        """Check if this answer has a user-corrected mapping in the learned DB."""
        try:
            import sqlite3
            from jobpulse.config import DATA_DIR
            db_path = str(DATA_DIR / "option_alignment_learned.db")
            norm = OptionAligner._normalise(answer)
            with sqlite3.connect(db_path) as conn:
                row = conn.execute(
                    """SELECT correct_option FROM learned_option_mappings
                       WHERE agent_answer_norm = ?
                         AND (field_type = ? OR field_type = '')
                       ORDER BY times_seen DESC LIMIT 1""",
                    (norm, field_type),
                ).fetchone()
            if row:
                return row[0]
        except Exception:
            pass
        return None

    @staticmethod
    def _normalise(text: str) -> str:
        """Normalise a text string for comparison."""
        t = str(text).lower().strip()
        t = t.replace("-", " ").replace("_", " ")
        # Remove articles
        t = re.sub(r"\b(a|an|the)\b", "", t)
        # Normalise whitespace
        t = re.sub(r"\s+", " ", t).strip()
        # Map known variants
        if t in _OPTION_NORMALISATION:
            t = _OPTION_NORMALISATION[t]
        return t

    @staticmethod
    def _fuzzy_score(a: str, b: str) -> float:
        """Simple fuzzy score between 0 and 1."""
        if a == b:
            return 1.0
        if a in b or b in a:
            return min(len(a), len(b)) / max(len(a), len(b)) * 0.9
        # Word overlap
        words_a = set(a.split())
        words_b = set(b.split())
        if not words_a or not words_b:
            return 0.0
        overlap = len(words_a & words_b)
        return overlap / max(len(words_a), len(words_b))


class BoolFieldHandler:
    """Handles yes/no / true/false / required boolean fields."""

    # Common yes/no option sets seen in ATS forms
    YES_PATTERNS = {"yes", "true", "1", "required", "mandatory", "agree", "accept", "i agree", "i consent"}
    NO_PATTERNS = {"no", "false", "0", "not required", "decline", "i do not agree", "i do not consent"}

    @classmethod
    def resolve(cls, answer: str, options: list[str]) -> str:
        """Resolve a boolean answer to match available options."""
        answer_norm = OptionAligner._normalise(answer)

        # Map to yes/no intent
        is_yes = answer_norm in cls.YES_PATTERNS or any(
            answer_norm.startswith(p) for p in cls.YES_PATTERNS
        )
        is_no = answer_norm in cls.NO_PATTERNS or any(
            answer_norm.startswith(p) for p in cls.NO_PATTERNS
        )

        if not is_yes and not is_no:
            # Ambiguous — try fuzzy alignment
            aligner = OptionAligner()
            return aligner.align_answer(answer, options)

        # Find the best matching option
        options_norm = {opt: OptionAligner._normalise(opt) for opt in options}

        target = "yes" if is_yes else "no"
        best_match: str | None = None
        best_score = -1.0

        for opt, opt_norm in options_norm.items():
            score = 0
            if target == "yes":
                score = sum(1 for p in cls.YES_PATTERNS if p in opt_norm)
            else:
                score = sum(1 for p in cls.NO_PATTERNS if p in opt_norm)
            # Prefer shorter, more exact matches
            if score > best_score:
                best_score = score
                best_match = opt
            elif score == best_score and best_match and len(opt) < len(best_match):
                best_match = opt

        if best_match:
            return best_match

        # Fallback: first option for yes, second for no (common convention)
        if len(options) >= 2:
            return options[0] if is_yes else options[1]
        return options[0] if options else answer

    @classmethod
    def is_boolean_field(cls, field: dict[str, Any]) -> bool:
        """Heuristic: does this field look like a yes/no boolean?"""
        options = [str(o).lower().strip() for o in (field.get("options") or [])]
        if not options:
            return False
        # Single checkbox
        if field.get("type", "").lower() == "checkbox" and len(options) <= 1:
            return True
        # Two options that look like yes/no
        yes_count = sum(1 for o in options if any(p in o for p in cls.YES_PATTERNS))
        no_count = sum(1 for o in options if any(p in o for p in cls.NO_PATTERNS))
        return yes_count >= 1 and no_count >= 1


class SalaryFieldHandler:
    """Handles salary expectation fields."""

    @staticmethod
    def extract_numeric(answer: str) -> str | None:
        """Extract the numeric salary value from an answer."""
        # Match patterns like £50,000-£60,000, 50k, 50000, £50k
        # Try full number with commas first
        m = re.search(r"[\£\$\€]?\s*(\d{2,3}(?:,\d{3})+)\s*(k)?", answer, re.IGNORECASE)
        if m:
            num = m.group(1).replace(",", "")
            return num
        # Then try simple number or number+k
        m = re.search(r"[\£\$\€]?\s*(\d{2,3})\s*(k|000)?", answer, re.IGNORECASE)
        if m:
            num = m.group(1)
            suffix = m.group(2) or ""
            if suffix.lower() in {"k", "000"}:
                return f"{num}000"
            return num
        return None

    @staticmethod
    def format_for_range(answer: str, options: list[str]) -> str:
        """Format salary answer for a range-based select/dropdown."""
        numeric = SalaryFieldHandler.extract_numeric(answer)
        if not numeric:
            return answer

        numeric_val = int(numeric)
        aligner = OptionAligner()

        # Try to find the range bracket
        best_match: str | None = None
        best_midpoint_diff = float("inf")

        for opt in options:
            opt_lower = opt.lower()
            # Detect if option is in 'k' format (e.g., £40-50k)
            is_k_format = "k" in opt_lower
            multiplier = 1000 if is_k_format else 1

            opt_nums = re.findall(r"(\d{2,3}(?:,\d{3})*)", opt)
            if len(opt_nums) >= 2:
                low = int(opt_nums[0].replace(",", "")) * multiplier
                high = int(opt_nums[1].replace(",", "")) * multiplier
                if low <= numeric_val <= high:
                    return opt
                # If no direct match, find closest range
                midpoint = (low + high) / 2
                diff = abs(numeric_val - midpoint)
                if diff < best_midpoint_diff:
                    best_midpoint_diff = diff
                    best_match = opt

        if best_match:
            return best_match

        # Fallback to fuzzy alignment
        return aligner.align_answer(answer, options)
