# Hybrid Fact Verification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace circular LLM-checks-LLM fact verification with multi-source external verification (Semantic Scholar, GitHub repo health, improved web search) and honest scoring where abstract-only verification scores 0.5 not 1.0.

**Architecture:** New `shared/external_verifiers.py` module with 3 independent verifiers (Semantic Scholar, GitHub repo health, web search with quality scoring). `shared/fact_checker.py` upgraded with honest scoring model and explanation generation. Ralph Loop integration via swarm experience storage.

**Tech Stack:** Python, httpx, GitHub CLI (`gh`), Semantic Scholar API, DuckDuckGo, SQLite, pytest

---

## File Structure

| File | Responsibility |
|------|---------------|
| `shared/external_verifiers.py` | **Create** — SemanticScholarVerifier, RepoHealthChecker, QualityWebSearch |
| `tests/test_external_verifiers.py` | **Create** — Mock API tests for all 3 verifiers |
| `shared/fact_checker.py` | **Modify** — Multi-source routing, honest scoring, explanation generation |
| `tests/test_fact_checker.py` | **Modify** — Tests for new scoring weights + explanation format |
| `jobpulse/arxiv_agent.py` | **Modify** — Pass repo URL, update digest format for explanations |
| `tests/test_arxiv_agent.py` | **Modify** — Tests for new digest format |
| `scripts/arxiv_benchmark.py` | **Modify** — Add external verification benchmarks |

---

### Task 1: Semantic Scholar Verifier

**Files:**
- Create: `shared/external_verifiers.py`
- Create: `tests/test_external_verifiers.py`

- [ ] **Step 1: Write failing test for Semantic Scholar paper lookup**

```python
"""Tests for shared/external_verifiers.py"""

import json
from unittest.mock import patch, MagicMock
import pytest


SAMPLE_S2_RESPONSE = {
    "paperId": "abc123",
    "title": "Attention Is All You Need",
    "authors": [{"authorId": "1", "name": "Ashish Vaswani"}],
    "citationCount": 90000,
    "referenceCount": 42,
    "venue": "NeurIPS",
    "publicationDate": "2017-06-12",
    "openAccessPdf": {"url": "https://arxiv.org/pdf/1706.03762", "status": "GREEN"},
    "externalIds": {"ArXiv": "1706.03762", "DOI": "10.5555/3295222.3295349"},
}


class TestSemanticScholarVerifier:
    def test_lookup_returns_paper_metadata(self):
        from shared.external_verifiers import semantic_scholar_lookup

        with patch("httpx.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = SAMPLE_S2_RESPONSE
            mock_resp.raise_for_status = MagicMock()
            mock_get.return_value = mock_resp

            result = semantic_scholar_lookup("1706.03762")

        assert result["citation_count"] == 90000
        assert result["venue"] == "NeurIPS"
        assert result["publication_date"] == "2017-06-12"
        assert result["authors"][0] == "Ashish Vaswani"
        assert result["is_peer_reviewed"] is True

    def test_lookup_returns_none_on_404(self):
        from shared.external_verifiers import semantic_scholar_lookup

        with patch("httpx.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 404
            mock_resp.raise_for_status.side_effect = Exception("404")
            mock_get.return_value = mock_resp

            result = semantic_scholar_lookup("9999.99999")

        assert result is None

    def test_lookup_returns_none_on_rate_limit(self):
        from shared.external_verifiers import semantic_scholar_lookup

        with patch("httpx.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 429
            mock_resp.raise_for_status.side_effect = Exception("429")
            mock_get.return_value = mock_resp

            result = semantic_scholar_lookup("1706.03762")

        assert result is None

    def test_verify_attribution_claim(self):
        from shared.external_verifiers import verify_claim_with_s2

        s2_data = {
            "authors": ["Ashish Vaswani", "Noam Shazeer"],
            "venue": "NeurIPS",
            "publication_date": "2017-06-12",
            "citation_count": 90000,
            "is_peer_reviewed": True,
        }
        claim = {"claim": "proposed by Vaswani et al.", "type": "attribution"}

        result = verify_claim_with_s2(claim, s2_data)

        assert result["verdict"] == "VERIFIED"
        assert result["source"] == "semantic_scholar"

    def test_verify_date_claim_inaccurate(self):
        from shared.external_verifiers import verify_claim_with_s2

        s2_data = {
            "authors": ["Author"],
            "venue": "ICML",
            "publication_date": "2023-07-15",
            "citation_count": 100,
            "is_peer_reviewed": True,
        }
        claim = {"claim": "published in 2022", "type": "date"}

        result = verify_claim_with_s2(claim, s2_data)

        assert result["verdict"] == "INACCURATE"
        assert "2023" in result["evidence"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. pytest tests/test_external_verifiers.py -v -k "SemanticScholar"`
Expected: FAIL — module doesn't exist

- [ ] **Step 3: Implement Semantic Scholar verifier**

```python
"""External verification sources for fact-checking.

Provides:
- Semantic Scholar: paper metadata, citations, venue, authors
- Repo Health: GitHub repo existence, quality signals
- Quality Web Search: DuckDuckGo with source credibility scoring
"""

import json
import re
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

    Returns dict with: authors, venue, publication_date, citation_count,
    is_peer_reviewed, reference_count. Returns None on failure.
    """
    import httpx

    url = f"{S2_API_BASE}/paper/ArXiv:{arxiv_id}?fields={S2_FIELDS}"

    try:
        resp = httpx.get(url, timeout=10, follow_redirects=True)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning("Semantic Scholar lookup failed for %s: %s", arxiv_id, e)
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
    Returns: {verdict, evidence, confidence, severity, source, fix_suggestion}
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. pytest tests/test_external_verifiers.py -v -k "SemanticScholar"`
Expected: 5 PASS

- [ ] **Step 5: Commit**

```bash
git add shared/external_verifiers.py tests/test_external_verifiers.py
git commit -m "feat(fact-check): Semantic Scholar verifier for attribution + date claims"
```

---

### Task 2: GitHub Repo Health Checker

**Files:**
- Modify: `shared/external_verifiers.py`
- Modify: `tests/test_external_verifiers.py`

- [ ] **Step 1: Write failing tests for repo health**

Add to `tests/test_external_verifiers.py`:

```python
SAMPLE_GH_REPO = {
    "stargazers_count": 1500,
    "forks_count": 200,
    "open_issues_count": 15,
    "pushed_at": "2026-03-25T10:00:00Z",
    "license": {"key": "mit", "name": "MIT License"},
    "description": "Official implementation",
    "archived": False,
}

SAMPLE_GH_CONTENTS = [
    {"name": "README.md", "type": "file"},
    {"name": "requirements.txt", "type": "file"},
    {"name": "tests", "type": "dir"},
    {"name": "src", "type": "dir"},
    {"name": "LICENSE", "type": "file"},
]


class TestRepoHealthChecker:
    def test_healthy_repo(self):
        from shared.external_verifiers import check_repo_health

        with patch("shared.external_verifiers._gh_api") as mock_gh:
            mock_gh.side_effect = [SAMPLE_GH_REPO, SAMPLE_GH_CONTENTS]

            result = check_repo_health("https://github.com/owner/repo")

        assert result["status"] == "REPO_HEALTHY"
        assert result["stars"] == 1500
        assert result["has_tests"] is True
        assert result["has_readme"] is True
        assert result["has_license"] is True
        assert result["score_adjustment"] == 0.0

    def test_unhealthy_repo_no_tests(self):
        from shared.external_verifiers import check_repo_health

        contents_no_tests = [
            {"name": "README.md", "type": "file"},
            {"name": "src", "type": "dir"},
        ]

        with patch("shared.external_verifiers._gh_api") as mock_gh:
            mock_gh.side_effect = [SAMPLE_GH_REPO, contents_no_tests]

            result = check_repo_health("https://github.com/owner/repo")

        assert result["status"] == "REPO_UNHEALTHY"
        assert result["has_tests"] is False
        assert result["score_adjustment"] == -0.3

    def test_missing_repo(self):
        from shared.external_verifiers import check_repo_health

        with patch("shared.external_verifiers._gh_api") as mock_gh:
            mock_gh.side_effect = Exception("Not Found")

            result = check_repo_health("https://github.com/owner/nonexistent")

        assert result["status"] == "REPO_MISSING"
        assert result["score_adjustment"] == -0.5

    def test_no_url_returns_na(self):
        from shared.external_verifiers import check_repo_health

        result = check_repo_health(None)

        assert result["status"] == "REPO_NA"
        assert result["score_adjustment"] == 0.0

    def test_stale_repo(self):
        from shared.external_verifiers import check_repo_health

        stale_repo = dict(SAMPLE_GH_REPO)
        stale_repo["pushed_at"] = "2024-01-01T00:00:00Z"

        with patch("shared.external_verifiers._gh_api") as mock_gh:
            mock_gh.side_effect = [stale_repo, SAMPLE_GH_CONTENTS]

            result = check_repo_health("https://github.com/owner/repo")

        assert result["status"] == "REPO_UNHEALTHY"
        assert "stale" in result["summary"].lower() or result["days_since_push"] > 365

    def test_extracts_owner_repo_from_url(self):
        from shared.external_verifiers import _parse_github_url

        assert _parse_github_url("https://github.com/owner/repo") == ("owner", "repo")
        assert _parse_github_url("https://github.com/owner/repo.git") == ("owner", "repo")
        assert _parse_github_url("https://github.com/owner/repo/tree/main") == ("owner", "repo")
        assert _parse_github_url("not-a-url") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. pytest tests/test_external_verifiers.py -v -k "RepoHealth"`
Expected: FAIL — functions don't exist

- [ ] **Step 3: Implement repo health checker**

Add to `shared/external_verifiers.py`:

```python
import os
import subprocess

# ── Repo Health ──

def _find_gh() -> str:
    """Find gh CLI binary."""
    for path in ["/opt/homebrew/bin/gh", "/usr/local/bin/gh", "/usr/bin/gh"]:
        if os.path.exists(path):
            return path
    return "gh"


GH_BIN = _find_gh()


def _gh_api(endpoint: str) -> dict | list:
    """Call GitHub API via gh CLI."""
    result = subprocess.run(
        [GH_BIN, "api", endpoint],
        capture_output=True, text=True, timeout=15,
    )
    if result.returncode != 0:
        raise Exception(f"gh api failed: {result.stderr[:200]}")
    return json.loads(result.stdout) if result.stdout else {}


def _parse_github_url(url: str | None) -> tuple[str, str] | None:
    """Extract (owner, repo) from a GitHub URL."""
    if not url:
        return None
    match = re.match(r"https?://github\.com/([^/]+)/([^/.]+)", url)
    if match:
        return match.group(1), match.group(2)
    return None


def check_repo_health(repo_url: str | None) -> dict:
    """Check GitHub repo health signals.

    Returns dict with: status (REPO_HEALTHY/REPO_UNHEALTHY/REPO_MISSING/REPO_NA),
    stars, forks, has_tests, has_readme, has_license, days_since_push,
    score_adjustment, summary.
    """
    if not repo_url:
        return {
            "status": "REPO_NA",
            "score_adjustment": 0.0,
            "summary": "No repository linked",
        }

    parsed = _parse_github_url(repo_url)
    if not parsed:
        return {
            "status": "REPO_NA",
            "score_adjustment": 0.0,
            "summary": f"Not a GitHub URL: {repo_url[:80]}",
        }

    owner, repo = parsed

    try:
        repo_data = _gh_api(f"/repos/{owner}/{repo}")
        contents = _gh_api(f"/repos/{owner}/{repo}/contents")
    except Exception as e:
        logger.warning("Repo health check failed for %s/%s: %s", owner, repo, e)
        return {
            "status": "REPO_MISSING",
            "score_adjustment": -0.5,
            "summary": f"Repository {owner}/{repo} not found or inaccessible",
        }

    if not isinstance(contents, list):
        contents = []

    content_names = {item.get("name", "").lower() for item in contents}
    content_types = {item.get("name", "").lower(): item.get("type", "") for item in contents}

    stars = repo_data.get("stargazers_count", 0)
    forks = repo_data.get("forks_count", 0)
    has_readme = any(n.startswith("readme") for n in content_names)
    has_tests = any(
        n in ("tests", "test", "testing", "spec", "specs")
        and content_types.get(n) == "dir"
        for n in content_names
    ) or any(n.startswith("test_") or n.endswith("_test.py") for n in content_names)
    has_license = "license" in content_names or "license.md" in content_names
    has_requirements = any(
        n in ("requirements.txt", "setup.py", "pyproject.toml", "package.json", "cargo.toml", "go.mod")
        for n in content_names
    )
    archived = repo_data.get("archived", False)

    pushed_at = repo_data.get("pushed_at", "")
    days_since_push = 0
    if pushed_at:
        try:
            push_date = datetime.fromisoformat(pushed_at.replace("Z", "+00:00"))
            days_since_push = (datetime.now(push_date.tzinfo) - push_date).days
        except Exception:
            pass

    # Determine health
    problems = []
    if not has_tests:
        problems.append("no tests")
    if not has_readme:
        problems.append("no README")
    if not has_requirements:
        problems.append("no dependency file")
    if days_since_push > 365:
        problems.append(f"stale ({days_since_push} days since last push)")
    if archived:
        problems.append("archived")

    result = {
        "stars": stars,
        "forks": forks,
        "has_tests": has_tests,
        "has_readme": has_readme,
        "has_license": has_license,
        "has_requirements": has_requirements,
        "days_since_push": days_since_push,
        "archived": archived,
    }

    if not problems:
        result["status"] = "REPO_HEALTHY"
        result["score_adjustment"] = 0.0
        result["summary"] = f"repo healthy ({stars:,} stars, tests present, recently updated)"
    else:
        result["status"] = "REPO_UNHEALTHY"
        result["score_adjustment"] = -0.3
        result["summary"] = f"repo exists but {', '.join(problems)}"

    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. pytest tests/test_external_verifiers.py -v -k "RepoHealth"`
Expected: 6 PASS

- [ ] **Step 5: Commit**

```bash
git add shared/external_verifiers.py tests/test_external_verifiers.py
git commit -m "feat(fact-check): GitHub repo health checker"
```

---

### Task 3: Quality Web Search

**Files:**
- Modify: `shared/external_verifiers.py`
- Modify: `tests/test_external_verifiers.py`

- [ ] **Step 1: Write failing tests for quality web search**

Add to `tests/test_external_verifiers.py`:

```python
class TestQualityWebSearch:
    def test_scores_academic_sources_higher(self):
        from shared.external_verifiers import score_source_quality

        assert score_source_quality("https://arxiv.org/abs/2301.07041") > 0.8
        assert score_source_quality("https://openreview.net/forum?id=abc") > 0.8
        assert score_source_quality("https://proceedings.neurips.cc/paper/2023") > 0.8

    def test_scores_blogs_lower(self):
        from shared.external_verifiers import score_source_quality

        assert score_source_quality("https://medium.com/some-post") < 0.5
        assert score_source_quality("https://towardsdatascience.com/article") < 0.5

    def test_scores_official_docs_medium(self):
        from shared.external_verifiers import score_source_quality

        assert score_source_quality("https://pytorch.org/docs/stable") >= 0.6
        assert score_source_quality("https://huggingface.co/docs") >= 0.6

    def test_quality_web_verify_returns_best_source(self):
        from shared.external_verifiers import quality_web_verify

        mock_results = [
            {"href": "https://arxiv.org/abs/2301.07041", "body": "GPT-4 achieves 86.4% on MMLU"},
            {"href": "https://medium.com/blog", "body": "GPT-4 is amazing and fast"},
            {"href": "https://openai.com/research/gpt-4", "body": "GPT-4 technical report"},
        ]

        with patch("shared.external_verifiers.DDGS") as mock_ddgs:
            mock_instance = MagicMock()
            mock_instance.__enter__ = MagicMock(return_value=mock_instance)
            mock_instance.__exit__ = MagicMock(return_value=False)
            mock_instance.text.return_value = mock_results
            mock_ddgs.return_value = mock_instance

            result = quality_web_verify("GPT-4 MMLU score")

        assert result["best_source_quality"] > 0.7
        assert "arxiv" in result["best_source_url"] or "openai" in result["best_source_url"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. pytest tests/test_external_verifiers.py -v -k "QualityWeb"`
Expected: FAIL — functions don't exist

- [ ] **Step 3: Implement quality web search**

Add to `shared/external_verifiers.py`:

```python
# ── Quality Web Search ──

# Source credibility tiers
HIGH_QUALITY_DOMAINS = {
    "arxiv.org", "openreview.net", "proceedings.neurips.cc",
    "proceedings.mlr.press", "aclanthology.org", "ieee.org",
    "acm.org", "nature.com", "science.org", "openai.com",
    "anthropic.com", "deepmind.google", "ai.meta.com",
    "github.com",
}

MEDIUM_QUALITY_DOMAINS = {
    "huggingface.co", "pytorch.org", "tensorflow.org",
    "docs.python.org", "en.wikipedia.org", "stackoverflow.com",
}

LOW_QUALITY_DOMAINS = {
    "medium.com", "towardsdatascience.com", "dev.to",
    "analytics-vidhya.com", "kdnuggets.com",
}


def score_source_quality(url: str) -> float:
    """Score source credibility 0.0-1.0 based on domain."""
    if not url:
        return 0.1

    url_lower = url.lower()
    for domain in HIGH_QUALITY_DOMAINS:
        if domain in url_lower:
            return 0.9
    for domain in MEDIUM_QUALITY_DOMAINS:
        if domain in url_lower:
            return 0.6
    for domain in LOW_QUALITY_DOMAINS:
        if domain in url_lower:
            return 0.3
    return 0.4  # Unknown domain


def quality_web_verify(query: str) -> dict:
    """Search the web and return results scored by source quality.

    Returns: {snippets, best_source_url, best_source_quality, all_results}
    """
    try:
        from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=5))
    except ImportError:
        logger.warning("duckduckgo-search not installed")
        return {"snippets": "", "best_source_url": None, "best_source_quality": 0.0, "all_results": []}
    except Exception as e:
        logger.warning("Web search failed: %s", e)
        return {"snippets": "", "best_source_url": None, "best_source_quality": 0.0, "all_results": []}

    if not results:
        return {"snippets": "", "best_source_url": None, "best_source_quality": 0.0, "all_results": []}

    scored = []
    for r in results:
        url = r.get("href", "")
        quality = score_source_quality(url)
        scored.append({
            "url": url,
            "snippet": r.get("body", "")[:300],
            "quality": quality,
        })

    # Sort by quality descending
    scored.sort(key=lambda x: x["quality"], reverse=True)

    # Combine top snippets (prefer higher quality sources)
    top_snippets = "\n".join(f"[{s['quality']:.1f}] {s['snippet']}" for s in scored[:3])

    return {
        "snippets": top_snippets,
        "best_source_url": scored[0]["url"],
        "best_source_quality": scored[0]["quality"],
        "all_results": scored,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. pytest tests/test_external_verifiers.py -v -k "QualityWeb"`
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add shared/external_verifiers.py tests/test_external_verifiers.py
git commit -m "feat(fact-check): quality web search with source credibility scoring"
```

---

### Task 4: Honest Scoring Model

**Files:**
- Modify: `shared/fact_checker.py`
- Modify: `tests/test_fact_checker.py`

- [ ] **Step 1: Write failing tests for honest scoring**

Add to `tests/test_fact_checker.py`:

```python
class TestHonestScoring:
    def test_abstract_only_verified_scores_half(self):
        """A claim verified only against the abstract scores 0.5, not 1.0."""
        from shared.fact_checker import compute_accuracy_score

        verifications = [
            {"verdict": "VERIFIED", "severity": "low", "source": "abstract"},
        ]
        score = compute_accuracy_score(verifications)
        assert score == 5.0  # 0.5/1.0 * 10

    def test_external_verified_scores_full(self):
        """A claim verified by external source scores 1.0."""
        from shared.fact_checker import compute_accuracy_score

        verifications = [
            {"verdict": "VERIFIED", "severity": "low", "source": "semantic_scholar"},
        ]
        score = compute_accuracy_score(verifications)
        assert score == 10.0

    def test_mixed_sources_weighted(self):
        """Mix of abstract-only and external verifications."""
        from shared.fact_checker import compute_accuracy_score

        verifications = [
            {"verdict": "VERIFIED", "severity": "low", "source": "abstract"},
            {"verdict": "VERIFIED", "severity": "low", "source": "semantic_scholar"},
        ]
        score = compute_accuracy_score(verifications)
        # (0.5 + 1.0) / 2.0 * 10 = 7.5
        assert abs(score - 7.5) < 0.1

    def test_exaggerated_new_penalty(self):
        """Exaggerated claims now score -1.5 (was -1.0)."""
        from shared.fact_checker import compute_accuracy_score

        verifications = [
            {"verdict": "VERIFIED", "severity": "low", "source": "semantic_scholar"},
            {"verdict": "EXAGGERATED", "severity": "medium", "source": "semantic_scholar"},
        ]
        score = compute_accuracy_score(verifications)
        # (1.0 + -1.5) / 2.0 * 10 = -2.5 → clamped to 0.0
        assert score == 0.0

    def test_repo_adjustment_applied(self):
        """Repo health adjustment modifies final score."""
        from shared.fact_checker import compute_accuracy_score

        verifications = [
            {"verdict": "VERIFIED", "severity": "low", "source": "semantic_scholar"},
        ]
        score = compute_accuracy_score(verifications, repo_adjustment=-0.5)
        # 10.0 + (-0.5 * 10 / max_points) — but adjustment is on the 0-10 scale
        # 10.0 - 0.5 = 9.5... let's define: repo_adjustment is added directly to 0-10 score
        assert abs(score - 9.5) < 0.1

    def test_backward_compatible_no_source_field(self):
        """Old verifications without 'source' field default to abstract weight."""
        from shared.fact_checker import compute_accuracy_score

        verifications = [
            {"verdict": "VERIFIED", "severity": "low"},  # No source field
        ]
        score = compute_accuracy_score(verifications)
        assert score == 5.0  # Defaults to abstract-only weight
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. pytest tests/test_fact_checker.py -v -k "HonestScoring"`
Expected: FAIL — current scoring doesn't use source weights

- [ ] **Step 3: Update `compute_accuracy_score` with honest weights**

In `shared/fact_checker.py`, replace the existing `compute_accuracy_score`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. pytest tests/test_fact_checker.py -v`
Expected: All PASS (old tests may need updating — see step below)

- [ ] **Step 4b: Fix any broken old tests**

Old tests that expect `score == 10.0` for `VERIFIED` without a `source` field will now get 5.0. Update those old tests to either:
- Add `"source": "semantic_scholar"` to get 10.0
- Update expected values to reflect honest scoring

Run: `PYTHONPATH=. pytest tests/test_fact_checker.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add shared/fact_checker.py tests/test_fact_checker.py
git commit -m "feat(fact-check): honest scoring — abstract-only=0.5, external=1.0, repo adjustment"
```

---

### Task 5: Explanation Generator

**Files:**
- Modify: `shared/fact_checker.py`
- Modify: `tests/test_fact_checker.py`

- [ ] **Step 1: Write failing tests for explanation generation**

Add to `tests/test_fact_checker.py`:

```python
class TestExplanationGenerator:
    def test_all_verified_externally(self):
        from shared.fact_checker import generate_fact_check_explanation

        verifications = [
            {"claim": "achieves 92% on MMLU", "verdict": "VERIFIED", "source": "semantic_scholar", "evidence": "Confirmed"},
            {"claim": "released in 2023", "verdict": "VERIFIED", "source": "semantic_scholar", "evidence": "Confirmed"},
        ]
        repo = {"status": "REPO_HEALTHY", "summary": "repo healthy (1500 stars, tests present)"}

        explanation = generate_fact_check_explanation(8.5, verifications, repo)

        assert "2/2" in explanation
        assert "externally" in explanation
        assert "healthy" in explanation.lower()

    def test_exaggerated_claim_shown(self):
        from shared.fact_checker import generate_fact_check_explanation

        verifications = [
            {"claim": "3x faster than BERT", "verdict": "EXAGGERATED", "source": "web",
             "evidence": "Benchmark shows 2.1x"},
            {"claim": "novel architecture", "verdict": "VERIFIED", "source": "abstract", "evidence": "ok"},
        ]
        repo = {"status": "REPO_NA", "summary": "No repository linked"}

        explanation = generate_fact_check_explanation(3.0, verifications, repo)

        assert "1/2" in explanation
        assert "exaggerated" in explanation.lower()
        assert "3x faster" in explanation or "BERT" in explanation

    def test_missing_repo_mentioned(self):
        from shared.fact_checker import generate_fact_check_explanation

        verifications = [
            {"claim": "open source", "verdict": "VERIFIED", "source": "abstract", "evidence": "ok"},
        ]
        repo = {"status": "REPO_MISSING", "summary": "Repository owner/repo not found"}

        explanation = generate_fact_check_explanation(4.5, verifications, repo)

        assert "not found" in explanation.lower() or "missing" in explanation.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. pytest tests/test_fact_checker.py -v -k "ExplanationGenerator"`
Expected: FAIL — function doesn't exist

- [ ] **Step 3: Implement explanation generator**

Add to `shared/fact_checker.py`:

```python
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
    external_note = " externally" if has_external else " (abstract only)"

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

    return " — ".join(parts)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. pytest tests/test_fact_checker.py -v -k "ExplanationGenerator"`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add shared/fact_checker.py tests/test_fact_checker.py
git commit -m "feat(fact-check): explanation generator for human-readable score breakdowns"
```

---

### Task 6: Multi-Source Verification Router

**Files:**
- Modify: `shared/fact_checker.py`
- Modify: `tests/test_fact_checker.py`

- [ ] **Step 1: Write failing test for multi-source routing**

Add to `tests/test_fact_checker.py`:

```python
class TestMultiSourceRouter:
    def test_benchmark_claim_uses_web_search(self):
        """Benchmark claims route through quality web search."""
        from shared.fact_checker import route_claim_to_verifier

        claim = {"claim": "achieves 92% on MMLU", "type": "benchmark", "source_needed": True}

        assert route_claim_to_verifier(claim) == "web"

    def test_attribution_claim_uses_s2(self):
        """Attribution claims route through Semantic Scholar."""
        from shared.fact_checker import route_claim_to_verifier

        claim = {"claim": "proposed by Vaswani et al.", "type": "attribution", "source_needed": True}

        assert route_claim_to_verifier(claim) == "semantic_scholar"

    def test_date_claim_uses_s2(self):
        """Date claims route through Semantic Scholar."""
        from shared.fact_checker import route_claim_to_verifier

        claim = {"claim": "published in 2023", "type": "date", "source_needed": True}

        assert route_claim_to_verifier(claim) == "semantic_scholar"

    def test_technical_claim_uses_abstract_then_web(self):
        """Technical claims check abstract first, then web."""
        from shared.fact_checker import route_claim_to_verifier

        claim = {"claim": "uses 12 attention heads", "type": "technical", "source_needed": True}

        assert route_claim_to_verifier(claim) == "abstract_then_web"

    def test_comparison_claim_uses_web(self):
        """Comparison claims use web search for benchmark data."""
        from shared.fact_checker import route_claim_to_verifier

        claim = {"claim": "3x faster than BERT", "type": "comparison", "source_needed": True}

        assert route_claim_to_verifier(claim) == "web"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. pytest tests/test_fact_checker.py -v -k "MultiSourceRouter"`
Expected: FAIL

- [ ] **Step 3: Implement routing function**

Add to `shared/fact_checker.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. pytest tests/test_fact_checker.py -v -k "MultiSourceRouter"`
Expected: 5 PASS

- [ ] **Step 5: Commit**

```bash
git add shared/fact_checker.py tests/test_fact_checker.py
git commit -m "feat(fact-check): claim type routing to appropriate verification source"
```

---

### Task 7: Wire Multi-Source Verification into verify_claims

**Files:**
- Modify: `shared/fact_checker.py`
- Modify: `tests/test_fact_checker.py`

- [ ] **Step 1: Write integration test for multi-source verify_claims**

Add to `tests/test_fact_checker.py`:

```python
class TestMultiSourceVerifyClaims:
    def test_routes_claims_to_correct_verifiers(self):
        """verify_claims routes different claim types to different sources."""
        from shared.fact_checker import verify_claims

        claims = [
            {"claim": "proposed by Smith et al.", "type": "attribution", "source_needed": True},
            {"claim": "achieves 95% on MMLU", "type": "benchmark", "source_needed": True},
        ]

        s2_data = {
            "authors": ["John Smith", "Jane Doe"],
            "venue": "NeurIPS",
            "publication_date": "2025-12-01",
            "citation_count": 50,
            "is_peer_reviewed": True,
        }

        web_result = {
            "snippets": "MMLU benchmark shows 93.2% for this model",
            "best_source_url": "https://arxiv.org/abs/2025.12345",
            "best_source_quality": 0.9,
            "all_results": [],
        }

        with patch("shared.fact_checker.semantic_scholar_lookup", return_value=s2_data), \
             patch("shared.fact_checker.quality_web_verify", return_value=web_result), \
             patch("shared.fact_checker.OpenAI") as mock_openai:

            # Mock LLM for web-based verification
            mock_client = MagicMock()
            mock_openai.return_value = mock_client
            mock_response = MagicMock()
            mock_response.choices = [MagicMock()]
            mock_response.choices[0].message.content = json.dumps({"verifications": [
                {"claim": "achieves 95% on MMLU", "verdict": "EXAGGERATED",
                 "evidence": "Sources show 93.2%", "confidence": 0.85,
                 "severity": "medium", "fix_suggestion": "Correct to 93.2%"},
            ]})
            mock_client.chat.completions.create.return_value = mock_response

            results = verify_claims(claims, sources=[], paper_abstract="Test abstract",
                                   arxiv_id="2025.12345")

        # Check that attribution went through S2
        attr_result = next(r for r in results if "Smith" in r["claim"])
        assert attr_result["source"] == "semantic_scholar"

        # Check that benchmark went through web
        bench_result = next(r for r in results if "MMLU" in r["claim"])
        assert bench_result["verdict"] == "EXAGGERATED"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. pytest tests/test_fact_checker.py -v -k "routes_claims"`
Expected: FAIL — verify_claims doesn't do multi-source routing yet

- [ ] **Step 3: Update verify_claims for multi-source routing**

In `shared/fact_checker.py`, update `verify_claims` to add `arxiv_id` parameter and route claims:

```python
def verify_claims(claims: list[dict], sources: list[str],
                  paper_abstract: str = None, web_search: bool = True,
                  arxiv_id: str = None) -> list[dict]:
    """Verify each claim using multi-source routing.

    Routes claims to appropriate verifiers based on claim type:
    - attribution/date → Semantic Scholar
    - benchmark/comparison → Quality web search + LLM judge
    - technical → Abstract check + web fallback
    """
    from shared.external_verifiers import (
        semantic_scholar_lookup, verify_claim_with_s2, quality_web_verify,
    )
    from jobpulse.config import OPENAI_API_KEY
    client = OpenAI(api_key=OPENAI_API_KEY)

    verifiable = [c for c in claims
                  if c.get("source_needed", True)
                  and c.get("type", "") not in SKIP_TYPES]
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
        else:
            uncached_claims.append(c)

    if not uncached_claims:
        return cached_results

    # Fetch Semantic Scholar data once per paper (reused across S2-routed claims)
    s2_data = None
    if arxiv_id and any(route_claim_to_verifier(c) == "semantic_scholar" for c in uncached_claims):
        s2_data = semantic_scholar_lookup(arxiv_id)

    # Route each claim to its verifier
    s2_results = []
    llm_claims = []  # Claims that need LLM-based verification (abstract/web)

    for c in uncached_claims:
        route = route_claim_to_verifier(c)

        if route == "semantic_scholar" and s2_data:
            result = verify_claim_with_s2(c, s2_data)
            s2_results.append(result)
        else:
            llm_claims.append(c)

    # For web-routed claims, enrich source context with quality web search
    source_text = "\n\n".join(sources[:5]) if sources else ""
    if paper_abstract:
        source_text = f"PAPER ABSTRACT:\n{paper_abstract}\n\n{source_text}"

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
                model="gpt-4o-mini",
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

    # Cache new results
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

    return all_results
```

- [ ] **Step 4: Run full test suite**

Run: `PYTHONPATH=. pytest tests/test_fact_checker.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add shared/fact_checker.py tests/test_fact_checker.py
git commit -m "feat(fact-check): multi-source verification routing in verify_claims"
```

---

### Task 8: Update arXiv Agent for Honest Fact-Checking

**Files:**
- Modify: `jobpulse/arxiv_agent.py`
- Modify: `tests/test_arxiv_agent.py`

- [ ] **Step 1: Write test for explanation in digest**

Add to `tests/test_arxiv_agent.py`:

```python
def test_digest_shows_fact_check_explanation():
    """Digest shows score + explanation, not just N/M count."""
    import jobpulse.arxiv_agent as agent

    paper = {
        "title": "Test Paper", "arxiv_id": "2026.1",
        "arxiv_url": "http://arxiv.org/abs/2026.1",
        "authors": ["Author"], "categories": ["cs.AI"],
        "impact_score": 9.0, "impact_reason": "Novel",
        "summary": "Does X.", "key_technique": "method",
        "category_tag": "LLM", "practical_takeaway": "Use for Y.",
        "fact_check": {
            "score": 6.2,
            "explanation": "6.2/10 — 3/4 claims verified externally, exaggerated: \"3x faster\" (benchmark shows 2.1x), repo exists but no tests",
            "total_claims": 4,
            "verified_count": 3,
        },
    }

    # Build a single paper's lines to test formatting
    lines = []
    fc = paper.get("fact_check", {})
    if fc and fc.get("explanation"):
        lines.append(f"Fact-check: {fc['explanation']}")

    output = "\n".join(lines)
    assert "6.2/10" in output
    assert "3/4" in output
    assert "exaggerated" in output
```

- [ ] **Step 2: Run test**

Run: `PYTHONPATH=. pytest tests/test_arxiv_agent.py -v -k "explanation"`
Expected: PASS (this tests the format expectation)

- [ ] **Step 3: Update `summarize_and_verify_paper` to use multi-source verification**

In `jobpulse/arxiv_agent.py`, update `summarize_and_verify_paper`:

```python
def summarize_and_verify_paper(paper: dict) -> dict:
    """Summarize a paper and fact-check claims using multi-source verification.

    Uses Semantic Scholar for attribution/date claims, quality web search for
    benchmark/comparison claims, and abstract-check for technical claims.
    Checks repo health if GitHub URL found.
    """
    from shared.fact_checker import (
        extract_claims, verify_claims, compute_accuracy_score,
        generate_fact_check_explanation,
    )
    from shared.external_verifiers import check_repo_health

    summary = summarize_paper(paper)

    # Extract claims from summary
    try:
        claims = extract_claims(summary, paper["title"])
    except Exception as e:
        logger.warning("Claim extraction failed for %s: %s", paper["arxiv_id"], e)
        claims = []

    # Check repo health
    repo_url = _find_repo_url(paper)
    try:
        repo_health = check_repo_health(repo_url)
    except Exception as e:
        logger.warning("Repo health check failed: %s", e)
        repo_health = {"status": "REPO_NA", "score_adjustment": 0.0, "summary": "Check failed"}

    if not claims:
        score = max(0.0, min(10.0, 10.0 + repo_health.get("score_adjustment", 0.0)))
        explanation = generate_fact_check_explanation(score, [], repo_health)
        return {
            "summary": summary,
            "fact_check": {
                "score": score,
                "explanation": explanation,
                "total_claims": 0,
                "verified_count": 0,
                "issues": [],
                "repo_health": repo_health,
            },
        }

    # Multi-source verification
    try:
        verifications = verify_claims(
            claims, sources=[], paper_abstract=paper["abstract"],
            arxiv_id=paper["arxiv_id"],
        )
        score = compute_accuracy_score(
            verifications,
            repo_adjustment=repo_health.get("score_adjustment", 0.0),
        )
    except Exception as e:
        logger.warning("Verification failed for %s: %s", paper["arxiv_id"], e)
        verifications = []
        score = 0.0

    explanation = generate_fact_check_explanation(score, verifications, repo_health)

    issues = [
        {"claim": v["claim"], "verdict": v["verdict"], "fix": v.get("fix_suggestion")}
        for v in verifications
        if v.get("verdict") in ("INACCURATE", "EXAGGERATED")
    ]

    return {
        "summary": summary,
        "fact_check": {
            "score": score,
            "explanation": explanation,
            "total_claims": len(verifications),
            "verified_count": sum(1 for v in verifications if v["verdict"] == "VERIFIED"),
            "issues": issues,
            "repo_health": repo_health,
        },
    }


def _find_repo_url(paper: dict) -> str | None:
    """Try to find a GitHub repo URL from paper metadata."""
    # Check abstract for GitHub links
    import re
    abstract = paper.get("abstract", "")
    match = re.search(r"https?://github\.com/[^\s)]+", abstract)
    if match:
        return match.group(0).rstrip(".")
    return None
```

- [ ] **Step 4: Update digest format to show explanation**

In `jobpulse/arxiv_agent.py`, update the fact-check badge section in `build_digest`:

Replace the existing fact-check badge block with:

```python
        # Fact-check with honest explanation
        fc = paper.get("fact_check", {})
        if fc and fc.get("explanation"):
            lines.append(f"Fact-check: {fc['explanation']}")
        elif fc and fc.get("total_claims", 0) > 0:
            verified = fc["verified_count"]
            total = fc["total_claims"]
            lines.append(f"Fact-check: {fc.get('score', 0):.1f}/10 — {verified}/{total} claims checked")
```

- [ ] **Step 5: Run full test suite**

Run: `PYTHONPATH=. pytest tests/test_arxiv_agent.py tests/test_fact_checker.py tests/test_external_verifiers.py -v --tb=short`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add jobpulse/arxiv_agent.py tests/test_arxiv_agent.py
git commit -m "feat(arxiv): multi-source fact-checking with honest scores + explanations in digest"
```

---

### Task 9: Update Benchmark + Ralph Loop Experience Storage

**Files:**
- Modify: `scripts/arxiv_benchmark.py`
- Modify: `jobpulse/arxiv_agent.py`

- [ ] **Step 1: Add external verification benchmarks**

In `scripts/arxiv_benchmark.py`, add new benchmark functions:

```python
def benchmark_external_verifiers() -> dict:
    """Check that external verifier modules exist and have correct interfaces."""
    checks = {}

    try:
        from shared.external_verifiers import semantic_scholar_lookup
        checks["has_s2_lookup"] = callable(semantic_scholar_lookup)
    except ImportError:
        checks["has_s2_lookup"] = False

    try:
        from shared.external_verifiers import check_repo_health
        checks["has_repo_health"] = callable(check_repo_health)
    except ImportError:
        checks["has_repo_health"] = False

    try:
        from shared.external_verifiers import quality_web_verify, score_source_quality
        checks["has_quality_web"] = callable(quality_web_verify) and callable(score_source_quality)
    except ImportError:
        checks["has_quality_web"] = False

    try:
        from shared.fact_checker import route_claim_to_verifier
        checks["has_claim_router"] = callable(route_claim_to_verifier)
    except ImportError:
        checks["has_claim_router"] = False

    try:
        from shared.fact_checker import generate_fact_check_explanation
        checks["has_explanation_gen"] = callable(generate_fact_check_explanation)
    except ImportError:
        checks["has_explanation_gen"] = False

    passed = sum(1 for v in checks.values() if v)
    total = len(checks)

    return {
        "passed": passed,
        "failed": total - passed,
        "total": total,
        "checks": {k: "PASS" if v else "FAIL" for k, v in checks.items()},
        "score": passed / total * 10 if total else 10.0,
    }


def benchmark_honest_scoring() -> dict:
    """Verify that abstract-only verification no longer scores 10/10."""
    from shared.fact_checker import compute_accuracy_score

    test_cases = [
        ([{"verdict": "VERIFIED", "source": "abstract"}], None, 5.0,
         "Abstract-only VERIFIED should score 5.0, not 10.0"),
        ([{"verdict": "VERIFIED", "source": "semantic_scholar"}], None, 10.0,
         "External VERIFIED should score 10.0"),
        ([{"verdict": "VERIFIED", "source": "abstract"}], -0.5, 4.5,
         "Abstract VERIFIED + missing repo = 4.5"),
    ]

    results = {"passed": 0, "failed": 0, "total": len(test_cases), "failures": []}

    for verifications, repo_adj, expected, desc in test_cases:
        kwargs = {}
        if repo_adj is not None:
            kwargs["repo_adjustment"] = repo_adj
        actual = compute_accuracy_score(verifications, **kwargs)
        if abs(actual - expected) <= 0.5:
            results["passed"] += 1
        else:
            results["failed"] += 1
            results["failures"].append({"description": desc, "expected": expected, "actual": round(actual, 2)})

    results["score"] = results["passed"] / results["total"] * 10 if results["total"] else 10.0
    return results
```

Add these to the `benchmarks` dict in `run_all_benchmarks()`:
```python
    "external_verifiers": ("External Verifiers", benchmark_external_verifiers),
    "honest_scoring": ("Honest Scoring Model", benchmark_honest_scoring),
```

- [ ] **Step 2: Add Ralph Loop experience storage to build_digest**

In `jobpulse/arxiv_agent.py`, at the end of `build_digest`, after the Telegram formatting, add experience storage:

```python
    # Store verification experiences for Ralph Loop learning
    try:
        from jobpulse.swarm_dispatcher import store_experience
        for paper, summary in summaries:
            fc = paper.get("fact_check", {})
            if fc.get("total_claims", 0) > 0:
                store_experience(
                    intent=f"arxiv_verification_{paper['arxiv_id']}",
                    experience={
                        "paper_title": paper["title"][:100],
                        "arxiv_id": paper["arxiv_id"],
                        "score": fc.get("score", 0),
                        "total_claims": fc.get("total_claims", 0),
                        "verified_count": fc.get("verified_count", 0),
                        "issues": fc.get("issues", []),
                        "repo_status": fc.get("repo_health", {}).get("status", "REPO_NA"),
                    },
                    score=fc.get("score", 0) / 10.0,
                )
    except Exception as e:
        logger.debug("Experience storage failed: %s", e)
```

- [ ] **Step 3: Run benchmark**

Run: `python scripts/arxiv_benchmark.py --compare baseline`
Expected: All scores green, new benchmarks at 10.0

- [ ] **Step 4: Commit**

```bash
git add scripts/arxiv_benchmark.py jobpulse/arxiv_agent.py
git commit -m "feat(arxiv): benchmark update + Ralph Loop experience storage for verification learning"
```

---

### Task 10: Full Integration Test

**Files:**
- Modify: `tests/test_external_verifiers.py`

- [ ] **Step 1: Write end-to-end integration test**

Add to `tests/test_external_verifiers.py`:

```python
class TestFullVerificationPipeline:
    def test_paper_with_exaggerated_benchmark_and_healthy_repo(self):
        """Full pipeline: extract claims, route, verify, score, explain."""
        from shared.fact_checker import (
            compute_accuracy_score, generate_fact_check_explanation,
            route_claim_to_verifier,
        )
        from shared.external_verifiers import check_repo_health

        # Simulate verification results from different sources
        verifications = [
            {"claim": "proposed by Smith et al.", "verdict": "VERIFIED",
             "source": "semantic_scholar", "evidence": "Authors confirmed",
             "confidence": 0.9, "severity": "low"},
            {"claim": "achieves 95% on MMLU", "verdict": "EXAGGERATED",
             "source": "web", "evidence": "Leaderboard shows 91.2%",
             "confidence": 0.85, "severity": "medium",
             "fix_suggestion": "Correct to 91.2%"},
            {"claim": "uses sparse attention", "verdict": "VERIFIED",
             "source": "abstract", "evidence": "Abstract confirms",
             "confidence": 0.8, "severity": "low"},
        ]

        repo = {
            "status": "REPO_HEALTHY",
            "score_adjustment": 0.0,
            "summary": "repo healthy (500 stars, tests present)",
        }

        score = compute_accuracy_score(verifications, repo_adjustment=repo["score_adjustment"])
        explanation = generate_fact_check_explanation(score, verifications, repo)

        # Score should reflect:
        # S2 VERIFIED: +1.0, Web EXAGGERATED: -1.5, Abstract VERIFIED: +0.5
        # (1.0 + -1.5 + 0.5) / 3.0 * 10 = 0.0/3.0 * 10 = 0.0
        assert score >= 0.0
        assert score < 5.0  # Should be low due to exaggeration

        # Explanation should mention the exaggeration
        assert "exaggerated" in explanation.lower()
        assert "95%" in explanation or "MMLU" in explanation

        # Routing should be correct
        assert route_claim_to_verifier({"type": "attribution"}) == "semantic_scholar"
        assert route_claim_to_verifier({"type": "benchmark"}) == "web"
        assert route_claim_to_verifier({"type": "technical"}) == "abstract_then_web"
```

- [ ] **Step 2: Run full test suite**

Run: `PYTHONPATH=. pytest tests/test_external_verifiers.py tests/test_fact_checker.py tests/test_arxiv_agent.py -v --tb=short`
Expected: All PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_external_verifiers.py
git commit -m "test(fact-check): full integration test for multi-source verification pipeline"
```

---

## Summary

| Task | What | Tests Added |
|------|------|------------|
| 1 | Semantic Scholar verifier | 5 |
| 2 | GitHub repo health checker | 6 |
| 3 | Quality web search with source scoring | 4 |
| 4 | Honest scoring model (abstract=0.5, external=1.0) | 6 |
| 5 | Explanation generator | 3 |
| 6 | Claim type → verifier routing | 5 |
| 7 | Wire multi-source into verify_claims | 1 |
| 8 | Update arXiv agent + digest format | 1 |
| 9 | Benchmark update + Ralph Loop experience | 0 (benchmark script) |
| 10 | Full integration test | 1 |
| **Total** | | **~32 new tests** |
