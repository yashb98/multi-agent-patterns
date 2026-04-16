"""PaperRanker — deterministic fast scoring + LLM-powered ranking + theme extraction."""

from __future__ import annotations

import json
import re

from jobpulse.papers.models import FactCheckResult, Paper, RankedPaper
from shared.logging_config import get_logger

logger = get_logger(__name__)

# Category weights for fast_score
_CATEGORY_WEIGHTS: dict[str, float] = {
    "cs.AI": 2.0,
    "cs.LG": 2.0,
    "cs.CL": 1.5,
    "stat.ML": 1.5,
    "cs.MA": 1.0,
}

# Lens-specific LLM scoring weights
_LENS_WEIGHTS: dict[str, dict[str, float]] = {
    "daily": {
        "novelty": 0.30,
        "significance": 0.25,
        "practical": 0.30,
        "breadth": 0.15,
    },
    "weekly": {
        "novelty": 0.25,
        "significance": 0.35,
        "practical": 0.25,
        "breadth": 0.15,
    },
}


def _get_openai_client():  # pragma: no cover
    """Return an OpenAI client, or None if the key is not configured."""
    try:
        from jobpulse.config import OPENAI_API_KEY

        if not OPENAI_API_KEY:
            return None
        from openai import OpenAI

        return OpenAI(api_key=OPENAI_API_KEY)
    except Exception:
        return None


def fast_score(paper: Paper) -> float:
    """Deterministic score for a paper. Maximum possible value is 10.0.

    Scoring breakdown:
    - Category bonus       : up to 2.0
    - Community buzz       : up to 2.0
    - HF upvotes           : up to 1.5
    - S2 citations         : up to 1.5
    - GitHub repo          : up to 1.0
    - Linked models/datasets: up to 1.0
    - Multi-source bonus   : up to 0.5
    - Recency              : 0.5
    """
    score = 0.0

    # Category bonus — best matching weight
    cat_bonus = max((_CATEGORY_WEIGHTS.get(c, 0.0) for c in paper.categories), default=0.0)
    score += cat_bonus

    # Community buzz (aggregated across sources)
    buzz = paper.community_buzz
    if buzz > 100:
        score += 2.0
    elif buzz > 50:
        score += 1.5
    elif buzz > 20:
        score += 1.0
    elif buzz > 5:
        score += 0.5

    # HF upvotes
    if paper.hf_upvotes is not None:
        if paper.hf_upvotes > 50:
            score += 1.5
        elif paper.hf_upvotes > 20:
            score += 1.0
        elif paper.hf_upvotes > 5:
            score += 0.5

    # S2 citations
    cites = paper.s2_citation_count
    if cites > 20:
        score += 1.5
    elif cites > 10:
        score += 1.0
    elif cites > 3:
        score += 0.5

    # GitHub repo
    if paper.github_url:
        score += 0.5
        if paper.github_stars > 50:
            score += 0.5

    # Linked models/datasets (0.5 each, capped at 1.0)
    model_ds_score = 0.0
    if paper.linked_models:
        model_ds_score += 0.5
    if paper.linked_datasets:
        model_ds_score += 0.5
    score += min(model_ds_score, 1.0)

    # Multi-source bonus
    n_sources = len(paper.sources)
    if n_sources >= 3:
        score += 0.5
    elif n_sources >= 2:
        score += 0.25

    # Recency bonus
    score += 0.5

    return min(score, 10.0)


def _extract_json_array(raw: str) -> list:
    """Strip markdown fences and parse a JSON array.  Returns [] on any error."""
    if not raw:
        return []
    # Strip markdown code fences
    cleaned = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`").strip()
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, list):
            return parsed
        return []
    except (json.JSONDecodeError, ValueError):
        return []


def llm_rank(
    papers: list[Paper],
    top_n: int = 5,
    lens: str = "daily",
) -> list[RankedPaper]:
    """Rank papers via LLM.

    Pre-filters to the top 30 by fast_score, then asks the LLM to rank them
    and return structured JSON.  Falls back to the top-N by fast_score on any
    failure (no API key, network error, bad JSON, etc.).
    """
    if not papers:
        return []

    # Pre-filter
    scored = sorted(papers, key=fast_score, reverse=True)
    candidates = scored[:30]

    # Build fallback early so every exit path can use it
    def _fallback() -> list[RankedPaper]:
        top = candidates[:top_n]
        return [
            RankedPaper(**{**p.model_dump(), "fast_score": fast_score(p)})
            for p in top
        ]

    client = _get_openai_client()
    if client is None:
        logger.warning("llm_rank: no OpenAI client — using fast_score fallback")
        return _fallback()

    weights = _LENS_WEIGHTS.get(lens, _LENS_WEIGHTS["daily"])
    weight_desc = ", ".join(f"{k} {v:.0%}" for k, v in weights.items())

    paper_list = "\n".join(
        f"{i + 1}. [{p.arxiv_id}] {p.title} | categories: {', '.join(p.categories)}"
        for i, p in enumerate(candidates)
    )

    prompt = (
        f"You are an AI research curator ranking papers for a {lens} digest.\n"
        f"Scoring weights: {weight_desc}.\n\n"
        f"Papers:\n{paper_list}\n\n"
        f"Return a JSON array of exactly {top_n} objects with this schema:\n"
        '[\n  {\n    "arxiv_id": "...",\n    "impact_score": <float 0-10>,\n'
        '    "impact_reason": "...",\n    "category_tag": "one of LLM|Agents|Vision|RL|Efficiency|Safety|Reasoning|Data",\n'
        '    "key_technique": "...",\n    "practical_takeaway": "..."\n  }\n]\n'
        "Return ONLY the JSON array, no other text."
    )

    try:
        response = client.chat.completions.create(
            model="gpt-5-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
        )
        raw = response.choices[0].message.content or ""
        rankings = _extract_json_array(raw)
        if not rankings:
            logger.warning("llm_rank: empty/invalid JSON from LLM — using fallback")
            return _fallback()

        # Build index for fast lookup
        paper_by_id = {p.arxiv_id: p for p in candidates}
        results: list[RankedPaper] = []
        for entry in rankings[:top_n]:
            arxiv_id = entry.get("arxiv_id", "")
            paper = paper_by_id.get(arxiv_id)
            if paper is None:
                continue
            data = paper.model_dump()
            data.update(
                fast_score=fast_score(paper),
                impact_score=float(entry.get("impact_score", 0.0)),
                impact_reason=entry.get("impact_reason", ""),
                category_tag=entry.get("category_tag", ""),
                key_technique=entry.get("key_technique", ""),
                practical_takeaway=entry.get("practical_takeaway", ""),
            )
            results.append(RankedPaper(**data))

        if not results:
            logger.warning("llm_rank: no matching arxiv_ids from LLM — using fallback")
            return _fallback()

        return results

    except Exception as exc:
        logger.warning("llm_rank: LLM call failed (%s) — using fallback", exc)
        return _fallback()


def extract_themes(papers: list[Paper]) -> list[str]:
    """Extract 3-5 high-level themes from a set of papers via LLM.

    Returns an empty list on any failure.
    """
    if not papers:
        return []

    client = _get_openai_client()
    if client is None:
        return []

    titles_and_cats = "\n".join(
        f"- {p.title} [{', '.join(p.categories)}]" for p in papers[:20]
    )
    prompt = (
        "Given these AI research paper titles and categories, extract 3-5 overarching themes.\n"
        f"{titles_and_cats}\n\n"
        'Return a JSON array of strings, e.g. ["Theme 1", "Theme 2"].\n'
        "Return ONLY the JSON array."
    )

    try:
        response = client.chat.completions.create(
            model="gpt-5-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
        )
        raw = response.choices[0].message.content or ""
        themes = _extract_json_array(raw)
        return [str(t) for t in themes if isinstance(t, str)]
    except Exception as exc:
        logger.warning("extract_themes: failed (%s) — returning []", exc)
        return []


# ---------------------------------------------------------------------------
# Summarise + verify helpers
# ---------------------------------------------------------------------------


def _summarize_paper(paper: Paper, client) -> str:  # type: ignore[no-untyped-def]
    """Return a one-paragraph summary of the paper.  Empty string on error."""
    prompt = (
        f"Summarize this research paper in 2-3 sentences for a technical audience.\n"
        f"Title: {paper.title}\nAbstract: {paper.abstract[:800]}"
    )
    try:
        response = client.chat.completions.create(
            model="gpt-5-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
        )
        return (response.choices[0].message.content or "").strip()
    except Exception as exc:
        logger.warning("_summarize_paper: failed for %s (%s)", paper.arxiv_id, exc)
        return ""


def _verify_paper(paper: Paper, summary: str) -> FactCheckResult:
    """Run fact-checking via shared/fact_checker.

    Pipeline: extract_claims → verify_claims → compute_accuracy_score.
    Returns a default FactCheckResult on any failure.
    """
    try:
        from shared.fact_checker import (
            extract_claims,
            verify_claims,
            compute_accuracy_score,
            generate_fact_check_explanation,
        )

        claims = extract_claims(summary, topic=paper.title)
        if not claims:
            return FactCheckResult()

        verifications = verify_claims(
            claims,
            sources=[paper.abstract],
            paper_abstract=paper.abstract,
            arxiv_id=paper.arxiv_id,
        )
        score = compute_accuracy_score(verifications)
        verified_count = sum(
            1 for v in verifications if v.get("verdict", "").upper() == "VERIFIED"
        )
        total_claims = len([c for c in claims if c.get("source_needed", True)])
        issues = [
            v.get("claim", "") for v in verifications
            if v.get("verdict", "").upper() not in ("VERIFIED", "SKIPPED")
        ]
        explanation = generate_fact_check_explanation(score, verifications)

        return FactCheckResult(
            score=score,
            total_claims=total_claims,
            verified_count=verified_count,
            issues=issues,
            explanation=explanation,
        )
    except ImportError:
        logger.debug("_verify_paper: shared.fact_checker not available — skipping")
        return FactCheckResult()
    except Exception as exc:
        logger.warning("_verify_paper: fact-check failed for %s (%s)", paper.arxiv_id, exc)
        return FactCheckResult()


def summarize_and_verify(papers: list[Paper]) -> list[RankedPaper]:
    """For each paper: generate a summary and run fact-checking.

    Returns RankedPaper objects.  Uses the OpenAI client for summarization.
    """
    client = _get_openai_client()
    results: list[RankedPaper] = []

    for paper in papers:
        summary = _summarize_paper(paper, client) if client else ""
        fact_check = _verify_paper(paper, summary) if summary else FactCheckResult()
        data = paper.model_dump()
        data.update(fast_score=fast_score(paper), summary=summary, fact_check=fact_check)
        results.append(RankedPaper(**data))

    return results
