"""NLP rejection email parser — extracts structured insights from auto-rejections.

Lightweight rule-based + keyword classifier. No LLM call — keeps cost at zero.
Processes Gmail auto-replies and extracts skill gaps, salary mismatch,
experience gaps, visa issues, and generic rejections.

Usage:
    parser = RejectionEmailParser()
    insight = parser.parse(email_body, subject="...")
    # insight.blocker = "skill_gap"
    # insight.skill_gaps = ["Kubernetes", "GCP"]
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from shared.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class RejectionInsight:
    """Structured insight from a rejection email."""

    blocker: str = "unclear"
    confidence: float = 0.0
    skill_gaps: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    escalate: bool = False
    raw_category: str = ""


# ── Keyword patterns per blocker category ──────────────────────────────────

_SKILL_GAP_PATTERNS = [
    r"\b(more experience with|stronger background in|deeper expertise in)\s+(.+?)(?:\.|,|;)",
    r"\b(candidates? with|looking for someone with)\s+(.+?)(?:\.|,|;)",
    r"\b(does not match|not aligned with)\s+(?:our |the )?(?:requirements?|needs?|skill set)",
    r"\b(skill set|background|experience)\s+(?:does not|doesn't)\s+(?:match|align)",
    r"\b(other candidates?|another candidate)\s+(?:have|had|with)\s+(?:more|stronger|deeper)\s+(.+?)(?:\.|,|;)",
    r"\b(requires?|needs?)\s+(?:more |stronger )?(?:experience |knowledge |expertise )?(?:in |with )?(.+?)(?:\.|,|;)",
]

_EXPERIENCE_MISMATCH_PATTERNS = [
    r"\b(more experience|senior|seasoned|extensive background)\b",
    r"\b(\d+)\+?\s*years?\s+(?:of )?experience",
    r"\b(years of experience|level of experience)\b",
    r"\b(seniority|career stage|professional level)\b",
    r"\b(too junior|not senior enough|insufficient experience)\b",
]

_SALARY_MISMATCH_PATTERNS = [
    r"\b(salary|compensation|pay|remuneration|package)\s+(?:expectations?|requirements?)",
    r"\b(budget|range|band)\s+(?:does not|doesn't)\s+(?:allow|accommodate|permit)",
    r"\b(exceeds?|outside|beyond)\s+(?:our |the )?(?:budget|range)",
    r"\b(overqualified|underqualified)\b",
]

_VISA_PATTERNS = [
    r"\b(visa|sponsorship|work authori[sz]ation|right to work)\b",
    r"\b(require|unable to|cannot|can't)\s+(?:provide |offer )?(?:visa|sponsorship)\b",
    r"\b(citizen|permanent resident|local candidate)\b",
]

_LOCATION_PATTERNS = [
    r"\b(location|relocate|remote work|on-site|on site|hybrid|distance|commute)\b",
    r"\b(local candidates?|in-office|in office)\s+(?:requirement|preference|policy|only)\b",
    r"\b(unable|not able|cannot)\s+(?:to )?\s*(?:relocate|move|commute)\b",
    r"\b(require|need)\s+(?:someone|candidate)\s+(?:who can|to)\s+(?:work|be)\s+(?:on.?site|in.?office|in person)\b",
    r"\b(only|exclusively)\s+(?:considering|hiring)\s+(?:local|on.?site)\b",
    r"\b(must be|needs to be)\s+(?:based|located)\s+(?:in|near|within)\b",
]

_COMPETITION_PATTERNS = [
    r"\b(many|large number of|high volume of)\s+(?:qualified |strong )?candidates?\b",
    r"\b(strong|exceptional|highly qualified|competitive)\s+(?:pool|field|applicant)\b",
    r"\b(difficult decision|tough decision|many strong)\b",
]

_GENERIC_PATTERNS = [
    r"\b(chosen|selected|decided|proceeding with|moving forward with)\s+(?:another|a different|an alternative|other)\s+(?:candidate|applicant)\b",
    r"\b(not selected|unsuccessful|regret to inform|unfortunately|we are sorry)\b",
    r"\b(position has been filled|role has been filled|closed the position|position is filled)\b",
    r"\b(decided to proceed|will not be moving forward|not moving forward)\b",
]

_GHOST_PATTERNS = [
    r"\b(no longer|position (?:is )?closed|requisition (?:is )?closed)\b",
    r"\b(on hold|paused|frozen|suspended)\b",
]

# Skill extraction from context
_TECH_KEYWORDS = re.compile(
    r"\b(Python|JavaScript|TypeScript|Java|Go|Rust|C\+\+|C#|Ruby|Scala|Kotlin|Swift|"
    r"React|Vue|Angular|Svelte|Next\.js|Node\.js|Django|Flask|FastAPI|Spring|Rails|"
    r"PostgreSQL|MySQL|MongoDB|Redis|Elasticsearch|DynamoDB|Cassandra|"
    r"AWS|GCP|Azure|Docker|Kubernetes|Terraform|Ansible|CI/CD|Jenkins|GitHub Actions|"
    r"TensorFlow|PyTorch|scikit-learn|Keras|Pandas|NumPy|Spark|Hadoop|Airflow|"
    r"Kafka|RabbitMQ|gRPC|GraphQL|REST|SOAP|"
    r"Linux|Bash|PowerShell|Nginx|Apache|"
    r"Machine Learning|Deep Learning|NLP|Computer Vision|Data Science|"
    r"Agile|Scrum|Kanban|TDD|BDD|"
    r"Microservices|Serverless|Event-Driven|Domain-Driven Design)\b",
    re.IGNORECASE,
)


class RejectionEmailParser:
    """Parse auto-rejection emails into structured insights."""

    def parse(self, email_body: str, subject: str = "") -> RejectionInsight:
        """Parse a rejection email and extract structured insights.

        Args:
            email_body: The raw email body text.
            subject: Optional email subject line.

        Returns:
            RejectionInsight with blocker classification and recommendations.
        """
        text = f"{subject}\n{email_body}".lower()
        text_clean = self._clean_text(text)

        # Run all classifiers
        scores = {
            "skill_gap": self._score_patterns(text_clean, _SKILL_GAP_PATTERNS),
            "experience_mismatch": self._score_patterns(text_clean, _EXPERIENCE_MISMATCH_PATTERNS),
            "salary_mismatch": self._score_patterns(text_clean, _SALARY_MISMATCH_PATTERNS),
            "visa_issue": self._score_patterns(text_clean, _VISA_PATTERNS),
            "location_issue": self._score_patterns(text_clean, _LOCATION_PATTERNS),
            "competition": self._score_patterns(text_clean, _COMPETITION_PATTERNS),
            "generic_rejection": self._score_patterns(text_clean, _GENERIC_PATTERNS),
            "ghosted": self._score_patterns(text_clean, _GHOST_PATTERNS),
        }

        # Determine primary blocker
        best_blocker = max(scores, key=scores.get)
        best_score = scores[best_blocker]

        # Confidence is the margin over the second-best score
        sorted_scores = sorted(scores.values(), reverse=True)
        margin = sorted_scores[0] - sorted_scores[1] if len(sorted_scores) > 1 else sorted_scores[0]
        confidence = min(0.3 + margin * 0.15, 0.95)

        # Extract skill gaps if applicable
        skill_gaps: list[str] = []
        if best_blocker in ("skill_gap", "experience_mismatch"):
            skill_gaps = self._extract_skills(email_body)

        # Generate recommendations
        recommendations = self._generate_recommendations(
            best_blocker, skill_gaps, text_clean
        )

        # Escalate if systemic issue detected
        escalate = best_blocker in ("salary_mismatch", "visa_issue") or (
            best_blocker == "skill_gap" and len(skill_gaps) >= 3
        )

        return RejectionInsight(
            blocker=best_blocker if best_score > 0 else "unclear",
            confidence=round(confidence, 2),
            skill_gaps=skill_gaps,
            recommendations=recommendations,
            escalate=escalate,
            raw_category=best_blocker,
        )

    @staticmethod
    def _clean_text(text: str) -> str:
        """Normalize text for pattern matching."""
        # Remove URLs
        text = re.sub(r"https?://\S+", "", text)
        # Remove email addresses
        text = re.sub(r"\S+@\S+", "", text)
        # Normalize whitespace
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    @staticmethod
    def _score_patterns(text: str, patterns: list[str]) -> float:
        """Score text against a list of regex patterns."""
        score = 0.0
        for pattern in patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            score += len(matches) * 1.0
        return score

    @staticmethod
    def _extract_skills(text: str) -> list[str]:
        """Extract technology keywords from rejection context."""
        matches = _TECH_KEYWORDS.findall(text)
        # Deduplicate preserving order
        seen = set()
        result = []
        for m in matches:
            skill = m if isinstance(m, str) else m[0] if m else ""
            skill = skill.strip()
            if skill and skill.lower() not in seen:
                seen.add(skill.lower())
                result.append(skill)
        return result[:10]  # Cap at 10 skills

    @staticmethod
    def _generate_recommendations(blocker: str, skill_gaps: list[str], text: str) -> list[str]:
        """Generate actionable recommendations based on blocker type."""
        recs: list[str] = []

        if blocker == "skill_gap":
            if skill_gaps:
                recs.append(f"Add projects demonstrating: {', '.join(skill_gaps[:3])}")
            recs.append("Update CV to highlight relevant technical skills prominently")
            recs.append("Consider applying to roles with fewer hard requirements")

        elif blocker == "experience_mismatch":
            years = re.search(r"(\d+)\+?\s*years?", text)
            if years:
                recs.append(f"Target roles requiring ≤{years.group(1)} years experience")
            else:
                recs.append("Apply to mid-level rather than senior roles")
            recs.append("Quantify impact in current role to demonstrate seniority")

        elif blocker == "salary_mismatch":
            recs.append("Research market rate for this role/location")
            recs.append("Consider stating a range rather than fixed number")
            recs.append("Evaluate total compensation including equity/benefits")

        elif blocker == "visa_issue":
            recs.append("Prioritize companies known to sponsor visas")
            recs.append("Highlight right-to-work status early in application")

        elif blocker == "location_issue":
            recs.append("Clarify relocation/remote flexibility in cover letter")
            recs.append("Filter for remote-first or location-flexible roles")

        elif blocker == "competition":
            recs.append("Strengthen CV differentiation with unique project metrics")
            recs.append("Follow up 1 week after application")
            recs.append("Apply within first 3 days of posting")

        elif blocker == "generic_rejection":
            recs.append("Request feedback from recruiter if possible")
            recs.append("Review CV for ATS keyword optimization")

        elif blocker == "ghosted":
            recs.append("Follow up after 7 days, then 14 days")
            recs.append("Consider this a low-priority application")

        else:
            recs.append("No specific insight extracted — review application manually")

        return recs


# ── Singleton ──────────────────────────────────────────────────────────────
_parser_instance: RejectionEmailParser | None = None


def get_rejection_parser() -> RejectionEmailParser:
    """Return shared singleton parser."""
    global _parser_instance
    if _parser_instance is None:
        _parser_instance = RejectionEmailParser()
    return _parser_instance
