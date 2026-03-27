"""Unified fact-checker — claim extraction, verification, accuracy scoring.

Used by both orchestration patterns (via fact_check_node) and blog generator.
Replaces blog_generator.py's inline fact_check().
"""

import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from openai import OpenAI
from shared.logging_config import get_logger

logger = get_logger(__name__)

# Claim types that need verification
VERIFIABLE_TYPES = {"benchmark", "date", "attribution", "comparison", "technical"}
SKIP_TYPES = {"opinion", "definition"}


@dataclass
class Claim:
    text: str
    claim_type: str  # benchmark, date, attribution, comparison, technical, opinion, definition
    source_needed: bool


@dataclass
class ClaimVerification:
    claim: str
    verdict: str  # VERIFIED, UNVERIFIED, INACCURATE, EXAGGERATED
    evidence: str
    confidence: float  # 0.0-1.0
    severity: str  # high, medium, low
    fix_suggestion: Optional[str] = None


def extract_claims(draft: str, topic: str) -> list[dict]:
    """Extract every verifiable claim from a draft article.

    Returns list of dicts with: claim, type, source_needed
    """
    from jobpulse.config import OPENAI_API_KEY
    client = OpenAI(api_key=OPENAI_API_KEY)

    prompt = f"""Extract ALL verifiable factual claims from this article about "{topic}".

For each claim, classify its type:
- "benchmark": specific numbers, percentages, metrics (e.g., "achieves 86.4% on MMLU")
- "date": specific dates or timeframes (e.g., "released in 2023")
- "attribution": who did what (e.g., "proposed by Smith et al.")
- "comparison": relative claims (e.g., "3x faster than BERT")
- "technical": technical facts (e.g., "uses 12 attention heads")
- "opinion": subjective statements (e.g., "promising approach") — mark source_needed=false
- "definition": standard definitions (e.g., "RAG stands for...") — mark source_needed=false

ARTICLE:
{draft[:4000]}

Return JSON: {{"claims": [{{"claim": "exact text", "type": "benchmark|date|...", "source_needed": true|false}}]}}"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1000,
            temperature=0,
            response_format={"type": "json_object"},
        )
        result = json.loads(response.choices[0].message.content)
        claims = result.get("claims", [])
        logger.info("Extracted %d claims (%d verifiable)", len(claims),
                    sum(1 for c in claims if c.get("source_needed", True)))
        return claims
    except Exception as e:
        logger.error("Claim extraction failed: %s", e)
        return []


def verify_claims(claims: list[dict], sources: list[str],
                  paper_abstract: str = None) -> list[dict]:
    """Verify each claim against available sources.

    Args:
        claims: list of claim dicts from extract_claims()
        sources: research notes from Researcher agent
        paper_abstract: optional paper abstract for arXiv blog generation

    Returns list of verification dicts with: claim, verdict, evidence, confidence, severity, fix_suggestion
    """
    from jobpulse.config import OPENAI_API_KEY
    client = OpenAI(api_key=OPENAI_API_KEY)

    verifiable = [c for c in claims if c.get("source_needed", True)]
    if not verifiable:
        return []

    # Combine all sources
    source_text = "\n\n".join(sources[:5]) if sources else ""
    if paper_abstract:
        source_text = f"PAPER ABSTRACT:\n{paper_abstract}\n\n{source_text}"
    source_text = source_text[:5000]  # Cap context

    # Batch verify all claims in one call
    claims_text = "\n".join(f"{i+1}. {c['claim']} [type: {c.get('type', 'unknown')}]"
                           for i, c in enumerate(verifiable))

    prompt = f"""Verify each claim against the provided sources. Be STRICT.

SOURCES (ground truth):
{source_text}

CLAIMS TO VERIFY:
{claims_text}

For each claim, return:
- verdict: "VERIFIED" (supported by sources), "UNVERIFIED" (no evidence found), "INACCURATE" (contradicted by sources), "EXAGGERATED" (overstated)
- evidence: what the source actually says (quote if possible)
- confidence: 0.0-1.0 how sure you are
- severity: "high" (factual error), "medium" (missing nuance), "low" (minor imprecision)
- fix_suggestion: null if verified, otherwise how to fix

Return JSON: {{"verifications": [{{"claim": "...", "verdict": "...", "evidence": "...", "confidence": 0.9, "severity": "high|medium|low", "fix_suggestion": null|"..."}}]}}"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1500,
            temperature=0,
            response_format={"type": "json_object"},
        )
        result = json.loads(response.choices[0].message.content)
        verifications = result.get("verifications", [])

        # Log summary
        verdicts = [v.get("verdict", "UNKNOWN") for v in verifications]
        logger.info("Verification results: %d VERIFIED, %d UNVERIFIED, %d INACCURATE, %d EXAGGERATED",
                    verdicts.count("VERIFIED"), verdicts.count("UNVERIFIED"),
                    verdicts.count("INACCURATE"), verdicts.count("EXAGGERATED"))
        return verifications
    except Exception as e:
        logger.error("Claim verification failed: %s", e)
        return []


def compute_accuracy_score(verifications: list[dict]) -> float:
    """Compute deterministic accuracy score from verification results.

    Scoring:
    - VERIFIED: +1.0
    - UNVERIFIED (low severity): -0.5
    - UNVERIFIED (medium/high severity): -1.5
    - INACCURATE: -2.0
    - EXAGGERATED: -1.0

    Score = 10.0 * (total_points / max_possible_points)
    """
    if not verifications:
        return 10.0  # No claims to verify = perfect score

    max_points = len(verifications) * 1.0
    total_points = 0.0

    for v in verifications:
        verdict = v.get("verdict", "UNVERIFIED").upper()
        severity = v.get("severity", "medium").lower()

        if verdict == "VERIFIED":
            total_points += 1.0
        elif verdict == "EXAGGERATED":
            total_points -= 1.0
        elif verdict == "INACCURATE":
            total_points -= 2.0
        elif verdict == "UNVERIFIED":
            if severity == "low":
                total_points -= 0.5
            else:
                total_points -= 1.5

    # Normalize to 0-10 scale
    score = 10.0 * (total_points / max_points) if max_points > 0 else 10.0
    return max(0.0, min(10.0, score))


def generate_revision_notes(verifications: list[dict]) -> str:
    """Generate specific fix instructions for the writer from verification results.

    Only includes claims that need fixing (not VERIFIED ones).
    """
    issues = [v for v in verifications if v.get("verdict", "").upper() != "VERIFIED"]
    if not issues:
        return ""

    lines = ["FACT-CHECK REVISION INSTRUCTIONS:",
             "The following claims need correction. Fix ONLY these — preserve all other content.", ""]

    for i, v in enumerate(issues, 1):
        verdict = v.get("verdict", "UNKNOWN").upper()
        claim = v.get("claim", "unknown claim")
        evidence = v.get("evidence", "no evidence provided")
        fix = v.get("fix_suggestion", "Review and correct")
        severity = v.get("severity", "medium").upper()

        lines.append(f"{i}. [{severity}] {verdict}: \"{claim}\"")
        lines.append(f"   → Evidence: {evidence}")
        lines.append(f"   → Fix: {fix}")
        lines.append("")

    return "\n".join(lines)
