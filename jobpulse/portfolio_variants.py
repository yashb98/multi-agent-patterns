"""Per-archetype bullet variants for CV projects.

Hand-crafted variants for hero projects ensure the #1 project always
shows the right framing. Auto-generated variants for other projects
are cached in data/portfolio_auto.json by the nightly sync.

Public API:
  get_variant_bullets(repo_name, archetype) -> list[str] | None
  generate_portfolio_entry(repo_name, description, readme, languages, url) -> dict | None
  generate_variant_bullets(title, bullets, archetype, archetype_keywords) -> list[str] | None
  load_auto_portfolio() -> dict
  save_auto_portfolio(data) -> None
"""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path

from shared.logging_config import get_logger


def _load_variants_from_db(repo_name: str, archetype: str) -> list[str] | None:
    """Look up archetype-specific bullets for a repo in user_profile.db.cv_variants.

    Per pii-policy.md, project bullets are PII (reveal user's GitHub repos
    and technical specifics). The canonical store is the cv_variants table.
    The static MANUAL_VARIANTS dict in this module remains as a bootstrap
    fallback for fresh installs only.

    Lookup strategy:
      1. Resolve repo_name → canonical URL via cv_projects (also DB-backed)
      2. Query cv_variants WHERE repo_url = ? AND archetype = ?
      3. Return parsed bullets or None
    """
    try:
        import sqlite3
        import json
        from pathlib import Path
        db_path = Path(__file__).parent.parent / "data" / "user_profile.db"
        if not db_path.exists():
            return None
        # Resolve URL from cv_projects if repo_name doesn't already look like a URL
        url = repo_name if repo_name.startswith("http") else f"https://github.com/{repo_name}"
        with sqlite3.connect(str(db_path)) as conn:
            row = conn.execute(
                "SELECT bullets FROM cv_variants WHERE repo_url = ? AND archetype = ?",
                (url, archetype),
            ).fetchone()
            if not row:
                return None
            try:
                return list(json.loads(row[0]))
            except Exception:
                return None
    except Exception:
        return None

logger = get_logger(__name__)

_AUTO_PATH = Path(__file__).parent.parent / "data" / "portfolio_auto.json"

# ---------------------------------------------------------------------------
# Hand-crafted variants for hero projects
# ---------------------------------------------------------------------------

# Note: _multi_agent_variants() previously held hardcoded archetype variant
# bullets for one specific repo. Removed 2026-05-04 — all variant data lives
# in user_profile.db.cv_variants now (loaded via _load_variants_from_db).


MANUAL_VARIANTS: dict[str, dict[str, list[str]]] = {}
# All archetype variant bullets live in user_profile.db.cv_variants
# (loaded via _load_variants_from_db). To re-populate after a fresh
# install run: python -m jobpulse.runner profile-sync
# Empty fallback intentional — forces all lookups through DB.


# ---------------------------------------------------------------------------
# Auto-generated portfolio cache
# ---------------------------------------------------------------------------


def load_auto_portfolio() -> dict:
    try:
        with open(_AUTO_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"entries": {}, "variants": {}, "last_synced": {}}


def save_auto_portfolio(data: dict) -> None:
    _AUTO_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_AUTO_PATH, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    logger.info("portfolio_variants: saved auto portfolio to %s", _AUTO_PATH)


# ---------------------------------------------------------------------------
# Public API: variant selection
# ---------------------------------------------------------------------------


def get_variant_bullets(repo_name: str, archetype: str) -> list[str] | None:
    """Return archetype-specific bullets for hero projects only (instant, no LLM).

    DB-first via `_load_variants_from_db`; falls back to the legacy
    MANUAL_VARIANTS dict only if cv_variants table has no row for this
    (repo_url, archetype) combination. Per pii-policy.md the canonical store
    is `user_profile.db.cv_variants`.

    For non-hero projects, returns None — caller should use
    get_or_generate_variant_bullets() for on-demand JD-aware generation.
    """
    db_bullets = _load_variants_from_db(repo_name, archetype)
    if db_bullets:
        return db_bullets
    return MANUAL_VARIANTS.get(repo_name, {}).get(archetype)


def get_auto_entry(repo_name: str) -> dict | None:
    """Return an auto-generated PORTFOLIO entry for a repo not in the manual PORTFOLIO."""
    auto = load_auto_portfolio()
    return auto.get("entries", {}).get(repo_name)


def get_or_generate_variant_bullets(
    repo_name: str,
    archetype: str,
    title: str,
    default_bullets: list[str],
    jd_skills: list[str],
) -> list[str]:
    """Return tailored bullets for a project based on archetype + JD skills.

    Priority:
    1. Manual variants (hero projects) — instant, no LLM
    2. Cached on-demand variants — instant, previously generated
    3. Generate on-demand via LLM using actual JD skills — cache for reuse

    Falls back to default_bullets if generation fails.
    """
    manual = _load_variants_from_db(repo_name, archetype) or \
        MANUAL_VARIANTS.get(repo_name, {}).get(archetype)
    if manual:
        return manual

    auto = load_auto_portfolio()
    cached = auto.get("variants", {}).get(repo_name, {}).get(archetype)
    if cached:
        return cached

    generated = _generate_jd_aware_bullets(title, default_bullets, archetype, jd_skills)
    if not generated:
        return default_bullets

    variants = auto.setdefault("variants", {})
    variants.setdefault(repo_name, {})[archetype] = generated
    save_auto_portfolio(auto)

    return generated


# ---------------------------------------------------------------------------
# On-demand LLM generation (called at CV time with actual JD context)
# ---------------------------------------------------------------------------

_VARIANT_PROMPT = """Reframe these CV project bullets for a {archetype_label} role. The job requires: {jd_skills}.

Project: {title}
Original bullets:
{bullets_text}

Rules:
- Write exactly 3 bullets
- Emphasise aspects matching the job's required skills: {jd_skills}
- Naturally weave in soft skills (teamwork, communication, decision making, adaptability, leadership) where they fit the project context. Do NOT list them, demonstrate them through action.
- Keep all quantified metrics from originals, reframe the narrative
- Wrap key metrics in <b> HTML tags
- Professional tone, no conversational language
- No em-dashes, en-dashes, or double dashes

Output ONLY a JSON list: ["bullet1", "bullet2", "bullet3"]"""

_ARCHETYPE_LABELS = {
    "agentic": "AI/Agentic Engineer",
    "data_scientist": "Data Scientist",
    "data_analyst": "Data Analyst",
    "ai_ml": "AI/ML Engineer",
    "data_engineer": "Data Engineer",
    "data_platform": "ML Platform/MLOps Engineer",
}


def _generate_jd_aware_bullets(
    title: str,
    bullets: list[str],
    archetype: str,
    jd_skills: list[str],
) -> list[str] | None:
    """Generate JD-tailored bullet variants via LLM at CV time. Cached
    for 14 days per (jd_skills + archetype + title + bullets) hash so
    re-applying the same JD skips the LLM."""

    label = _ARCHETYPE_LABELS.get(archetype)
    if not label:
        return None

    cache_key = _portfolio_cache_key(
        kind="bullets", title=title, archetype=archetype,
        bullets=bullets, jd_skills=jd_skills,
    )
    cached = _portfolio_variant_cache_lookup("bullets", cache_key)
    if cached is not None:
        try:
            parsed = json.loads(cached) if isinstance(cached, str) else cached
            if isinstance(parsed, list) and len(parsed) >= 2:
                return parsed[:4]
        except (TypeError, ValueError):
            pass

    from shared.agents import get_llm, smart_llm_call

    bullets_text = "\n".join(f"- {b}" for b in bullets)
    prompt = _VARIANT_PROMPT.format(
        title=title,
        bullets_text=bullets_text,
        archetype_label=label,
        jd_skills=", ".join(jd_skills[:15]),
    )

    try:
        llm = get_llm(model="gpt-5-mini", temperature=0.3, agent_name="portfolio_variants")
        response = smart_llm_call(llm, prompt)
        text = response.content if hasattr(response, "content") else str(response)

        start = text.find("[")
        end = text.rfind("]") + 1
        if start < 0 or end <= start:
            return None

        parsed = json.loads(text[start:end])
        if not isinstance(parsed, list) or len(parsed) < 2:
            return None

        result = parsed[:4]
        try:
            _portfolio_variant_cache_store(
                "bullets", cache_key, json.dumps(result),
            )
        except Exception as exc:
            logger.debug("portfolio_variants: cache store failed: %s", exc)
        return result
    except Exception as exc:
        logger.warning("portfolio_variants: on-demand generation failed for %s/%s: %s", title, archetype, exc)
        return None


# ---------------------------------------------------------------------------
# Portfolio entry generation (used by nightly sync for missing repos)
# ---------------------------------------------------------------------------

_ENTRY_PROMPT = """Generate a CV project entry for this GitHub repository. Output ONLY valid JSON.

Repository: {repo_name}
Description: {description}
Languages: {languages}
Topics: {topics}

README excerpt (first 2000 chars):
{readme}

Output format:
{{"title": "Project Name | Tech1 | Tech2 | Tech3", "bullets": ["bullet1", "bullet2", "bullet3"]}}

Rules:
- Title: concise project name + key technologies separated by pipes
- 3 achievement bullets with quantified metrics
- Wrap key metrics in <b> HTML tags (e.g., <b>95%</b>)
- Professional tone, no conversational language
- No em-dashes, en-dashes, or double dashes
- If README lacks metrics, estimate reasonable ones from the project scope"""


def generate_portfolio_entry(
    repo_name: str,
    description: str,
    readme_content: str,
    languages: list[str],
    topics: list[str],
    url: str,
) -> dict | None:
    """Generate a PORTFOLIO entry for a repo via LLM. Returns dict with
    title, url, bullets. Cached 14 days per (repo + content) hash —
    nightly profile-sync re-runs the same readme until a real edit
    bumps the hash."""

    cache_key = _portfolio_cache_key(
        kind="entry", repo_name=repo_name, description=description,
        readme_content=readme_content, languages=languages, topics=topics,
    )
    cached = _portfolio_variant_cache_lookup("entry", cache_key)
    if cached is not None:
        try:
            parsed = json.loads(cached) if isinstance(cached, str) else cached
            if isinstance(parsed, dict) and "title" in parsed and "bullets" in parsed:
                return {
                    "title": parsed["title"],
                    "url": url,
                    "bullets": parsed["bullets"][:4],
                }
        except (TypeError, ValueError):
            pass

    from shared.agents import get_llm, smart_llm_call

    prompt = _ENTRY_PROMPT.format(
        repo_name=repo_name.split("/")[-1],
        description=description or "No description",
        languages=", ".join(languages) if languages else "Python",
        topics=", ".join(topics) if topics else "N/A",
        readme=readme_content[:2000] if readme_content else "No README available",
    )

    try:
        llm = get_llm(model="gpt-5-mini", temperature=0.3, agent_name="portfolio_variants")
        response = smart_llm_call(llm, prompt)
        text = response.content if hasattr(response, "content") else str(response)

        start = text.find("{")
        end = text.rfind("}") + 1
        if start < 0 or end <= start:
            return None

        parsed = json.loads(text[start:end])
        if "title" not in parsed or "bullets" not in parsed:
            return None

        result = {
            "title": parsed["title"],
            "url": url,
            "bullets": parsed["bullets"][:4],
        }
        try:
            _portfolio_variant_cache_store(
                "entry", cache_key,
                json.dumps({"title": result["title"], "bullets": result["bullets"]}),
            )
        except Exception as exc:
            logger.debug("portfolio_variants: cache store failed: %s", exc)
        return result
    except Exception as exc:
        logger.warning("portfolio_variants: entry generation failed for %s: %s", repo_name, exc)
        return None


# ---------------------------------------------------------------------------
# portfolio_variant_cache (Items 6 + 7) — 14-day per-input cache
# ---------------------------------------------------------------------------
#
# Single SQLite table inside applications.db (lazily created), keyed by
# (kind, cache_key) where kind ∈ {"bullets", "entry"}. Same shape as
# tailored_cv_cache (S4 / commit 4509f6d) and cover_letter_cache
# (S5 / commit 7aba244): TTL → miss; hit_count incremented; test mode
# short-circuits to None when no JobDB is supplied.
#
# Why combined rather than two tables: keeping one table reduces
# migration churn — adding a new kind is just a new ``kind`` value, not
# a new schema migration.

import hashlib  # noqa: E402 — keep adjacent to the cache helpers


_PORTFOLIO_CACHE_TTL_DAYS = 14
_PORTFOLIO_CACHE_LOCK = threading.Lock()


def _portfolio_cache_key(*, kind: str, **inputs: object) -> str:
    """Stable SHA256 over the cache inputs. Sorting keys keeps the
    hash deterministic across Python versions."""

    h = hashlib.sha256()
    h.update(kind.encode("utf-8"))
    for k in sorted(inputs):
        v = inputs[k]
        if isinstance(v, list):
            v = "|".join(str(x) for x in v)
        elif v is None:
            v = ""
        h.update(b"|")
        h.update(str(k).encode("utf-8"))
        h.update(b"=")
        h.update(str(v).encode("utf-8"))
    return h.hexdigest()


def _portfolio_variant_cache_init(db) -> None:
    conn = db._connect()
    conn.execute(
        "CREATE TABLE IF NOT EXISTS portfolio_variant_cache ("
        "kind TEXT NOT NULL, cache_key TEXT NOT NULL, "
        "payload TEXT NOT NULL, generated_at TEXT NOT NULL, "
        "hit_count INTEGER NOT NULL DEFAULT 0, "
        "PRIMARY KEY (kind, cache_key))"
    )
    conn.commit()


def _portfolio_variant_cache_lookup(
    kind: str, cache_key: str, *, db=None,
) -> str | None:
    """Return cached payload string or ``None`` on miss / TTL expiry.

    JOBPULSE_TEST_MODE=1 with ``db=None`` short-circuits to None so
    unrelated test runs don't see prior cache entries — same guard as
    ``cv_tailor._tailored_cv_cache_lookup``.
    """

    import os as _os
    if not (kind and cache_key):
        return None
    if db is None and _os.environ.get("JOBPULSE_TEST_MODE") == "1":
        return None
    from jobpulse.job_db import JobDB
    db = db or JobDB()
    with _PORTFOLIO_CACHE_LOCK:
        _portfolio_variant_cache_init(db)
        conn = db._connect()
        row = conn.execute(
            "SELECT payload, generated_at FROM portfolio_variant_cache "
            "WHERE kind = ? AND cache_key = ?",
            (kind, cache_key),
        ).fetchone()
        if not row:
            return None
        try:
            generated = datetime.fromisoformat(row["generated_at"])
            if (datetime.now() - generated).days > _PORTFOLIO_CACHE_TTL_DAYS:
                return None
        except (ValueError, TypeError):
            return None
        conn.execute(
            "UPDATE portfolio_variant_cache SET hit_count = hit_count + 1 "
            "WHERE kind = ? AND cache_key = ?",
            (kind, cache_key),
        )
        conn.commit()
        return row["payload"]


def _portfolio_variant_cache_store(
    kind: str, cache_key: str, payload: str, *, db=None,
) -> None:
    """Persist a freshly-generated bullets / entry payload."""

    import os as _os
    if not (kind and cache_key and payload):
        return
    if db is None and _os.environ.get("JOBPULSE_TEST_MODE") == "1":
        return
    from jobpulse.job_db import JobDB
    db = db or JobDB()
    with _PORTFOLIO_CACHE_LOCK:
        _portfolio_variant_cache_init(db)
        conn = db._connect()
        conn.execute(
            "INSERT OR REPLACE INTO portfolio_variant_cache "
            "(kind, cache_key, payload, generated_at, hit_count) "
            "VALUES (?, ?, ?, ?, 0)",
            (kind, cache_key, payload, datetime.now().isoformat()),
        )
        conn.commit()
