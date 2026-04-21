"""Score validation — clamp, detect anomalies, preserve audit trail."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from shared.logging_config import get_logger

logger = get_logger(__name__)

NAN_FALLBACK = 5.0
_anomaly_counter: int = 0
ANOMALY_THRESHOLD: int = 3


def reset_anomaly_counter() -> None:
    global _anomaly_counter
    _anomaly_counter = 0


def get_anomaly_count() -> int:
    return _anomaly_counter


def _increment_anomaly() -> None:
    global _anomaly_counter
    _anomaly_counter += 1
    if _anomaly_counter == ANOMALY_THRESHOLD:
        logger.warning("Anomaly threshold reached: %d anomalies in this run", _anomaly_counter)
        try:
            from shared.execution import emit
            emit("governance:anomalies", "governance.score_anomaly", {
                "count": _anomaly_counter,
            })
        except Exception:
            pass


def clamp_score(value: float, lo: float = 0.0, hi: float = 10.0) -> float:
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        if math.isnan(value):
            logger.warning("NaN score detected, using fallback %.1f", NAN_FALLBACK)
            _increment_anomaly()
            return NAN_FALLBACK
        value = hi if value > 0 else lo
    if value < lo:
        logger.warning("Score %.2f below minimum %.1f, clamping", value, lo)
        _increment_anomaly()
        return lo
    if value > hi:
        logger.warning("Score %.2f above maximum %.1f, clamping", value, hi)
        _increment_anomaly()
        return hi
    return value


@dataclass
class ReviewResult:
    overall_score: float
    accuracy_score: float
    anomalies: list[str] = field(default_factory=list)
    original_raw: dict = field(default_factory=dict)


def validate_review(review_dict: dict) -> ReviewResult:
    anomalies: list[str] = []
    original_raw = review_dict.copy()

    raw_overall = review_dict.get("overall_score", None)
    try:
        overall = float(raw_overall) if raw_overall is not None else NAN_FALLBACK
        if raw_overall is None:
            anomalies.append("missing overall_score, using fallback")
    except (ValueError, TypeError):
        overall = NAN_FALLBACK
        anomalies.append(f"could not parse overall_score={raw_overall!r}, using fallback")
        _increment_anomaly()

    if isinstance(overall, float) and math.isnan(overall):
        anomalies.append("NaN overall_score detected")

    clamped_overall = clamp_score(overall)
    if clamped_overall != overall and not any("nan" in a.lower() for a in anomalies):
        anomalies.append(f"overall_score {overall} clamped to {clamped_overall}")

    raw_accuracy = review_dict.get("accuracy_score", None)
    try:
        accuracy = float(raw_accuracy) if raw_accuracy is not None else 0.0
    except (ValueError, TypeError):
        accuracy = 0.0
        anomalies.append(f"could not parse accuracy_score={raw_accuracy!r}")
        _increment_anomaly()

    clamped_accuracy = clamp_score(accuracy)
    if clamped_accuracy != accuracy:
        anomalies.append(f"accuracy_score {accuracy} clamped to {clamped_accuracy}")

    return ReviewResult(
        overall_score=clamped_overall,
        accuracy_score=clamped_accuracy,
        anomalies=anomalies,
        original_raw=original_raw,
    )
