"""Per-field confidence scoring for AUQ dual-process form filling.

System 1 (fast): deterministic/cached mappings, confidence >= 0.9
System 2 (slow): Best-of-N sampling when confidence < 0.9
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from typing import Any

from shared.logging_config import get_logger
from shared.parallel_executor import parallel_grpo_candidates

logger = get_logger(__name__)

CONFIDENCE_THRESHOLD = 0.9

_SOURCE_CONFIDENCE = {
    "deterministic": 1.0,
    "cached": 0.95,
    "consensus": 0.92,
}

_LLM_BASE_CONFIDENCE = 0.85
_SCREENING_PENALTY = 0.15

_TEMPERATURES = [0.0, 0.3, 0.7]


@dataclass
class FieldMapping:
    label: str
    value: str
    confidence: float
    source: str  # "deterministic", "cached", "llm", "consensus"

    @property
    def is_confident(self) -> bool:
        return self.confidence >= CONFIDENCE_THRESHOLD


class ConfidenceScorer:
    def score_mappings(
        self,
        mappings: dict[str, str],
        *,
        source: str,
        fields: list[dict] | None = None,
    ) -> list[FieldMapping]:
        if not mappings:
            return []

        from jobpulse.form_engine.field_mapper import is_screening_like_field

        field_lookup = {f["label"]: f for f in (fields or [])}
        result: list[FieldMapping] = []

        base = _SOURCE_CONFIDENCE.get(source)
        for label, value in mappings.items():
            if base is not None:
                confidence = base
            else:
                confidence = _LLM_BASE_CONFIDENCE
                field = field_lookup.get(label, {})
                if is_screening_like_field(field):
                    confidence -= _SCREENING_PENALTY

            result.append(FieldMapping(
                label=label, value=value,
                confidence=round(confidence, 3),
                source=source,
            ))
        return result

    def pick_consensus(
        self,
        candidates: list[str],
        *,
        field_labels: list[str],
    ) -> dict[str, str]:
        """Parse JSON candidates and pick the majority vote per field label."""
        parsed: list[dict[str, str]] = []
        for raw in candidates:
            try:
                obj = json.loads(raw)
                if isinstance(obj, dict):
                    parsed.append(obj)
            except (json.JSONDecodeError, ValueError):
                logger.debug("Skipping malformed candidate: %.60s", raw)

        result: dict[str, str] = {}
        for label in field_labels:
            values = [p[label] for p in parsed if label in p]
            if not values:
                continue
            counts = Counter(values)
            winner, winner_count = counts.most_common(1)[0]
            # Use majority winner only if it appears more than once;
            # otherwise fall back to the first parsed candidate's value.
            if winner_count > 1 or len(values) == 1:
                result[label] = winner
            else:
                # All values different — log so calibration can spot
                # high-disagreement labels later, then return the
                # lowest-temperature (first) candidate.
                logger.info(
                    "consensus: no majority for %r (%d distinct values), using first",
                    label, len(values),
                )
                result[label] = values[0]

        return result

    def escalate_low_confidence(
        self,
        *,
        low_confidence_mappings: list[FieldMapping],
        fields: list[dict],
        profile: dict[str, Any],
        custom_answers: dict[str, str],
        platform: str,
    ) -> dict[str, str]:
        """Run Best-of-N GRPO sampling for low-confidence fields and return consensus."""
        # Lazy imports to avoid circular dependencies and import-time side effects
        from shared.agents import get_llm
        from jobpulse.form_engine.field_resolver import _profile_prompt_json

        field_labels = [fm.label for fm in low_confidence_mappings]
        field_descriptions = [
            f"- {f['label']} (type={f.get('type','text')}, options={f.get('options',[])})"
            for f in fields
            if f.get("label") in field_labels
        ]

        profile_text = _profile_prompt_json(profile)
        system_prompt = (
            f"You are filling a {platform} job application form.\n"
            f"Profile:\n{profile_text}\n\n"
            "Return a JSON object mapping field label → value for ONLY the listed fields."
        )
        user_message = (
            f"Fields to fill:\n" + "\n".join(field_descriptions) + "\n\n"
            f"Return JSON only, no explanation."
        )

        logger.info(
            "Escalating %d low-confidence fields via Best-of-N on platform=%s",
            len(field_labels), platform,
        )

        candidates = parallel_grpo_candidates(
            llm_factory=lambda temp: get_llm(temperature=temp, model="gpt-4.1-nano"),
            system_prompt=system_prompt,
            user_message=user_message,
            temperatures=_TEMPERATURES,
        )

        return self.pick_consensus(candidates, field_labels=field_labels)


def log_fill_outcomes(
    domain: str,
    outcomes: list[dict],
    *,
    db=None,
) -> None:
    """Log per-field confidence vs actual correctness for calibration."""
    if db is None:
        from jobpulse.form_experience_db import FormExperienceDB
        db = FormExperienceDB()

    for o in outcomes:
        db.log_field_confidence(
            domain=domain,
            field_label=o["label"],
            predicted_confidence=o["confidence"],
            actual_correct=o["correct"],
        )
