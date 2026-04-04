"""Unified fact-checker — claim extraction, verification, accuracy scoring.

Used by both orchestration patterns (via fact_check_node) and blog generator.
Replaces blog_generator.py's inline fact_check().
"""

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional
from openai import OpenAI
from shared.logging_config import get_logger

logger = get_logger(__name__)

CACHE_DB_PATH = Path(__file__).parent.parent / "data" / "verified_facts.db"

# Claim types that need verification
VERIFIABLE_TYPES = {"benchmark", "date", "attribution", "comparison", "technical"}
SKIP_TYPES = {"opinion", "definition"}

# Claim type → verification source routing
CLAIM_ROUTING = {
    "benchmark": "web",
    "comparison": "web",
    "attribution": "semantic_scholar",
    "date": "semantic_scholar",
    "technical": "abstract_then_web",
}


def route_claim_to_verifier(claim: dict) -> str:
    """Decide which verification source to use for a claim type.

    Returns: 'semantic_scholar', 'web', or 'abstract_then_web'
    """
    claim_type = claim.get("type", "technical")
    return CLAIM_ROUTING.get(claim_type, "abstract_then_web")


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


def web_verify_claim(claim: str) -> dict:
    """Verify a claim using web search (DuckDuckGo).

    Returns: {"source": "url or description", "supports": True/False, "snippet": "relevant text"}
    """
    try:
        from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.text(claim, max_results=3))

        if not results:
            return {"source": None, "supports": False, "snippet": "No web results found"}

        # Combine top snippets for context
        snippets = "\n".join(r.get("body", "")[:200] for r in results[:3])
        source_urls = [r.get("href", "") for r in results[:3]]

        return {
            "source": source_urls[0] if source_urls else None,
            "supports": True,  # We have results — LLM will judge relevance
            "snippet": snippets[:500],
            "all_sources": source_urls,
        }
    except ImportError:
        logger.warning("duckduckgo-search not installed. pip install duckduckgo-search")
        return {"source": None, "supports": False, "snippet": "Web search unavailable"}
    except Exception as e:
        logger.warning("Web search failed for claim: %s — %s", claim[:50], e)
        return {"source": None, "supports": False, "snippet": f"Search error: {e}"}


def _get_cache_conn():
    conn = sqlite3.connect(str(CACHE_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""CREATE TABLE IF NOT EXISTS verified_facts (
        claim_hash TEXT PRIMARY KEY,
        claim TEXT NOT NULL,
        verdict TEXT NOT NULL,
        evidence TEXT,
        source_url TEXT,
        confidence REAL,
        verified_at TEXT DEFAULT (datetime('now'))
    )""")
    return conn


def _claim_hash(claim: str) -> str:
    """Simple hash for claim dedup."""
    return hashlib.md5(claim.lower().strip().encode()).hexdigest()


def cache_verified_fact(claim: str, verdict: str, evidence: str,
                        source_url: str = None, confidence: float = 0.9):
    """Store a verified fact in SQLite cache for future reuse."""
    conn = _get_cache_conn()
    conn.execute(
        "INSERT OR REPLACE INTO verified_facts (claim_hash, claim, verdict, evidence, source_url, confidence) VALUES (?,?,?,?,?,?)",
        (_claim_hash(claim), claim, verdict, evidence, source_url, confidence)
    )
    conn.commit()
    conn.close()


def get_cached_fact(claim: str) -> dict:
    """Check if a claim has been previously verified. Returns dict or None."""
    conn = _get_cache_conn()
    row = conn.execute(
        "SELECT * FROM verified_facts WHERE claim_hash=?", (_claim_hash(claim),)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def extract_claims(draft: str, topic: str) -> list[dict]:
    """Extract every verifiable claim from a draft article.

    Returns list of dicts with: claim, type, source_needed
    """
    from jobpulse.config import OPENAI_API_KEY
    client = OpenAI(api_key=OPENAI_API_KEY, timeout=30.0)

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
            model="gpt-5o-mini",
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
                  paper_abstract: str = None, web_search: bool = True,
                  arxiv_id: str = None) -> list[dict]:
    """Verify each claim using multi-source routing.

    Routes claims to appropriate verifiers based on claim type:
    - attribution/date → Semantic Scholar
    - benchmark/comparison → Quality web search + LLM judge
    - technical → Abstract check + web fallback

    Args:
        claims: list of claim dicts from extract_claims()
        sources: research notes from Researcher agent
        paper_abstract: optional paper abstract
        web_search: enable live web search verification
        arxiv_id: paper's arXiv ID for Semantic Scholar lookup
    """
    from jobpulse.config import OPENAI_API_KEY
    from shared.external_verifiers import (
        semantic_scholar_lookup, verify_claim_with_s2, quality_web_verify,
    )
    client = OpenAI(api_key=OPENAI_API_KEY, timeout=30.0)

    verifiable = [c for c in claims if c.get("source_needed", True) and c.get("type", "") not in SKIP_TYPES]
    if not verifiable:
        return []

    # Check cache first
    cached_results = []
    uncached_claims = []
    for c in verifiable:
        cached = get_cached_fact(c["claim"])
        if cached:
            cached_results.append({
                "claim": c["claim"],
                "verdict": cached["verdict"],
                "evidence": f"[CACHED] {cached['evidence']}",
                "confidence": max(0.0, min(1.0, cached["confidence"])),
                "severity": "low" if cached["verdict"] == "VERIFIED" else "medium",
                "source": cached.get("source_url", "abstract") or "abstract",
                "fix_suggestion": None if cached["verdict"] == "VERIFIED" else "Review against latest sources",
            })
            logger.info("Cache hit for: %s → %s", c["claim"][:50], cached["verdict"])
        else:
            uncached_claims.append(c)

    if not uncached_claims:
        return cached_results

    # Fetch Semantic Scholar data once per paper (reused across S2-routed claims)
    s2_data = None
    if arxiv_id and any(route_claim_to_verifier(c) == "semantic_scholar" for c in uncached_claims):
        s2_data = semantic_scholar_lookup(arxiv_id)

    # Route each claim: S2-routable claims go directly, rest go to LLM
    s2_results = []
    llm_claims = []

    for c in uncached_claims:
        route = route_claim_to_verifier(c)
        if route == "semantic_scholar" and s2_data:
            result = verify_claim_with_s2(c, s2_data)
            s2_results.append(result)
        else:
            llm_claims.append(c)

    # Build source context for LLM verification
    source_text = "\n\n".join(sources[:5]) if sources else ""
    if paper_abstract:
        source_text = f"PAPER ABSTRACT:\n{paper_abstract}\n\n{source_text}"

    # Enrich with quality web search for web-routed claims
    if web_search and llm_claims:
        web_context = []
        for c in llm_claims[:5]:
            route = route_claim_to_verifier(c)
            if route in ("web", "abstract_then_web"):
                web_result = quality_web_verify(c["claim"])
                if web_result.get("snippets"):
                    web_context.append(
                        f"Web search for \"{c['claim'][:80]}\" "
                        f"(source quality: {web_result['best_source_quality']:.1f}):\n"
                        f"{web_result['snippets']}"
                    )
        if web_context:
            source_text += "\n\nWEB SEARCH RESULTS:\n" + "\n\n".join(web_context)

    source_text = source_text[:6000]

    # LLM verification for remaining claims
    llm_results = []
    if llm_claims:
        claims_text = "\n".join(f"{i+1}. {c['claim']} [type: {c.get('type', 'unknown')}]"
                               for i, c in enumerate(llm_claims))

        prompt = f"""Verify each claim against the provided sources. Be STRICT and HONEST.

If a claim can only be verified against the paper's own abstract (not an independent source),
mark the source as "abstract". If verified by web search results from credible sources,
mark the source as "web".

SOURCES (ground truth):
{source_text}

CLAIMS TO VERIFY:
{claims_text}

For each claim, return:
- verdict: "VERIFIED" | "UNVERIFIED" | "INACCURATE" | "EXAGGERATED"
- evidence: what the source actually says (quote if possible)
- confidence: 0.0-1.0
- severity: "high" | "medium" | "low"
- source: "abstract" | "web" (which source confirmed/denied this)
- fix_suggestion: null if verified, otherwise how to fix

Return JSON: {{"verifications": [...]}}"""

        try:
            response = client.chat.completions.create(
                model="gpt-5o-mini",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=1500,
                temperature=0,
                response_format={"type": "json_object"},
            )
            result = json.loads(response.choices[0].message.content)
            llm_results = result.get("verifications", [])

            # Post-process: normalize verdicts, clamp confidence
            for v in llm_results:
                v["verdict"] = v.get("verdict", "UNVERIFIED").upper()
                v["confidence"] = max(0.0, min(1.0, float(v.get("confidence", 0.5))))
                v["source"] = v.get("source", "abstract")

        except Exception as e:
            logger.error("LLM verification failed: %s", e)

    # Combine all results
    all_results = cached_results + s2_results + llm_results

    # Cache new results with source attribution
    for v in s2_results + llm_results:
        try:
            cache_verified_fact(
                claim=v.get("claim", ""),
                verdict=v.get("verdict", "UNVERIFIED"),
                evidence=v.get("evidence", ""),
                source_url=v.get("source", "abstract"),
                confidence=v.get("confidence", 0.5),
            )
        except Exception:
            pass

    verdicts = [v.get("verdict", "UNKNOWN") for v in all_results]
    sources_used = set(v.get("source", "unknown") for v in all_results)
    logger.info("Verification: %d VERIFIED, %d UNVERIFIED, %d INACCURATE, %d EXAGGERATED | Sources: %s",
                verdicts.count("VERIFIED"), verdicts.count("UNVERIFIED"),
                verdicts.count("INACCURATE"), verdicts.count("EXAGGERATED"),
                ", ".join(sources_used))

    return all_results


def compute_accuracy_score(verifications: list[dict], repo_adjustment: float = 0.0) -> float:
    """Compute honest accuracy score from verification results.

    Key difference from v1: abstract-only verification scores 0.5, not 1.0.
    External source verification scores full 1.0.

    Scoring per claim:
    - VERIFIED (external source): +1.0
    - VERIFIED (abstract only):   +0.5
    - UNVERIFIED:                 -1.0
    - EXAGGERATED:                -1.5
    - INACCURATE:                 -2.0
    - SKIPPED:                    not counted

    repo_adjustment: added directly to 0-10 scale (-0.5 for missing, -0.3 for unhealthy)
    """
    scorable = [v for v in verifications if v.get("verdict", "").upper() != "SKIPPED"]

    if not scorable:
        return max(0.0, min(10.0, 10.0 + repo_adjustment))

    max_points = len(scorable) * 1.0
    total_points = 0.0

    for v in scorable:
        verdict = v.get("verdict", "UNVERIFIED").upper()
        source = v.get("source", "abstract")
        is_external = source in ("semantic_scholar", "web", "papers_with_code")

        if verdict == "VERIFIED":
            total_points += 1.0 if is_external else 0.5
        elif verdict == "EXAGGERATED":
            total_points -= 1.5
        elif verdict == "INACCURATE":
            total_points -= 2.0
        elif verdict == "UNVERIFIED":
            total_points -= 1.0

    score = 10.0 * (total_points / max_points) if max_points > 0 else 10.0
    score += repo_adjustment
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


def generate_fact_check_explanation(score: float, verifications: list[dict],
                                     repo_health: dict | None = None) -> str:
    """Generate human-readable explanation for a fact-check score.

    Format: "{score}/10 — {verified_count}/{total} claims verified{external_note},
    {issues}, {repo_summary}"
    """
    scorable = [v for v in verifications if v.get("verdict", "").upper() != "SKIPPED"]
    total = len(scorable)
    verified = sum(1 for v in scorable if v.get("verdict", "").upper() == "VERIFIED")

    has_external = any(
        v.get("source", "abstract") in ("semantic_scholar", "web", "papers_with_code")
        for v in scorable if v.get("verdict", "").upper() == "VERIFIED"
    )
    external_note = " externally" if has_external else " (abstract only)" if verified > 0 else ""

    parts = [f"{score:.1f}/10"]

    if total > 0:
        parts.append(f"{verified}/{total} claims verified{external_note}")

    # List issues (non-VERIFIED claims)
    issues = []
    for v in scorable:
        verdict = v.get("verdict", "").upper()
        if verdict in ("EXAGGERATED", "INACCURATE", "UNVERIFIED"):
            claim_short = v.get("claim", "")[:60]
            evidence_short = v.get("evidence", "")[:80]
            if verdict == "EXAGGERATED":
                issues.append(f"exaggerated: \"{claim_short}\" ({evidence_short})")
            elif verdict == "INACCURATE":
                issues.append(f"inaccurate: \"{claim_short}\" ({evidence_short})")
            elif verdict == "UNVERIFIED":
                issues.append(f"unverified: \"{claim_short}\"")

    if issues:
        parts.append(", ".join(issues[:2]))  # Max 2 issues shown

    # Repo summary
    if repo_health:
        repo_status = repo_health.get("status", "REPO_NA")
        if repo_status != "REPO_NA":
            parts.append(repo_health.get("summary", ""))

    return " — ".join(p for p in parts if p)
