"""Project Portfolio — maps GitHub repos to CV-ready entries.

Each project has a title line, URL, and 3-5 bullet points with metrics.
Used by the CV generator to dynamically select projects per JD.

Public API:
  get_best_projects_for_jd(required_skills, preferred_skills, top_n=4) -> list[dict]
  get_project_entry(repo_name) -> dict | None
"""

from __future__ import annotations

from shared.logging_config import get_logger

logger = get_logger(__name__)


# Note: _multi_agent_entry() previously built a hardcoded portfolio entry
# for one specific repo. Removed 2026-05-04 — the data lives in
# user_profile.db.cv_projects via the nightly profile-sync.


# ---------------------------------------------------------------------------
# Portfolio — DB-first loader, with the static dict below as a one-time
# bootstrap fallback for fresh installs.
#
# CANONICAL SOURCE OF TRUTH: data/user_profile.db → cv_projects table,
# accessed via shared.profile_store.ProfileStore.cv_projects(). Code below
# tries that first; only falls back to the static dict if the DB is empty
# (e.g. before profile-sync has run).
#
# Per pii-policy.md: project URLs / titles / bullets are PII (they reveal
# the user's GitHub username, employment history, and technical specifics).
# We keep the legacy dict in code only as a bootstrap so the system isn't
# broken pre-DB-init; production runs always read from the DB.
# ---------------------------------------------------------------------------


def _load_portfolio_from_db() -> dict[str, dict]:
    """Load all CV-ready project entries from user_profile.db at runtime.

    Returns: dict keyed by `<github_user>/<repo_slug>` (extracted from URL),
    each value matching the static-dict schema (`title`, `url`, `bullets`).
    Empty dict if ProfileStore is unavailable or DB has no entries.
    """
    out: dict[str, dict] = {}
    try:
        from shared.profile_store import get_profile_store
        store = get_profile_store()
        for proj in store.cv_projects() or []:
            url = (proj.get("url") or "").strip()
            if not url:
                continue
            # Extract `<owner>/<repo>` from a github URL — the static dict uses
            # this as its key, so we match the same convention so callers see
            # identical lookup semantics.
            tail = url.rstrip("/").rsplit("github.com/", 1)[-1]
            if "/" not in tail:
                continue
            key = "/".join(tail.split("/")[:2])
            out[key] = {
                "title": (proj.get("title") or "").strip(),
                "url": url,
                "bullets": list(proj.get("bullets") or []),
            }
    except Exception as exc:
        logger.debug("project_portfolio: DB load failed: %s — using static fallback", exc)
    return out


# Module-level cache populated on first lookup. Reset by tests with monkeypatch
# if needed. Cleared on every run because the DB can be updated externally
# (profile sync, manual SQL).
_PORTFOLIO_DB_CACHE: dict[str, dict] | None = None


def _portfolio_lookup(repo_name: str) -> dict | None:
    """DB-first portfolio lookup. Falls back to the legacy static dict only
    when the DB has no entry for this repo (e.g. fresh install)."""
    global _PORTFOLIO_DB_CACHE
    if _PORTFOLIO_DB_CACHE is None:
        _PORTFOLIO_DB_CACHE = _load_portfolio_from_db()
    if repo_name in _PORTFOLIO_DB_CACHE:
        return _PORTFOLIO_DB_CACHE[repo_name]
    return _LEGACY_PORTFOLIO_FALLBACK.get(repo_name)


# Legacy static fallback — only consulted when the DB has no matching entry.
# DO NOT add new entries here; populate user_profile.db.cv_projects instead.
_LEGACY_PORTFOLIO_FALLBACK: dict[str, dict] = {}
# All project data lives in user_profile.db.cv_projects (loaded via
# _load_portfolio_from_db). To re-populate after a fresh install run:
#   python -m jobpulse.runner profile-sync
# The empty fallback dict above is intentional — it forces every lookup
# through the DB so PII never re-enters source code.


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_project_entry(repo_name: str) -> dict | None:
    """Look up a single project's CV entry by repo name.

    DB-first via `_portfolio_lookup`. Returns None if the repo isn't in the
    DB or the static fallback dict.
    """
    return _portfolio_lookup(repo_name)


def get_best_projects_for_jd(
    required_skills: list[str],
    preferred_skills: list[str] | None = None,
    top_n: int = 4,
    archetype: str | None = None,
) -> list[dict]:
    """Select the top N projects that best match the JD skills.

    Uses SkillGraphStore to find projects with highest skill overlap,
    then looks up CV-ready entries from PORTFOLIO + auto-generated entries.

    When archetype is provided, swaps in archetype-specific bullet variants
    so the CV emphasises the aspects most relevant to the JD type.

    Returns list of dicts matching generate_cv_pdf's projects format:
        [{"title": "...", "url": "...", "bullets": ["...", ...]}, ...]

    Falls back to DEFAULT_PROJECTS from generate_cv.py if no matches found.
    """
    from jobpulse.portfolio_variants import get_auto_entry, get_or_generate_variant_bullets
    from jobpulse.skill_graph_store import SkillGraphStore

    store = SkillGraphStore()
    all_skills = required_skills + (preferred_skills or [])

    try:
        matches = store.get_projects_for_skills(all_skills)
    except Exception as exc:
        logger.warning("project_portfolio: SkillGraphStore query failed: %s", exc)
        matches = []

    prioritized = []
    for match in matches:
        entry = _portfolio_lookup(match.name)
        if not entry:
            entry = get_auto_entry(match.name)
        if entry:
            # Priority is a soft boost (1.15x for priority=1), not a hard sort
            priority = entry.get("priority", 99)
            boost = 1.15 if priority == 1 else 1.0
            score = match.relevance_score * boost
            prioritized.append((score, match, entry))

    prioritized.sort(key=lambda x: -x[0])

    selected: list[dict] = []
    for _score, match, entry in prioritized:
        if len(selected) >= top_n:
            break
        if entry:
            numbered = dict(entry)
            if archetype:
                numbered["bullets"] = get_or_generate_variant_bullets(
                    match.name, archetype,
                    entry["title"], entry["bullets"],
                    required_skills,
                )
            numbered["title"] = f"{len(selected) + 1}. {entry['title']}"
            selected.append(numbered)

    if not selected:
        from jobpulse.cv_templates.generate_cv import DEFAULT_PROJECTS
        return DEFAULT_PROJECTS

    return selected


# ---------------------------------------------------------------------------
# Backwards compatibility — `PORTFOLIO` was historically a static dict.
# External callers (e.g. github_profile_sync.py) may still reference it.
# We expose a property-like merged view: DB entries override / extend the
# legacy fallback dict. Mutations against this view do NOT propagate to the
# DB; new entries should be inserted via `cv_projects` table directly.
# ---------------------------------------------------------------------------


def _portfolio_merged_view() -> dict[str, dict]:
    db_entries = _load_portfolio_from_db()
    merged = dict(_LEGACY_PORTFOLIO_FALLBACK)  # legacy first
    merged.update(db_entries)                  # DB wins on conflict
    return merged


# Module-level proxy that re-evaluates the merged view on every access. This
# preserves the historical `from project_portfolio import PORTFOLIO` import
# pattern without locking the module state to an old DB snapshot.
class _PortfolioProxy(dict):
    def __getitem__(self, key):
        return _portfolio_merged_view()[key]
    def __contains__(self, key):
        return key in _portfolio_merged_view()
    def get(self, key, default=None):
        return _portfolio_merged_view().get(key, default)
    def items(self):
        return _portfolio_merged_view().items()
    def keys(self):
        return _portfolio_merged_view().keys()
    def values(self):
        return _portfolio_merged_view().values()
    def __iter__(self):
        return iter(_portfolio_merged_view())
    def __len__(self):
        return len(_portfolio_merged_view())


PORTFOLIO: dict[str, dict] = _PortfolioProxy()  # type: ignore[assignment]
