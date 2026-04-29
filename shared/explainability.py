"""DecisionExplainer — human-readable explanations for every automated decision.

Integrates into Telegram review messages, Notion pages, and ProcessTrail logs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from shared.logging_config import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class DecisionExplanation:
    """Structured explanation of an automated decision."""

    decision_type: str
    decision: str
    headline: str
    details: list[str]
    confidence: float = 1.0
    suggested_action: str | None = None

    def to_markdown(self) -> str:
        """Render as a concise markdown block."""
        lines = [
            f"**{self.headline}**",
            "",
            "*Why:*",
        ]
        for d in self.details:
            lines.append(f"- {d}")
        if self.suggested_action:
            lines.append("")
            lines.append(f"*Suggestion:* {self.suggested_action}")
        return "\n".join(lines)

    def to_telegram(self) -> str:
        """Render as a Telegram-friendly message (≤400 chars)."""
        text = f"{self.headline}\n\n"
        text += "\n".join(f"• {d}" for d in self.details[:3])
        if self.suggested_action:
            text += f"\n\n💡 {self.suggested_action}"
        return text[:400]


class DecisionExplainer:
    """Generate human-readable explanations for gate decisions, screening answers,
    CV scrutiny, and project selection.
    """

    # ═══════════════════════════════════════════════════════════════════
    # Gate Decisions (Gates 1-4)
    # ═══════════════════════════════════════════════════════════════════

    @staticmethod
    def explain_gate_decision(
        gate_name: str,
        passed: bool,
        listing: dict[str, Any] | None = None,
        gate_result: Any | None = None,
    ) -> DecisionExplanation:
        """Explain why a job passed or failed a gate.

        Args:
            gate_name: e.g. "Gate 1 (JD Quality)", "Gate 3 (Skill Match)"
            passed: Whether the gate approved the job.
            listing: Job listing dict with company, title, skills, etc.
            gate_result: The raw result object from the gate check.
        """
        listing = listing or {}
        company = listing.get("company", "Unknown")
        title = listing.get("title", "Unknown role")

        if passed:
            return DecisionExplanation(
                decision_type="gate",
                decision="passed",
                headline=f"✅ {gate_name}: Approved '{title}' at {company}",
                details=[
                    f"Job met all criteria for {gate_name.lower()}.",
                    "Proceeding to next stage.",
                ],
                confidence=1.0,
            )

        # Extract reason from gate_result if available
        reason = _extract_reason(gate_result)
        details = [reason] if reason else [f"Job did not meet criteria for {gate_name.lower()}."]

        # Add contextual suggestion
        suggestion = None
        if gate_name.lower().startswith("gate 1") and listing.get("skill_count", 0) < 5:
            suggestion = "Consider lowering skill threshold or broadening search."
        elif gate_name.lower().startswith("gate 4a"):
            suggestion = "Check company reliability data before applying."

        return DecisionExplanation(
            decision_type="gate",
            decision="rejected",
            headline=f"❌ {gate_name}: Blocked '{title}' at {company}",
            details=details,
            confidence=0.9,
            suggested_action=suggestion,
        )

    @staticmethod
    def explain_cv_scrutiny(
        score: float,
        threshold: float,
        verdict: str,
        strengths: list[str] | None = None,
        weaknesses: list[str] | None = None,
        breakdown: dict[str, float] | None = None,
    ) -> DecisionExplanation:
        """Explain why a CV passed or failed LLM scrutiny (Gate 4B).

        Args:
            score: LLM scrutiny score (0-10).
            threshold: Calibrated threshold used.
            verdict: "shortlist", "maybe", or "reject".
            strengths: List of strengths identified.
            weaknesses: List of weaknesses identified.
            breakdown: Dict of dimension scores (relevance, evidence, etc.).
        """
        strengths = strengths or []
        weaknesses = weaknesses or []
        breakdown = breakdown or {}

        if verdict == "shortlist" and score >= threshold:
            return DecisionExplanation(
                decision_type="cv_scrutiny",
                decision="passed",
                headline=f"✅ CV Scrutiny: Passed ({score:.1f}/{threshold:.1f})",
                details=[
                    f"Score {score:.1f} meets threshold {threshold:.1f}.",
                    *(f"Strength: {s}" for s in strengths[:3]),
                ],
                confidence=min(score / 10, 1.0),
            )

        details = [
            f"Score {score:.1f} below threshold {threshold:.1f}.",
        ]
        if breakdown:
            lowest = min(breakdown, key=breakdown.get)  # type: ignore[arg-type]
            details.append(f"Weakest dimension: {lowest} ({breakdown[lowest]:.1f})")
        if weaknesses:
            details.append(f"Key issue: {weaknesses[0]}")

        return DecisionExplanation(
            decision_type="cv_scrutiny",
            decision="rejected",
            headline=f"❌ CV Scrutiny: Needs Review ({score:.1f}/{threshold:.1f})",
            details=details,
            confidence=max(0.5, score / threshold) if threshold > 0 else 0.5,
            suggested_action="Consider tailoring CV to JD keywords or adding metrics.",
        )

    # ═══════════════════════════════════════════════════════════════════
    # Screening Answers
    # ═══════════════════════════════════════════════════════════════════

    @staticmethod
    def explain_screening_answer(
        question: str,
        answer: str,
        source: str,
        confidence: float | None = None,
        job_context: dict[str, Any] | None = None,
    ) -> DecisionExplanation:
        """Explain why a particular screening answer was chosen.

        Args:
            question: The screening question.
            answer: The generated answer.
            source: How the answer was resolved (e.g. "pattern_match",
                "semantic_cache", "v2_pipeline", "llm_fallback").
            confidence: Optional confidence score.
            job_context: Job listing context (salary, remote, seniority).
        """
        source_labels = {
            "pattern_match": "Matched a known answer pattern",
            "semantic_cache": "Retrieved from semantic cache (similar question seen before)",
            "agent_rules": "Applied a learned agent rule",
            "v2_pipeline": "Generated by V2 screening pipeline",
            "llm_fallback": "Generated by LLM fallback",
            "manual": "User-provided override",
        }
        source_label = source_labels.get(source, f"Resolved via {source}")

        details = [source_label]
        if job_context:
            if job_context.get("salary"):
                details.append(f"Context: salary band {job_context['salary']}")
            if job_context.get("remote") is not None:
                details.append(f"Context: {'remote' if job_context['remote'] else 'onsite'}")

        conf = confidence or (0.95 if source in ("pattern_match", "manual") else 0.75)

        return DecisionExplanation(
            decision_type="screening",
            decision="answered",
            headline=f'📝 Screening: "{question[:50]}..."',
            details=details,
            confidence=conf,
        )

    # ═══════════════════════════════════════════════════════════════════
    # Project Selection
    # ═══════════════════════════════════════════════════════════════════

    @staticmethod
    def explain_project_selection(
        selected_projects: list[str],
        archetype: str,
        jd_skills: list[str],
        outcome_data: dict[str, Any] | None = None,
    ) -> DecisionExplanation:
        """Explain why these projects were selected for the CV.

        Args:
            selected_projects: List of project names selected.
            archetype: Job archetype (e.g. "backend", "ml_engineer").
            jd_skills: Skills mentioned in the JD.
            outcome_data: Optional outcome tracking data.
        """
        details = [
            f"Archetype: {archetype}",
            f"Matched {len(selected_projects)} projects to JD skills: {', '.join(jd_skills[:5])}",
        ]
        if outcome_data:
            best = outcome_data.get("best_project")
            if best:
                rate = outcome_data.get("interview_rate", 0)
                details.append(
                    f"'{best}' has {rate:.0%} interview rate for this archetype"
                )

        return DecisionExplanation(
            decision_type="project_selection",
            decision="selected",
            headline=f"📂 Selected {len(selected_projects)} projects for '{archetype}'",
            details=details,
            confidence=0.85,
        )

    # ═══════════════════════════════════════════════════════════════════
    # Company Reliability
    # ═══════════════════════════════════════════════════════════════════

    @staticmethod
    def explain_company_reliability(
        company: str,
        total_applied: int,
        interview_rate: float,
        auto_skipped: bool,
    ) -> DecisionExplanation:
        """Explain why a company was auto-skipped or flagged.

        Args:
            company: Company name.
            total_applied: Total applications sent to this company.
            interview_rate: Historical interview rate (0.0-1.0).
            auto_skipped: Whether the company was auto-skipped.
        """
        if auto_skipped and interview_rate == 0.0 and total_applied >= 10:
            return DecisionExplanation(
                decision_type="company_reliability",
                decision="blocked",
                headline=f"🏢 {company}: Auto-skipped (0% interview rate)",
                details=[
                    f"Applied {total_applied} times with zero interviews.",
                    "Company may have ghost listings or high competition.",
                ],
                confidence=0.95,
                suggested_action="Consider applying only to roles with personal referrals at this company.",
            )

        if interview_rate < 0.1 and total_applied >= 5:
            return DecisionExplanation(
                decision_type="company_reliability",
                decision="warned",
                headline=f"⚠️ {company}: Low interview rate ({interview_rate:.0%})",
                details=[
                    f"Only {interview_rate:.0%} interview rate from {total_applied} applications.",
                ],
                confidence=0.8,
                suggested_action="Tailor CV more specifically to this company's tech stack.",
            )

        return DecisionExplanation(
            decision_type="company_reliability",
            decision="ok",
            headline=f"🏢 {company}: Reliable ({interview_rate:.0%} interview rate)",
            details=[f"{total_applied} applications, {interview_rate:.0%} interview rate."],
            confidence=0.9,
        )


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════


def _extract_reason(gate_result: Any | None) -> str | None:
    """Extract a human-readable reason from a gate result object."""
    if gate_result is None:
        return None
    # Handle pydantic models and dataclasses
    if hasattr(gate_result, "reason"):
        return str(gate_result.reason)
    if hasattr(gate_result, "note"):
        return str(gate_result.note)
    if hasattr(gate_result, "weaknesses") and gate_result.weaknesses:
        return f"Issues: {', '.join(gate_result.weaknesses[:3])}"
    if isinstance(gate_result, dict):
        return gate_result.get("reason") or gate_result.get("note")
    return None
