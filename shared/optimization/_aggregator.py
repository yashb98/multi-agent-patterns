"""SignalAggregator — detects cross-loop patterns from the signal bus."""

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from shared.logging_config import get_logger
from shared.optimization._signals import SignalBus, LearningSignal
from shared.optimization._tracker import PerformanceTracker

logger = get_logger(__name__)

_SYSTEMIC_THRESHOLD = 3
_PLATFORM_FAILURE_THRESHOLD = 3
_PERSONA_DRIFT_WINDOW = 5
_CONFIDENCE_CROSS_DOMAIN_BOOST = 0.07


@dataclass
class AggregatedInsight:
    pattern_type: str
    confidence: float
    contributing_signals: list[str]
    domain: str
    recommended_action: str
    evidence: str


class SignalAggregator:
    """Consumes the signal bus, detects cross-loop patterns."""

    def __init__(self, signal_bus: SignalBus, tracker: PerformanceTracker,
                 memory_manager=None):
        self._bus = signal_bus
        self._tracker = tracker
        self._memory = memory_manager
        self._paused_loops: set[str] = set()

    def pause_loop(self, loop_name: str):
        self._paused_loops.add(loop_name)

    def resume_loop(self, loop_name: str):
        self._paused_loops.discard(loop_name)

    def _filter_paused(self, signals: list[LearningSignal]) -> list[LearningSignal]:
        return [s for s in signals if s.source_loop not in self._paused_loops]

    def check_realtime(self) -> list[AggregatedInsight]:
        insights: list[AggregatedInsight] = []
        recent = self._filter_paused(self._bus.recent())
        insights.extend(self._detect_systemic_failures(recent))
        insights.extend(self._detect_platform_change(recent))
        return insights

    def check_regressions(self) -> list[AggregatedInsight]:
        insights: list[AggregatedInsight] = []
        rows = self._tracker.get_recent_actions(limit=50)
        for row in rows:
            before = row["before_metrics"]
            after = row["after_metrics"]
            common = set(before.keys()) & set(after.keys())
            for key in common:
                b_val = float(before[key])
                a_val = float(after[key])
                if b_val == 0:
                    continue
                is_rate = "rate" in key
                if is_rate:
                    regressed = a_val > b_val and (a_val - b_val) / b_val > 0.15
                else:
                    regressed = a_val < b_val and (b_val - a_val) / b_val > 0.15
                if regressed:
                    insights.append(AggregatedInsight(
                        pattern_type="regression",
                        confidence=0.9,
                        contributing_signals=[],
                        domain=row["domain"],
                        recommended_action=f"rollback_{row['loop_name']}",
                        evidence=(
                            f"{row['loop_name']} on {row['domain']}: "
                            f"{key} went from {b_val:.3f} to {a_val:.3f}"
                        ),
                    ))
        return insights

    def sweep(self) -> list[AggregatedInsight]:
        insights: list[AggregatedInsight] = []
        since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        signals = self._filter_paused(self._bus.query(since=since, limit=2000))
        insights.extend(self._detect_persona_drift(signals))
        insights.extend(self._detect_redundant(signals))
        insights.extend(self._detect_repeated_failures(signals))
        return insights

    def _detect_systemic_failures(
        self, signals: list[LearningSignal],
    ) -> list[AggregatedInsight]:
        corrections = [s for s in signals if s.signal_type == "correction"]
        by_domain_field: dict[tuple[str, str], list[LearningSignal]] = defaultdict(list)
        for s in corrections:
            field_key = s.payload.get("field", "unknown")
            by_domain_field[(s.domain, field_key)].append(s)

        insights = []
        for (domain, field_key), sigs in by_domain_field.items():
            sessions = {s.session_id for s in sigs}
            if len(sessions) < _SYSTEMIC_THRESHOLD:
                continue

            if self._dedup_with_memory(domain, field_key):
                continue

            # D1: Scale confidence by sample size — 3 sessions = 0.7, 10+ = 0.9
            confidence = min(0.6 + 0.03 * len(sessions), 0.9)
            cross = self._cross_domain_search(field_key)
            if cross:
                same_domain = any(c.get("domain") == domain for c in cross)
                if same_domain:
                    continue
                confidence += _CONFIDENCE_CROSS_DOMAIN_BOOST

            insights.append(AggregatedInsight(
                pattern_type="systemic_failure",
                confidence=min(confidence, 1.0),
                contributing_signals=[s.signal_id for s in sigs],
                domain=domain,
                recommended_action="generate_insight",
                evidence=(
                    f"{len(sigs)} corrections on {domain}/{field_key} "
                    f"across {len(sessions)} sessions"
                ),
            ))
        return insights

    def _detect_platform_change(
        self, signals: list[LearningSignal],
    ) -> list[AggregatedInsight]:
        failures = [s for s in signals
                    if s.signal_type == "failure" and s.severity == "critical"]
        by_domain: dict[str, list[LearningSignal]] = defaultdict(list)
        for s in failures:
            by_domain[s.domain].append(s)

        insights = []
        for domain, sigs in by_domain.items():
            if len(sigs) >= _PLATFORM_FAILURE_THRESHOLD:
                insights.append(AggregatedInsight(
                    pattern_type="platform_change",
                    confidence=0.7,
                    contributing_signals=[s.signal_id for s in sigs],
                    domain=domain,
                    recommended_action="alert_human",
                    evidence=f"{len(sigs)} critical failures on {domain}",
                ))
        return insights

    def _detect_persona_drift(
        self, signals: list[LearningSignal],
    ) -> list[AggregatedInsight]:
        score_changes = [s for s in signals
                         if s.signal_type == "score_change"
                         and s.source_loop == "persona_evolution"]
        by_domain: dict[str, list[LearningSignal]] = defaultdict(list)
        for s in score_changes:
            by_domain[s.domain].append(s)

        insights = []
        for domain, sigs in by_domain.items():
            if len(sigs) < _PERSONA_DRIFT_WINDOW:
                continue
            # Sort ascending by timestamp so first half = older, second half = newer
            ordered = sorted(sigs, key=lambda s: s.timestamp)
            new_scores = [s.payload.get("new_score", 0) for s in ordered]
            n = len(new_scores)
            if n >= _PERSONA_DRIFT_WINDOW:
                # D4: Linear regression slope — more robust than half-split
                x_mean = (n - 1) / 2.0
                y_mean = sum(new_scores) / n
                num = sum((i - x_mean) * (y - y_mean) for i, y in enumerate(new_scores))
                den = sum((i - x_mean) ** 2 for i in range(n))
                slope = num / den if den else 0
                # Negative slope means declining; threshold: -0.1 per data point
                if slope < -0.1:
                    first_score = new_scores[0]
                    last_score = new_scores[-1]
                    insights.append(AggregatedInsight(
                        pattern_type="persona_drift",
                        confidence=min(0.7 + 0.02 * n, 0.95),
                        contributing_signals=[s.signal_id for s in ordered],
                        domain=domain,
                        recommended_action="rollback_persona",
                        evidence=(
                            f"Score declining for {domain}: "
                            f"{first_score:.1f} → {last_score:.1f} "
                            f"(slope={slope:.2f}, n={n})"
                        ),
                    ))
        return insights

    def _detect_redundant(
        self, signals: list[LearningSignal],
    ) -> list[AggregatedInsight]:
        by_domain_field: dict[tuple[str, str], set[str]] = defaultdict(set)
        for s in signals:
            field_key = s.payload.get("field") or s.payload.get("reason", "")
            if field_key:
                by_domain_field[(s.domain, field_key)].add(s.source_loop)

        insights = []
        for (domain, field_key), loops in by_domain_field.items():
            if len(loops) >= 2:
                insights.append(AggregatedInsight(
                    pattern_type="redundant",
                    confidence=0.6,
                    contributing_signals=[],
                    domain=domain,
                    recommended_action="merge_actions",
                    evidence=f"Loops {', '.join(loops)} acting on {domain}/{field_key}",
                ))
        return insights

    def _detect_repeated_failures(
        self, signals: list[LearningSignal],
    ) -> list[AggregatedInsight]:
        """Detect domains with repeated failures (any severity) across multiple sessions."""
        failures = [s for s in signals if s.signal_type == "failure"]
        by_domain: dict[str, list[LearningSignal]] = defaultdict(list)
        for s in failures:
            by_domain[s.domain].append(s)

        insights = []
        for domain, sigs in by_domain.items():
            sessions = {s.session_id for s in sigs}
            if len(sigs) >= _PLATFORM_FAILURE_THRESHOLD and len(sessions) >= 2:
                insights.append(AggregatedInsight(
                    pattern_type="repeated_failures",
                    confidence=0.65,
                    contributing_signals=[s.signal_id for s in sigs],
                    domain=domain,
                    recommended_action="investigate_domain",
                    evidence=f"{len(sigs)} failures on {domain} across {len(sessions)} sessions",
                ))
        return insights

    def _dedup_with_memory(self, domain: str, field_key: str) -> bool:
        if not self._memory:
            return False
        try:
            results = self._memory.search_semantic(
                query=f"{domain} {field_key} format",
                domain=domain,
                limit=3,
            )
            for r in results:
                score = r.get("score", 0)
                result_domain = r.get("domain")
                # Dedup if high-confidence match for this domain, or no domain
                # qualifier (general knowledge applies to all domains)
                if score >= 0.85 and (result_domain is None or result_domain == domain):
                    return True
        except Exception:
            pass
        return False

    def _cross_domain_search(self, field_key: str) -> list[dict]:
        if not self._memory:
            return []
        try:
            return self._memory.search_semantic(
                query=f"{field_key} format",
                limit=3,
            )
        except Exception:
            return []
