"""Universal screening question detector.

Uses embedding similarity as the primary signal, supplemented by structural
signals (field type, question mark, options). No regex for classification.
"""
from __future__ import annotations

from typing import Any

from shared.logging_config import get_logger

logger = get_logger(__name__)

_DEFAULT_SIGNAL_WEIGHTS = {
    "embedding_similarity": 0.40,
    "is_select_radio_checkbox": 0.20,
    "has_question_mark": 0.15,
    "options_contain_yes_no": 0.15,
    "is_required_and_unmapped": 0.10,
}

_FINAL_THRESHOLD = 0.45

_SCREENING_ANCHORS = [
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


class ScreeningDetector:
    """Embedding-primary detector for screening questions in job application forms."""

    def __init__(self, embedder: Any | None = None) -> None:
        self._embedder = embedder
        self._weights = _DEFAULT_SIGNAL_WEIGHTS.copy()
        self._load_adaptive_weights()

    def _load_adaptive_weights(self) -> None:
        try:
            from shared.semantic_utils import get_adaptive_weights
            self._weights = get_adaptive_weights(
                "screening_detector", _DEFAULT_SIGNAL_WEIGHTS,
            )
        except Exception:
            pass

    def _ensure_embedder(self) -> None:
        if self._embedder is not None:
            return
        try:
            from shared.semantic_utils import _get_embedder
            self._embedder = _get_embedder()
        except Exception as exc:
            logger.debug("ScreeningDetector: embedder unavailable (%s)", exc)

    def is_screening(
        self,
        field: dict[str, Any],
        profile_mapping: dict[str, str] | None = None,
    ) -> bool:
        """Return True if the field is likely a screening question."""
        scores = self._compute_signals(field, profile_mapping or {})
        total = sum(
            scores.get(sig, 0.0) * self._weights.get(sig, 0.0)
            for sig in self._weights
        )
        return total >= _FINAL_THRESHOLD

    def _compute_signals(
        self,
        field: dict[str, Any],
        profile_mapping: dict[str, str],
    ) -> dict[str, float]:
        label = field.get("label", "")
        field_type = field.get("type", "")
        required = field.get("required", False)
        options = field.get("options", []) or []

        signals: dict[str, float] = {}

        # Embedding similarity (primary)
        signals["embedding_similarity"] = self._embedding_score(label)

        # Structural signals
        signals["has_question_mark"] = 1.0 if "?" in label else 0.0
        signals["is_select_radio_checkbox"] = 1.0 if field_type in {"select", "combobox", "radio", "checkbox"} else 0.0
        signals["options_contain_yes_no"] = 1.0 if self._options_look_screening(options) else 0.0
        signals["is_required_and_unmapped"] = 1.0 if required and label.lower().strip() not in profile_mapping else 0.0

        return signals

    def _embedding_score(self, label: str) -> float:
        if not label or not label.strip():
            return 0.0
        self._ensure_embedder()
        if self._embedder is None:
            return 0.0
        try:
            from shared.semantic_utils import semantic_similarity
            return max(
                semantic_similarity(label, anchor) for anchor in _SCREENING_ANCHORS
            )
        except Exception:
            return 0.0

    def _options_look_screening(self, options: list[str]) -> bool:
        if not options:
            return False
        opts_lower = [str(o).lower().strip() for o in options]
        yes_no = {"yes", "no", "true", "false", "1", "0", "prefer not to say", "n/a"}
        matches = sum(1 for o in opts_lower if o in yes_no or o.startswith(("yes", "no")))
        if matches >= 2:
            return True
        screening_options = {
            "male", "female", "non-binary", "other",
            "full-time", "part-time", "contract", "permanent",
            "uk", "eu", "international", "british",
            "native", "fluent", "intermediate", "beginner",
        }
        return sum(1 for o in opts_lower if o in screening_options) >= 2

    def record_outcome(self, field: dict[str, Any], was_screening: bool) -> None:
        """Record outcome for adaptive weight learning."""
        signals = self._compute_signals(field, {})
        try:
            from shared.semantic_utils import record_weight_outcome
            record_weight_outcome("screening_detector", signals, was_screening)
        except Exception:
            pass
