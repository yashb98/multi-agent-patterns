"""Gate policy — suggests per-domain threshold adjustments based on outcomes.

Reads gate_effectiveness tables and computes optimal thresholds per domain
(e.g., "ml_engineer", "backend", "devops") to maximize interview rate.

Usage:
    policy = GatePolicy()
    suggestions = policy.suggest_thresholds("ml_engineer")
    # -> {"gate3": 72, "gate2": 3, "reason": "30% of rejected jobs got interviews"}
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Optional

from shared.logging_config import get_logger
# shared/ MUST NOT import from jobpulse/ (Principle 1, dependency direction).
# DATA_DIR was previously sourced from jobpulse.config; resolved via the
# shared.paths constant instead. (S10 audit M-B.)
from shared.paths import DATA_DIR

logger = get_logger(__name__)

_DEFAULT_DB = str(DATA_DIR / "applications.db")

# Baseline thresholds from skill_graph_store.py
_BASELINE_THRESHOLDS = {
    "gate1_experience_years": 3,
    "gate2_min_skills_matched": 3,
    "gate2_min_projects": 2,
    "gate2_skill_match_pct": 0.92,
    "gate3_competitiveness": 75,
}

_MIN_SAMPLES_PER_DOMAIN = 5


@dataclass
class ThresholdSuggestion:
    """A suggested threshold change with evidence."""

    gate_name: str
    current_value: float
    suggested_value: float
    domain: str
    evidence: str
    confidence: float
    sample_size: int


class GatePolicy:
    """Analyzes gate effectiveness and suggests threshold adjustments."""

    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = db_path or _DEFAULT_DB

    def suggest_thresholds(self, domain: str) -> list[ThresholdSuggestion]:
        """Suggest threshold adjustments for a specific job domain.

        Args:
            domain: Job domain extracted from skills (e.g., "ml_engineer",
                "backend", "devops", "frontend", "data_engineer").

        Returns:
            List of threshold suggestions. Empty if insufficient data.
        """
        suggestions: list[ThresholdSuggestion] = []

        # Analyze Gate 3: competitiveness score threshold
        gate3_suggestion = self._analyze_gate3(domain)
        if gate3_suggestion:
            suggestions.append(gate3_suggestion)

        # Analyze Gate 2: minimum skills matched
        gate2_suggestion = self._analyze_gate2(domain)
        if gate2_suggestion:
            suggestions.append(gate2_suggestion)

        return suggestions

    def _analyze_gate3(self, domain: str) -> ThresholdSuggestion | None:
        """Analyze Gate 3 effectiveness and suggest threshold adjustment."""
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            # Get applications for this domain with gate decisions and outcomes
            rows = conn.execute(
                """
                SELECT a.job_id, a.match_tier, a.ats_score, a.status,
                       g.decision, g.final_outcome
                FROM applications a
                LEFT JOIN gate_effectiveness g ON a.job_id = g.job_id
                WHERE a.matched_projects LIKE ?
                   OR a.title LIKE ?
                   OR EXISTS (
                       SELECT 1 FROM application_outcomes o
                       WHERE o.job_id = a.job_id
                   )
                ORDER BY a.applied_at DESC
                LIMIT 200
                """,
                (f"%{domain}%", f"%{domain}%"),
            ).fetchall()

        if len(rows) < _MIN_SAMPLES_PER_DOMAIN:
            return None

        # Separate rejected vs passed applications
        rejected = [r for r in rows if r["decision"] == "reject" or r["match_tier"] == "reject"]
        passed = [r for r in rows if r["decision"] != "reject" and r["match_tier"] != "reject"]

        if not rejected or not passed:
            return None

        # Compute interview rates
        rejected_interviews = sum(
            1 for r in rejected
            if r["final_outcome"] in ("interview", "offer", "hired")
            or r["status"] in ("Interview", "Offer", "Hired")
        )
        passed_interviews = sum(
            1 for r in passed
            if r["final_outcome"] in ("interview", "offer", "hired")
            or r["status"] in ("Interview", "Offer", "Hired")
        )

        rejected_rate = rejected_interviews / len(rejected) if rejected else 0.0
        passed_rate = passed_interviews / len(passed) if passed else 0.0

        # If rejected jobs have high interview rate, threshold is too strict
        if rejected_rate > 0.25 and rejected_rate > passed_rate * 0.5:
            current = _BASELINE_THRESHOLDS["gate3_competitiveness"]
            suggested = max(current - 5, 60)
            return ThresholdSuggestion(
                gate_name="gate3_competitiveness",
                current_value=current,
                suggested_value=suggested,
                domain=domain,
                evidence=(
                    f"{rejected_rate:.0%} of rejected {domain} jobs later got interviews "
                    f"({rejected_interviews}/{len(rejected)}). "
                    f"Passed jobs: {passed_rate:.0%} interview rate."
                ),
                confidence=min(rejected_rate * 2, 0.9),
                sample_size=len(rows),
            )

        # If passed jobs have very low interview rate, threshold is too lax
        if passed_rate < 0.05 and len(passed) >= 10:
            current = _BASELINE_THRESHOLDS["gate3_competitiveness"]
            suggested = min(current + 3, 85)
            return ThresholdSuggestion(
                gate_name="gate3_competitiveness",
                current_value=current,
                suggested_value=suggested,
                domain=domain,
                evidence=(
                    f"Only {passed_rate:.0%} of passed {domain} jobs got interviews "
                    f"({passed_interviews}/{len(passed)}). Threshold may be too low."
                ),
                confidence=min(0.5 + (0.05 - passed_rate) * 5, 0.8),
                sample_size=len(rows),
            )

        return None

    def _analyze_gate2(self, domain: str) -> ThresholdSuggestion | None:
        """Analyze Gate 2 effectiveness (min skills matched)."""
        # Gate 2 analysis requires more granular data than currently stored
        # This is a placeholder for when skill-level gate data is available
        return None

    def get_all_suggestions(self) -> list[ThresholdSuggestion]:
        """Analyze all domains with sufficient data and return suggestions."""
        domains = self._discover_domains()
        all_suggestions: list[ThresholdSuggestion] = []
        for domain in domains:
            suggestions = self.suggest_thresholds(domain)
            all_suggestions.extend(suggestions)
        return all_suggestions

    def _discover_domains(self) -> list[str]:
        """Discover job domains from recent applications."""
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT title FROM applications
                WHERE applied_at > datetime('now', '-90 days')
                  AND title IS NOT NULL
                LIMIT 100
                """
            ).fetchall()

        # Simple domain extraction from titles
        domains = set()
        for (title,) in rows:
            if not title:
                continue
            t = title.lower()
            if any(kw in t for kw in ("machine learning", "ml engineer", "ai engineer")):
                domains.add("ml_engineer")
            elif any(kw in t for kw in ("backend", "back end", "server-side")):
                domains.add("backend")
            elif any(kw in t for kw in ("frontend", "front end", "ui engineer")):
                domains.add("frontend")
            elif any(kw in t for kw in ("devops", "sre", "platform engineer")):
                domains.add("devops")
            elif any(kw in t for kw in ("data engineer", "etl", "pipeline")):
                domains.add("data_engineer")
            elif any(kw in t for kw in ("full stack", "fullstack")):
                domains.add("fullstack")
            elif any(kw in t for kw in ("python", "software engineer")):
                domains.add("software_engineer")

        return sorted(domains)

    def format_report(self, suggestions: list[ThresholdSuggestion]) -> str:
        """Format suggestions into a human-readable report."""
        if not suggestions:
            return "No threshold adjustments recommended at this time."

        lines = ["## Gate Threshold Adjustments", ""]
        for s in suggestions:
            lines.append(f"### {s.gate_name} ({s.domain})")
            lines.append(f"- Current: {s.current_value}")
            lines.append(f"- Suggested: {s.suggested_value}")
            lines.append(f"- Confidence: {s.confidence:.0%}")
            lines.append(f"- Evidence: {s.evidence}")
            lines.append("")

        return "\n".join(lines)
