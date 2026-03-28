"""GitHub Matcher — Task 6 of Job Autopilot pipeline.

Scores GitHub repos against JD requirements and picks the top N projects
to highlight in a CV. Uses synonym-aware matching via skill_synonyms.json.
"""

import json
from pathlib import Path

import httpx

from jobpulse.config import DATA_DIR, GITHUB_USERNAME, GITHUB_TOKEN
from shared.logging_config import get_logger

logger = get_logger(__name__)

_SYNONYMS_PATH = DATA_DIR / "skill_synonyms.json"
_REPO_CACHE_PATH = DATA_DIR / "github_repo_cache.json"


# ---------------------------------------------------------------------------
# Synonym helpers
# ---------------------------------------------------------------------------

def load_skill_synonyms() -> dict[str, list[str]]:
    """Load synonym mapping from data/skill_synonyms.json.

    Returns a dict mapping canonical skill -> [synonym, ...].
    Returns an empty dict if the file is missing or malformed.
    """
    if not _SYNONYMS_PATH.exists():
        logger.warning("skill_synonyms.json not found at %s", _SYNONYMS_PATH)
        return {}
    try:
        with _SYNONYMS_PATH.open(encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            logger.warning("skill_synonyms.json has unexpected format")
            return {}
        return data
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("Failed to load skill_synonyms.json: %s", exc)
        return {}


def _normalize(skill: str) -> str:
    """Lowercase, strip, replace hyphens/underscores with spaces."""
    return skill.lower().strip().replace("-", " ").replace("_", " ")


def _skill_match(skill: str, keywords: list[str], synonyms: dict[str, list[str]]) -> bool:
    """Return True if *skill* (or any of its synonyms) appears in *keywords*.

    The synonym dict maps canonical -> [synonyms].
    We build a full equivalence set for the skill and check intersection with
    the normalised keyword list.
    """
    norm_skill = _normalize(skill)
    norm_keywords = {_normalize(kw) for kw in keywords}

    # Equivalence set: start with the skill itself
    equiv: set[str] = {norm_skill}

    for canonical, alts in synonyms.items():
        norm_canonical = _normalize(canonical)
        norm_alts = {_normalize(a) for a in alts}
        full_group = {norm_canonical} | norm_alts
        if norm_skill in full_group:
            equiv |= full_group

    return bool(equiv & norm_keywords)


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def score_repo(
    repo: dict,
    jd_required: list[str],
    jd_preferred: list[str],
) -> float:
    """Score a repo against JD skill requirements.

    score = required_match * 0.5 + preferred_match * 0.3 + keyword_density * 0.2

    *repo* must contain: name, description, languages, topics, keywords.
    """
    synonyms = load_skill_synonyms()

    # All searchable text for this repo (flattened, normalised)
    all_keywords: list[str] = (
        repo.get("keywords", [])
        + repo.get("languages", [])
        + repo.get("topics", [])
    )

    # --- Required match ratio (0-1) ---
    if jd_required:
        required_hits = sum(
            1 for skill in jd_required
            if _skill_match(skill, all_keywords, synonyms)
        )
        required_match = required_hits / len(jd_required)
    else:
        required_match = 0.0

    # --- Preferred match ratio (0-1) ---
    if jd_preferred:
        preferred_hits = sum(
            1 for skill in jd_preferred
            if _skill_match(skill, all_keywords, synonyms)
        )
        preferred_match = preferred_hits / len(jd_preferred)
    else:
        preferred_match = 0.0

    # --- Keyword density: fraction of JD skills covered by this repo ---
    all_jd = jd_required + jd_preferred
    if all_jd:
        density_hits = sum(
            1 for skill in all_jd
            if _skill_match(skill, all_keywords, synonyms)
        )
        keyword_density = density_hits / len(all_jd)
    else:
        keyword_density = 0.0

    score = required_match * 0.5 + preferred_match * 0.3 + keyword_density * 0.2
    logger.debug(
        "repo=%s req=%.2f pref=%.2f density=%.2f score=%.3f",
        repo.get("name"),
        required_match,
        preferred_match,
        keyword_density,
        score,
    )
    return score


# ---------------------------------------------------------------------------
# Project selection
# ---------------------------------------------------------------------------

def pick_top_projects(
    repos: list[dict],
    jd_required: list[str],
    jd_preferred: list[str],
    top_n: int = 4,
) -> list[dict]:
    """Score all repos and return the top *top_n* sorted by relevance (desc)."""
    scored = sorted(
        repos,
        key=lambda r: score_repo(r, jd_required, jd_preferred),
        reverse=True,
    )
    return scored[:top_n]


# ---------------------------------------------------------------------------
# GitHub API fetch + cache
# ---------------------------------------------------------------------------

def fetch_and_cache_repos() -> list[dict]:
    """Fetch public repos from GitHub API for GITHUB_USERNAME, cache locally.

    Returns a list of repo dicts with: name, description, languages, topics, keywords.
    Uses GITHUB_TOKEN if available. Falls back to cached data on failure.
    """
    headers: dict[str, str] = {"Accept": "application/vnd.github+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"

    repos: list[dict] = []

    try:
        url = f"https://api.github.com/users/{GITHUB_USERNAME}/repos?per_page=100&type=public"
        with httpx.Client(timeout=30) as client:
            response = client.get(url, headers=headers)
            response.raise_for_status()
            raw_repos: list[dict] = response.json()

        for raw in raw_repos:
            # Fetch language breakdown
            lang_url = raw.get("languages_url", "")
            languages: list[str] = []
            if lang_url:
                try:
                    with httpx.Client(timeout=10) as client:
                        lang_resp = client.get(lang_url, headers=headers)
                        lang_resp.raise_for_status()
                        languages = [l.lower() for l in lang_resp.json()]
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Could not fetch languages for %s: %s", raw.get("name"), exc)

            topics: list[str] = [t.lower() for t in raw.get("topics", [])]
            description: str = raw.get("description") or ""

            # keywords = languages + topics + description tokens (alpha only)
            desc_tokens = [
                w.lower() for w in description.split()
                if w.isalpha() and len(w) > 2
            ]
            keywords = list({*languages, *topics, *desc_tokens})

            repos.append({
                "name": raw.get("full_name", raw.get("name", "")),
                "description": description,
                "languages": languages,
                "topics": topics,
                "keywords": keywords,
                "stars": raw.get("stargazers_count", 0),
                "url": raw.get("html_url", ""),
            })

        # Persist cache
        _REPO_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _REPO_CACHE_PATH.open("w", encoding="utf-8") as fh:
            json.dump(repos, fh, indent=2)
        logger.info("Cached %d repos to %s", len(repos), _REPO_CACHE_PATH)

    except Exception as exc:  # noqa: BLE001
        logger.warning("GitHub API fetch failed (%s). Trying cache.", exc)
        if _REPO_CACHE_PATH.exists():
            with _REPO_CACHE_PATH.open(encoding="utf-8") as fh:
                repos = json.load(fh)
            logger.info("Loaded %d repos from cache", len(repos))
        else:
            logger.error("No cache available and fetch failed.")

    return repos
