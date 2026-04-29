"""Universal screening question detector.

Replaces the brittle `is_screening_like_field()` (which only checks for `?`
or select/radio/checkbox types) with a multi-signal classifier that also
catches text-based screening questions via embedding similarity.

Usage:
    detector = ScreeningDetector()
    is_screening = detector.is_screening(field, profile_mapping)
"""

from __future__ import annotations

import re
from typing import Any

from shared.logging_config import get_logger
from shared.memory_layer._embedder import MemoryEmbedder

logger = get_logger(__name__)

# Fast keyword regex for screening-related terms
_SCREENING_KEYWORDS = re.compile(
    r"\b(experience|salary|compensation|pay|visa|sponsor|right to work|"
    r"work auth|notice|availability|start date|starting date|earliest start|"
    r"relocation|relocate|commute|remote|hybrid|on.?site|in.?person|"
    r"education|degree|qualification|university|college|"
    r"language|fluent|proficiency|english|"
    r"clearance|security|background|criminal|conviction|dbs|"
    r"disability|diversity|gender|ethnicity|race|nationality|age|"
    r"referral|refer|referred|"
    r"consent|agree|confirm|privacy|gdpr|"
    r"driving|licen[cs]e|travel|shift|overtime|"
    r"portfolio|github|website|link|"
    r"cover letter|why apply|motivation|tell us about)\b",
    re.IGNORECASE,
)

# Signals and their weights
_SIGNAL_WEIGHTS = {
    "has_question_mark": 0.30,
    "is_select_radio_checkbox": 0.25,
    "label_has_screening_keywords": 0.20,
    "options_contain_yes_no": 0.15,
    "is_required_and_unmapped": 0.20,
    "label_embedding_similarity": 0.35,
}

# Thresholds
_FAST_PASS_THRESHOLD = 0.50
_EMBEDDING_FALLBACK_THRESHOLD = 0.30
_FINAL_THRESHOLD = 0.55


class ScreeningDetector:
    """Multi-signal detector for screening questions in job application forms."""

    def __init__(self, embedder: Any | None = None) -> None:
        self._embedder = embedder
        self._known_screening_embeddings: list[list[float]] = []
        self._embedding_loaded = False

        if self._embedder is None:
            try:
                self._embedder = MemoryEmbedder()
            except Exception as exc:
                logger.debug("ScreeningDetector: embedder unavailable (%s)", exc)

    def _ensure_embeddings(self) -> None:
        """Lazy-load embeddings of known screening question anchors."""
        if self._embedding_loaded or self._embedder is None:
            return

        anchors = [
            "What is your current salary?",
            "What is your expected salary?",
            "Do you have the right to work in the UK?",
            "Do you require visa sponsorship?",
            "What is your notice period?",
            "When can you start?",
            "Are you willing to relocate?",
            "Are you comfortable working remotely?",
            "How many years of experience do you have?",
            "What is your highest level of education?",
            "Do you have a driving license?",
            "Are you willing to travel?",
            "Do you hold security clearance?",
            "Are you willing to undergo a background check?",
            "What is your gender?",
            "Do you consent to data processing?",
            "Why do you want this role?",
            "Tell us about yourself",
            "Describe your experience with",
            "What languages do you speak?",
            "Are you currently employed?",
            "Who is your current employer?",
            "What is your current job title?",
            "Are you a veteran?",
            "Do you have any criminal convictions?",
            "Please upload your cover letter",
        ]
        try:
            self._known_screening_embeddings = self._embedder.embed_batch(anchors)
            self._embedding_loaded = True
            logger.debug("ScreeningDetector: loaded %d anchor embeddings", len(anchors))
        except Exception as exc:
            logger.debug("ScreeningDetector: failed to load anchor embeddings: %s", exc)

    def is_screening(
        self,
        field: dict[str, Any],
        profile_mapping: dict[str, str] | None = None,
    ) -> bool:
        """Return True if the field is likely a screening question.

        Args:
            field: Dict with keys: label, type, required, options, etc.
            profile_mapping: Optional mapping of already-resolved profile fields.
                A required field that is NOT in this mapping is more likely screening.
        """
        score = self._score_field(field, profile_mapping or {})

        # Fast pass: strong signals alone are enough
        if score >= _FAST_PASS_THRESHOLD:
            return True

        # Weak signals: need embedding boost
        if score >= _EMBEDDING_FALLBACK_THRESHOLD:
            self._ensure_embeddings()
            if self._embedder is not None and self._known_screening_embeddings:
                emb_score = self._embedding_similarity_score(field.get("label", ""))
                score += emb_score * _SIGNAL_WEIGHTS["label_embedding_similarity"]

        return score >= _FINAL_THRESHOLD

    def _score_field(
        self,
        field: dict[str, Any],
        profile_mapping: dict[str, str],
    ) -> float:
        """Compute a screening-likelihood score from 0.0 to ~1.0."""
        label = field.get("label", "")
        field_type = field.get("type", "")
        required = field.get("required", False)
        options = field.get("options", []) or []

        score = 0.0

        # Signal 1: Question mark
        if "?" in label:
            score += _SIGNAL_WEIGHTS["has_question_mark"]

        # Signal 2: Input type
        if field_type in {"select", "combobox", "radio", "checkbox"}:
            score += _SIGNAL_WEIGHTS["is_select_radio_checkbox"]

        # Signal 3: Screening keywords
        if _SCREENING_KEYWORDS.search(label):
            score += _SIGNAL_WEIGHTS["label_has_screening_keywords"]

        # Signal 4: Options contain yes/no/common variants
        if options and self._options_look_screening(options):
            score += _SIGNAL_WEIGHTS["options_contain_yes_no"]

        # Signal 5: Required but unmapped (not a standard profile field)
        if required and label.lower().strip() not in profile_mapping:
            score += _SIGNAL_WEIGHTS["is_required_and_unmapped"]

        return score

    def _options_look_screening(self, options: list[str]) -> bool:
        """Return True if option list looks like a screening question."""
        if not options:
            return False
        opts_lower = [str(o).lower().strip() for o in options]
        # Yes/No variants
        yes_no = {"yes", "no", "true", "false", "1", "0", "prefer not to say", "n/a"}
        matches = sum(1 for o in opts_lower if o in yes_no or o.startswith(("yes", "no")))
        if matches >= 2:
            return True
        # Common screening option sets
        screening_options = {
            "male", "female", "non-binary", "other",
            "full-time", "part-time", "contract", "permanent",
            "uk", "eu", "international", "british",
            "native", "fluent", "intermediate", "beginner",
            "daily", "weekly", "monthly", "annually",
        }
        matches = sum(1 for o in opts_lower if o in screening_options)
        return matches >= 2

    def _embedding_similarity_score(self, label: str) -> float:
        """Return max cosine similarity between label and known screening anchors."""
        if not label or not self._embedder or not self._known_screening_embeddings:
            return 0.0
        try:
            vec = self._embedder.embed(label.strip())
            import math
            dot = max(
                sum(a * b for a, b in zip(vec, anchor))
                for anchor in self._known_screening_embeddings
            )
            norm_q = math.sqrt(sum(x * x for x in vec))
            if norm_q == 0:
                return 0.0
            # Pre-normalised anchors (MiniLM normalises), so dot = cosine
            score = dot / norm_q
            return min(score, 1.0)
        except Exception as exc:
            logger.debug("Embedding similarity failed: %s", exc)
            return 0.0
