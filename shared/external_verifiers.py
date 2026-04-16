"""External verification sources for fact-checking.

Provides:
- Semantic Scholar: paper metadata, citations, venue, authors
- Repo Health: GitHub repo existence, quality signals (Task 2)
- Quality Web Search: DuckDuckGo with source credibility scoring (Task 3)
"""

import json
import os
import re
import subprocess
from datetime import datetime
from shared.logging_config import get_logger

logger = get_logger(__name__)

# ── Semantic Scholar ──

S2_API_BASE = "https://api.semanticscholar.org/graph/v1"
S2_FIELDS = "title,authors,citationCount,referenceCount,venue,publicationDate,openAccessPdf,externalIds"

# Venues that indicate peer review
PEER_REVIEWED_VENUES = {
    "neurips", "nips", "icml", "iclr", "aaai", "cvpr", "iccv", "eccv",
    "acl", "emnlp", "naacl", "coling", "sigir", "kdd", "www", "ijcai",
    "uai", "aistats", "colt", "icra", "iros", "nature", "science",
}


def semantic_scholar_lookup(arxiv_id: str) -> dict | None:
    """Fetch paper metadata from Semantic Scholar.

    Uses circuit breaker to fail fast when S2 is down.
    Returns dict with: authors, venue, publication_date, citation_count,
    is_peer_reviewed, reference_count, doi. Returns None on failure.
    """
    import httpx
    from shared.circuit_breaker import s2_breaker

    url = f"{S2_API_BASE}/paper/ArXiv:{arxiv_id}?fields={S2_FIELDS}"

    def _do_lookup():
        resp = httpx.get(url, timeout=10, follow_redirects=True)
        resp.raise_for_status()
        return resp.json()

    data = s2_breaker.call(fn=_do_lookup, fallback=None)
    if data is None:
        return None

    venue = data.get("venue", "") or ""
    authors = [a.get("name", "") for a in data.get("authors", [])]

    return {
        "authors": authors,
        "venue": venue,
        "publication_date": data.get("publicationDate", ""),
        "citation_count": data.get("citationCount", 0),
        "reference_count": data.get("referenceCount", 0),
        "is_peer_reviewed": any(v in venue.lower() for v in PEER_REVIEWED_VENUES),
        "doi": data.get("externalIds", {}).get("DOI", ""),
    }


def verify_claim_with_s2(claim: dict, s2_data: dict) -> dict:
    """Verify a single claim against Semantic Scholar data.

    Handles claim types: attribution, date, comparison (partial).
    Returns: {claim, verdict, evidence, confidence, severity, source, fix_suggestion}
    """
    claim_text = claim.get("claim", "").lower()
    claim_type = claim.get("type", "")

    result = {
        "claim": claim["claim"],
        "source": "semantic_scholar",
        "fix_suggestion": None,
    }

    if claim_type == "attribution":
        # Check if claimed authors match
        s2_authors_lower = [a.lower() for a in s2_data.get("authors", [])]
        # Extract surname-like words from claim
        matched = any(
            any(word in author for author in s2_authors_lower)
            for word in claim_text.split()
            if len(word) > 3 and word not in {"proposed", "introduced", "developed", "presented", "published"}
        )
        if matched:
            result["verdict"] = "VERIFIED"
            result["evidence"] = f"Authors confirmed: {', '.join(s2_data['authors'][:3])}"
            result["confidence"] = 0.9
            result["severity"] = "low"
        else:
            result["verdict"] = "UNVERIFIED"
            result["evidence"] = f"S2 authors: {', '.join(s2_data['authors'][:3])}. Claim mentions different names."
            result["confidence"] = 0.6
            result["severity"] = "medium"
            result["fix_suggestion"] = f"Verify author names against: {', '.join(s2_data['authors'][:3])}"

    elif claim_type == "date":
        s2_date = s2_data.get("publication_date", "")
        if s2_date:
            s2_year = s2_date[:4]
            # Check if claimed year matches
            year_match = re.search(r"20\d{2}", claim_text)
            if year_match:
                claimed_year = year_match.group(0)
                if claimed_year == s2_year:
                    result["verdict"] = "VERIFIED"
                    result["evidence"] = f"Publication date confirmed: {s2_date}"
                    result["confidence"] = 0.95
                    result["severity"] = "low"
                else:
                    result["verdict"] = "INACCURATE"
                    result["evidence"] = f"Claim says {claimed_year}, S2 shows {s2_date}"
                    result["confidence"] = 0.9
                    result["severity"] = "medium"
                    result["fix_suggestion"] = f"Correct year to {s2_year}"
            else:
                result["verdict"] = "UNVERIFIED"
                result["evidence"] = f"Cannot extract year from claim. S2 date: {s2_date}"
                result["confidence"] = 0.4
                result["severity"] = "low"
        else:
            result["verdict"] = "UNVERIFIED"
            result["evidence"] = "No publication date in Semantic Scholar"
            result["confidence"] = 0.3
            result["severity"] = "low"

    else:
        # For other claim types, provide metadata context but no verdict
        result["verdict"] = "UNVERIFIED"
        result["evidence"] = (
            f"S2 metadata: {s2_data.get('citation_count', 0)} citations, "
            f"venue: {s2_data.get('venue', 'unknown')}, "
            f"peer-reviewed: {s2_data.get('is_peer_reviewed', False)}"
        )
        result["confidence"] = 0.3
        result["severity"] = "low"

    return result


# ── GitHub Repo Health ──


def _find_gh() -> str:
    """Find the gh CLI binary path."""
    for candidate in ["/opt/homebrew/bin/gh", "/usr/local/bin/gh", "/usr/bin/gh"]:
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    # Fall back to PATH lookup
    try:
        result = subprocess.run(
            ["which", "gh"], capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (subprocess.SubprocessError, OSError):
        pass  # Fall through to default
    return "gh"  # Hope it's on PATH


GH_BIN = _find_gh()


def _gh_api(endpoint: str) -> dict | list:
    """Call GitHub API via gh CLI subprocess.

    Raises RuntimeError on failure.
    """
    result = subprocess.run(
        [GH_BIN, "api", endpoint, "--cache", "1h"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"gh api failed: {result.stderr.strip()}")
    return json.loads(result.stdout)


def _parse_github_url(url: str | None) -> tuple[str, str] | None:
    """Extract (owner, repo) from a GitHub URL.

    Handles:
    - https://github.com/owner/repo
    - https://github.com/owner/repo.git
    - https://github.com/owner/repo/tree/main
    - https://github.com/owner/repo/blob/main/README.md
    - github.com/owner/repo (no scheme)

    Returns None if the URL cannot be parsed.
    """
    if not url:
        return None

    # Normalize: strip whitespace, add scheme if missing
    url = url.strip()
    if not url.startswith("http"):
        url = "https://" + url

    match = re.match(
        r"https?://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?(?:/.*)?$",
        url,
    )
    if not match:
        return None

    owner, repo = match.group(1), match.group(2)
    # Filter out non-repo paths like github.com/owner (no repo)
    if not repo:
        return None
    return (owner, repo)


def check_repo_health(repo_url: str | None) -> dict:
    """Check GitHub repo health signals.

    Returns dict with:
        status: REPO_HEALTHY | REPO_UNHEALTHY | REPO_MISSING | REPO_NA
        stars, forks, has_tests, has_readme, has_license, has_requirements,
        days_since_push, archived, score_adjustment, summary
    """
    base = {
        "status": "REPO_NA",
        "stars": 0,
        "forks": 0,
        "has_tests": False,
        "has_readme": False,
        "has_license": False,
        "has_requirements": False,
        "days_since_push": -1,
        "archived": False,
        "score_adjustment": 0.0,
        "summary": "",
    }

    if not repo_url:
        base["summary"] = "No repository URL provided"
        return base

    parsed = _parse_github_url(repo_url)
    if not parsed:
        base["summary"] = f"Cannot parse GitHub URL: {repo_url}"
        return base

    owner, repo = parsed

    # Fetch repo metadata
    try:
        data = _gh_api(f"repos/{owner}/{repo}")
    except Exception as e:
        logger.warning("GitHub API failed for %s/%s: %s", owner, repo, e)
        base["status"] = "REPO_MISSING"
        base["score_adjustment"] = -0.5
        base["summary"] = f"Repository not accessible: {e}"
        return base

    if not isinstance(data, dict):
        base["status"] = "REPO_MISSING"
        base["score_adjustment"] = -0.5
        base["summary"] = "Unexpected API response format"
        return base

    base["stars"] = data.get("stargazers_count", 0)
    base["forks"] = data.get("forks_count", 0)
    base["archived"] = data.get("archived", False)
    base["has_license"] = data.get("license") is not None

    # Calculate days since last push
    pushed_at = data.get("pushed_at", "")
    if pushed_at:
        try:
            push_dt = datetime.fromisoformat(pushed_at.replace("Z", "+00:00"))
            now = datetime.now(push_dt.tzinfo)
            base["days_since_push"] = (now - push_dt).days
        except Exception:
            base["days_since_push"] = -1

    # Check for tests, README, requirements via repo contents
    try:
        contents = _gh_api(f"repos/{owner}/{repo}/contents")
        if isinstance(contents, list):
            names_lower = [item.get("name", "").lower() for item in contents]
            base["has_readme"] = any(n.startswith("readme") for n in names_lower)
            base["has_tests"] = any(
                n in ("tests", "test", "spec", "specs", "__tests__") for n in names_lower
            )
            base["has_requirements"] = any(
                n in ("requirements.txt", "pyproject.toml", "package.json", "cargo.toml", "go.mod")
                for n in names_lower
            )
    except Exception:
        # Contents API failed — don't penalize, just leave defaults
        pass

    # Determine health status
    problems = []
    if not base["has_tests"]:
        problems.append("no tests directory")
    if not base["has_readme"]:
        problems.append("no README")
    if base["days_since_push"] > 365:
        problems.append(f"stale ({base['days_since_push']} days since last push)")
    if base["archived"]:
        problems.append("archived")

    if problems:
        base["status"] = "REPO_UNHEALTHY"
        base["score_adjustment"] = -0.3
        base["summary"] = f"Issues: {'; '.join(problems)}"
    else:
        base["status"] = "REPO_HEALTHY"
        base["score_adjustment"] = 0.0
        base["summary"] = f"Healthy repo: {base['stars']} stars, {base['forks']} forks"

    return base


# ── Quality Web Search with Source Credibility ──

HIGH_QUALITY_DOMAINS = {
    "arxiv.org", "openreview.net", "proceedings.neurips.cc", "proceedings.mlr.press",
    "aclanthology.org", "ieee.org", "acm.org", "nature.com", "science.org",
    "openai.com", "anthropic.com", "deepmind.google", "ai.meta.com", "github.com",
}

MEDIUM_QUALITY_DOMAINS = {
    "huggingface.co", "pytorch.org", "tensorflow.org", "docs.python.org",
    "en.wikipedia.org", "stackoverflow.com",
}

LOW_QUALITY_DOMAINS = {
    "medium.com", "towardsdatascience.com", "dev.to", "analytics-vidhya.com",
    "kdnuggets.com",
}


def score_source_quality(url: str) -> float:
    """Score a URL's source credibility from 0.0 to 1.0.

    High-quality academic/official sources: 0.9
    Medium-quality docs/reference sites: 0.6
    Low-quality blog/aggregator sites: 0.3
    Unknown domains: 0.4
    Empty/missing URL: 0.1
    """
    if not url or not url.strip():
        return 0.1

    url = url.strip().lower()

    # Extract domain from URL
    domain = url
    if "://" in domain:
        domain = domain.split("://", 1)[1]
    domain = domain.split("/", 1)[0]
    domain = domain.split(":", 1)[0]

    # Check against domain sets (try with and without www.)
    domains_to_check = {domain}
    if domain.startswith("www."):
        domains_to_check.add(domain[4:])
    else:
        domains_to_check.add("www." + domain)

    for d in domains_to_check:
        if d in HIGH_QUALITY_DOMAINS:
            return 0.9
        if d in MEDIUM_QUALITY_DOMAINS:
            return 0.6
        if d in LOW_QUALITY_DOMAINS:
            return 0.3

    return 0.4


def quality_web_verify(query: str) -> dict:
    """Search DuckDuckGo and score results by source credibility.

    Returns:
        dict with keys:
            snippets: list[str] -- text snippets from results
            best_source_url: str -- URL of highest-quality result
            best_source_quality: float -- quality score of best result
            all_results: list[dict] -- all results with url, snippet, quality
    """
    empty_result = {
        "snippets": [],
        "best_source_url": "",
        "best_source_quality": 0.0,
        "all_results": [],
    }

    raw_results = []
    try:
        try:
            from ddgs import DDGS
        except ImportError:
            from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            raw_results = list(ddgs.text(query, max_results=8))
    except ImportError:
        logger.warning("ddgs/duckduckgo_search not installed, trying SearXNG fallback")
    except Exception as e:
        logger.warning("DuckDuckGo search failed for '%s': %s", query, e)

    if not raw_results:
        # Fallback: try SearXNG if DuckDuckGo returned nothing
        try:
            from shared.searxng_client import search_smart
            sxng_results = search_smart(query, context="fact_check", max_results=5)
            if sxng_results:
                raw_results = [
                    {"href": r.url, "body": r.content}
                    for r in sxng_results
                ]
                logger.info("DuckDuckGo empty, SearXNG returned %d results", len(raw_results))
        except Exception as e:
            logger.debug("SearXNG fallback failed: %s", e)

    if not raw_results:
        return empty_result

    scored_results = []
    for r in raw_results:
        url = r.get("href", "") or r.get("link", "") or ""
        snippet = r.get("body", "") or r.get("snippet", "") or ""
        quality = score_source_quality(url)
        scored_results.append({
            "url": url,
            "snippet": snippet,
            "quality": quality,
        })

    # Sort by quality descending
    scored_results.sort(key=lambda x: x["quality"], reverse=True)

    best = scored_results[0] if scored_results else {"url": "", "quality": 0.0}

    return {
        "snippets": [r["snippet"] for r in scored_results if r["snippet"]],
        "best_source_url": best["url"],
        "best_source_quality": best["quality"],
        "all_results": scored_results,
    }
